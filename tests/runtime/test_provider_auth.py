"""Hermetic contract for the pre-start provider authentication gate.

Nothing here starts a provider, reads a credential, or touches a network: every
process result is constructed in-test, so the suite proves the controller's own
rules rather than the machine it happens to run on.
"""

from __future__ import annotations

import os
import threading
import time
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import pytest

import agent_commons.runtime.provider_auth as provider_auth_module
from agent_commons.errors import ConfigurationError
from agent_commons.platform_support import try_lock_exclusive, unlock
from agent_commons.runtime import (
    PROVIDER_AUTH_MAX_OUTPUT_BYTES,
    BuiltinProfileId,
    CancellationToken,
    ClaudeAuthAdapter,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexAuthAdapter,
    CodexRunnerProfile,
    CodexSandbox,
    DiagnosticCode,
    ProcessResult,
    ProviderAuthController,
    ProviderAuthOperation,
    ProviderAuthState,
    RunOutcome,
    RunReason,
    SubprocessRunner,
    default_profile_registry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService

_SECRET = "sk-ant-oat01-do-not-persist-this"


def _result(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    outcome: RunOutcome = RunOutcome.SUCCEEDED,
    reason: RunReason = RunReason.COMPLETED,
    output_truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        outcome=outcome,
        reason=reason,
        exit_code=0 if outcome is RunOutcome.SUCCEEDED else 1,
        pid=4242,
        duration_seconds=0.01,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes_seen=len(stdout),
        stderr_bytes_seen=len(stderr),
        output_truncated=output_truncated,
    )


class FakeAuthRunner:
    """A process runner that returns scripted results and records argv."""

    def __init__(self, *results: ProcessResult) -> None:
        self.results = list(results) or [_result()]
        self.invocations: list[tuple[str, ...]] = []
        self.stdins: list[bytes] = []
        self.timeouts: list[int] = []

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        self.invocations.append(tuple(invocation.argv))
        self.stdins.append(invocation.stdin)
        self.timeouts.append(int(values["timeout_seconds"]))
        index = min(len(self.invocations) - 1, len(self.results) - 1)
        return self.results[index]


class BlockingAuthRunner:
    """Holds the first probe inside the runner so a second can contend."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation, values
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=10)
        return _result(stdout=b'{"loggedIn":true}\n')


def _claude_profile(executable: str = "/bin/echo") -> ClaudeRunnerProfile:
    return ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable=executable,
        mcp_executable="/bin/echo",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )


def _controller(runner: Any, **values: Any) -> ProviderAuthController:
    return ProviderAuthController(runner=runner, **values)


def _cross_process_status(
    executable: str,
    workspace: str,
    lock_root: str,
    results: Any,
) -> None:
    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=Path(lock_root),
    )
    status = controller.status(
        _claude_profile(executable),
        workspace_root=Path(workspace),
    )
    results.put(status.state.value)


def _cross_process_login(
    executable: str,
    workspace: str,
    lock_root: str,
    results: Any,
) -> None:
    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=Path(lock_root),
    )
    status = controller.login(
        _claude_profile(executable),
        workspace_root=Path(workspace),
    )
    results.put(status.state.value)


def test_ready_output_reports_ready_and_never_blocks(tmp_path: Path) -> None:
    runner = FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n'))
    status = _controller(runner).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.READY
    assert status.blocks_launch is False
    assert status.diagnostic.code is DiagnosticCode.NONE


def test_positive_status_with_nonzero_exit_fails_closed(tmp_path: Path) -> None:
    runner = FakeAuthRunner(
        _result(
            stdout=b'{"loggedIn":true}\n',
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        )
    )

    status = _controller(runner).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.FAILED
    assert status.blocks_launch is True


def test_signed_out_output_blocks_and_carries_fixed_recovery(tmp_path: Path) -> None:
    runner = FakeAuthRunner(
        _result(
            stdout=b'{"loggedIn":false,"email":"must be ignored"}\n',
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        )
    )
    status = _controller(runner).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.AUTHENTICATION_REQUIRED
    assert status.blocks_launch is True
    assert status.diagnostic.code is DiagnosticCode.PROVIDER_AUTH_REQUIRED
    assert status.diagnostic.safe_next_actions


def test_boolean_false_is_authentication_required_even_on_exit_zero(tmp_path: Path) -> None:
    runner = FakeAuthRunner(_result(stdout=b'{"loggedIn":false}\n'))
    status = _controller(runner).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.AUTHENTICATION_REQUIRED


@pytest.mark.parametrize(
    "result",
    [
        _result(stdout=b"{}\n"),
        _result(stdout=b"\xff\xfe binary noise \x00\n"),
        _result(stdout=b"", stderr=b"", outcome=RunOutcome.FAILED, reason=RunReason.NONZERO_EXIT),
        _result(
            stdout=b"error: unknown command 'auth'\n",
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        ),
    ],
)
def test_malformed_output_fails_closed_to_unknown(tmp_path: Path, result: ProcessResult) -> None:
    status = _controller(FakeAuthRunner(result)).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.FAILED
    assert status.state.determinate is False
    assert status.blocks_launch is True
    assert status.diagnostic.code is DiagnosticCode.PROVIDER_AUTH_UNKNOWN


def test_oversized_and_truncated_output_is_never_read_as_ready(tmp_path: Path) -> None:
    flood = b'{"loggedIn":true}\n' * 4096
    assert len(flood) > PROVIDER_AUTH_MAX_OUTPUT_BYTES

    oversized = _controller(FakeAuthRunner(_result(stdout=flood))).status(
        _claude_profile(), workspace_root=tmp_path
    )
    truncated = _controller(
        FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n', output_truncated=True))
    ).status(_claude_profile(), workspace_root=tmp_path)

    assert oversized.state is ProviderAuthState.FAILED
    assert truncated.state is ProviderAuthState.FAILED


def test_timeout_and_cancellation_are_distinct_indeterminate_states(tmp_path: Path) -> None:
    timed_out = _controller(
        FakeAuthRunner(
            _result(
                stdout=b'{"loggedIn":true}\n',
                outcome=RunOutcome.TIMED_OUT,
                reason=RunReason.TIMEOUT,
            )
        )
    ).status(_claude_profile(), workspace_root=tmp_path)
    cancelled = _controller(
        FakeAuthRunner(
            _result(
                stdout=b'{"loggedIn":true}\n',
                outcome=RunOutcome.CANCELLED,
                reason=RunReason.CANCELLED,
            )
        )
    ).status(_claude_profile(), workspace_root=tmp_path)

    assert timed_out.state is ProviderAuthState.TIMED_OUT
    assert cancelled.state is ProviderAuthState.CANCELLED
    for status in (timed_out, cancelled):
        # A provider that never answered cannot be reported as authenticated.
        assert status.state.determinate is False
        assert status.blocks_launch is True


def test_second_caller_never_starts_a_second_auth_process(tmp_path: Path) -> None:
    runner = BlockingAuthRunner()
    controller = _controller(runner)
    outcomes: list[ProviderAuthState] = []

    def probe() -> None:
        outcomes.append(
            controller.status(_claude_profile(), workspace_root=tmp_path).state,
        )

    holder = threading.Thread(target=probe, daemon=True)
    holder.start()
    assert runner.entered.wait(timeout=10)

    contender = threading.Thread(target=probe, daemon=True)
    contender.start()
    time.sleep(0.05)
    runner.release.set()
    holder.join(timeout=10)
    contender.join(timeout=10)

    assert runner.calls == 1
    assert outcomes == [ProviderAuthState.READY, ProviderAuthState.READY]


def test_login_waits_for_status_then_starts_real_login_flow(tmp_path: Path) -> None:
    runner = BlockingAuthRunner()
    controller = _controller(runner)
    status_outcomes: list[ProviderAuthState] = []
    login_outcomes: list[ProviderAuthState] = []

    status_thread = threading.Thread(
        target=lambda: status_outcomes.append(
            controller.status(_claude_profile(), workspace_root=tmp_path).state
        ),
        daemon=True,
    )
    status_thread.start()
    assert runner.entered.wait(timeout=10)
    login_thread = threading.Thread(
        target=lambda: login_outcomes.append(
            controller.login(_claude_profile(), workspace_root=tmp_path).state
        ),
        daemon=True,
    )
    login_thread.start()
    time.sleep(0.05)
    assert runner.calls == 1

    runner.release.set()
    status_thread.join(timeout=10)
    login_thread.join(timeout=10)

    assert status_outcomes == [ProviderAuthState.READY]
    assert login_outcomes == [ProviderAuthState.READY]
    # Initial status, actual login, and the fixed post-login status proof.
    assert runner.calls == 3


def test_status_reports_authenticating_while_provider_login_owns_single_flight(
    tmp_path: Path,
) -> None:
    runner = BlockingAuthRunner()
    controller = _controller(runner)
    outcomes: list[ProviderAuthState] = []

    def login() -> None:
        outcomes.append(
            controller.login(_claude_profile(), workspace_root=tmp_path).state,
        )

    holder = threading.Thread(target=login, daemon=True)
    holder.start()
    assert runner.entered.wait(timeout=10)

    contender = controller.status(_claude_profile(), workspace_root=tmp_path)
    runner.release.set()
    holder.join(timeout=10)

    assert contender.state is ProviderAuthState.AUTHENTICATING
    assert contender.blocks_launch is True
    # Login completion is proven by a fixed status recheck under the same lock.
    assert runner.calls == 2
    assert outcomes == [ProviderAuthState.READY]


def test_provider_output_never_reaches_the_read_projection(tmp_path: Path) -> None:
    runner = FakeAuthRunner(
        _result(
            stdout=(
                f'{{"loggedIn":true,"email":"operator@example.com","token":"{_SECRET}"}}\n'
            ).encode(),
            stderr=f"trace: /Users/operator/.claude/{_SECRET}\n".encode(),
        )
    )
    status = _controller(runner).status(_claude_profile(), workspace_root=tmp_path)
    rendered = repr(status) + repr(status.as_dict())

    assert status.state is ProviderAuthState.READY
    assert _SECRET not in rendered
    assert "operator@example.com" not in rendered
    assert ".claude" not in rendered
    assert set(status.as_dict()) == {
        "provider",
        "operation",
        "state",
        "supported",
        "blocks_launch",
        "diagnostic_code",
        "diagnostic_hint",
        "safe_next_actions",
    }


def test_operations_are_fixed_argv_with_no_caller_supplied_material(tmp_path: Path) -> None:
    runner = FakeAuthRunner(
        _result(stdout=b'{"loggedIn":true}\n'),
        _result(stdout=b"browser login completed\n"),
        _result(stdout=b'{"loggedIn":true}\n'),
    )
    controller = _controller(runner, login_timeout_seconds=42, status_timeout_seconds=7)
    profile = _claude_profile()

    controller.status(profile, workspace_root=tmp_path)
    controller.login(profile, workspace_root=tmp_path)

    resolved = str(Path("/bin/echo").resolve())
    assert runner.invocations == [
        (resolved, "auth", "status", "--json"),
        (resolved, "auth", "login", "--claudeai"),
        (resolved, "auth", "status", "--json"),
    ]
    assert runner.stdins == [b"", b"", b""]
    assert runner.timeouts == [7, 42, 7]


def test_adapter_operations_are_a_closed_set() -> None:
    adapter = ClaudeAuthAdapter()

    assert adapter.arguments(ProviderAuthOperation.STATUS) == ("auth", "status", "--json")
    with pytest.raises(ValueError):
        adapter.arguments("logout")  # type: ignore[arg-type]


def test_codex_fixed_status_and_login_contract(tmp_path: Path) -> None:
    runner = FakeAuthRunner(
        _result(stdout=b"Logged in using ChatGPT\n"),
        _result(stdout=b"browser login completed\n"),
        _result(stdout=b"Logged in using ChatGPT\n"),
    )
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        sandbox=CodexSandbox.READ_ONLY,
    )
    controller = _controller(runner)

    status = controller.status(profile, workspace_root=tmp_path)
    login = controller.login(profile, workspace_root=tmp_path)

    assert status.state is ProviderAuthState.READY
    assert login.state is ProviderAuthState.READY
    resolved = str(Path("/bin/echo").resolve())
    assert runner.invocations == [
        (resolved, "login", "status"),
        (resolved, "login"),
        (resolved, "login", "status"),
    ]


def test_codex_signed_out_and_inconsistent_status_fail_closed(tmp_path: Path) -> None:
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        sandbox=CodexSandbox.READ_ONLY,
    )
    signed_out = _controller(
        FakeAuthRunner(
            _result(
                stderr=b"Not logged in\n",
                outcome=RunOutcome.FAILED,
                reason=RunReason.NONZERO_EXIT,
            )
        )
    ).status(profile, workspace_root=tmp_path)
    inconsistent = _controller(
        FakeAuthRunner(
            _result(
                stdout=b"Logged in using ChatGPT\n",
                outcome=RunOutcome.FAILED,
                reason=RunReason.NONZERO_EXIT,
            )
        )
    ).status(profile, workspace_root=tmp_path)
    contradictory = _controller(
        FakeAuthRunner(
            _result(
                stdout=b"Logged in using ChatGPT\n",
                stderr=b"Not logged in\n",
            )
        )
    ).status(profile, workspace_root=tmp_path)

    assert signed_out.state is ProviderAuthState.AUTHENTICATION_REQUIRED
    assert inconsistent.state is ProviderAuthState.FAILED
    assert contradictory.state is ProviderAuthState.FAILED


def test_codex_accepts_exact_success_marker_from_bounded_stderr(tmp_path: Path) -> None:
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        sandbox=CodexSandbox.READ_ONLY,
    )
    status = _controller(FakeAuthRunner(_result(stderr=b"Logged in using ChatGPT\n"))).status(
        profile, workspace_root=tmp_path
    )

    assert status.state is ProviderAuthState.READY
    assert status.blocks_launch is False


def test_codex_adapter_operations_are_a_closed_set() -> None:
    adapter = CodexAuthAdapter()

    assert adapter.arguments(ProviderAuthOperation.STATUS) == ("login", "status")
    assert adapter.arguments(ProviderAuthOperation.LOGIN) == ("login",)


def test_unresolvable_executable_does_not_become_an_auth_verdict(tmp_path: Path) -> None:
    runner = FakeAuthRunner(_result(stdout=b"not logged in\n"))
    status = _controller(runner).status(
        _claude_profile(executable="/nonexistent/agent-commons-absent-provider"),
        workspace_root=tmp_path,
    )

    assert status.state is ProviderAuthState.FAILED
    assert runner.invocations == []


def test_lock_filesystem_failure_is_a_typed_closed_state(tmp_path: Path) -> None:
    invalid_root = tmp_path / "state-file"
    invalid_root.write_text("not a directory", encoding="utf-8")

    status = _controller(
        FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n')),
        lock_root=invalid_root,
    ).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.FAILED
    assert status.blocks_launch is True


@pytest.mark.parametrize("failure_point", ("fchmod", "unlock"))
def test_lock_failures_close_every_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    opened: list[int] = []
    original_open = os.open

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(provider_auth_module.os, "open", tracked_open)
    if failure_point == "fchmod":
        monkeypatch.setattr(
            provider_auth_module.os,
            "fchmod",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fchmod failed")),
        )
    else:
        monkeypatch.setattr(
            provider_auth_module,
            "unlock",
            lambda _descriptor: (_ for _ in ()).throw(OSError("unlock failed")),
        )

    status = _controller(
        FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n')),
        lock_root=tmp_path / "state",
    ).status(_claude_profile(), workspace_root=tmp_path)

    assert status.state is ProviderAuthState.FAILED
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_login_cleanup_failure_releases_both_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[int] = []
    original_open = os.open
    original_ftruncate = os.ftruncate
    truncate_calls = 0

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def fail_provider_clear(descriptor: int, length: int) -> None:
        nonlocal truncate_calls
        truncate_calls += 1
        if truncate_calls == 2:
            raise OSError("provider lock clear failed")
        original_ftruncate(descriptor, length)

    monkeypatch.setattr(provider_auth_module.os, "open", tracked_open)
    monkeypatch.setattr(provider_auth_module.os, "ftruncate", fail_provider_clear)
    runner = FakeAuthRunner(
        _result(stdout=b"browser login completed\n"),
        _result(stdout=b'{"loggedIn":true}\n'),
        _result(stdout=b'{"loggedIn":true}\n'),
    )
    controller = _controller(runner, lock_root=tmp_path / "state")

    login = controller.login(_claude_profile(), workspace_root=tmp_path)
    retry = controller.status(_claude_profile(), workspace_root=tmp_path)

    assert login.state is ProviderAuthState.FAILED
    assert retry.state is ProviderAuthState.READY
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_contended_provider_lock_closes_each_retry_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_dir = tmp_path / "state" / "runtime" / "provider-auth-locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / "claude.lock"
    holder = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    assert try_lock_exclusive(holder) is True
    opened: list[int] = []
    original_open = os.open

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(provider_auth_module.os, "open", tracked_open)
    try:
        status = _controller(
            FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n')),
            lock_root=tmp_path / "state",
            status_timeout_seconds=1,
        ).status(_claude_profile(), workspace_root=tmp_path)
    finally:
        unlock(holder)
        os.close(holder)

    assert status.state is ProviderAuthState.TIMED_OUT
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_two_process_controllers_serialize_the_same_provider(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock_root = tmp_path / "state"
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    calls = tmp_path / "calls"
    executable = tmp_path / "provider-auth-cross-process"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, time\n"
        f"entered = pathlib.Path({str(entered)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "with calls.open('a', encoding='utf-8') as stream:\n"
        "    stream.write('call\\n')\n"
        "entered.touch()\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
        "print('{\\\"loggedIn\\\":true}')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    context = get_context("spawn")
    child_results = context.Queue()
    child = context.Process(
        target=_cross_process_status,
        args=(str(executable), str(workspace), str(lock_root), child_results),
    )
    child.start()
    deadline = time.monotonic() + 10
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()

    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=lock_root,
    )
    parent_results: list[ProviderAuthState] = []
    parent = threading.Thread(
        target=lambda: parent_results.append(
            controller.status(
                _claude_profile(str(executable)),
                workspace_root=workspace,
            ).state
        ),
        daemon=True,
    )
    parent.start()
    time.sleep(0.1)
    assert calls.read_text(encoding="utf-8").splitlines() == ["call"]

    release.touch()
    child.join(timeout=10)
    parent.join(timeout=10)

    assert child.exitcode == 0
    assert child_results.get(timeout=1) == ProviderAuthState.READY.value
    assert parent_results == [ProviderAuthState.READY]
    assert calls.read_text(encoding="utf-8").splitlines() == ["call", "call"]


def test_cross_process_login_waits_for_status_then_really_starts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock_root = tmp_path / "state"
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    calls = tmp_path / "calls"
    executable = tmp_path / "provider-auth-cross-process-reverse"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, time\n"
        f"entered = pathlib.Path({str(entered)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "with calls.open('a', encoding='utf-8') as stream:\n"
        "    stream.write('call\\n')\n"
        "entered.touch()\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
        "print('{\\\"loggedIn\\\":true}')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    context = get_context("spawn")
    child_results = context.Queue()
    child = context.Process(
        target=_cross_process_status,
        args=(str(executable), str(workspace), str(lock_root), child_results),
    )
    child.start()
    deadline = time.monotonic() + 10
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()

    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=lock_root,
    )
    login_results: list[ProviderAuthState] = []
    login = threading.Thread(
        target=lambda: login_results.append(
            controller.login(
                _claude_profile(str(executable)),
                workspace_root=workspace,
            ).state
        ),
        daemon=True,
    )
    login.start()
    time.sleep(0.1)
    assert calls.read_text(encoding="utf-8").splitlines() == ["call"]

    release.touch()
    child.join(timeout=10)
    login.join(timeout=10)

    assert child.exitcode == 0
    assert child_results.get(timeout=1) == ProviderAuthState.READY.value
    assert login_results == [ProviderAuthState.READY]
    # Initial status, actual login, and fixed post-login status proof.
    assert calls.read_text(encoding="utf-8").splitlines() == ["call", "call", "call"]


def test_cross_process_status_reports_only_a_genuinely_live_login(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock_root = tmp_path / "state"
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    calls = tmp_path / "calls"
    executable = tmp_path / "provider-auth-live-login"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, time\n"
        f"entered = pathlib.Path({str(entered)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        "with calls.open('a', encoding='utf-8') as stream:\n"
        "    stream.write('call\\n')\n"
        "entered.touch()\n"
        "while not release.exists():\n"
        "    time.sleep(0.01)\n"
        "print('{\\\"loggedIn\\\":true}')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    context = get_context("spawn")
    child_results = context.Queue()
    child = context.Process(
        target=_cross_process_login,
        args=(str(executable), str(workspace), str(lock_root), child_results),
    )
    child.start()
    deadline = time.monotonic() + 10
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists()

    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=lock_root,
    )
    status = controller.status(_claude_profile(str(executable)), workspace_root=workspace)

    assert status.state is ProviderAuthState.AUTHENTICATING
    assert calls.read_text(encoding="utf-8").splitlines() == ["call"]
    release.touch()
    child.join(timeout=10)
    assert child.exitcode == 0
    assert child_results.get(timeout=1) == ProviderAuthState.READY.value
    assert calls.read_text(encoding="utf-8").splitlines() == ["call", "call"]


def _workspace(tmp_path: Path) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="provider-auth-gate")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="provider-auth-parent-session-1234",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
        ttl_seconds=3600,
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Exercise the pre-start provider auth gate",
        description="One exact target used only to reach the launch path.",
        acceptance_criteria=("the gate refuses a signed-out launch",),
        priority="high",
        idempotency_key="provider-auth-task",
    )
    return manager, task


def _delegation(manager: CommonsManager, task: dict[str, Any]) -> dict[str, Any]:
    manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key="provider-auth-review",
    )
    return manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="provider-auth-delegation",
    )


class RecordingProviderRunner:
    """Stands in for the paid provider; a signed-out launch must never call it."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation
        self.calls += 1
        values["on_started"](9100 + self.calls)
        return _result(stdout=b"provider content must remain ephemeral")


def _service(
    manager: CommonsManager,
    provider_runner: Any,
    auth_runner: Any,
) -> DelegationRuntimeService:
    return DelegationRuntimeService(
        manager,
        runner=provider_runner,
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
        provider_auth=_controller(auth_runner),
    )


def test_signed_out_launch_creates_no_child_session_attempt_or_provider_work(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    provider = RecordingProviderRunner()
    auth = FakeAuthRunner(
        _result(
            stdout=b'{"loggedIn":false}\n',
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        )
    )
    service = _service(manager, provider, auth)
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as refusal:
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="provider-auth-signed-out",
        )

    assert refusal.value.code == DiagnosticCode.PROVIDER_AUTH_REQUIRED.value
    assert refusal.value.safe_next_actions
    assert provider.calls == 0
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    current = manager.get_delegation(delegation["entity_ref"]["id"])
    assert current["state"] == "requested"


def test_the_single_late_auth_gate_refuses_before_any_attempt_is_reserved(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    provider = RecordingProviderRunner()
    auth = FakeAuthRunner(
        _result(
            stdout=b'{"loggedIn":false}\n',
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        ),
    )
    service = _service(manager, provider, auth)
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as refusal:
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="provider-auth-toctou",
        )

    assert refusal.value.code == DiagnosticCode.PROVIDER_AUTH_REQUIRED.value
    assert len(auth.invocations) == 1
    assert provider.calls == 0
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    assert manager.get_delegation(delegation["entity_ref"]["id"])["state"] == "requested"


def test_an_undetermined_state_refuses_without_spending_an_attempt(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    provider = RecordingProviderRunner()
    auth = FakeAuthRunner(_result(stdout=b"error: unknown command 'auth'\n"))
    service = _service(manager, provider, auth)

    with pytest.raises(ConfigurationError) as refusal:
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="provider-auth-unknown",
        )

    assert refusal.value.code == DiagnosticCode.PROVIDER_AUTH_UNKNOWN.value
    assert provider.calls == 0
    assert len(auth.invocations) == 1
    assert service.attempts.list_attempts() == ()


def test_a_ready_launch_checks_exactly_once_immediately_before_starting(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    provider = RecordingProviderRunner()
    auth = FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n'))
    service = _service(manager, provider, auth)

    service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="provider-auth-ready",
    )

    assert len(auth.invocations) == 1
    assert provider.calls == 1


def test_post_start_auth_failure_is_needs_operator_with_provider_auth(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)

    class AuthFailingProviderRunner(RecordingProviderRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            del invocation
            self.calls += 1
            values["on_started"](9200 + self.calls)
            return _result(
                stderr=b"Authentication failed: please log in\n",
                outcome=RunOutcome.FAILED,
                reason=RunReason.NONZERO_EXIT,
            )

    provider = AuthFailingProviderRunner()
    auth = FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n'))
    service = _service(manager, provider, auth)

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="provider-auth-post-start",
    )

    canonical = result["delegation"]
    assert provider.calls == 1
    assert canonical["state"] == "needs_operator"
    assert canonical["reason_code"] == "provider_auth"
    assert result["attempt"]["diagnostic_code"] == DiagnosticCode.PROVIDER_AUTH_FAILED.value
    # Never success, and this runtime has no channel that could resume it.
    assert canonical["state"] != "succeeded"
    assert canonical.get("result_refs") in (None, [])


def test_zero_exit_structured_auth_error_is_also_provider_auth(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)

    class StructuredAuthErrorRunner(RecordingProviderRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            del invocation
            self.calls += 1
            values["on_started"](9300 + self.calls)
            return _result(
                stdout=b'{"type":"result","is_error":true,"result":"Please run /login"}\n'
            )

    provider = StructuredAuthErrorRunner()
    auth = FakeAuthRunner(_result(stdout=b'{"loggedIn":true}\n'))
    service = _service(manager, provider, auth)

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="provider-auth-zero-exit",
    )

    assert result["process"]["outcome"] == "succeeded"
    assert result["delegation"]["state"] == "needs_operator"
    assert result["delegation"]["reason_code"] == "provider_auth"
    assert result["workflow_diagnostic_code"] == DiagnosticCode.PROVIDER_AUTH_FAILED.value


def test_provider_auth_status_seam_returns_only_bounded_values(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    del task
    service = _service(
        manager,
        RecordingProviderRunner(),
        FakeAuthRunner(_result(stdout=f'{{"loggedIn":true,"token":"{_SECRET}"}}\n'.encode())),
    )

    body = service.provider_auth_status("claude-independent-reviewer")

    assert body["state"] == ProviderAuthState.READY.value
    assert body["blocks_launch"] is False
    assert _SECRET not in repr(body)


def test_provider_auth_login_seam_returns_only_closed_status(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    del task
    service = _service(
        manager,
        RecordingProviderRunner(),
        FakeAuthRunner(
            _result(stdout=f"browser opened {_SECRET}\n".encode()),
            _result(stdout=b'{"loggedIn":true}\n'),
        ),
    )

    body = service.provider_auth_login("claude-independent-reviewer")

    assert body["operation"] == ProviderAuthOperation.LOGIN.value
    assert body["state"] == ProviderAuthState.READY.value
    assert _SECRET not in repr(body)


def test_signed_out_login_then_exact_retry_creates_only_one_attempt(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    provider = RecordingProviderRunner()
    auth = FakeAuthRunner(
        _result(
            stdout=b'{"loggedIn":false}\n',
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        ),
        _result(stdout=b"provider-owned browser completed\n"),
        _result(stdout=b'{"loggedIn":true}\n'),
        _result(stdout=b'{"loggedIn":true}\n'),
    )
    service = _service(manager, provider, auth)
    launch_key = "provider-auth-exact-retry"

    with pytest.raises(ConfigurationError):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key=launch_key,
        )
    assert service.attempts.list_attempts() == ()

    login = service.provider_auth_login("claude-independent-reviewer")
    assert login["state"] == ProviderAuthState.READY.value
    service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key=launch_key,
    )

    assert provider.calls == 1
    assert len(service.attempts.list_attempts()) == 1


def test_auth_output_is_absent_from_workspace_and_operational_state(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    del task
    service = _service(
        manager,
        RecordingProviderRunner(),
        FakeAuthRunner(
            _result(stdout=f"browser {_SECRET}\n".encode()),
            _result(stdout=b'{"loggedIn":true}\n'),
        ),
    )

    assert service.provider_auth_login("claude-independent-reviewer")["state"] == "ready"

    for root in (manager.repo_root / ".agent-commons", manager.paths.state_root):
        for path in root.rglob("*"):
            if path.is_file():
                assert _SECRET.encode() not in path.read_bytes(), path


def test_cancelling_real_auth_process_releases_single_flight_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "provider-auth-fixture"
    marker = tmp_path / "block"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        f"marker = pathlib.Path({str(marker)!r})\n"
        "if marker.exists():\n"
        "    time.sleep(30)\n"
        "else:\n"
        "    print('{\\\"loggedIn\\\":true}')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    marker.write_text("block", encoding="utf-8")
    controller = ProviderAuthController(
        runner=SubprocessRunner(poll_interval_seconds=0.01),
        lock_root=tmp_path / "state",
    )
    cancellation = CancellationToken()
    outcomes: list[ProviderAuthState] = []

    worker = threading.Thread(
        target=lambda: outcomes.append(
            controller.status(
                _claude_profile(str(executable)),
                workspace_root=workspace,
                cancellation=cancellation,
            ).state
        ),
        daemon=True,
    )
    worker.start()
    lock_path = tmp_path / "state" / "runtime" / "provider-auth-locks" / "claude.lock"
    deadline = time.monotonic() + 10
    while (not lock_path.exists() or not lock_path.read_bytes()) and time.monotonic() < deadline:
        time.sleep(0.01)
    cancellation.cancel()
    worker.join(timeout=10)

    assert outcomes == [ProviderAuthState.CANCELLED]
    marker.unlink()
    retry = controller.status(_claude_profile(str(executable)), workspace_root=workspace)
    assert retry.state is ProviderAuthState.READY
