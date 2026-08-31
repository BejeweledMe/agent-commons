"""Closed provider-adapter facade over the existing runner profiles.

P1 intentionally keeps launch ownership and invocation construction in the
existing immutable profile classes.  The adapters make provider description,
capabilities, and the future migration seam explicit without introducing a
second profile/configuration model or changing one launch byte.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from agent_commons.errors import ConfigurationError

from .capabilities import (
    PROVIDER_ADAPTER_VERSION,
    BudgetUnit,
    CancellationMode,
    CapabilitySet,
    LaunchPlan,
    LaunchPurpose,
    ProviderCapability,
    ProviderDescriptor,
    ProviderInitializationProbeSpec,
    ProviderInitializationState,
    ProviderInitializationStatus,
    ProviderOutcome,
    ProviderRefusalCode,
    ResumeMode,
    SandboxBoundary,
    TypedRefusal,
    UsageReporting,
)
from .diagnostics import classify_process_result
from .model import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    Provider,
    RunnerInvocation,
    RunnerProfile,
    profile_tool_summary,
)
from .skill_projection import (
    BUILTIN_SKILL_IDS,
    EphemeralSkillBundle,
    compile_skill_bundle,
    project_builtin_skills,
)
from .subprocess_runner import ProcessResult, RunOutcome

CODEX_HOST_SANDBOX_MARKER = b"Error: failed to initialize sqlite state runtime under "
CODEX_INITIALIZATION_WARNING = (
    b"WARNING: proceeding, even though we could not create PATH aliases: "
    b"Operation not permitted (os error 1)"
)


class ProviderAdapter(Protocol):
    """Provider-specific compatibility seam for one allowlisted profile."""

    provider: Provider
    adapter_version: str

    def describe(self, profile: RunnerProfile) -> ProviderDescriptor: ...

    def capabilities(self, profile: RunnerProfile) -> CapabilitySet: ...

    def validate(
        self, plan: LaunchPlan, capabilities: CapabilitySet
    ) -> LaunchPlan | TypedRefusal: ...

    def project_skills(self, skill_refs: Sequence[str]) -> EphemeralSkillBundle | TypedRefusal: ...

    def compile_instruction(
        self, plan: LaunchPlan, skill_bundle: EphemeralSkillBundle
    ) -> str | TypedRefusal: ...

    def build_invocation(
        self,
        profile: RunnerProfile,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
    ) -> RunnerInvocation: ...

    def initialization_probe_spec(
        self, profile: RunnerProfile
    ) -> ProviderInitializationProbeSpec | ProviderInitializationStatus: ...

    def classify_initialization(
        self,
        profile: RunnerProfile,
        result: ProcessResult,
    ) -> ProviderInitializationStatus: ...

    def decode_result(self, bounded_process_output: ProcessResult) -> ProviderOutcome: ...


def _budget_units(profile: RunnerProfile) -> tuple[BudgetUnit, ...]:
    if profile.supports_budget:
        return (BudgetUnit.MICRO_USD, BudgetUnit.PROVIDER_UNITS)
    return (BudgetUnit.PROVIDER_UNITS,)


def _sandbox_boundary(profile: RunnerProfile) -> SandboxBoundary:
    if profile.provider is Provider.CODEX:
        return SandboxBoundary.OS_ENFORCED
    if bool(getattr(profile, "trusted_workspace", False)):
        return SandboxBoundary.TRUSTED_WORKSPACE
    return SandboxBoundary.NONE


def _permission_mode(profile: RunnerProfile) -> str:
    if isinstance(profile, CodexRunnerProfile):
        return f"{profile.sandbox.value}:{profile.approval_policy.value}"
    if isinstance(profile, ClaudeRunnerProfile):
        return profile.permission_mode.value
    raise ConfigurationError("provider adapter received an unsupported profile type")


def _mcp_tools(profile_id: BuiltinProfileId) -> tuple[str, ...]:
    summary = profile_tool_summary()[profile_id.value]
    names = {
        *(str(name) for name in summary["fixed"]),
        *(str(name) for name in summary["narrowable"]),
        *(str(name) for name in summary["grant_tools"].values()),
    }
    return tuple(sorted(names))


def _descriptor(profile: RunnerProfile) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider=profile.provider,
        adapter_version=PROVIDER_ADAPTER_VERSION,
        profile_id=profile.profile_id,
        model=getattr(profile, "model", None),
        sandbox_boundary=_sandbox_boundary(profile),
        permission_mode=_permission_mode(profile),
        budget_units=_budget_units(profile),
    )


def _capabilities(profile: RunnerProfile) -> CapabilitySet:
    return CapabilitySet(
        provider=profile.provider,
        profile_id=profile.profile_id,
        adapter_version=PROVIDER_ADAPTER_VERSION,
        mcp=True,
        mcp_tool_names=_mcp_tools(profile.profile_id),
        skills=BUILTIN_SKILL_IDS,
        input_modes=("stdin",),
        resume_mode=ResumeMode.NONE,
        cancellation_mode=CancellationMode.BROKER,
        # Existing diagnostics are bounded, but provider-reported usage totals
        # are not yet decoded into a public contract.
        usage_reporting=UsageReporting.NONE,
        sandbox_boundary=_sandbox_boundary(profile),
        budget_units=_budget_units(profile),
    )


def _skill_projection(
    provider: Provider, skill_refs: Sequence[str]
) -> EphemeralSkillBundle | TypedRefusal:
    try:
        return project_builtin_skills(provider, skill_refs)
    except ConfigurationError:
        return TypedRefusal.create(
            ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
            provider=provider,
            capability=ProviderCapability.SKILL_PROJECTION,
        )


def _capability_refusal(
    plan: LaunchPlan,
    *,
    code: ProviderRefusalCode = ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED,
    capability: ProviderCapability | None = None,
) -> TypedRefusal:
    return TypedRefusal.create(
        code,
        provider=plan.provider,
        profile_id=plan.profile_id,
        capability=capability,
    )


def _validate_plan(
    provider: Provider,
    plan: LaunchPlan,
    capabilities: CapabilitySet,
) -> LaunchPlan | TypedRefusal:
    if (
        plan.provider is not provider
        or capabilities.provider is not provider
        or capabilities.profile_id is not plan.profile_id
    ):
        return _capability_refusal(plan)
    if plan.profile_id.independent_reviewer:
        if plan.purpose not in {LaunchPurpose.INDEPENDENT_REVIEW, LaunchPurpose.VERIFICATION}:
            return _capability_refusal(plan)
    elif plan.purpose is not LaunchPurpose.IMPLEMENTATION:
        return _capability_refusal(plan)
    if any(skill_ref not in capabilities.skills for skill_ref in plan.skill_refs):
        return _capability_refusal(
            plan,
            code=ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
            capability=ProviderCapability.SKILL_PROJECTION,
        )
    if plan.budget_unit not in capabilities.budget_units:
        return _capability_refusal(
            plan,
            code=(
                ProviderRefusalCode.BUDGET_NOT_ENFORCEABLE
                if plan.budget_unit is BudgetUnit.MICRO_USD
                else ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED
            ),
            capability=ProviderCapability.MONETARY_BUDGET,
        )
    for capability in plan.required_capabilities:
        supported = {
            ProviderCapability.MCP: capabilities.mcp,
            ProviderCapability.SKILL_PROJECTION: bool(capabilities.skills),
            ProviderCapability.MONETARY_BUDGET: (BudgetUnit.MICRO_USD in capabilities.budget_units),
            ProviderCapability.RESUME: capabilities.resume_mode is not ResumeMode.NONE,
            ProviderCapability.USAGE_REPORTING: (
                capabilities.usage_reporting is not UsageReporting.NONE
            ),
        }[capability]
        if not supported:
            return _capability_refusal(
                plan,
                code=(
                    ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE
                    if capability is ProviderCapability.SKILL_PROJECTION
                    else (
                        ProviderRefusalCode.BUDGET_NOT_ENFORCEABLE
                        if capability is ProviderCapability.MONETARY_BUDGET
                        else ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED
                    )
                ),
                capability=capability,
            )
    return plan


def _compile_instruction(
    provider: Provider,
    plan: LaunchPlan,
    skill_bundle: EphemeralSkillBundle,
) -> str | TypedRefusal:
    if plan.provider is not provider:
        return _capability_refusal(plan)
    if skill_bundle.provider is not provider or skill_bundle.skill_ids != plan.skill_refs:
        return _capability_refusal(
            plan,
            code=ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
            capability=ProviderCapability.SKILL_PROJECTION,
        )
    try:
        return compile_skill_bundle(plan.instruction, skill_bundle)
    except ConfigurationError:
        return _capability_refusal(
            plan,
            code=ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
            capability=ProviderCapability.SKILL_PROJECTION,
        )


def _decode_result(provider: Provider, bounded_process_output: ProcessResult) -> ProviderOutcome:
    diagnostic = classify_process_result(bounded_process_output)
    tags = [
        f"process.{bounded_process_output.outcome.value}",
        f"reason.{bounded_process_output.reason.value}",
    ]
    if bounded_process_output.output_truncated:
        tags.append("output.truncated")
    return ProviderOutcome(
        provider=provider,
        diagnostic_code=diagnostic.code,
        event_shape_tags=tuple(tags),
    )


def _build_invocation(
    profile: RunnerProfile,
    instruction: str,
    *,
    workspace_root: Path,
    state_root: Path | None,
    delegation_id: str | None,
    child_session_id: str | None,
    max_budget_microusd: int | None,
    worker_purpose: str | None,
    role_tools: Sequence[str] | None,
    role_grants: Mapping[str, str] | None,
) -> RunnerInvocation:
    return profile.build_invocation(
        instruction,
        workspace_root=workspace_root,
        state_root=state_root,
        delegation_id=delegation_id,
        child_session_id=child_session_id,
        max_budget_microusd=max_budget_microusd,
        worker_purpose=worker_purpose,
        role_tools=role_tools,
        role_grants=role_grants,
    )


@dataclass(frozen=True, slots=True)
class CodexProviderAdapter:
    provider: Provider = field(default=Provider.CODEX, init=False)
    adapter_version: str = field(default=PROVIDER_ADAPTER_VERSION, init=False)

    @staticmethod
    def _require_profile(profile: RunnerProfile) -> CodexRunnerProfile:
        if not isinstance(profile, CodexRunnerProfile):
            raise ConfigurationError("Codex adapter requires a Codex runner profile")
        return profile

    def describe(self, profile: RunnerProfile) -> ProviderDescriptor:
        return _descriptor(self._require_profile(profile))

    def capabilities(self, profile: RunnerProfile) -> CapabilitySet:
        return _capabilities(self._require_profile(profile))

    def validate(self, plan: LaunchPlan, capabilities: CapabilitySet) -> LaunchPlan | TypedRefusal:
        return _validate_plan(self.provider, plan, capabilities)

    def project_skills(self, skill_refs: Sequence[str]) -> EphemeralSkillBundle | TypedRefusal:
        return _skill_projection(self.provider, skill_refs)

    def compile_instruction(
        self, plan: LaunchPlan, skill_bundle: EphemeralSkillBundle
    ) -> str | TypedRefusal:
        return _compile_instruction(self.provider, plan, skill_bundle)

    def build_invocation(
        self,
        profile: RunnerProfile,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
    ) -> RunnerInvocation:
        return _build_invocation(
            self._require_profile(profile),
            instruction,
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            child_session_id=child_session_id,
            max_budget_microusd=max_budget_microusd,
            worker_purpose=worker_purpose,
            role_tools=role_tools,
            role_grants=role_grants,
        )

    def decode_result(self, bounded_process_output: ProcessResult) -> ProviderOutcome:
        return _decode_result(self.provider, bounded_process_output)

    def initialization_probe_spec(self, profile: RunnerProfile) -> ProviderInitializationProbeSpec:
        profile = self._require_profile(profile)
        return ProviderInitializationProbeSpec(
            provider=self.provider,
            profile_id=profile.profile_id,
            arguments=("app-server", "--stdio"),
        )

    def classify_initialization(
        self,
        profile: RunnerProfile,
        result: ProcessResult,
    ) -> ProviderInitializationStatus:
        self._require_profile(profile)
        if result.outcome is RunOutcome.TIMED_OUT:
            state = ProviderInitializationState.TIMED_OUT
        elif result.outcome is RunOutcome.FAILED and any(
            line.startswith(CODEX_HOST_SANDBOX_MARKER) for line in result.stderr.splitlines()
        ):
            state = ProviderInitializationState.HOST_SANDBOX_REFUSED
        elif result.outcome is RunOutcome.SUCCEEDED and result.exit_code == 0:
            state = ProviderInitializationState.READY
        else:
            state = ProviderInitializationState.UNAVAILABLE
        return ProviderInitializationStatus(provider=self.provider, state=state)


@dataclass(frozen=True, slots=True)
class ClaudeProviderAdapter:
    provider: Provider = field(default=Provider.CLAUDE, init=False)
    adapter_version: str = field(default=PROVIDER_ADAPTER_VERSION, init=False)

    @staticmethod
    def _require_profile(profile: RunnerProfile) -> ClaudeRunnerProfile:
        if not isinstance(profile, ClaudeRunnerProfile):
            raise ConfigurationError("Claude adapter requires a Claude runner profile")
        return profile

    def describe(self, profile: RunnerProfile) -> ProviderDescriptor:
        return _descriptor(self._require_profile(profile))

    def capabilities(self, profile: RunnerProfile) -> CapabilitySet:
        return _capabilities(self._require_profile(profile))

    def validate(self, plan: LaunchPlan, capabilities: CapabilitySet) -> LaunchPlan | TypedRefusal:
        return _validate_plan(self.provider, plan, capabilities)

    def project_skills(self, skill_refs: Sequence[str]) -> EphemeralSkillBundle | TypedRefusal:
        return _skill_projection(self.provider, skill_refs)

    def compile_instruction(
        self, plan: LaunchPlan, skill_bundle: EphemeralSkillBundle
    ) -> str | TypedRefusal:
        return _compile_instruction(self.provider, plan, skill_bundle)

    def build_invocation(
        self,
        profile: RunnerProfile,
        instruction: str,
        *,
        workspace_root: Path,
        state_root: Path | None = None,
        delegation_id: str | None = None,
        child_session_id: str | None = None,
        max_budget_microusd: int | None = None,
        worker_purpose: str | None = None,
        role_tools: Sequence[str] | None = None,
        role_grants: Mapping[str, str] | None = None,
    ) -> RunnerInvocation:
        return _build_invocation(
            self._require_profile(profile),
            instruction,
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            child_session_id=child_session_id,
            max_budget_microusd=max_budget_microusd,
            worker_purpose=worker_purpose,
            role_tools=role_tools,
            role_grants=role_grants,
        )

    def decode_result(self, bounded_process_output: ProcessResult) -> ProviderOutcome:
        return _decode_result(self.provider, bounded_process_output)

    def initialization_probe_spec(self, profile: RunnerProfile) -> ProviderInitializationProbeSpec:
        profile = self._require_profile(profile)
        # `mcp list` exercises Claude Code's real configuration/runtime
        # initialization without starting model work or accepting caller argv.
        return ProviderInitializationProbeSpec(
            provider=self.provider,
            profile_id=profile.profile_id,
            arguments=("mcp", "list"),
        )

    def classify_initialization(
        self,
        profile: RunnerProfile,
        result: ProcessResult,
    ) -> ProviderInitializationStatus:
        self._require_profile(profile)
        if result.outcome is RunOutcome.TIMED_OUT:
            state = ProviderInitializationState.TIMED_OUT
        elif result.outcome is RunOutcome.SUCCEEDED and result.exit_code == 0:
            state = ProviderInitializationState.READY
        else:
            state = ProviderInitializationState.UNAVAILABLE
        return ProviderInitializationStatus(provider=self.provider, state=state)


_ALLOWLISTED_ADAPTER_TYPES: dict[Provider, type[object]] = {
    Provider.CODEX: CodexProviderAdapter,
    Provider.CLAUDE: ClaudeProviderAdapter,
}


@dataclass(frozen=True, slots=True)
class AdapterRegistry:
    """Fixed adapter allowlist; workspace configuration cannot add implementations."""

    _adapters: Mapping[Provider, ProviderAdapter]

    def __post_init__(self) -> None:
        normalized: dict[Provider, ProviderAdapter] = {}
        for raw_provider, adapter in self._adapters.items():
            try:
                provider = Provider(raw_provider)
            except ValueError as exc:
                raise ConfigurationError(
                    "adapter registry contains an unsupported provider"
                ) from exc
            allowed_type = _ALLOWLISTED_ADAPTER_TYPES[provider]
            if type(adapter) is not allowed_type or adapter.provider is not provider:
                raise ConfigurationError(
                    f"adapter registry contains a non-allowlisted {provider.value} implementation"
                )
            normalized[provider] = adapter
        if not normalized:
            raise ConfigurationError("at least one provider adapter must be configured")
        object.__setattr__(self, "_adapters", MappingProxyType(normalized))

    def get(self, provider: str | Provider) -> ProviderAdapter | TypedRefusal:
        try:
            normalized = Provider(provider)
        except ValueError:
            return TypedRefusal.create(ProviderRefusalCode.PROVIDER_UNAVAILABLE)
        adapter = self._adapters.get(normalized)
        if adapter is None:
            return TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_UNAVAILABLE,
                provider=normalized,
            )
        return adapter

    def for_profile(self, profile: RunnerProfile) -> ProviderAdapter | TypedRefusal:
        return self.get(profile.provider)

    @property
    def providers(self) -> tuple[Provider, ...]:
        return tuple(sorted(self._adapters, key=lambda provider: provider.value))


def default_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry(
        {
            Provider.CODEX: CodexProviderAdapter(),
            Provider.CLAUDE: ClaudeProviderAdapter(),
        }
    )
