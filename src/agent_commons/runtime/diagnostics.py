"""Secret-free classification for bounded ephemeral provider output.

The broker may inspect the already bounded process buffers exactly once.  This
module returns only a closed code and a maintainer-owned hint; raw provider
bytes, prompts, tool arguments, paths, and matched fragments are never returned
or persisted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_commons.errors import (
    ClaimConflictError,
    ConfigurationError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy

from .subprocess_runner import PROVIDER_STDERR_TAIL_BYTES, ProcessResult, RunOutcome, RunReason

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:[^\s/:;,()\[\]{}<>\"']+/)+[^\s:;,()\[\]{}<>\"']*"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\(?:[^\s:;,()\[\]{}<>\"']+\\)+"
    r"[^\s:;,()\[\]{}<>\"']*"
)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REDACTED_DIAGNOSTIC_LINE = "[agent-commons redacted unsafe diagnostic line]"
_REDACTED_PATH = "[agent-commons redacted path]"


class DiagnosticCode(StrEnum):
    NONE = "none"
    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    PROVIDER_START_FAILED = "provider_start_failed"
    PROVIDER_AUTH_FAILED = "provider_auth_failed"
    PROVIDER_AUTH_REQUIRED = "provider_auth_required"
    PROVIDER_AUTH_UNKNOWN = "provider_auth_unknown"
    PROVIDER_BUDGET_EXHAUSTED = "provider_budget_exhausted"
    UNSUPPORTED_PROVIDER_FLAG = "unsupported_provider_flag"
    MCP_CONFIG_INVALID = "mcp_config_invalid"
    MCP_EXECUTABLE_UNAVAILABLE = "mcp_executable_unavailable"
    GIT_EXECUTABLE_UNAVAILABLE = "git_executable_unavailable"
    MCP_SPAWN_FAILED = "mcp_spawn_failed"
    MCP_HANDSHAKE_FAILED = "mcp_handshake_failed"
    MCP_BINDING_TIMEOUT = "mcp_binding_timeout"
    MCP_TOOL_CONTRACT_FAILED = "mcp_tool_contract_failed"
    BROKER_CONTROL_ERROR = "broker_control_error"
    PROVIDER_NONZERO_UNKNOWN = "provider_nonzero_unknown"
    PROVIDER_REPORTED_ERROR = "provider_reported_error"
    TERMINAL_TOOL_NOT_CALLED = "terminal_tool_not_called"
    TERMINAL_TOOL_REJECTED = "terminal_tool_rejected"
    PROCESS_CANONICAL_MISMATCH = "process_canonical_mismatch"
    CANONICAL_FINALIZATION_FAILED = "canonical_finalization_failed"
    REQUESTER_SESSION_REQUIRED = "requester_session_required"
    REQUESTER_UNAVAILABLE = "requester_unavailable"
    TRUSTED_WORKSPACE_REQUIRED = "trusted_workspace_required"


def workflow_diagnostic_code(value: Mapping[str, Any]) -> DiagnosticCode:
    """Combine stored process classification with terminal workflow evidence."""

    stored = DiagnosticCode(str(value["diagnostic_code"]))
    if stored not in {DiagnosticCode.NONE, DiagnosticCode.LEGACY_UNCLASSIFIED}:
        return stored
    if value.get("process_canonical_mismatch") is True:
        if value.get("terminal_tool_audit_available") is True:
            if int(value.get("terminal_tool_calls", 0)) == 0:
                return DiagnosticCode.TERMINAL_TOOL_NOT_CALLED
            if (
                int(value.get("terminal_tool_rejections", 0)) > 0
                and int(value.get("terminal_tool_completions", 0)) == 0
            ):
                return DiagnosticCode.TERMINAL_TOOL_REJECTED
        return DiagnosticCode.PROCESS_CANONICAL_MISMATCH
    return stored


def canonical_reason_code(canonical: Mapping[str, Any]) -> str:
    """Return the stable canonical reason label for runtime diagnostics."""

    return str(canonical.get("reason_code") or canonical.get("state") or "unknown")


def process_canonical_mismatch(
    attempt_state: str,
    canonical: Mapping[str, Any] | None,
) -> bool | None:
    """Compare one terminal operational attempt with its canonical delegation."""

    state = str(attempt_state)
    expected = {
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "timed_out": "timed_out",
        "needs_operator": "needs_operator",
    }
    if canonical is None or state not in expected:
        return None
    return expected[state] != canonical.get("state")


_HINTS = {
    DiagnosticCode.NONE: "No provider failure was classified.",
    DiagnosticCode.LEGACY_UNCLASSIFIED: "This attempt predates sanitized diagnostics.",
    DiagnosticCode.PROVIDER_START_FAILED: "The configured provider executable did not start.",
    DiagnosticCode.PROVIDER_AUTH_FAILED: "The provider reported an authentication failure.",
    DiagnosticCode.PROVIDER_AUTH_REQUIRED: (
        "The provider CLI reported that this host is signed out, so no work was started."
    ),
    DiagnosticCode.PROVIDER_AUTH_UNKNOWN: (
        "The provider authentication state could not be determined safely."
    ),
    DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED: (
        "The provider reported that its budget was exhausted."
    ),
    DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG: "The provider rejected an allowlisted launch flag.",
    DiagnosticCode.MCP_CONFIG_INVALID: "The provider rejected the generated MCP configuration.",
    DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE: (
        "The configured Agent Commons MCP executable is unavailable."
    ),
    DiagnosticCode.GIT_EXECUTABLE_UNAVAILABLE: ("The configured Git executable is unavailable."),
    DiagnosticCode.MCP_SPAWN_FAILED: "The configured Agent Commons MCP process did not start.",
    DiagnosticCode.MCP_HANDSHAKE_FAILED: (
        "The provider and MCP server did not complete startup negotiation."
    ),
    DiagnosticCode.MCP_BINDING_TIMEOUT: (
        "The MCP worker did not observe its canonical child binding in time."
    ),
    DiagnosticCode.MCP_TOOL_CONTRACT_FAILED: (
        "The provider reported a missing or incompatible Agent Commons tool."
    ),
    DiagnosticCode.BROKER_CONTROL_ERROR: "Broker lifecycle control failed after process start.",
    DiagnosticCode.PROVIDER_NONZERO_UNKNOWN: (
        "The provider exited nonzero without a recognized safe classification."
    ),
    DiagnosticCode.PROVIDER_REPORTED_ERROR: (
        "The provider reported a structured error even though its process exited successfully."
    ),
    DiagnosticCode.TERMINAL_TOOL_NOT_CALLED: (
        "The provider exited without calling a bounded terminal outcome tool."
    ),
    DiagnosticCode.TERMINAL_TOOL_REJECTED: (
        "A bounded terminal outcome tool call was rejected before canonical completion."
    ),
    DiagnosticCode.PROCESS_CANONICAL_MISMATCH: (
        "The terminal provider-process state disagrees with the canonical delegation state."
    ),
    DiagnosticCode.TRUSTED_WORKSPACE_REQUIRED: (
        "A writable provider profile refused to build its launch: the workspace is "
        "not marked trusted and no external isolation was declared."
    ),
    DiagnosticCode.CANONICAL_FINALIZATION_FAILED: (
        "Canonical finalization failed after the provider process became terminal."
    ),
    DiagnosticCode.REQUESTER_SESSION_REQUIRED: (
        "Provider launch must run as the active canonical delegation requester session."
    ),
    DiagnosticCode.REQUESTER_UNAVAILABLE: (
        "The attempt owner is unavailable, so this session cannot reconcile it automatically."
    ),
}

_SAFE_NEXT_ACTIONS = {
    DiagnosticCode.NONE: (),
    DiagnosticCode.LEGACY_UNCLASSIFIED: (
        "Inspect the canonical delegation and retry only from an explicit safe state.",
    ),
    DiagnosticCode.PROVIDER_START_FAILED: (
        "Run broker preflight for the selected profile.",
        "Verify the operator-owned executable path and installation.",
        "Until preflight passes, use the manual workflow from an authorized session "
        "instead of retrying provider launch.",
    ),
    DiagnosticCode.PROVIDER_AUTH_FAILED: (
        "Authenticate with the provider outside Agent Commons, then rerun preflight.",
    ),
    DiagnosticCode.PROVIDER_AUTH_REQUIRED: (
        "Sign in to the provider CLI directly, in the same host account Agent Commons runs as.",
        "Rerun broker preflight afterwards; preflight does not consume a delegation attempt.",
        "Agent Commons never supplies, stores, or switches a provider credential.",
    ),
    DiagnosticCode.PROVIDER_AUTH_UNKNOWN: (
        "Confirm the provider CLI is installed and its fixed auth status operation works "
        "in this host account.",
        "An undetermined state blocks launch before a child session or attempt exists; "
        "resolve the provider condition, then check authentication again.",
    ),
    DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED: (
        "Inspect the operator and delegation budget before authorizing new work.",
    ),
    DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG: (
        "Run broker preflight and update the provider CLI or profile compatibility.",
        "Until preflight passes, use the manual workflow from an authorized session.",
    ),
    DiagnosticCode.MCP_CONFIG_INVALID: (
        "Run broker preflight and inspect only the operator-owned profile configuration.",
    ),
    DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE: (
        "Add the uv tool executable directory to PATH or configure an absolute "
        "mcp_executable path.",
        "Install the matching agent-commons[mcp] source and rerun broker preflight.",
    ),
    DiagnosticCode.GIT_EXECUTABLE_UNAVAILABLE: (
        "Configure an operator-owned absolute git_executable path.",
        "Run broker preflight for the selected profile before retrying; preflight does "
        "not consume a delegation attempt.",
        "Until preflight passes, use the manual workflow from an authorized session.",
    ),
    DiagnosticCode.MCP_SPAWN_FAILED: ("Install the MCP extra and rerun broker preflight.",),
    DiagnosticCode.MCP_HANDSHAKE_FAILED: (
        "Verify provider and Agent Commons MCP versions with broker preflight.",
    ),
    DiagnosticCode.MCP_BINDING_TIMEOUT: (
        "Confirm every process uses the same explicit operational state root.",
        "Reconcile the attempt instead of relaunching it blindly.",
    ),
    DiagnosticCode.MCP_TOOL_CONTRACT_FAILED: (
        "Run broker preflight and compare the fixed worker tool catalog.",
    ),
    DiagnosticCode.BROKER_CONTROL_ERROR: (
        "Reconcile the attempt and inspect the canonical delegation before retrying.",
    ),
    DiagnosticCode.PROVIDER_NONZERO_UNKNOWN: (
        "Inspect provider-local logs outside Agent Commons without copying secrets into state.",
        "Mark the delegation needs_operator if process identity or outcome is ambiguous.",
    ),
    DiagnosticCode.PROVIDER_REPORTED_ERROR: (
        "Inspect provider-local logs outside Agent Commons without copying secrets into state.",
        "Resolve the provider condition, then create a new explicit delegation instead of "
        "blindly retrying this run.",
    ),
    DiagnosticCode.TERMINAL_TOOL_NOT_CALLED: (
        "Inspect the exact delegation and worker tool catalog before creating new work.",
        "Do not treat the successful provider exit as a successful workflow.",
    ),
    DiagnosticCode.TERMINAL_TOOL_REJECTED: (
        "Refresh the canonical delegation revision and inspect terminal-tool audit counters.",
        "Reconcile instead of blindly retrying an ambiguous worker.",
    ),
    DiagnosticCode.PROCESS_CANONICAL_MISMATCH: (
        "Join the attempt with its canonical delegation and inspect finalization telemetry.",
        "Reconcile the attempt; never promote process success to approval.",
    ),
    DiagnosticCode.CANONICAL_FINALIZATION_FAILED: (
        "Run doctor, inspect the canonical delegation, and reconcile the terminal attempt.",
    ),
    DiagnosticCode.REQUESTER_SESSION_REQUIRED: (
        "Return to the active canonical requester session and launch from there.",
        "Only if that requester is absent, expired, or closed and the delegation is still "
        "requested, use an operator-authorized delegation:recover session, then create a "
        "new delegation owned by an active requester.",
        "Otherwise perform the work manually from an authorized session and record its "
        "result outside this delegation; do not impersonate the canonical requester.",
    ),
    DiagnosticCode.REQUESTER_UNAVAILABLE: (
        "If the canonical delegation is still requested and its requester is absent, "
        "expired, or closed, use an operator-authorized delegation:recover session, "
        "then create a new delegation owned by an active requester.",
        "For active or input-needed work, prove provider termination before any canonical "
        "classification; never relaunch blindly.",
        "Otherwise perform the work manually from an authorized session and record its "
        "result outside this delegation; do not impersonate the canonical requester.",
    ),
    DiagnosticCode.TRUSTED_WORKSPACE_REQUIRED: (
        "Pass trusted_workspace: true in the writable profile's runtime configuration, "
        "or run the builder inside an externally OS-isolated worktree.",
        "Run broker preflight for the selected profile before retrying; preflight does "
        "not consume a delegation attempt.",
        "If neither trust mode is operator-authorized, use the manual workflow instead "
        "of launching the provider.",
        "The independent-reviewer profile of the same provider needs no such opt-in.",
    ),
}


@dataclass(frozen=True, slots=True)
class SafeDiagnostic:
    code: DiagnosticCode
    hint: str
    safe_next_actions: tuple[str, ...]

    @classmethod
    def create(cls, code: DiagnosticCode) -> SafeDiagnostic:
        return cls(
            code=code,
            hint=_HINTS[code],
            safe_next_actions=_SAFE_NEXT_ACTIONS[code],
        )


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for pattern in patterns)


def _structured_provider_error(stdout: bytes) -> str | None:
    """Return bounded text only for a known top-level provider error event.

    Stream output is untrusted and ephemeral.  Ordinary model prose may contain
    words such as "login" or "error", so it is never classified.  Only the
    fixed JSONL envelopes advertised by the bundled providers opt into the
    diagnostic classifier.
    """

    for raw_line in stdout.splitlines():
        if not raw_line.strip() or len(raw_line) > 64 * 1024:
            continue
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping):
            continue
        event_type = value.get("type")
        is_error = (event_type == "result" and value.get("is_error") is True) or event_type in {
            "error",
            "turn.failed",
        }
        if not is_error:
            continue
        fields: list[str] = []
        for key in ("subtype", "result", "message", "error"):
            field = value.get(key)
            if isinstance(field, str):
                fields.append(field)
            elif isinstance(field, Mapping):
                fields.extend(nested for nested in field.values() if isinstance(nested, str))
        return " ".join(fields)[:16_384].casefold()
    return None


def _classify_failure_text(value: str, *, structured_success: bool) -> DiagnosticCode:
    if "agent-commons-exec-gate: invalid control frame" in value:
        return DiagnosticCode.BROKER_CONTROL_ERROR
    if "agent-commons-exec-gate: provider exec failed" in value:
        return DiagnosticCode.PROVIDER_START_FAILED
    if _contains_any(
        value,
        (
            "authentication failed",
            "not authenticated",
            "please log in",
            "please run /login",
            "invalid api key",
            "unauthorized",
            "oauth token",
        ),
    ):
        return DiagnosticCode.PROVIDER_AUTH_FAILED
    if _contains_any(
        value,
        (
            "max budget",
            "max_budget_usd",
            "error_max_budget_usd",
            "budget exceeded",
            "budget exhausted",
            "cost limit",
            "spending limit",
        ),
    ):
        return DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED
    if _contains_any(value, ("unknown option", "unknown argument", "unrecognized option")):
        return DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG
    if _contains_any(value, ("invalid mcp config", "mcp config is invalid", "parse mcp config")):
        return DiagnosticCode.MCP_CONFIG_INVALID
    if _contains_any(
        value,
        ("failed to spawn mcp", "could not start mcp", "mcp server failed to start"),
    ):
        return DiagnosticCode.MCP_SPAWN_FAILED
    if _contains_any(
        value,
        ("mcp handshake", "mcp initialize", "mcp initialization", "protocol version mismatch"),
    ):
        return DiagnosticCode.MCP_HANDSHAKE_FAILED
    if _contains_any(
        value,
        ("binding was not canonically started", "mcp binding timeout", "binding deadline"),
    ):
        return DiagnosticCode.MCP_BINDING_TIMEOUT
    if _contains_any(
        value,
        ("mcp tool not found", "unknown mcp tool", "tool is not allowed", "missing mcp tool"),
    ):
        return DiagnosticCode.MCP_TOOL_CONTRACT_FAILED
    return (
        DiagnosticCode.PROVIDER_REPORTED_ERROR
        if structured_success
        else DiagnosticCode.PROVIDER_NONZERO_UNKNOWN
    )


def classify_process_result(result: ProcessResult) -> SafeDiagnostic:
    """Classify one result without returning any provider-controlled content."""

    structured_error = _structured_provider_error(result.stdout)
    if result.outcome is not RunOutcome.FAILED and structured_error is None:
        return SafeDiagnostic.create(DiagnosticCode.NONE)
    if result.reason is RunReason.START_FAILED:
        return SafeDiagnostic.create(DiagnosticCode.PROVIDER_START_FAILED)
    if result.reason is RunReason.CONTROL_ERROR:
        return SafeDiagnostic.create(DiagnosticCode.BROKER_CONTROL_ERROR)

    # Decode only the already bounded buffers. Replacement characters keep the
    # classifier total while the original bytes remain ephemeral.
    stderr = result.stderr_tail or result.stderr
    value = structured_error or stderr.decode("utf-8", "replace").casefold()
    code = _classify_failure_text(
        value,
        structured_success=structured_error is not None,
    )
    return SafeDiagnostic.create(code)


def _utf8_tail(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    tail = encoded[-limit:]
    while tail and (tail[0] & 0xC0) == 0x80:
        tail = tail[1:]
    return tail.decode("utf-8", "replace"), True


def sanitize_provider_stderr_tail(
    result: ProcessResult,
    *,
    policy: SecurityPolicy | None = None,
) -> tuple[str | None, bool, bool]:
    """Return a bounded local-only diagnostic tail for an unsuccessful run.

    Provider stdout remains ineligible because it can be a response or
    transcript.  Stderr is retained only for non-successful processes, has ANSI
    and control bytes removed, replaces absolute paths, and redacts complete
    secret/PII-bearing lines before the attempt document sees it.  A final
    fail-closed scan prevents a sanitizer regression from persisting unsafe
    content.
    """

    if result.outcome is RunOutcome.SUCCEEDED:
        return None, False, False
    raw = result.stderr_tail or result.stderr[-PROVIDER_STDERR_TAIL_BYTES:]
    if not raw:
        return None, bool(result.stderr_tail_truncated), False
    text = raw.decode("utf-8", "replace")
    rendered = _ANSI_ESCAPE.sub("", text)
    rendered = _CONTROL_CHARACTER.sub("�", rendered)
    rendered, paths = _POSIX_ABSOLUTE_PATH.subn(_REDACTED_PATH, rendered)
    rendered, windows_paths = _WINDOWS_ABSOLUTE_PATH.subn(_REDACTED_PATH, rendered)
    redacted = rendered != text or paths > 0 or windows_paths > 0
    security = policy or SecurityPolicy()
    blocked_lines: set[int] = set()
    for start_line, end_line, finding in security.scan_text_lines(rendered):
        if finding.classification in security.blocked_classifications:
            blocked_lines.update(range(start_line, end_line + 1))
    lines: list[str] = []
    for number, line in enumerate(rendered.splitlines(keepends=True), start=1):
        if number not in blocked_lines:
            lines.append(line)
            continue
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        lines.append(_REDACTED_DIAGNOSTIC_LINE + ending)
        redacted = True
    safe = "".join(lines).strip()
    if not safe:
        return None, bool(result.stderr_tail_truncated), redacted
    safe, capped = _utf8_tail(safe, PROVIDER_STDERR_TAIL_BYTES)
    try:
        security.assert_safe(safe, context="provider stderr diagnostic tail")
    except SecurityPolicyError:
        safe = _REDACTED_DIAGNOSTIC_LINE
        redacted = True
    return safe, bool(result.stderr_tail_truncated or capped), redacted


def diagnostic_hint(code: str | DiagnosticCode) -> str:
    """Return the fixed allowlisted operator hint for a stored code."""

    return _HINTS[DiagnosticCode(code)]


def diagnostic_safe_next_actions(code: str | DiagnosticCode) -> list[str]:
    """Return fixed, content-free recovery actions for a diagnostic code."""

    return list(_SAFE_NEXT_ACTIONS[DiagnosticCode(code)])


def configuration_failure_diagnostic(exc: ConfigurationError) -> SafeDiagnostic:
    """Classify a launch-plan refusal before any provider attempt is reserved.

    Executable resolution errors expose only their fixed component role.  Other
    profile refusals are classified from maintainer-owned validation messages;
    no provider output or operator configuration value is persisted here.
    """

    role = str(getattr(exc, "role", ""))
    if role == "mcp":
        code = DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE
    elif role == "git":
        code = DiagnosticCode.GIT_EXECUTABLE_UNAVAILABLE
    elif "trusted_workspace" in str(exc):
        code = DiagnosticCode.TRUSTED_WORKSPACE_REQUIRED
    else:
        code = DiagnosticCode.PROVIDER_START_FAILED
    return SafeDiagnostic.create(code)


def sanitized_configuration_failure(exc: ConfigurationError) -> ConfigurationError:
    """Return a public pre-start error without rejected configuration content."""

    diagnostic = configuration_failure_diagnostic(exc)
    error = ConfigurationError(
        "Provider launch was refused before any delegation attempt was reserved. " + diagnostic.hint
    )
    error.code = diagnostic.code.value  # type: ignore[attr-defined]
    error.safe_next_actions = diagnostic.safe_next_actions  # type: ignore[attr-defined]
    return error


def error_safe_next_actions(exc: Exception) -> list[str]:
    """Map a public failure class to fixed recovery actions."""

    if isinstance(exc, SecurityPolicyError):
        return [
            "Remove or redact secret-bearing content before retrying.",
            "Do not paste the rejected content into diagnostics or canonical state.",
        ]
    if isinstance(exc, ClaimConflictError):
        return [
            "List active claims and coordinate with the current owner.",
            "Break a claim only with explicit operator authority and a recorded reason.",
        ]
    if isinstance(exc, IdempotencyConflictError):
        return [
            "Reuse the idempotency key only with identical content.",
            "Choose a new stable key for materially different work.",
        ]
    if isinstance(exc, LifecycleConflictError):
        return [
            "Refresh the entity and retry against its current exact revision.",
            "Do not bypass the lifecycle transition or independent-review boundary.",
        ]
    if isinstance(exc, IntegrityError):
        return [
            "Run doctor in read-only mode before attempting another write.",
            "Use an explicit maintenance event for repair; do not edit ledger files directly.",
        ]
    if isinstance(exc, ConfigurationError):
        return [
            "Inspect the operator-owned configuration and run the support command.",
        ]
    if isinstance(exc, ValidationError):
        return ["Correct the bounded input using the command help, then retry."]
    if isinstance(exc, FileNotFoundError):
        return ["Verify the requested path or install the optional component, then retry."]
    return ["Run the support and doctor commands, then inspect the reported safe metadata."]
