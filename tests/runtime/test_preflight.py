from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_commons.mcp.server import (
    IMPLEMENTATION_WORKER_TOOL_NAMES,
    INDEPENDENT_REVIEW_WORKER_TOOL_NAMES,
    VERIFICATION_WORKER_TOOL_NAMES,
)
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    CodexSandbox,
    DiagnosticCode,
    ProcessResult,
    ProfileRegistry,
    RunOutcome,
    RunReason,
    preflight_profile,
)
from agent_commons.runtime.source_contract import agent_commons_source_sha256


def _result(*, output: bytes, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        outcome=RunOutcome.SUCCEEDED if exit_code == 0 else RunOutcome.FAILED,
        reason=RunReason.COMPLETED if exit_code == 0 else RunReason.NONZERO_EXIT,
        exit_code=exit_code,
        pid=123,
        duration_seconds=0.01,
        stdout=output,
        stderr=b"",
        stdout_bytes_seen=len(output),
        stderr_bytes_seen=0,
        output_truncated=False,
    )


_CLAUDE_HELP_FLAGS = (
    "--print",
    "--verbose",
    "--output-format",
    "--permission-mode",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--setting-sources",
    "--mcp-config",
    "--strict-mcp-config",
    "--allowed-tools",
    "--disallowed-tools",
    "--tools",
    "--max-budget-usd",
)
_DEFAULT_MCP_BODY = object()


def _worker_catalog(tool_names: frozenset[str]) -> dict[str, object]:
    names = sorted(tool_names)
    return {
        "tool_names": names,
        "tool_catalog_sha256": hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
    }


def _mcp_preflight_body() -> dict[str, object]:
    return {
        "schema": "agent_commons.mcp_preflight.v2",
        "agent_commons_source_sha256": agent_commons_source_sha256(),
        "tool_count": 14,
        "tool_catalog_sha256": "a" * 64,
        "worker_catalogs": {
            "implementation": _worker_catalog(IMPLEMENTATION_WORKER_TOOL_NAMES),
            "independent_review": _worker_catalog(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
            "verification": _worker_catalog(VERIFICATION_WORKER_TOOL_NAMES),
        },
    }


def _mcp_handshake_output(tool_names: frozenset[str]) -> bytes:
    responses = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": name} for name in sorted(tool_names)]},
        },
    )
    return b"".join(json.dumps(response).encode() + b"\n" for response in responses)


class ProbeRunner:
    def __init__(
        self,
        *,
        missing_provider_flags: tuple[str, ...] = (),
        legacy_mcp_contract: bool = False,
        mcp_body: object = _DEFAULT_MCP_BODY,
        handshake_exit_code: int = 0,
        handshake_result: ProcessResult | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.call_kwargs: list[dict] = []
        self.missing_provider_flags = missing_provider_flags
        self.legacy_mcp_contract = legacy_mcp_contract
        self.mcp_body = mcp_body
        self.handshake_exit_code = handshake_exit_code
        self.handshake_result = handshake_result

    def run(self, invocation, **kwargs) -> ProcessResult:
        self.calls.append(invocation.argv)
        self.call_kwargs.append(kwargs)
        if "--help" in invocation.argv:
            flags = " ".join(
                flag for flag in _CLAUDE_HELP_FLAGS if flag not in self.missing_provider_flags
            )
            return _result(output=flags.encode())
        if invocation.stdin:
            if self.handshake_result is not None:
                return self.handshake_result
            return _result(
                output=_mcp_handshake_output(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
                exit_code=self.handshake_exit_code,
            )
        if self.mcp_body is not _DEFAULT_MCP_BODY:
            body = self.mcp_body
        elif self.legacy_mcp_contract:
            body = {
                "schema": "agent_commons.mcp_preflight.v1",
                "tool_count": 14,
                "tool_catalog_sha256": "a" * 64,
            }
        else:
            body = _mcp_preflight_body()
        return _result(output=json.dumps(body).encode())


def _profiles() -> ProfileRegistry:
    profile_id = BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    return ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable="/bin/echo",
                mcp_executable="/bin/echo",
                git_executable="/usr/bin/git",
                permission_mode=ClaudePermissionMode.DONT_ASK,
            )
        }
    )


def test_preflight_validates_provider_flags_and_mcp_without_an_attempt(tmp_path: Path) -> None:
    runner = ProbeRunner()

    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is True
    assert result["consumed_delegation_attempt"] is False
    assert result["provider_help_process_started"] is True
    assert result["provider_work_process_started"] is False
    assert result["checks"] == {
        "provider_help": {"ok": True, "required_flags": "present"},
        "mcp_contract": {
            "ok": True,
            "catalog": "available",
            "agent_commons_source_sha256": agent_commons_source_sha256(),
            "tool_catalog_sha256": hashlib.sha256(
                "\n".join(sorted(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)).encode("utf-8")
            ).hexdigest(),
            "tool_count": len(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
        },
        "mcp_handshake": {
            "ok": True,
            "protocol": "initialized",
            "required_tools": "available",
            "tool_count": len(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
        },
    }
    assert len(runner.calls) == 3
    assert "--preflight" in runner.calls[1]
    assert "--delegation-id" not in runner.calls[1]
    assert "--preflight" not in runner.calls[2]
    # The handshake probe must hold stdin open until the tools/list response
    # has arrived: the stdio server starts shutdown on stdin EOF, and an
    # immediate close races the response flush against teardown.
    assert runner.call_kwargs[0]["close_stdin_when"] is None
    assert runner.call_kwargs[1]["close_stdin_when"] is None
    handshake_finished = runner.call_kwargs[2]["close_stdin_when"]
    complete = _mcp_handshake_output(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)
    assert handshake_finished(b"") is False
    assert handshake_finished(complete[:-10]) is False
    assert handshake_finished(complete) is True


def test_preflight_is_red_when_real_stdio_handshake_fails(tmp_path: Path) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(handshake_exit_code=2),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["mcp_handshake"]["diagnostic_code"]
        == DiagnosticCode.MCP_HANDSHAKE_FAILED.value
    )
    assert result["consumed_delegation_attempt"] is False
    assert result["provider_work_process_started"] is False


def test_preflight_keeps_a_truncated_handshake_distinguishable_from_a_timeout(
    tmp_path: Path,
) -> None:
    # A catalog that outgrows the probe's output budget arrives with the final
    # tools/list line cut off; the runner closes stdin on budget exhaustion, so
    # the child still exits cleanly and only parsing fails.  A dead-silent
    # child, by contrast, runs into the probe timeout.  Both used to collapse
    # into the same mcp_handshake_failed signature as the EOF race bf482fd
    # fixed; outcome and reason must keep them apart.
    complete = _mcp_handshake_output(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)
    truncated = ProcessResult(
        outcome=RunOutcome.SUCCEEDED,
        reason=RunReason.COMPLETED,
        exit_code=0,
        pid=123,
        duration_seconds=0.01,
        stdout=complete[:-10],
        stderr=b"",
        stdout_bytes_seen=len(complete),
        stderr_bytes_seen=0,
        output_truncated=True,
    )
    timed_out = ProcessResult(
        outcome=RunOutcome.TIMED_OUT,
        reason=RunReason.TIMEOUT,
        exit_code=None,
        pid=123,
        duration_seconds=15.0,
        stdout=b"",
        stderr=b"",
        stdout_bytes_seen=0,
        stderr_bytes_seen=0,
        output_truncated=False,
    )

    bodies: dict[str, dict] = {}
    for label, handshake_result in (("truncated", truncated), ("timed_out", timed_out)):
        result = preflight_profile(
            _profiles(),
            BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
            workspace_root=tmp_path,
            runner=ProbeRunner(handshake_result=handshake_result),  # type: ignore[arg-type]
        )
        assert result["ok"] is False
        bodies[label] = result["checks"]["mcp_handshake"]

    for body in bodies.values():
        assert body["diagnostic_code"] == DiagnosticCode.MCP_HANDSHAKE_FAILED.value
    assert bodies["truncated"]["outcome"] == "succeeded"
    assert bodies["truncated"]["reason"] == "completed"
    assert bodies["timed_out"]["outcome"] == "timed_out"
    assert bodies["timed_out"]["reason"] == "timeout"


def test_preflight_identifies_a_missing_mcp_executable_before_provider_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    profile_id = BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    profiles = ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable="/bin/echo",
                mcp_executable="agent-commons-mcp-missing-for-test",
                git_executable="/usr/bin/git",
                permission_mode=ClaudePermissionMode.DONT_ASK,
            )
        }
    )
    runner = ProbeRunner()

    result = preflight_profile(
        profiles,
        profile_id,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert result["checks"]["mcp_executable"]["diagnostic_code"] == (
        DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE.value
    )
    assert "PATH" in " ".join(result["checks"]["mcp_executable"]["safe_next_actions"])
    assert "preflight" in " ".join(result["checks"]["mcp_executable"]["safe_next_actions"])
    assert result["provider_help_process_started"] is False
    assert result["consumed_delegation_attempt"] is False
    assert runner.calls == []


def test_preflight_identifies_a_missing_git_executable_without_echoing_its_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    rejected_value = "git-SUPERSECRET-operator-path-missing"
    profile_id = BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    profiles = ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable="/bin/echo",
                mcp_executable="/bin/echo",
                git_executable=rejected_value,
                permission_mode=ClaudePermissionMode.DONT_ASK,
            )
        }
    )
    runner = ProbeRunner()

    result = preflight_profile(
        profiles,
        profile_id,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    check = result["checks"]["git_executable"]
    assert check["diagnostic_code"] == DiagnosticCode.GIT_EXECUTABLE_UNAVAILABLE.value
    assert rejected_value not in json.dumps(result)
    assert result["consumed_delegation_attempt"] is False
    assert result["provider_work_process_started"] is False
    assert runner.calls == []


def test_preflight_names_a_trusted_workspace_refusal_not_a_start_failure(
    tmp_path: Path,
) -> None:
    """A writable builder in an untrusted workspace refuses to build its launch
    with a trusted_workspace ConfigurationError.  That was flattened into a bare
    provider_start_failed, so preflight blamed the executable on a machine where
    it runs fine (M8).  The real refusal must survive as a maintainer-owned hint."""

    profile_id = BuiltinProfileId.CLAUDE_BUILDER
    profiles = ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable="/bin/echo",
                mcp_executable="/bin/echo",
                git_executable="/usr/bin/git",
                permission_mode=ClaudePermissionMode.DONT_ASK,
                trusted_workspace=False,
            )
        }
    )

    result = preflight_profile(
        profiles,
        profile_id,
        workspace_root=tmp_path,
        runner=ProbeRunner(),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    check = result["checks"]["profile"]
    assert check["diagnostic_code"] == DiagnosticCode.TRUSTED_WORKSPACE_REQUIRED.value
    assert "not marked trusted" in check["detail"]
    actions = " ".join(check["safe_next_actions"])
    assert "preflight" in actions
    assert "manual workflow" in actions
    assert result["consumed_delegation_attempt"] is False


def test_preflight_returns_closed_code_for_provider_flag_drift(tmp_path: Path) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(missing_provider_flags=("--strict-mcp-config", "--allowed-tools")),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["provider_help"]["diagnostic_code"]
        == DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG.value
    )
    assert result["checks"]["provider_help"]["missing_flag_count"] == 2
    assert "manual workflow" in " ".join(result["checks"]["provider_help"]["safe_next_actions"])
    assert result["consumed_delegation_attempt"] is False
    assert result["provider_work_process_started"] is False


def test_preflight_checks_generated_session_and_tool_flags(tmp_path: Path) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(missing_provider_flags=("--disable-slash-commands",)),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert result["checks"]["provider_help"]["missing_flag_count"] == 1


def test_preflight_does_not_mistake_allowed_tools_for_tools_flag(tmp_path: Path) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(missing_provider_flags=("--tools",)),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert result["checks"]["provider_help"]["missing_flag_count"] == 1


def test_preflight_rejects_a_stale_worker_mcp_contract(tmp_path: Path) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(legacy_mcp_contract=True),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["mcp_contract"]["diagnostic_code"]
        == DiagnosticCode.MCP_TOOL_CONTRACT_FAILED.value
    )


def test_preflight_uses_the_exact_verification_worker_catalog(tmp_path: Path) -> None:
    runner = ProbeRunner()

    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        purpose="verification",
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is True


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        {"schema": "agent_commons.mcp_preflight.v2", "worker_catalogs": []},
        {
            "schema": "agent_commons.mcp_preflight.v2",
            "worker_catalogs": {"independent_review": []},
        },
    ],
)
def test_preflight_fails_closed_for_malformed_successful_mcp_json(
    tmp_path: Path,
    body: object,
) -> None:
    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(mcp_body=body),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["mcp_contract"]["diagnostic_code"]
        == DiagnosticCode.MCP_TOOL_CONTRACT_FAILED.value
    )


def test_preflight_rejects_a_same_catalog_from_a_different_source_build(
    tmp_path: Path,
) -> None:
    names = sorted(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)
    body = {
        "schema": "agent_commons.mcp_preflight.v2",
        "agent_commons_source_sha256": "0" * 64,
        "tool_count": 1,
        "worker_catalogs": {
            "independent_review": {
                "tool_names": names,
                "tool_catalog_sha256": hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
            }
        },
    }

    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(mcp_body=body),  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["mcp_contract"]["diagnostic_code"]
        == DiagnosticCode.MCP_TOOL_CONTRACT_FAILED.value
    )


@pytest.mark.parametrize("mutation", ["bad_digest", "duplicate", "missing", "unexpected"])
def test_preflight_rejects_worker_catalog_drift(tmp_path: Path, mutation: str) -> None:
    names = sorted(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)
    if mutation == "duplicate":
        names.append(names[0])
    elif mutation == "missing":
        names.pop()
    elif mutation == "unexpected":
        names.append("commons_unexpected")
    digest = hashlib.sha256("\n".join(sorted(set(names))).encode("utf-8")).hexdigest()
    if mutation == "bad_digest":
        digest = "f" * 64
    body = {
        "schema": "agent_commons.mcp_preflight.v2",
        "agent_commons_source_sha256": agent_commons_source_sha256(),
        "tool_count": 1,
        "worker_catalogs": {
            "independent_review": {
                "tool_names": names,
                "tool_catalog_sha256": digest,
            }
        },
    }

    result = preflight_profile(
        _profiles(),
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        workspace_root=tmp_path,
        runner=ProbeRunner(mcp_body=body),  # type: ignore[arg-type]
    )

    assert result["ok"] is False


_CODEX_ROOT_HELP = (
    b"-m, --model <MODEL>\n-s, --sandbox <SANDBOX_MODE>\n-a, --ask-for-approval <APPROVAL_POLICY>"
)
_CODEX_EXEC_HELP = b"-c, --config <key=value>\n--ignore-user-config\n--strict-config\n--json"


class CodexProbeRunner:
    """Serve distinct root and exec help texts like a real codex CLI."""

    def __init__(
        self,
        *,
        root_help: bytes = _CODEX_ROOT_HELP,
        exec_help: bytes = _CODEX_EXEC_HELP,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.root_help = root_help
        self.exec_help = exec_help

    def run(self, invocation, **_kwargs) -> ProcessResult:
        self.calls.append(invocation.argv)
        if "--help" in invocation.argv:
            output = self.exec_help if "exec" in invocation.argv else self.root_help
            return _result(output=output)
        if invocation.stdin:
            return _result(output=_mcp_handshake_output(IMPLEMENTATION_WORKER_TOOL_NAMES))
        return _result(output=json.dumps(_mcp_preflight_body()).encode())


def _codex_profiles() -> ProfileRegistry:
    profile_id = BuiltinProfileId.CODEX_BUILDER
    return ProfileRegistry(
        {
            profile_id: CodexRunnerProfile(
                profile_id=profile_id,
                executable="/bin/echo",
                mcp_executable="/bin/echo",
                git_executable="/usr/bin/git",
                sandbox=CodexSandbox.WORKSPACE_WRITE,
                trusted_workspace=True,
            )
        }
    )


def test_codex_preflight_accepts_root_only_approval_flag(tmp_path: Path) -> None:
    """Real codex builds list --ask-for-approval only in root help; that must pass."""

    runner = CodexProbeRunner()

    result = preflight_profile(
        _codex_profiles(),
        BuiltinProfileId.CODEX_BUILDER,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is True
    assert result["checks"]["provider_help"] == {"ok": True, "required_flags": "present"}
    assert result["provider_help_process_started"] is True
    executable = str(Path("/bin/echo").resolve())
    assert runner.calls[:2] == [(executable, "--help"), (executable, "exec", "--help")]
    assert "--preflight" in runner.calls[2]
    assert "--delegation-id" not in runner.calls[2]
    assert "--preflight" not in runner.calls[3]


def test_codex_preflight_fails_closed_when_root_scope_lacks_a_launch_flag(
    tmp_path: Path,
) -> None:
    """A flag present only outside the scope where the argv places it must not count."""

    runner = CodexProbeRunner(
        root_help=b"-m, --model <MODEL>\n-s, --sandbox <SANDBOX_MODE>",
        exec_help=_CODEX_EXEC_HELP + b"\n-a, --ask-for-approval <APPROVAL_POLICY>",
    )

    result = preflight_profile(
        _codex_profiles(),
        BuiltinProfileId.CODEX_BUILDER,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert (
        result["checks"]["provider_help"]["diagnostic_code"]
        == DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG.value
    )
    assert result["checks"]["provider_help"]["missing_flag_count"] == 1


def test_codex_preflight_fails_closed_when_exec_scope_lacks_json(tmp_path: Path) -> None:
    runner = CodexProbeRunner(exec_help=_CODEX_EXEC_HELP.replace(b"--json", b""))

    result = preflight_profile(
        _codex_profiles(),
        BuiltinProfileId.CODEX_BUILDER,
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result["ok"] is False
    assert result["checks"]["provider_help"]["missing_flag_count"] == 1
