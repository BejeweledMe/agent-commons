"""One-build provider launch planning and fixed initialization probes.

Everything in this module is operational and ephemeral.  The validated plan
contains the exact immutable invocation handed to the broker, while its public
metadata contains only a digest and maintainer-owned enum values.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_commons.errors import ConfigurationError

from .adapters import AdapterRegistry, ProviderAdapter, default_adapter_registry
from .capabilities import (
    CapabilitySet,
    LaunchPlan,
    ProviderCapability,
    ProviderDescriptor,
    ProviderInitializationProbeSpec,
    ProviderInitializationState,
    ProviderInitializationStatus,
    ProviderRefusalCode,
    TypedRefusal,
)
from .context_binding import ContextBinding, ContextBindingRefusal
from .diagnostics import configuration_failure_diagnostic
from .model import (
    ExecutableRole,
    Provider,
    RunnerInvocation,
    RunnerProfile,
    resolve_trusted_executable,
    validate_profile_launch_boundary,
    validate_worker_scope,
)
from .skill_projection import EphemeralSkillBundle, compile_skill_bundle, verify_skill_bundle
from .subprocess_runner import SubprocessRunner

PROVIDER_INITIALIZATION_TIMEOUT_SECONDS = 5
PROVIDER_INITIALIZATION_MAX_OUTPUT_BYTES = 16 * 1024
_INITIALIZATION_SESSION_ID = "provider-initialization-probe"


def invocation_fingerprint(invocation: RunnerInvocation) -> str:
    """Hash every launch byte without retaining prompt material in metadata."""

    digest = hashlib.sha256()
    for value in (
        invocation.provider.value.encode(),
        invocation.profile_id.value.encode(),
        *(argument.encode("utf-8") for argument in invocation.argv),
        invocation.stdin,
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class StaticLaunchValidation:
    """Child-independent validation result used before any durable mutation."""

    plan: LaunchPlan
    profile: RunnerProfile
    adapter: ProviderAdapter
    descriptor: ProviderDescriptor
    capabilities: CapabilitySet
    instruction: str
    skill_bundle: EphemeralSkillBundle


def _validate_invocation_composition(
    validation: StaticLaunchValidation,
    invocation: RunnerInvocation,
    context: ContextBinding,
) -> None:
    bundle = validation.skill_bundle
    if bundle.skill_ids != validation.plan.skill_refs or not verify_skill_bundle(bundle):
        raise ConfigurationError("launch skill projection is missing or stale")
    instruction = compile_skill_bundle(validation.plan.instruction, bundle)
    if instruction != validation.instruction:
        raise ConfigurationError("launch instruction does not match its exact skill projection")
    expected_stdin = launch_instruction_with_context(instruction, context).encode("utf-8")
    if invocation.stdin != expected_stdin:
        raise ConfigurationError("launch stdin does not match its exact skill/context composition")


@dataclass(frozen=True, slots=True)
class ValidatedLaunchPlan:
    """The exact immutable invocation that validation authorized for launch."""

    plan: LaunchPlan
    descriptor: ProviderDescriptor
    capabilities: CapabilitySet
    invocation: RunnerInvocation
    invocation_fingerprint: str
    context: ContextBinding = field(default_factory=ContextBinding.fresh)
    skill_bundle: EphemeralSkillBundle | None = None

    def __post_init__(self) -> None:
        if self.invocation.provider is not self.plan.provider:
            raise ConfigurationError("validated launch provider does not match its invocation")
        if self.invocation.profile_id is not self.plan.profile_id:
            raise ConfigurationError("validated launch profile does not match its invocation")
        if type(self.context) is not ContextBinding:
            raise ConfigurationError("validated launch context must be an owned context binding")
        skill_bundle = self.skill_bundle or EphemeralSkillBundle.empty(self.plan.provider)
        object.__setattr__(self, "skill_bundle", skill_bundle)
        if skill_bundle.provider is not self.plan.provider:
            raise ConfigurationError("validated launch skill provider does not match its plan")
        if skill_bundle.skill_ids != self.plan.skill_refs or not verify_skill_bundle(skill_bundle):
            raise ConfigurationError("validated launch skill projection is missing or stale")
        expected_instruction = compile_skill_bundle(self.plan.instruction, skill_bundle)
        expected_stdin = launch_instruction_with_context(expected_instruction, self.context).encode(
            "utf-8"
        )
        if self.invocation.stdin != expected_stdin:
            raise ConfigurationError(
                "validated launch stdin does not match its exact skill/context composition"
            )
        if self.invocation_fingerprint != invocation_fingerprint(self.invocation):
            raise ConfigurationError("validated launch invocation fingerprint is invalid")

    @classmethod
    def create(
        cls,
        *,
        validation: StaticLaunchValidation,
        invocation: RunnerInvocation,
        context: ContextBinding | None = None,
    ) -> ValidatedLaunchPlan:
        resolved_context = context if context is not None else ContextBinding.fresh()
        _validate_invocation_composition(validation, invocation, resolved_context)
        return cls(
            plan=validation.plan,
            descriptor=validation.descriptor,
            capabilities=validation.capabilities,
            invocation=invocation,
            invocation_fingerprint=invocation_fingerprint(invocation),
            context=resolved_context,
            skill_bundle=validation.skill_bundle,
        )

    def as_dict(self) -> dict[str, object]:
        """Return secret-free metadata: never argv, stdin, or provider output."""

        skill_bundle = self.skill_bundle
        if skill_bundle is None:  # pragma: no cover - normalized in __post_init__
            raise ConfigurationError("validated launch skill bundle is missing")
        return {
            "profile_id": self.plan.profile_id.value,
            "provider": self.plan.provider.value,
            "purpose": self.plan.purpose.value,
            "adapter_version": self.descriptor.adapter_version,
            "invocation_fingerprint": self.invocation_fingerprint,
            "context_mode": self.context.mode.value,
            "compiled_context_fingerprint": self.context.compiled_context_fingerprint,
            "skill_source_digest": skill_bundle.source_digest,
            "skill_projection_digest": skill_bundle.projection_digest,
            "skill_installer_digest": skill_bundle.installer_digest,
        }


@dataclass(frozen=True, slots=True)
class LaunchPlanner:
    """Validate once and build exactly one provider invocation."""

    adapters: AdapterRegistry

    @classmethod
    def default(cls) -> LaunchPlanner:
        return cls(default_adapter_registry())

    def validate_static(
        self,
        plan: LaunchPlan,
        profile: RunnerProfile,
        *,
        workspace_root: Path,
        resolve_executables: bool = True,
        role_tools: tuple[str, ...] = (),
        role_grants: Mapping[str, str] | None = None,
    ) -> StaticLaunchValidation | TypedRefusal:
        adapter = self.adapters.for_profile(profile)
        if isinstance(adapter, TypedRefusal):
            return adapter
        try:
            # The host-isolation boundary is pure profile policy.  Refuse it
            # before consulting any configured path so an untrusted profile
            # cannot be masked by, or disclose the ordering of, host lookups.
            validate_profile_launch_boundary(profile)
            if resolve_executables:
                # Preserve the established profile build order.  Operators
                # and tests depend on the first typed failure remaining stable
                # when more than one configured executable is unavailable.
                fields = (
                    (
                        ("mcp_executable", ExecutableRole.MCP),
                        ("git_executable", ExecutableRole.GIT),
                        ("executable", ExecutableRole.PROVIDER),
                    )
                    if plan.provider is Provider.CODEX
                    else (
                        ("executable", ExecutableRole.PROVIDER),
                        ("mcp_executable", ExecutableRole.MCP),
                        ("git_executable", ExecutableRole.GIT),
                    )
                )
                for field, role in fields:
                    resolve_trusted_executable(
                        str(getattr(profile, field, "")),
                        workspace_root=workspace_root,
                        role=role,
                    )
            descriptor = adapter.describe(profile)
            capabilities = adapter.capabilities(profile)
        except ConfigurationError as exc:
            diagnostic = configuration_failure_diagnostic(exc)
            return TypedRefusal.create(
                ProviderRefusalCode(diagnostic.code.value),
                provider=plan.provider,
                profile_id=plan.profile_id,
            )
        try:
            validate_worker_scope(
                profile.profile_id,
                plan.purpose.value,
                role_tools,
                role_grants,
            )
        except ConfigurationError:
            return TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED,
                provider=plan.provider,
                profile_id=plan.profile_id,
            )
        validated = adapter.validate(plan, capabilities)
        if isinstance(validated, TypedRefusal):
            return validated
        skill_bundle = adapter.project_skills(validated.skill_refs)
        if isinstance(skill_bundle, TypedRefusal):
            return skill_bundle
        instruction = adapter.compile_instruction(validated, skill_bundle)
        if isinstance(instruction, TypedRefusal):
            return instruction
        return StaticLaunchValidation(
            plan=validated,
            profile=profile,
            adapter=adapter,
            descriptor=descriptor,
            capabilities=capabilities,
            instruction=instruction,
            skill_bundle=skill_bundle,
        )

    @staticmethod
    def validate_skill_projection(
        validation: StaticLaunchValidation,
    ) -> TypedRefusal | None:
        """Purely prove the exact projection still matches static validation."""

        bundle = validation.skill_bundle
        if bundle.skill_ids != validation.plan.skill_refs or not verify_skill_bundle(bundle):
            return TypedRefusal.create(
                ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
                provider=validation.plan.provider,
                profile_id=validation.plan.profile_id,
                capability=ProviderCapability.SKILL_PROJECTION,
            )
        compiled = validation.adapter.compile_instruction(validation.plan, bundle)
        if isinstance(compiled, TypedRefusal) or compiled != validation.instruction:
            return TypedRefusal.create(
                ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE,
                provider=validation.plan.provider,
                profile_id=validation.plan.profile_id,
                capability=ProviderCapability.SKILL_PROJECTION,
            )
        return None

    @staticmethod
    def build(
        validation: StaticLaunchValidation,
        *,
        workspace_root: Path,
        state_root: Path,
        delegation_id: str,
        child_session_id: str,
        max_budget_microusd: int | None,
        worker_purpose: str,
        role_tools: tuple[str, ...],
        role_grants: Mapping[str, str],
        context: ContextBinding | None = None,
    ) -> ValidatedLaunchPlan:
        context = context if context is not None else ContextBinding.fresh()
        refusal = LaunchPlanner.validate_skill_projection(validation)
        if refusal is not None:
            raise launch_refusal_error(refusal)
        # This is the sole production call to ProviderAdapter.build_invocation.
        invocation = validation.adapter.build_invocation(
            validation.profile,
            launch_instruction_with_context(validation.instruction, context),
            workspace_root=workspace_root,
            state_root=state_root,
            delegation_id=delegation_id,
            child_session_id=child_session_id,
            max_budget_microusd=max_budget_microusd,
            worker_purpose=worker_purpose,
            role_tools=role_tools,
            role_grants=role_grants,
        )
        return ValidatedLaunchPlan.create(
            validation=validation, invocation=invocation, context=context
        )


class InitializationProbe(Protocol):
    def probe(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: Path,
    ) -> ProviderInitializationStatus: ...


class ProviderInitializationProbe:
    """Run an adapter-owned no-model operation with generic bounded mechanics."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        adapters: AdapterRegistry | None = None,
        timeout_seconds: int = PROVIDER_INITIALIZATION_TIMEOUT_SECONDS,
        max_output_bytes: int = PROVIDER_INITIALIZATION_MAX_OUTPUT_BYTES,
    ) -> None:
        if timeout_seconds < 1 or not 1 <= max_output_bytes <= 64 * 1024:
            raise ConfigurationError("provider initialization probe limits are invalid")
        self.runner = runner or SubprocessRunner()
        self.adapters = adapters or default_adapter_registry()
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def probe(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: Path,
    ) -> ProviderInitializationStatus:
        adapter = self.adapters.for_profile(profile)
        if isinstance(adapter, TypedRefusal):
            return ProviderInitializationStatus(
                provider=profile.provider,
                state=ProviderInitializationState.UNAVAILABLE,
            )
        operation = adapter.initialization_probe_spec(profile)
        if isinstance(operation, ProviderInitializationStatus):
            return operation
        if not isinstance(operation, ProviderInitializationProbeSpec):  # pragma: no cover
            raise TypeError("provider adapter returned an invalid initialization probe spec")
        try:
            executable = resolve_trusted_executable(
                str(getattr(profile, "executable", "")),
                workspace_root=workspace_root,
                role=ExecutableRole.PROVIDER,
            )
        except ConfigurationError:
            return ProviderInitializationStatus(
                provider=profile.provider,
                state=ProviderInitializationState.UNAVAILABLE,
            )
        invocation = RunnerInvocation(
            provider=operation.provider,
            profile_id=operation.profile_id,
            argv=(executable, *operation.arguments),
            stdin=b"",
        )
        try:
            result = self.runner.run(
                invocation,
                cwd=workspace_root,
                child_session_id=_INITIALIZATION_SESSION_ID,
                timeout_seconds=self.timeout_seconds,
                max_output_bytes=self.max_output_bytes,
            )
        except OSError:
            return ProviderInitializationStatus(
                provider=profile.provider,
                state=ProviderInitializationState.UNAVAILABLE,
            )
        return adapter.classify_initialization(profile, result)


def launch_instruction_with_context(instruction: str, context: ContextBinding) -> str:
    """Append the immutable compiled baseline after the role/task instruction.

    Fresh context returns the instruction unchanged, so existing launches keep
    byte-identical invocations.  The baseline arrives as clearly labelled data
    after the instruction; it never replaces or reorders instruction bytes.
    """

    compiled = context.compiled_context_bytes
    if compiled is None:
        return instruction
    return instruction + "\n\n" + compiled.decode("utf-8")


def context_binding_refusal_error(refusal: ContextBindingRefusal) -> ConfigurationError:
    """Translate a pure context refusal into the existing service error surface."""

    error = ConfigurationError(
        "Context binding was refused before any delegation attempt or child session existed. "
        + refusal.message
    )
    error.code = refusal.code.value  # type: ignore[attr-defined]
    error.safe_next_actions = (refusal.remediation,)  # type: ignore[attr-defined]
    return error


def launch_refusal_error(refusal: TypedRefusal) -> ConfigurationError:
    """Translate a pure typed refusal into the existing service error surface."""

    error = ConfigurationError(
        "Provider launch was refused before any delegation attempt or child session existed. "
        + refusal.message
    )
    error.code = refusal.code.value  # type: ignore[attr-defined]
    error.safe_next_actions = refusal.safe_next_actions  # type: ignore[attr-defined]
    return error
