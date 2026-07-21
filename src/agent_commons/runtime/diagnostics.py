"""Secret-free classification for bounded ephemeral provider output.

The broker may inspect the already bounded process buffers exactly once.  This
module returns only a closed code and a maintainer-owned hint; raw provider
bytes, prompts, tool arguments, paths, and matched fragments are never returned
or persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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
}


@dataclass(frozen=True, slots=True)
class SafeDiagnostic:
    code: DiagnosticCode
    hint: str

    @classmethod
    def create(cls, code: DiagnosticCode) -> SafeDiagnostic:
        return cls(code=code, hint=_HINTS[code])


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
