"""Provider-neutral runtime capability and refusal value objects.

These records are deliberately descriptive.  They expose what an allowlisted
profile can do without exposing the executable, provider argv, MCP
configuration, environment, or ephemeral instruction that implements it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from typing import Any, Literal

from agent_commons.errors import ValidationError

from .diagnostics import DiagnosticCode, diagnostic_hint, diagnostic_safe_next_actions
from .model import BuiltinProfileId, Provider, validate_model_name

PROVIDER_ADAPTER_VERSION = "provider-adapter.v1"
PROVIDER_CAPABILITY_COLLECTION_LIMIT = 128
PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT = 16 * 1024
LAUNCH_PLAN_SKILL_REF_LIMIT = 8
_SAFE_CAPABILITY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_CODEX_PERMISSION_MODES = frozenset({"workspace-write:never", "read-only:never"})
_CLAUDE_PERMISSION_MODES = frozenset({"acceptEdits", "dontAsk", "plan"})


def _normalized_enum(value: object, enum_type: type[StrEnum], *, label: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} is unsupported") from exc


def _normalized_names(
    value: object,
    *,
    label: str,
    allow_empty: bool,
    max_count: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{label} must be a collection of names")
    try:
        raw_names = tuple(islice(iter(value), max_count + 1))
    except Exception as exc:
        raise ValidationError(f"{label} must be a collection of names") from exc
    if not allow_empty and not raw_names:
        raise ValidationError(f"{label} must not be empty")
    if len(raw_names) > max_count:
        raise ValidationError(f"{label} exceeds the collection-size limit")
    if any(
        not isinstance(name, str) or _SAFE_CAPABILITY_NAME.fullmatch(name) is None
        for name in raw_names
    ):
        raise ValidationError(f"{label} contains an invalid name")
    if len(set(raw_names)) != len(raw_names):
        raise ValidationError(f"{label} contains duplicate names")
    return raw_names


def _capability_serialized_size(
    *,
    provider: Provider,
    profile_id: BuiltinProfileId,
    mcp: bool,
    mcp_tool_names: tuple[str, ...],
    skills: tuple[str, ...],
    input_modes: tuple[str, ...],
    resume_mode: ResumeMode,
    cancellation_mode: CancellationMode,
    usage_reporting: UsageReporting,
    sandbox_boundary: SandboxBoundary,
    budget_units: tuple[BudgetUnit, ...],
) -> int:
    payload = {
        "provider": provider.value,
        "profile_id": profile_id.value,
        "adapter_version": PROVIDER_ADAPTER_VERSION,
        "mcp": mcp,
        "mcp_tool_names": list(mcp_tool_names),
        "skills": list(skills),
        "input_modes": list(input_modes),
        "resume_mode": resume_mode.value,
        "cancellation_mode": cancellation_mode.value,
        "usage_reporting": usage_reporting.value,
        "sandbox_boundary": sandbox_boundary.value,
        "budget_units": [unit.value for unit in budget_units],
    }
    return len(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _normalized_budget_units(value: object, *, label: str) -> tuple[BudgetUnit, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{label} must be a collection of budget units")
    max_count = len(BudgetUnit)
    try:
        raw_units = tuple(islice(iter(value), max_count + 1))
    except Exception as exc:
        raise ValidationError(f"{label} must be a collection of budget units") from exc
    if len(raw_units) > max_count:
        raise ValidationError(f"{label} exceeds the collection-size limit")
    try:
        units = tuple(BudgetUnit(unit) for unit in raw_units)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} contains an unsupported budget unit") from exc
    if not units:
        raise ValidationError(f"{label} must not be empty")
    if len(set(units)) != len(units):
        raise ValidationError(f"{label} contains duplicate budget units")
    return tuple(unit for unit in BudgetUnit if unit in units)


class BudgetUnit(StrEnum):
    MICRO_USD = "micro_usd"
    PROVIDER_UNITS = "provider_units"


class SandboxBoundary(StrEnum):
    OS_ENFORCED = "os_enforced"
    TRUSTED_WORKSPACE = "trusted_workspace"
    NONE = "none"


class ResumeMode(StrEnum):
    NONE = "none"


class CancellationMode(StrEnum):
    BROKER = "broker"


class UsageReporting(StrEnum):
    NONE = "none"


class ProviderRefusalCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_START_FAILED = "provider_start_failed"
    MCP_EXECUTABLE_UNAVAILABLE = "mcp_executable_unavailable"
    GIT_EXECUTABLE_UNAVAILABLE = "git_executable_unavailable"
    TRUSTED_WORKSPACE_REQUIRED = "trusted_workspace_required"
    PROVIDER_CAPABILITY_UNSUPPORTED = "provider_capability_unsupported"
    SKILL_PROJECTION_UNAVAILABLE = "skill_projection_unavailable"
    BUDGET_NOT_ENFORCEABLE = "budget_not_enforceable"
    HOST_SANDBOX_REFUSED = "host_sandbox_refused"
    INITIALIZATION_TIMEOUT = "initialization_timeout"
    INITIALIZATION_UNAVAILABLE = "initialization_unavailable"
    PROVIDER_QUALIFICATION_REQUIRED = "provider_qualification_required"
    PROVIDER_QUALIFICATION_FAILED = "provider_qualification_failed"


class ProviderCapability(StrEnum):
    MCP = "mcp"
    SKILL_PROJECTION = "skill_projection"
    MONETARY_BUDGET = "monetary_budget"
    RESUME = "resume"
    USAGE_REPORTING = "usage_reporting"


class LaunchPurpose(StrEnum):
    IMPLEMENTATION = "implementation"
    INDEPENDENT_REVIEW = "independent_review"
    VERIFICATION = "verification"


_REFUSAL_COPY: dict[ProviderRefusalCode, tuple[str, tuple[str, ...]]] = {
    ProviderRefusalCode.PROVIDER_UNAVAILABLE: (
        "The requested provider has no allowlisted runtime adapter.",
        ("Select an operator-configured Codex or Claude profile.",),
    ),
    **{
        ProviderRefusalCode(code.value): (
            diagnostic_hint(code),
            tuple(diagnostic_safe_next_actions(code)),
        )
        for code in (
            DiagnosticCode.PROVIDER_START_FAILED,
            DiagnosticCode.MCP_EXECUTABLE_UNAVAILABLE,
            DiagnosticCode.GIT_EXECUTABLE_UNAVAILABLE,
            DiagnosticCode.TRUSTED_WORKSPACE_REQUIRED,
        )
    },
    ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED: (
        "The selected provider profile does not support the requested capability.",
        ("Choose a profile that advertises the required capability.",),
    ),
    ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE: (
        "Provider-specific skill projection is not available in this runtime version.",
        (
            "Remove the skill requirement or use the manual workflow until a verified "
            "provider projection is installed.",
        ),
    ),
    ProviderRefusalCode.BUDGET_NOT_ENFORCEABLE: (
        "The selected provider cannot enforce the requested monetary budget.",
        ("Use provider-unit limits or choose a profile that advertises micro_usd support.",),
    ),
    ProviderRefusalCode.HOST_SANDBOX_REFUSED: (
        "The host sandbox refused the provider initialization operation.",
        (
            "Run the provider through an operator-owned host outside the nested sandbox.",
            "Do not retry this delegation until the host boundary changes.",
        ),
    ),
    ProviderRefusalCode.INITIALIZATION_TIMEOUT: (
        "The fixed provider initialization probe exceeded its bounded wall time.",
        ("Inspect the provider installation, then rerun preflight before creating work.",),
    ),
    ProviderRefusalCode.INITIALIZATION_UNAVAILABLE: (
        "The fixed provider initialization probe could not complete safely.",
        ("Verify the allowlisted provider installation and rerun preflight.",),
    ),
    ProviderRefusalCode.PROVIDER_QUALIFICATION_REQUIRED: (
        "The selected provider profile has no qualification receipt for this runtime bundle.",
        ("Run the explicit provider canary for this exact profile before launching work.",),
    ),
    ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED: (
        "The selected provider profile is not qualified for this runtime bundle.",
        (
            "Resolve the failed static, initialization, or behavioral probe and rerun the "
            "explicit provider canary.",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider: Provider
    adapter_version: str
    profile_id: BuiltinProfileId
    model: str | None
    sandbox_boundary: SandboxBoundary
    permission_mode: str
    budget_units: tuple[BudgetUnit, ...]
    instruction_transport: Literal["stdin"] = "stdin"

    def __post_init__(self) -> None:
        provider = Provider(_normalized_enum(self.provider, Provider, label="provider descriptor"))
        profile_id = BuiltinProfileId(
            _normalized_enum(
                self.profile_id,
                BuiltinProfileId,
                label="provider descriptor profile",
            )
        )
        sandbox_boundary = SandboxBoundary(
            _normalized_enum(
                self.sandbox_boundary,
                SandboxBoundary,
                label="provider descriptor sandbox boundary",
            )
        )
        if profile_id.provider is not provider:
            raise ValidationError("provider descriptor profile/provider mismatch")
        if self.adapter_version != PROVIDER_ADAPTER_VERSION:
            raise ValidationError("provider descriptor adapter version is unsupported")
        if (
            not isinstance(self.permission_mode, str)
            or not self.permission_mode
            or len(self.permission_mode) > 128
            or any(ord(character) < 32 for character in self.permission_mode)
        ):
            raise ValidationError("provider descriptor permission mode is invalid")
        if self.instruction_transport != "stdin":
            raise ValidationError("provider descriptor instruction transport is unsupported")
        if self.model is not None and not isinstance(self.model, str):
            raise ValidationError("provider descriptor model is invalid")
        model = validate_model_name(self.model)
        budget_units = _normalized_budget_units(
            self.budget_units,
            label="provider descriptor budget units",
        )
        permission_modes = (
            _CODEX_PERMISSION_MODES if provider is Provider.CODEX else _CLAUDE_PERMISSION_MODES
        )
        if self.permission_mode not in permission_modes:
            raise ValidationError("provider descriptor permission mode is unsupported")
        if provider is Provider.CODEX and (
            sandbox_boundary is not SandboxBoundary.OS_ENFORCED
            or budget_units != (BudgetUnit.PROVIDER_UNITS,)
        ):
            raise ValidationError("Codex provider descriptor capabilities are inconsistent")
        if provider is Provider.CLAUDE and (
            sandbox_boundary is SandboxBoundary.OS_ENFORCED
            or budget_units != (BudgetUnit.MICRO_USD, BudgetUnit.PROVIDER_UNITS)
        ):
            raise ValidationError("Claude provider descriptor capabilities are inconsistent")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "sandbox_boundary", sandbox_boundary)
        object.__setattr__(self, "budget_units", budget_units)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "adapter_version": self.adapter_version,
            "profile_id": self.profile_id.value,
            "model": self.model,
            "sandbox_boundary": self.sandbox_boundary.value,
            "permission_mode": self.permission_mode,
            "budget_units": [unit.value for unit in self.budget_units],
            "instruction_transport": self.instruction_transport,
        }


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    provider: Provider
    profile_id: BuiltinProfileId
    adapter_version: str
    mcp: bool
    mcp_tool_names: tuple[str, ...]
    skills: tuple[str, ...]
    input_modes: tuple[str, ...]
    resume_mode: ResumeMode
    cancellation_mode: CancellationMode
    usage_reporting: UsageReporting
    sandbox_boundary: SandboxBoundary
    budget_units: tuple[BudgetUnit, ...]

    def __post_init__(self) -> None:
        provider = Provider(_normalized_enum(self.provider, Provider, label="capability provider"))
        profile_id = BuiltinProfileId(
            _normalized_enum(
                self.profile_id,
                BuiltinProfileId,
                label="capability profile",
            )
        )
        resume_mode = ResumeMode(
            _normalized_enum(self.resume_mode, ResumeMode, label="resume capability")
        )
        cancellation_mode = CancellationMode(
            _normalized_enum(
                self.cancellation_mode,
                CancellationMode,
                label="cancellation capability",
            )
        )
        usage_reporting = UsageReporting(
            _normalized_enum(
                self.usage_reporting,
                UsageReporting,
                label="usage-reporting capability",
            )
        )
        sandbox_boundary = SandboxBoundary(
            _normalized_enum(
                self.sandbox_boundary,
                SandboxBoundary,
                label="capability sandbox boundary",
            )
        )
        if profile_id.provider is not provider:
            raise ValidationError("capability profile/provider mismatch")
        if self.adapter_version != PROVIDER_ADAPTER_VERSION:
            raise ValidationError("capability adapter version is unsupported")
        if not isinstance(self.mcp, bool):
            raise ValidationError("MCP capability must be boolean")
        mcp_tool_names = _normalized_names(
            self.mcp_tool_names,
            label="MCP tool capabilities",
            allow_empty=not self.mcp,
            max_count=PROVIDER_CAPABILITY_COLLECTION_LIMIT,
        )
        if not self.mcp and mcp_tool_names:
            raise ValidationError("MCP-disabled capability set cannot advertise tools")
        skills = _normalized_names(
            self.skills,
            label="skill capabilities",
            allow_empty=True,
            max_count=PROVIDER_CAPABILITY_COLLECTION_LIMIT,
        )
        input_modes = _normalized_names(
            self.input_modes,
            label="input-mode capabilities",
            allow_empty=False,
            max_count=PROVIDER_CAPABILITY_COLLECTION_LIMIT,
        )
        if input_modes != ("stdin",):
            raise ValidationError("input-mode capabilities are unsupported")
        budget_units = _normalized_budget_units(
            self.budget_units,
            label="capability budget units",
        )
        if provider is Provider.CODEX and (
            sandbox_boundary is not SandboxBoundary.OS_ENFORCED
            or budget_units != (BudgetUnit.PROVIDER_UNITS,)
        ):
            raise ValidationError("Codex capability set is inconsistent")
        if provider is Provider.CLAUDE and (
            sandbox_boundary is SandboxBoundary.OS_ENFORCED
            or budget_units != (BudgetUnit.MICRO_USD, BudgetUnit.PROVIDER_UNITS)
        ):
            raise ValidationError("Claude capability set is inconsistent")
        serialized_size = _capability_serialized_size(
            provider=provider,
            profile_id=profile_id,
            mcp=self.mcp,
            mcp_tool_names=mcp_tool_names,
            skills=skills,
            input_modes=input_modes,
            resume_mode=resume_mode,
            cancellation_mode=cancellation_mode,
            usage_reporting=usage_reporting,
            sandbox_boundary=sandbox_boundary,
            budget_units=budget_units,
        )
        if serialized_size > PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT:
            raise ValidationError("capability set exceeds the serialized-size limit")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "mcp_tool_names", mcp_tool_names)
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "input_modes", input_modes)
        object.__setattr__(self, "resume_mode", resume_mode)
        object.__setattr__(self, "cancellation_mode", cancellation_mode)
        object.__setattr__(self, "usage_reporting", usage_reporting)
        object.__setattr__(self, "sandbox_boundary", sandbox_boundary)
        object.__setattr__(self, "budget_units", budget_units)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "profile_id": self.profile_id.value,
            "adapter_version": self.adapter_version,
            "mcp": self.mcp,
            "mcp_tool_names": list(self.mcp_tool_names),
            "skills": list(self.skills),
            "input_modes": list(self.input_modes),
            "resume_mode": self.resume_mode.value,
            "cancellation_mode": self.cancellation_mode.value,
            "usage_reporting": self.usage_reporting.value,
            "sandbox_boundary": self.sandbox_boundary.value,
            "budget_units": [unit.value for unit in self.budget_units],
        }


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """Provider-neutral, ephemeral inputs for P1 capability validation.

    The instruction and skill identities stay in memory.  ``as_dict`` exposes
    only bounded metadata and counts, so this value object cannot become an
    accidental prompt, skill, argv, or environment persistence surface.
    """

    profile_id: BuiltinProfileId
    purpose: LaunchPurpose
    instruction: str
    skill_refs: tuple[str, ...] = ()
    required_capabilities: tuple[ProviderCapability, ...] = ()
    budget_unit: BudgetUnit = BudgetUnit.PROVIDER_UNITS
    budget_limit: int | None = None

    def __post_init__(self) -> None:
        try:
            profile_id = BuiltinProfileId(self.profile_id)
            purpose = LaunchPurpose(self.purpose)
            required_capabilities = tuple(
                ProviderCapability(capability) for capability in self.required_capabilities
            )
            budget_unit = BudgetUnit(self.budget_unit)
        except ValueError as exc:
            raise ValidationError("provider launch plan contains an unsupported value") from exc
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise ValidationError("provider launch instruction must be non-empty")
        if len(self.instruction.encode("utf-8")) > 1_000_000:
            raise ValidationError("provider launch instruction exceeds the one-megabyte limit")
        skill_refs = _normalized_names(
            self.skill_refs,
            label="provider launch skill references",
            allow_empty=True,
            max_count=LAUNCH_PLAN_SKILL_REF_LIMIT,
        )
        if len(set(required_capabilities)) != len(required_capabilities):
            raise ValidationError("provider launch plan contains duplicate capabilities")
        if self.budget_limit is not None and (
            isinstance(self.budget_limit, bool)
            or not isinstance(self.budget_limit, int)
            or self.budget_limit < 1
        ):
            raise ValidationError("provider launch budget limit must be a positive integer")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "skill_refs", skill_refs)
        object.__setattr__(self, "required_capabilities", required_capabilities)
        object.__setattr__(self, "budget_unit", budget_unit)

    @property
    def provider(self) -> Provider:
        return self.profile_id.provider

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id.value,
            "provider": self.provider.value,
            "purpose": self.purpose.value,
            "skill_ref_count": len(self.skill_refs),
            "required_capabilities": [
                capability.value for capability in self.required_capabilities
            ],
            "budget_unit": self.budget_unit.value,
            "budget_limit": self.budget_limit,
            "has_ephemeral_instruction": True,
        }


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """Bounded process diagnostics that carry no canonical-success meaning."""

    provider: Provider
    diagnostic_code: DiagnosticCode
    event_shape_tags: tuple[str, ...]
    terminal_tool_signal: None = None
    usage_totals: None = None

    def __post_init__(self) -> None:
        provider = Provider(_normalized_enum(self.provider, Provider, label="provider outcome"))
        diagnostic_code = DiagnosticCode(
            _normalized_enum(
                self.diagnostic_code,
                DiagnosticCode,
                label="provider outcome diagnostic",
            )
        )
        tags = _normalized_names(
            self.event_shape_tags,
            label="provider outcome event-shape tags",
            allow_empty=True,
            max_count=4,
        )
        if any(len(tag) > 64 for tag in tags):
            raise ValidationError("provider outcome contains invalid event-shape tags")
        if self.terminal_tool_signal is not None:
            raise ValidationError("provider outcome cannot infer a terminal-tool signal")
        if self.usage_totals is not None:
            raise ValidationError("provider outcome usage totals are unavailable")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "diagnostic_code", diagnostic_code)
        object.__setattr__(self, "event_shape_tags", tags)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "diagnostic_code": self.diagnostic_code.value,
            "event_shape_tags": list(self.event_shape_tags),
            "terminal_tool_signal": self.terminal_tool_signal,
            "usage_totals": self.usage_totals,
        }


@dataclass(frozen=True, slots=True)
class TypedRefusal:
    """A closed, maintainer-authored refusal with no durable side effect."""

    code: ProviderRefusalCode
    provider: Provider | None = None
    profile_id: BuiltinProfileId | None = None
    capability: ProviderCapability | None = None
    durable_effect: Literal["none"] = field(default="none", init=False)
    message: str = field(init=False)
    safe_next_actions: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        code = ProviderRefusalCode(
            _normalized_enum(self.code, ProviderRefusalCode, label="provider refusal code")
        )
        provider = (
            None
            if self.provider is None
            else Provider(
                _normalized_enum(self.provider, Provider, label="provider refusal provider")
            )
        )
        profile_id = (
            None
            if self.profile_id is None
            else BuiltinProfileId(
                _normalized_enum(
                    self.profile_id,
                    BuiltinProfileId,
                    label="provider refusal profile",
                )
            )
        )
        capability = (
            None
            if self.capability is None
            else ProviderCapability(
                _normalized_enum(
                    self.capability,
                    ProviderCapability,
                    label="provider refusal capability",
                )
            )
        )
        if profile_id is not None:
            if provider is None:
                provider = profile_id.provider
            elif profile_id.provider is not provider:
                raise ValidationError("provider refusal profile/provider mismatch")
        message, safe_next_actions = _REFUSAL_COPY[code]
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "safe_next_actions", safe_next_actions)

    @classmethod
    def create(
        cls,
        code: ProviderRefusalCode,
        *,
        provider: Provider | None = None,
        profile_id: BuiltinProfileId | None = None,
        capability: ProviderCapability | None = None,
    ) -> TypedRefusal:
        return cls(
            code=code,
            provider=provider,
            profile_id=profile_id,
            capability=capability,
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "safe_next_actions": list(self.safe_next_actions),
            "durable_effect": self.durable_effect,
        }
        if self.provider is not None:
            result["provider"] = self.provider.value
        if self.profile_id is not None:
            result["profile_id"] = self.profile_id.value
        if self.capability is not None:
            result["capability"] = self.capability.value
        return result


class ProviderInitializationState(StrEnum):
    READY = "ready"
    WARNING = "warning"
    HOST_SANDBOX_REFUSED = "host_sandbox_refused"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"

    @property
    def blocks_launch(self) -> bool:
        return self not in {
            ProviderInitializationState.READY,
            ProviderInitializationState.WARNING,
        }


@dataclass(frozen=True, slots=True)
class ProviderInitializationProbeSpec:
    """Adapter-owned fixed no-model operation for the generic probe runner."""

    provider: Provider
    profile_id: BuiltinProfileId
    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        provider = Provider(self.provider)
        profile_id = BuiltinProfileId(self.profile_id)
        arguments = tuple(self.arguments)
        if profile_id.provider is not provider:
            raise ValidationError("initialization probe profile/provider mismatch")
        if not arguments or any(
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or len(argument.encode("utf-8")) > 256
            for argument in arguments
        ):
            raise ValidationError("initialization probe arguments are invalid")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "arguments", arguments)


@dataclass(frozen=True, slots=True)
class ProviderInitializationStatus:
    provider: Provider
    state: ProviderInitializationState

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", Provider(self.provider))
        object.__setattr__(self, "state", ProviderInitializationState(self.state))

    @property
    def supported(self) -> bool:
        return self.state is not ProviderInitializationState.WARNING

    @property
    def blocks_launch(self) -> bool:
        return self.state.blocks_launch

    def refusal(self, *, profile_id: BuiltinProfileId) -> TypedRefusal:
        if not self.blocks_launch:
            raise ValueError("a non-blocking initialization status is not a refusal")
        code = {
            ProviderInitializationState.HOST_SANDBOX_REFUSED: (
                ProviderRefusalCode.HOST_SANDBOX_REFUSED
            ),
            ProviderInitializationState.TIMED_OUT: ProviderRefusalCode.INITIALIZATION_TIMEOUT,
            ProviderInitializationState.UNAVAILABLE: (
                ProviderRefusalCode.INITIALIZATION_UNAVAILABLE
            ),
        }[self.state]
        return TypedRefusal.create(code, provider=self.provider, profile_id=profile_id)
