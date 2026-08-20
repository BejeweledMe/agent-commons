from __future__ import annotations

import io
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from agent_commons.errors import ValidationError
from agent_commons.runtime import (
    BuiltinProfileId,
    CancellationToken,
    ProcessResult,
    Provider,
    RunnerInvocation,
    RunOutcome,
    RunReason,
    SafeEnvironment,
    SubprocessRunner,
)
from agent_commons.runtime.exec_gate import _EXEC_GATE_FRAME


class MemoryStream(io.BytesIO):
    def close(self) -> None:
        pass


class FakeProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", exit_code: int | None = 0):
        self.pid = 4321
        self.stdin = MemoryStream()
        self.stdout = MemoryStream(stdout)
        self.stderr = MemoryStream(stderr)
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.exit_code or 0

    def terminate(self) -> None:
        self.exit_code = -15

    def kill(self) -> None:
        self.exit_code = -9


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += duration


def invocation() -> RunnerInvocation:
    return RunnerInvocation(
        provider=Provider.CODEX,
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        argv=("/bin/echo",),
        stdin=b"Do bounded work",
    )


def test_safe_environment_cannot_be_constructed_with_arbitrary_keys() -> None:
    with pytest.raises(ValidationError, match="unsupported key"):
        SafeEnvironment((("ANTHROPIC_API_KEY", "secret"),))


def test_runner_uses_explicit_cwd_sanitized_child_identity_and_bounded_output(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    process = FakeProcess(stdout=b"abcdef", stderr=b"uvwxyz")

    def factory(argv: tuple[str, ...], cwd: Path, env: Mapping[str, str]) -> FakeProcess:
        captured.update(argv=argv, cwd=cwd, env=dict(env))
        return process

    environment = SafeEnvironment.from_host(
        {
            "PATH": "/usr/bin",
            "HOME": "/safe/home",
            "AGENT_COMMONS_SESSION_ID": "session.parent",
            "UNSAFE_TOKEN": "must-not-pass",
        }
    )
    runner = SubprocessRunner(environment=environment, process_factory=factory)

    def after_durable_start(pid: int) -> None:
        assert pid == process.pid
        assert process.stdin.getvalue() == b""

    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        timeout_seconds=10,
        max_output_bytes=8,
        on_started=after_durable_start,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert result.output_truncated is True
    assert result.stdout_bytes_seen + result.stderr_bytes_seen == 12
    assert len(result.stdout) + len(result.stderr) == 8
    assert process.stdin.getvalue() == _EXEC_GATE_FRAME + b"Do bounded work"
    assert tuple(captured["argv"])[-1] == "/bin/echo"
    assert "Do bounded work" not in " ".join(captured["argv"])
    assert captured["cwd"] == tmp_path.resolve()
    assert captured["env"] == {
        "HOME": "/safe/home",
        "PATH": "/usr/bin",
        "AGENT_COMMONS_SESSION_ID": "session.child00000000000000000000000001",
        "AGENT_COMMONS_DELEGATION_ID": "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    }


def test_runner_keeps_the_final_four_kib_of_stderr_even_when_shared_output_fills(
    tmp_path: Path,
) -> None:
    prefix = b"first-error\n"
    final = b"F" * 4096
    process = FakeProcess(stdout=b"x" * 8192, stderr=prefix + final)
    runner = SubprocessRunner(process_factory=lambda argv, cwd, env: process)

    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=10,
        max_output_bytes=8,
    )

    assert result.stderr_tail == final
    assert result.stderr_tail_truncated is True


def test_runner_cancels_the_process_group(tmp_path: Path) -> None:
    clock = Clock()
    token = CancellationToken()
    process = FakeProcess(exit_code=None)
    terminated: list[bool] = []

    def sleep(duration: float) -> None:
        clock.sleep(duration)
        token.cancel()

    def terminate(candidate: FakeProcess, *, force: bool) -> None:
        terminated.append(force)
        candidate.exit_code = -9 if force else -15

    runner = SubprocessRunner(
        environment=SafeEnvironment.from_mapping({"PATH": "/usr/bin"}),
        process_factory=lambda argv, cwd, env: process,
        terminator=terminate,
        monotonic=clock,
        sleeper=sleep,
    )
    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=10,
        max_output_bytes=100,
        cancellation=token,
    )
    assert result.outcome is RunOutcome.CANCELLED
    assert result.reason is RunReason.CANCELLED
    assert terminated == [False]


def test_runner_times_out_and_normalizes_start_failure(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess(exit_code=None)

    def terminate(candidate: FakeProcess, *, force: bool) -> None:
        candidate.exit_code = -9 if force else -15

    runner = SubprocessRunner(
        environment=SafeEnvironment.from_mapping({"PATH": "/usr/bin"}),
        process_factory=lambda argv, cwd, env: process,
        terminator=terminate,
        monotonic=clock,
        sleeper=clock.sleep,
        poll_interval_seconds=0.5,
    )
    timed_out = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=1,
        max_output_bytes=100,
    )
    assert timed_out.outcome is RunOutcome.TIMED_OUT
    assert timed_out.reason is RunReason.TIMEOUT

    def missing(argv, cwd, env):
        raise FileNotFoundError

    failed = SubprocessRunner(process_factory=missing).run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=1,
        max_output_bytes=100,
    )
    assert failed == ProcessResult(
        outcome=RunOutcome.FAILED,
        reason=RunReason.START_FAILED,
        exit_code=None,
        pid=None,
        duration_seconds=failed.duration_seconds,
        stdout=b"",
        stderr=b"",
        stdout_bytes_seen=0,
        stderr_bytes_seen=0,
        output_truncated=False,
    )


def test_runner_aborts_when_post_journal_lifecycle_hook_fails(tmp_path: Path) -> None:
    process = FakeProcess(exit_code=None)
    terminated: list[bool] = []

    def terminate(candidate: FakeProcess, *, force: bool) -> None:
        terminated.append(force)
        candidate.exit_code = -9 if force else -15

    runner = SubprocessRunner(
        process_factory=lambda argv, cwd, env: process,
        terminator=terminate,
    )

    def rejected_start(pid: int) -> None:
        assert pid == process.pid
        raise RuntimeError("canonical start rejected")

    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=10,
        max_output_bytes=100,
        on_started=rejected_start,
    )
    assert result.outcome is RunOutcome.FAILED
    assert result.reason is RunReason.CONTROL_ERROR
    assert terminated == [False]
    assert process.stdin.getvalue() == b""


class EofDrivenProcess:
    """A stdio-server-like child: replies slowly, exits only on stdin EOF.

    Models the FastMCP stdio loop precisely enough to reproduce the CI flake:
    if EOF arrives before the reply has been produced, teardown drops the
    reply and stdout stays empty; if EOF arrives after, the reply survives.
    """

    def __init__(self, reply: bytes, clock: Clock) -> None:
        self.pid = 4321
        self.reply = reply
        self.reply_ready = threading.Event()
        self.stdin_eof = threading.Event()
        self.stdin_closed_at: float | None = None
        self.stderr = MemoryStream()
        process = self

        class _Stdin(io.BytesIO):
            def close(self) -> None:
                if process.stdin_closed_at is None:
                    process.stdin_closed_at = clock.value
                process.stdin_eof.set()

        class _Stdout:
            def __init__(self) -> None:
                self._reply_sent = False

            def read1(self, size: int = -1) -> bytes:
                del size
                while True:
                    if process.reply_ready.is_set() and not self._reply_sent:
                        self._reply_sent = True
                        return process.reply
                    if process.stdin_eof.is_set():
                        return b""
                    time.sleep(0.001)

            read = read1

            def close(self) -> None:
                pass

        self.stdin = _Stdin()
        self.stdout = _Stdout()

    def poll(self) -> int | None:
        return 0 if self.stdin_eof.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self) -> None:
        self.stdin_eof.set()

    def kill(self) -> None:
        self.stdin_eof.set()


def test_runner_delivers_eof_only_after_the_probe_reply_arrives(tmp_path: Path) -> None:
    clock = Clock()
    reply = b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n'
    reply_at = 0.2
    process = EofDrivenProcess(reply, clock)

    def loaded_machine_sleep(duration: float) -> None:
        clock.sleep(duration)
        if clock.value >= reply_at:
            process.reply_ready.set()
        # Give the real drain thread time to consume the released reply.
        time.sleep(0.001)

    runner = SubprocessRunner(
        environment=SafeEnvironment.from_mapping({"PATH": "/usr/bin"}),
        process_factory=lambda argv, cwd, env: process,
        terminator=lambda candidate, *, force: candidate.stdin_eof.set(),
        monotonic=clock,
        sleeper=loaded_machine_sleep,
    )
    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=30,
        max_output_bytes=4096,
        close_stdin_when=lambda stdout: stdout.endswith(reply),
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout == reply
    assert process.stdin.getvalue() == _EXEC_GATE_FRAME + b"Do bounded work"
    assert process.stdin_closed_at is not None
    assert process.stdin_closed_at >= reply_at


def test_runner_still_closes_stdin_when_the_child_exits_before_the_reply(
    tmp_path: Path,
) -> None:
    closed: list[bool] = []

    class RecordingStdin(io.BytesIO):
        def close(self) -> None:
            closed.append(True)

    process = FakeProcess(exit_code=2)
    process.stdin = RecordingStdin()
    runner = SubprocessRunner(process_factory=lambda argv, cwd, env: process)

    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=10,
        max_output_bytes=100,
        close_stdin_when=lambda stdout: False,
    )

    assert result.outcome is RunOutcome.FAILED
    assert result.reason is RunReason.NONZERO_EXIT
    assert closed == [True]


def test_runner_closes_held_stdin_once_the_output_budget_is_exhausted(tmp_path: Path) -> None:
    clock = Clock()
    # Enough output to exhaust the budget without ever containing the line the
    # predicate waits for: the retained stdout is frozen from that point on, so
    # a run that kept stdin open could only end in a timeout.
    noise = b"n" * 8192
    process = EofDrivenProcess(noise, clock)
    process.reply_ready.set()

    def draining_sleep(duration: float) -> None:
        clock.sleep(duration)
        # Give the real drain thread time to consume the released output.
        time.sleep(0.001)

    runner = SubprocessRunner(
        environment=SafeEnvironment.from_mapping({"PATH": "/usr/bin"}),
        process_factory=lambda argv, cwd, env: process,
        terminator=lambda candidate, *, force: candidate.stdin_eof.set(),
        monotonic=clock,
        sleeper=draining_sleep,
    )
    result = runner.run(
        invocation(),
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=30,
        max_output_bytes=1024,
        close_stdin_when=lambda stdout: False,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert result.reason is RunReason.COMPLETED
    assert result.output_truncated is True
    assert process.stdin_closed_at is not None


def test_real_exec_gate_starts_provider_only_after_durable_hook(tmp_path: Path) -> None:
    marker = tmp_path / "provider-started.txt"
    provider_code = (
        "import os,pathlib,sys;"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())"
    )
    gated = RunnerInvocation(
        provider=Provider.CODEX,
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        argv=(sys.executable, "-I", "-c", provider_code, str(marker)),
        stdin=b"bounded review instruction",
    )

    def after_durable_start(_pid: int) -> None:
        time.sleep(0.1)
        assert not marker.exists()

    result = SubprocessRunner().run(
        gated,
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        timeout_seconds=10,
        max_output_bytes=1_024,
        on_started=after_durable_start,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert result.stdout == b"bounded review instruction"
    assert marker.read_text() == str(result.pid)


def test_real_exec_gate_never_starts_provider_when_durable_hook_fails(tmp_path: Path) -> None:
    marker = tmp_path / "provider-started.txt"
    provider_code = "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()"
    gated = RunnerInvocation(
        provider=Provider.CODEX,
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        argv=(sys.executable, "-I", "-c", provider_code, str(marker)),
        stdin=b"must remain undisclosed",
    )

    def rejected_start(_pid: int) -> None:
        time.sleep(0.1)
        assert not marker.exists()
        raise RuntimeError("canonical start rejected")

    result = SubprocessRunner().run(
        gated,
        cwd=tmp_path,
        child_session_id="session.child00000000000000000000000001",
        timeout_seconds=10,
        max_output_bytes=1_024,
        on_started=rejected_start,
    )

    assert result.outcome is RunOutcome.FAILED
    assert result.reason is RunReason.CONTROL_ERROR
    assert not marker.exists()
