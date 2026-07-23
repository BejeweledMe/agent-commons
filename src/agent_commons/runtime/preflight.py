"""Credential-free compatibility checks for fixed provider profiles."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_commons.errors import ConfigurationError

from .diagnostics import DiagnosticCode, diagnostic_hint, diagnostic_safe_next_actions
from .model import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    ExecutableResolutionError,
    ExecutableRole,
    ProfileRegistry,
    RunnerInvocation,
)
from .source_contract import agent_commons_source_sha256
from .subprocess_runner import ProcessResult, RunOutcome, SubprocessRunner

_HELP_FLAGS = {
    BuiltinProfileId.CLAUDE_BUILDER: (
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
        "--max-budget-usd",
    ),
    BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER: (
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
    ),
}

# The codex launch argv places --ask-for-approval and --sandbox before the
# `exec` subcommand, so each flag must be validated against the help scope
# that actually parses it; current codex builds list --ask-for-approval only
# in root help.
_CODEX_ROOT_HELP_FLAGS = ("--ask-for-approval", "--sandbox")
_CODEX_EXEC_HELP_FLAGS = ("--json",)

_MCP_TOOL_PREFIX = "mcp__agent-commons__"


def _help_has_flag(help_text: str, flag: str) -> bool:
    """Match one complete CLI option, never a substring of another option."""

    return (
        re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(flag)}(?![A-Za-z0-9_-])",
            help_text,
        )
        is not None
    )


def _run_probe(
    runner: SubprocessRunner,
    invocation: RunnerInvocation,
    *,
    cwd: Path,
) -> ProcessResult:
    return runner.run(
        invocation,
        cwd=cwd,
        child_session_id="session.preflight",
        timeout_seconds=15,
        max_output_bytes=256 * 1024,
    )


def _safe_failure(code: DiagnosticCode) -> dict[str, Any]:
    return {
        "ok": False,
        "diagnostic_code": code.value,
        "hint": diagnostic_hint(code),
        "safe_next_actions": diagnostic_safe_next_actions(code),
    }


def preflight_profile(
    profiles: ProfileRegistry,
    profile_id: str | BuiltinProfileId,
    *,
    workspace_root: str | Path,
    state_root: str | Path | None = None,
    purpose: str | None = None,
    runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Check fixed argv and MCP compatibility without allocating an attempt."""

    normalized = BuiltinProfileId(profile_id)
    profile = profiles.get(normalized)
    root = Path(workspace_root).expanduser().resolve()
    effective_state_root = (
        Path(state_root if state_root is not None else root / ".agent-commons")
        .expanduser()
        .resolve()
    )
    probe = runner or SubprocessRunner()
    effective_purpose = purpose or (
        "independent_review" if normalized.independent_reviewer else "implementation"
    )
    try:
        invocation = profile.build_invocation(
            "Agent Commons credential-free compatibility preflight.",
            workspace_root=root,
            state_root=effective_state_root,
            delegation_id="delegation.preflight",
            max_budget_microusd=1 if profile.supports_budget else None,
            worker_purpose=effective_purpose,
        )
    except ExecutableResolutionError as exc:
        if exc.role is ExecutableRole.MCP:
            check_name = "mcp_executable"
            code = DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE
        elif exc.role is ExecutableRole.GIT:
            check_name = "git_executable"
            code = DiagnosticCode.MCP_CONFIG_INVALID
        else:
            check_name = "provider_executable"
            code = DiagnosticCode.PROVIDER_START_FAILED
        return {
            "profile_id": normalized.value,
            "provider": profile.provider.value,
            "ok": False,
            "checks": {check_name: _safe_failure(code)},
            "consumed_delegation_attempt": False,
            "provider_help_process_started": False,
            "provider_work_process_started": False,
        }
    except ConfigurationError:
        return {
            "profile_id": normalized.value,
            "provider": profile.provider.value,
            "ok": False,
            "checks": {"profile": _safe_failure(DiagnosticCode.PROVIDER_START_FAILED)},
            "consumed_delegation_attempt": False,
            "provider_help_process_started": False,
            "provider_work_process_started": False,
        }

    if isinstance(profile, CodexRunnerProfile):
        scoped_probes: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
            ((invocation.argv[0], "--help"), _CODEX_ROOT_HELP_FLAGS),
            ((invocation.argv[0], "exec", "--help"), _CODEX_EXEC_HELP_FLAGS),
        )
    else:
        scoped_probes = (((invocation.argv[0], "--help"), _HELP_FLAGS[normalized]),)
    help_process_started = False
    help_probes_succeeded = True
    missing: list[str] = []
    for help_argv, required_flags in scoped_probes:
        help_result = _run_probe(
            probe,
            RunnerInvocation(
                provider=invocation.provider,
                profile_id=invocation.profile_id,
                argv=help_argv,
                stdin=b"",
            ),
            cwd=root,
        )
        help_process_started = help_process_started or help_result.pid is not None
        help_probes_succeeded = (
            help_probes_succeeded and help_result.outcome is RunOutcome.SUCCEEDED
        )
        help_text = (help_result.stdout + b"\n" + help_result.stderr).decode("utf-8", "replace")
        missing.extend(flag for flag in required_flags if not _help_has_flag(help_text, flag))
    missing_flags = sorted(missing)
    checks: dict[str, Any] = {
        "provider_help": (
            {"ok": True, "required_flags": "present"}
            if help_probes_succeeded and not missing_flags
            else {
                **_safe_failure(DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG),
                "missing_flag_count": len(missing_flags),
            }
        )
    }

    if isinstance(profile, ClaudeRunnerProfile):
        raw_config = invocation.argv[invocation.argv.index("--mcp-config") + 1]
        config = json.loads(raw_config)
        mcp = config["mcpServers"]["agent-commons"]
        args = list(mcp["args"])
        if "--delegation-id" in args:
            position = args.index("--delegation-id")
            del args[position : position + 2]
        args.append("--preflight")
        mcp_result = _run_probe(
            probe,
            RunnerInvocation(
                provider=invocation.provider,
                profile_id=invocation.profile_id,
                argv=(str(mcp["command"]), *args),
                stdin=b"",
            ),
            cwd=root,
        )
        mcp_ok = False
        missing_tool_count = 0
        unexpected_tool_count = 0
        if mcp_result.outcome is RunOutcome.SUCCEEDED:
            try:
                body = json.loads(mcp_result.stdout)
                if not isinstance(body, Mapping):
                    raise TypeError("MCP preflight body is not an object")
                worker_catalogs = body.get("worker_catalogs")
                if not isinstance(worker_catalogs, Mapping):
                    raise TypeError("MCP worker catalogs are not an object")
                catalog = worker_catalogs.get(effective_purpose)
                if not isinstance(catalog, Mapping):
                    raise TypeError("MCP worker catalog is not an object")
                tool_names = catalog.get("tool_names")
                if not isinstance(tool_names, list) or any(
                    not isinstance(name, str) or not name for name in tool_names
                ):
                    raise TypeError("MCP worker tool names are invalid")
                actual_tools = set(tool_names)
                allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]
                expected_tools = {
                    name.removeprefix(_MCP_TOOL_PREFIX)
                    for name in allowed.split(",")
                    if name.startswith(_MCP_TOOL_PREFIX)
                }
                missing_tool_count = len(expected_tools - actual_tools)
                unexpected_tool_count = len(actual_tools - expected_tools)
                catalog_digest = hashlib.sha256(
                    "\n".join(sorted(actual_tools)).encode("utf-8")
                ).hexdigest()
                mcp_ok = (
                    body.get("schema") == "agent_commons.mcp_preflight.v2"
                    and int(body.get("tool_count", 0)) > 0
                    and body.get("agent_commons_source_sha256") == agent_commons_source_sha256()
                    and len(actual_tools) == len(tool_names)
                    and catalog.get("tool_catalog_sha256") == catalog_digest
                    and actual_tools == expected_tools
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ):
                mcp_ok = False
        checks["mcp_contract"] = (
            {
                "ok": True,
                "catalog": "available",
                "agent_commons_source_sha256": agent_commons_source_sha256(),
                "tool_catalog_sha256": catalog_digest,
                "tool_count": len(actual_tools),
            }
            if mcp_ok
            else {
                **_safe_failure(
                    DiagnosticCode.MCP_TOOL_CONTRACT_FAILED
                    if mcp_result.outcome is RunOutcome.SUCCEEDED
                    else DiagnosticCode.MCP_SPAWN_FAILED
                ),
                "missing_tool_count": missing_tool_count,
                "unexpected_tool_count": unexpected_tool_count,
            }
        )

    ok = all(bool(check.get("ok")) for check in checks.values())
    return {
        "profile_id": normalized.value,
        "provider": profile.provider.value,
        "ok": ok,
        "checks": checks,
        "consumed_delegation_attempt": False,
        "provider_help_process_started": help_process_started,
        "provider_work_process_started": False,
    }
