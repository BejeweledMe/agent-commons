"""Credential-free compatibility checks for fixed provider profiles."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_commons.errors import ConfigurationError

from .diagnostics import (
    DiagnosticCode,
    configuration_failure_diagnostic,
    diagnostic_hint,
    diagnostic_safe_next_actions,
)
from .model import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    ExecutableResolutionError,
    ExecutableRole,
    GrokRunnerProfile,
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
_CODEX_EXEC_HELP_FLAGS = ("--config", "--ignore-user-config", "--strict-config", "--json")
_GROK_HELP_FLAGS = (
    "--single",
    "--cwd",
    "--output-format",
    "--always-approve",
    "--no-alt-screen",
    "--max-turns",
    "--model",
    "--sandbox",
    "--allow",
    "--tools",
    "--disallowed-tools",
    "--no-plan",
    "--no-subagents",
    "--disable-web-search",
)

_MCP_TOOL_PREFIX = "mcp__agent-commons__"
_CODEX_MCP_PREFIX = "mcp_servers.agent-commons."
_MCP_PROTOCOL_VERSION = "2025-06-18"
_MCP_HANDSHAKE_FINAL_REQUEST_ID = 2


def _mcp_handshake_stdin() -> bytes:
    messages = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agent-commons-preflight", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": _MCP_HANDSHAKE_FINAL_REQUEST_ID,
            "method": "tools/list",
            "params": {},
        },
    )
    return b"".join(
        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n" for message in messages
    )


def _mcp_handshake_finished(stdout: bytes) -> bool:
    """True once the final handshake response has fully arrived on stdout.

    The stdio server begins shutdown on stdin EOF, so the probe must hold
    stdin open until this fires; closing earlier races the tools/list flush
    against EOF-driven teardown and intermittently loses the response on
    loaded machines.
    """

    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(message, Mapping) and message.get("id") == _MCP_HANDSHAKE_FINAL_REQUEST_ID:
            return True
    return False


def _parse_mcp_handshake(output: bytes) -> set[str]:
    responses: dict[int, Mapping[str, Any]] = {}
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        message = json.loads(raw_line)
        if isinstance(message, Mapping) and isinstance(message.get("id"), int):
            responses[int(message["id"])] = message
    initialized = responses[1]
    listed = responses[2]
    if "error" in initialized or "error" in listed:
        raise ValueError("MCP handshake returned an error")
    initialize_result = initialized.get("result")
    tools_result = listed.get("result")
    if not isinstance(initialize_result, Mapping) or not isinstance(tools_result, Mapping):
        raise TypeError("MCP handshake response is invalid")
    if not isinstance(initialize_result.get("protocolVersion"), str):
        raise TypeError("MCP initialize response has no protocol version")
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise TypeError("MCP tools/list response is invalid")
    names = {
        str(tool["name"])
        for tool in tools
        if isinstance(tool, Mapping) and isinstance(tool.get("name"), str) and tool["name"]
    }
    if len(names) != len(tools):
        raise TypeError("MCP tools/list contains invalid or duplicate names")
    return names


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
    close_stdin_when: Callable[[bytes], bool] | None = None,
) -> ProcessResult:
    return runner.run(
        invocation,
        cwd=cwd,
        child_session_id="session.preflight",
        timeout_seconds=15,
        max_output_bytes=256 * 1024,
        close_stdin_when=close_stdin_when,
    )


def _safe_failure(code: DiagnosticCode) -> dict[str, Any]:
    return {
        "ok": False,
        "diagnostic_code": code.value,
        "hint": diagnostic_hint(code),
        "safe_next_actions": diagnostic_safe_next_actions(code),
    }


def _without_delegation_binding(arguments: list[str]) -> list[str]:
    if "--delegation-id" in arguments:
        position = arguments.index("--delegation-id")
        del arguments[position : position + 2]
    return arguments


def _codex_mcp_config(invocation: RunnerInvocation) -> tuple[str, list[str], set[str]]:
    overrides: dict[str, object] = {}
    for index, argument in enumerate(invocation.argv):
        if argument not in {"-c", "--config"} or index + 1 >= len(invocation.argv):
            continue
        raw_override = invocation.argv[index + 1]
        key, separator, raw_value = raw_override.partition("=")
        if not separator or not key.startswith(_CODEX_MCP_PREFIX):
            continue
        overrides[key.removeprefix(_CODEX_MCP_PREFIX)] = tomllib.loads(f"value = {raw_value}")[
            "value"
        ]

    command = overrides["command"]
    args = overrides["args"]
    enabled_tools = overrides["enabled_tools"]
    required = overrides["required"]
    if not isinstance(command, str) or not command:
        raise TypeError("Codex MCP command is invalid")
    if not isinstance(args, list) or any(not isinstance(value, str) for value in args):
        raise TypeError("Codex MCP args are invalid")
    if not isinstance(enabled_tools, list) or any(
        not isinstance(value, str) or not value for value in enabled_tools
    ):
        raise TypeError("Codex MCP enabled tools are invalid")
    if required is not True:
        raise TypeError("Codex MCP server must be required")
    return command, _without_delegation_binding(list(args)), set(enabled_tools)


def _grok_mcp_config(
    invocation: RunnerInvocation, *, state_root: Path
) -> tuple[str, list[str], set[str]]:
    environment = dict(invocation.extra_env or {})
    command = environment["AGENT_COMMONS_GROK_MCP_COMMAND"]
    arguments = [
        "--repo",
        environment["AGENT_COMMONS_REPO_ROOT"],
        "--state-root",
        str(state_root),
        "--git-executable",
        environment["AGENT_COMMONS_GIT_EXECUTABLE"],
        "--session-id",
        "session.preflight",
    ]
    allowed = {
        value.removeprefix("MCPTool(agent-commons__").removesuffix(")")
        for index, value in enumerate(invocation.argv)
        if index > 0
        and invocation.argv[index - 1] == "--allow"
        and value.startswith("MCPTool(agent-commons__")
        and value.endswith(")")
    }
    if not command or not allowed:
        raise TypeError("Grok MCP launch contract is invalid")
    return command, arguments, allowed


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
        diagnostic = configuration_failure_diagnostic(exc)
        if exc.role is ExecutableRole.MCP:
            check_name = "mcp_executable"
        elif exc.role is ExecutableRole.GIT:
            check_name = "git_executable"
        else:
            check_name = "provider_executable"
        return {
            "profile_id": normalized.value,
            "provider": profile.provider.value,
            "ok": False,
            "checks": {check_name: _safe_failure(diagnostic.code)},
            "consumed_delegation_attempt": False,
            "provider_help_process_started": False,
            "provider_work_process_started": False,
        }
    except ConfigurationError as exc:
        # The profile refused to build its launch, and the reason matters: a
        # writable builder in an untrusted workspace fails here with a
        # trusted_workspace ConfigurationError, which was flattened into a bare
        # provider_start_failed -- so preflight blamed the executable on a
        # machine where the executable runs fine (M8, 2026-08-10 review).  Name
        # the real refusal, but expose only the maintainer-owned safe hint.
        diagnostic = configuration_failure_diagnostic(exc)
        return {
            "profile_id": normalized.value,
            "provider": profile.provider.value,
            "ok": False,
            "checks": {"profile": {**_safe_failure(diagnostic.code), "detail": diagnostic.hint}},
            "consumed_delegation_attempt": False,
            "provider_help_process_started": False,
            "provider_work_process_started": False,
        }

    if isinstance(profile, CodexRunnerProfile):
        scoped_probes: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
            ((invocation.argv[0], "--help"), _CODEX_ROOT_HELP_FLAGS),
            ((invocation.argv[0], "exec", "--help"), _CODEX_EXEC_HELP_FLAGS),
        )
    elif isinstance(profile, GrokRunnerProfile):
        scoped_probes = (((invocation.argv[0], "--help"), _GROK_HELP_FLAGS),)
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

    try:
        if isinstance(profile, ClaudeRunnerProfile):
            raw_config = invocation.argv[invocation.argv.index("--mcp-config") + 1]
            config = json.loads(raw_config)
            mcp = config["mcpServers"]["agent-commons"]
            mcp_command = str(mcp["command"])
            mcp_args = _without_delegation_binding(list(mcp["args"]))
            allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]
            expected_tools = {
                name.removeprefix(_MCP_TOOL_PREFIX)
                for name in allowed.split(",")
                if name.startswith(_MCP_TOOL_PREFIX)
            }
        elif isinstance(profile, CodexRunnerProfile):
            mcp_command, mcp_args, expected_tools = _codex_mcp_config(invocation)
        elif isinstance(profile, GrokRunnerProfile):
            mcp_command, mcp_args, expected_tools = _grok_mcp_config(
                invocation, state_root=effective_state_root
            )
        else:  # pragma: no cover - the profile registry is an exhaustive allowlist
            raise TypeError("unsupported provider profile")
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        tomllib.TOMLDecodeError,
    ):
        checks["mcp_contract"] = _safe_failure(DiagnosticCode.MCP_CONFIG_INVALID)
        checks["mcp_handshake"] = _safe_failure(DiagnosticCode.MCP_CONFIG_INVALID)
    else:
        contract_args = [*mcp_args, "--preflight"]
        mcp_result = _run_probe(
            probe,
            RunnerInvocation(
                provider=invocation.provider,
                profile_id=invocation.profile_id,
                argv=(mcp_command, *contract_args),
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

        handshake_result = _run_probe(
            probe,
            RunnerInvocation(
                provider=invocation.provider,
                profile_id=invocation.profile_id,
                argv=(mcp_command, *mcp_args, "--stdio-preflight-purpose", effective_purpose),
                stdin=_mcp_handshake_stdin(),
            ),
            cwd=root,
            close_stdin_when=_mcp_handshake_finished,
        )
        handshake_tools: set[str] = set()
        handshake_ok = False
        if handshake_result.outcome is RunOutcome.SUCCEEDED:
            try:
                handshake_tools = _parse_mcp_handshake(handshake_result.stdout)
                handshake_ok = expected_tools.issubset(handshake_tools)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                handshake_ok = False
        checks["mcp_handshake"] = (
            {
                "ok": True,
                "protocol": "initialized",
                "required_tools": "available",
                "tool_count": len(handshake_tools),
            }
            if handshake_ok
            else {
                **_safe_failure(
                    DiagnosticCode.MCP_SPAWN_FAILED
                    if handshake_result.pid is None
                    else DiagnosticCode.MCP_HANDSHAKE_FAILED
                ),
                "missing_tool_count": len(expected_tools - handshake_tools),
                # The diagnostic code alone cannot separate a probe timeout
                # from a bad provider exit or an unparseable reply.
                "outcome": handshake_result.outcome.value,
                "reason": handshake_result.reason.value,
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
