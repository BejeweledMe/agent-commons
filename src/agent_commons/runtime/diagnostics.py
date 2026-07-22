"""Secret-free classification for bounded ephemeral provider output.

The broker may inspect the already bounded process buffers exactly once.  This
module returns only a closed code and a maintainer-owned hint; raw provider
bytes, prompts, tool arguments, paths, and matched fragments are never returned
or persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_commons.errors import (
    ClaimConflictError,
    ConfigurationError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)

from .subprocess_runner import ProcessResult, RunOutcome, RunReason


class DiagnosticCode(StrEnum):
    NONE = "none"
    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    PROVIDER_START_FAILED = "provider_start_failed"
    PROVIDER_AUTH_FAILED = "provider_auth_failed"
    PROVIDER_BUDGET_EXHAUSTED = "provider_budget_exhausted"
    UNSUPPORTED_PROVIDER_FLAG = "unsupported_provider_flag"
    MCP_CONFIG_INVALID = "mcp_config_invalid"
    MCP_SPAWN_FAILED = "mcp_spawn_failed"
    MCP_HANDSHAKE_FAILED = "mcp_handshake_failed"
    MCP_BINDING_TIMEOUT = "mcp_binding_timeout"
    MCP_TOOL_CONTRACT_FAILED = "mcp_tool_contract_failed"
    BROKER_CONTROL_ERROR = "broker_control_error"
    PROVIDER_NONZERO_UNKNOWN = "provider_nonzero_unknown"
    TERMINAL_TOOL_NOT_CALLED = "terminal_tool_not_called"
    TERMINAL_TOOL_REJECTED = "terminal_tool_rejected"
    PROCESS_CANONICAL_MISMATCH = "process_canonical_mismatch"
    CANONICAL_FINALIZATION_FAILED = "canonical_finalization_failed"
    REQUESTER_UNAVAILABLE = "requester_unavailable"


_HINTS = {
    DiagnosticCode.NONE: "No provider failure was classified.",
    DiagnosticCode.LEGACY_UNCLASSIFIED: "This attempt predates sanitized diagnostics.",
    DiagnosticCode.PROVIDER_START_FAILED: "The configured provider executable did not start.",
    DiagnosticCode.PROVIDER_AUTH_FAILED: "The provider reported an authentication failure.",
    DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED: (
        "The provider reported that its budget was exhausted."
    ),
    DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG: "The provider rejected an allowlisted launch flag.",
    DiagnosticCode.MCP_CONFIG_INVALID: "The provider rejected the generated MCP configuration.",
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
    DiagnosticCode.TERMINAL_TOOL_NOT_CALLED: (
        "The provider exited without calling a bounded terminal outcome tool."
    ),
    DiagnosticCode.TERMINAL_TOOL_REJECTED: (
        "A bounded terminal outcome tool call was rejected before canonical completion."
    ),
    DiagnosticCode.PROCESS_CANONICAL_MISMATCH: (
        "The terminal provider-process state disagrees with the canonical delegation state."
    ),
    DiagnosticCode.CANONICAL_FINALIZATION_FAILED: (
        "Canonical finalization failed after the provider process became terminal."
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
    ),
    DiagnosticCode.PROVIDER_AUTH_FAILED: (
        "Authenticate with the provider outside Agent Commons, then rerun preflight.",
    ),
    DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED: (
        "Inspect the operator and delegation budget before authorizing new work.",
    ),
    DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG: (
        "Run broker preflight and update the provider CLI or profile compatibility.",
    ),
    DiagnosticCode.MCP_CONFIG_INVALID: (
        "Run broker preflight and inspect only the operator-owned profile configuration.",
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
    DiagnosticCode.REQUESTER_UNAVAILABLE: (
        "If the canonical delegation is still requested, use an operator-authorized "
        "delegation:recover session.",
        "For active or input-needed work, prove provider termination before any canonical "
        "classification; never relaunch blindly.",
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


def classify_process_result(result: ProcessResult) -> SafeDiagnostic:
    """Classify one result without returning any provider-controlled content."""

    if result.outcome is not RunOutcome.FAILED:
        return SafeDiagnostic.create(DiagnosticCode.NONE)
    if result.reason is RunReason.START_FAILED:
        return SafeDiagnostic.create(DiagnosticCode.PROVIDER_START_FAILED)
    if result.reason is RunReason.CONTROL_ERROR:
        return SafeDiagnostic.create(DiagnosticCode.BROKER_CONTROL_ERROR)

    # Decode only the already bounded buffers.  Replacement characters keep the
    # classifier total while the original bytes remain ephemeral.
    value = (result.stderr + b"\n" + result.stdout).decode("utf-8", "replace").casefold()
    if "agent-commons-exec-gate: invalid control frame" in value:
        code = DiagnosticCode.BROKER_CONTROL_ERROR
    elif "agent-commons-exec-gate: provider exec failed" in value:
        code = DiagnosticCode.PROVIDER_START_FAILED
    elif _contains_any(
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
        code = DiagnosticCode.PROVIDER_AUTH_FAILED
    elif _contains_any(
        value,
        (
            "max budget",
            "budget exceeded",
            "budget exhausted",
            "cost limit",
            "spending limit",
        ),
    ):
        code = DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED
    elif _contains_any(value, ("unknown option", "unknown argument", "unrecognized option")):
        code = DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG
    elif _contains_any(value, ("invalid mcp config", "mcp config is invalid", "parse mcp config")):
        code = DiagnosticCode.MCP_CONFIG_INVALID
    elif _contains_any(
        value,
        ("failed to spawn mcp", "could not start mcp", "mcp server failed to start"),
    ):
        code = DiagnosticCode.MCP_SPAWN_FAILED
    elif _contains_any(
        value,
        ("mcp handshake", "mcp initialize", "mcp initialization", "protocol version mismatch"),
    ):
        code = DiagnosticCode.MCP_HANDSHAKE_FAILED
    elif _contains_any(
        value,
        ("binding was not canonically started", "mcp binding timeout", "binding deadline"),
    ):
        code = DiagnosticCode.MCP_BINDING_TIMEOUT
    elif _contains_any(
        value,
        ("mcp tool not found", "unknown mcp tool", "tool is not allowed", "missing mcp tool"),
    ):
        code = DiagnosticCode.MCP_TOOL_CONTRACT_FAILED
    else:
        code = DiagnosticCode.PROVIDER_NONZERO_UNKNOWN
    return SafeDiagnostic.create(code)


def diagnostic_hint(code: str | DiagnosticCode) -> str:
    """Return the fixed allowlisted operator hint for a stored code."""

    return _HINTS[DiagnosticCode(code)]


def diagnostic_safe_next_actions(code: str | DiagnosticCode) -> list[str]:
    """Return fixed, content-free recovery actions for a diagnostic code."""

    return list(_SAFE_NEXT_ACTIONS[DiagnosticCode(code)])


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
