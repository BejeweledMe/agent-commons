from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

import agent_commons.runtime.capabilities as capability_contracts
from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.integrations import initialize_workspace
from agent_commons.runtime import (
    BUILTIN_SKILL_IDS,
    CODEX_HOST_SANDBOX_MARKER,
    CODEX_INITIALIZATION_WARNING,
    PROVIDER_ADAPTER_VERSION,
    AdapterRegistry,
    BudgetUnit,
    BuiltinProfileId,
    CapabilitySet,
    ClaudeProviderAdapter,
    CodexProviderAdapter,
    DiagnosticCode,
    EphemeralSkillBundle,
    GrokProviderAdapter,
    LaunchPlan,
    LaunchPurpose,
    ProcessResult,
    Provider,
    ProviderAdapter,
    ProviderCapability,
    ProviderDescriptor,
    ProviderInitializationProbeSpec,
    ProviderInitializationState,
    ProviderOutcome,
    ProviderRefusalCode,
    ResumeMode,
    RunnerProfile,
    RunOutcome,
    RunReason,
    SandboxBoundary,
    TypedRefusal,
    UsageReporting,
    default_adapter_registry,
    default_profile_registry,
)
from agent_commons.runtime.capabilities import (
    PROVIDER_CAPABILITY_COLLECTION_LIMIT,
    PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT,
)


class _MisreportingSequence(Sequence[str]):
    """A finite hostile sequence whose reported length must never be trusted."""

    def __init__(
        self,
        values: tuple[str, ...],
        *,
        actual_count: int,
        reported_count: int,
    ) -> None:
        self._values = values
        self._actual_count = actual_count
        self._reported_count = reported_count
        self.access_count = 0
        self.len_count = 0

    def __getitem__(self, index: int) -> str:
        if index < 0 or index >= self._actual_count:
            raise IndexError(index)
        self.access_count += 1
        return self._values[index % len(self._values)]

    def __len__(self) -> int:
        self.len_count += 1
        return self._reported_count


def _profiles() -> tuple[RunnerProfile, ...]:
    registry = default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        grok_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    )
    return tuple(registry.get(profile_id) for profile_id in BuiltinProfileId)


def _adapter(profile: RunnerProfile) -> ProviderAdapter:
    adapter = default_adapter_registry().for_profile(profile)
    assert not isinstance(adapter, TypedRefusal)
    return adapter


def _initialization_result(
    *,
    outcome: RunOutcome = RunOutcome.SUCCEEDED,
    reason: RunReason = RunReason.COMPLETED,
    stderr: bytes = b"",
    stdout: bytes = b"",
) -> ProcessResult:
    return ProcessResult(
        outcome=outcome,
        reason=reason,
        exit_code=0 if outcome is RunOutcome.SUCCEEDED else 1,
        pid=4242,
        duration_seconds=0.01,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes_seen=len(stdout),
        stderr_bytes_seen=len(stderr),
        output_truncated=False,
    )


@pytest.mark.parametrize("profile", _profiles(), ids=lambda profile: profile.profile_id.value)
def test_each_adapter_owns_an_explicit_initialization_probe_contract(
    profile: RunnerProfile,
) -> None:
    adapter = _adapter(profile)
    operation = adapter.initialization_probe_spec(profile)
    assert isinstance(operation, ProviderInitializationProbeSpec)
    assert operation.provider is profile.provider
    assert operation.profile_id is profile.profile_id
    expected = {
        Provider.CODEX: ("app-server", "--stdio"),
        Provider.CLAUDE: ("mcp", "list"),
        Provider.GROK: ("inspect", "--json"),
    }
    assert operation.arguments == expected[profile.provider]


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (_initialization_result(), ProviderInitializationState.READY),
        (
            _initialization_result(
                outcome=RunOutcome.TIMED_OUT,
                reason=RunReason.TIMEOUT,
            ),
            ProviderInitializationState.TIMED_OUT,
        ),
        (
            _initialization_result(
                outcome=RunOutcome.FAILED,
                reason=RunReason.NONZERO_EXIT,
                stderr=CODEX_HOST_SANDBOX_MARKER + b"/redacted/.codex",
            ),
            ProviderInitializationState.HOST_SANDBOX_REFUSED,
        ),
        (
            _initialization_result(
                outcome=RunOutcome.FAILED,
                reason=RunReason.NONZERO_EXIT,
                stderr=CODEX_INITIALIZATION_WARNING,
            ),
            ProviderInitializationState.UNAVAILABLE,
        ),
        (
            _initialization_result(stderr=CODEX_INITIALIZATION_WARNING),
            ProviderInitializationState.READY,
        ),
    ),
)
def test_codex_adapter_classifies_its_exact_initialization_markers(
    result: ProcessResult,
    expected: ProviderInitializationState,
) -> None:
    profile = next(
        profile for profile in _profiles() if profile.profile_id is BuiltinProfileId.CODEX_BUILDER
    )
    status = _adapter(profile).classify_initialization(profile, result)
    assert status.state is expected


def test_adapter_initialization_refusal_contains_no_provider_output() -> None:
    profile = next(
        profile for profile in _profiles() if profile.profile_id is BuiltinProfileId.CODEX_BUILDER
    )
    status = _adapter(profile).classify_initialization(
        profile,
        _initialization_result(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            stderr=CODEX_HOST_SANDBOX_MARKER + b"/secret/home/.codex",
        ),
    )
    refusal = status.refusal(profile_id=profile.profile_id)
    assert refusal.code is ProviderRefusalCode.HOST_SANDBOX_REFUSED
    assert CODEX_HOST_SANDBOX_MARKER.decode() not in str(refusal.as_dict())
    assert "/secret/home" not in str(refusal.as_dict())


def test_claude_adapter_classifies_real_initialization_probe() -> None:
    profile = next(
        profile for profile in _profiles() if profile.profile_id is BuiltinProfileId.CLAUDE_BUILDER
    )
    adapter = _adapter(profile)
    failed = adapter.classify_initialization(
        profile,
        _initialization_result(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
        ),
    )
    timed_out = adapter.classify_initialization(
        profile,
        _initialization_result(
            outcome=RunOutcome.TIMED_OUT,
            reason=RunReason.TIMEOUT,
        ),
    )
    ready = adapter.classify_initialization(profile, _initialization_result())
    assert failed.state is ProviderInitializationState.UNAVAILABLE
    assert timed_out.state is ProviderInitializationState.TIMED_OUT
    assert ready.state is ProviderInitializationState.READY


def test_grok_adapter_requires_exact_isolated_discovery_surface() -> None:
    profile = next(
        profile for profile in _profiles() if profile.profile_id is BuiltinProfileId.GROK_BUILDER
    )
    adapter = _adapter(profile)
    ready = adapter.classify_initialization(
        profile,
        _initialization_result(
            stdout=json.dumps(
                {
                    "projectTrusted": True,
                    "mcpServers": [{"name": "agent-commons"}],
                    "hooks": [],
                    "plugins": [],
                    "lspServers": [],
                    "mcpConfigProblems": [],
                }
            ).encode()
        ),
    )
    ambient = adapter.classify_initialization(
        profile,
        _initialization_result(
            stdout=json.dumps(
                {
                    "projectTrusted": True,
                    "mcpServers": [{"name": "agent-commons"}, {"name": "ambient"}],
                    "hooks": [],
                    "plugins": [],
                    "lspServers": [],
                    "mcpConfigProblems": [],
                }
            ).encode()
        ),
    )
    disabled_compatibility = adapter.classify_initialization(
        profile,
        _initialization_result(
            stdout=json.dumps(
                {
                    "projectTrusted": True,
                    "mcpServers": [
                        {
                            "name": "ambient-claude-compat",
                            "disabled": True,
                            "compatibilityStatus": "disabled",
                        },
                        {"name": "agent-commons"},
                    ],
                    "hooks": [],
                    "plugins": [],
                    "lspServers": [],
                    "mcpConfigProblems": [],
                }
            ).encode()
        ),
    )
    assert ready.state is ProviderInitializationState.READY
    assert ambient.state is ProviderInitializationState.UNAVAILABLE
    assert disabled_compatibility.state is ProviderInitializationState.READY


@pytest.mark.parametrize(
    "unsafe_surface",
    (
        {"projectTrusted": False},
        {"lspServers": [{"name": "ambient-lsp"}]},
        {"mcpConfigProblems": [{"name": "agent-commons"}]},
    ),
)
def test_grok_adapter_rejects_untrusted_or_extra_execution_surfaces(
    unsafe_surface: dict[str, object],
) -> None:
    profile = next(
        profile for profile in _profiles() if profile.profile_id is BuiltinProfileId.GROK_BUILDER
    )
    value: dict[str, object] = {
        "projectTrusted": True,
        "mcpServers": [{"name": "agent-commons"}],
        "hooks": [],
        "plugins": [],
        "lspServers": [],
        "mcpConfigProblems": [],
    }
    value.update(unsafe_surface)

    status = _adapter(profile).classify_initialization(
        profile,
        _initialization_result(stdout=json.dumps(value).encode()),
    )

    assert status.state is ProviderInitializationState.UNAVAILABLE


@pytest.mark.parametrize("profile", _profiles(), ids=lambda profile: profile.profile_id.value)
def test_adapter_invocation_is_exactly_the_wrapped_profile_invocation(
    profile: RunnerProfile,
    tmp_path: Path,
) -> None:
    if profile.profile_id is BuiltinProfileId.GROK_INDEPENDENT_REVIEWER:
        initialize_workspace(tmp_path, integrations=("grok",))
    purpose = "independent_review" if profile.profile_id.independent_reviewer else "implementation"
    values = {
        "workspace_root": tmp_path,
        "state_root": tmp_path / "external-state",
        "delegation_id": "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        "child_session_id": "session.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        "max_budget_microusd": 250_000 if profile.supports_budget else None,
        "worker_purpose": purpose,
        "role_tools": None,
        "role_grants": {"staffing": "deny"},
    }

    direct = profile.build_invocation("Perform the exact bound work", **values)
    wrapped = _adapter(profile).build_invocation(
        profile,
        "Perform the exact bound work",
        **values,
    )

    assert wrapped == direct
    assert wrapped.argv == direct.argv
    assert wrapped.stdin == direct.stdin


def test_descriptors_and_capabilities_expose_only_bounded_neutral_fields() -> None:
    by_id = {profile.profile_id: profile for profile in _profiles()}

    for profile_id, profile in by_id.items():
        adapter = _adapter(profile)
        descriptor = adapter.describe(profile)
        capabilities = adapter.capabilities(profile)
        serialized = json.dumps(
            {"descriptor": descriptor.as_dict(), "capabilities": capabilities.as_dict()}
        )

        assert descriptor.provider is profile.provider
        assert descriptor.profile_id is profile_id
        expected_transport = "prompt_argument" if profile.provider is Provider.GROK else "stdin"
        assert descriptor.instruction_transport == expected_transport
        assert capabilities.provider is profile.provider
        assert capabilities.profile_id is profile_id
        assert capabilities.mcp is True
        assert capabilities.mcp_tool_names
        assert capabilities.skills == BUILTIN_SKILL_IDS
        assert capabilities.input_modes == (expected_transport,)
        assert capabilities.resume_mode is ResumeMode.NONE
        assert capabilities.usage_reporting is UsageReporting.NONE
        assert "/bin/echo" not in serialized
        assert not {
            "executable",
            "argv",
            "env",
            "mcp_config",
            "instruction",
            "stderr",
        } & (descriptor.as_dict().keys() | capabilities.as_dict().keys())

    for profile_id in (
        BuiltinProfileId.CODEX_BUILDER,
        BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        BuiltinProfileId.GROK_BUILDER,
        BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
    ):
        profile = by_id[profile_id]
        descriptor = _adapter(profile).describe(profile)
        capabilities = _adapter(profile).capabilities(profile)
        assert descriptor.sandbox_boundary is SandboxBoundary.OS_ENFORCED
        assert descriptor.budget_units == (BudgetUnit.PROVIDER_UNITS,)
        assert capabilities.budget_units == (BudgetUnit.PROVIDER_UNITS,)

    claude_builder = by_id[BuiltinProfileId.CLAUDE_BUILDER]
    claude_reviewer = by_id[BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER]
    assert (
        _adapter(claude_builder).describe(claude_builder).sandbox_boundary
        is SandboxBoundary.TRUSTED_WORKSPACE
    )
    assert (
        _adapter(claude_reviewer).describe(claude_reviewer).sandbox_boundary is SandboxBoundary.NONE
    )
    for profile in (claude_builder, claude_reviewer):
        assert _adapter(profile).capabilities(profile).budget_units == (
            BudgetUnit.MICRO_USD,
            BudgetUnit.PROVIDER_UNITS,
        )


@pytest.mark.parametrize(
    "adapter", (CodexProviderAdapter(), ClaudeProviderAdapter(), GrokProviderAdapter())
)
def test_skill_projection_is_allowlisted_and_refuses_unknown_without_leaking_refs(
    adapter: ProviderAdapter,
) -> None:
    empty = adapter.project_skills(())
    assert isinstance(empty, EphemeralSkillBundle)
    assert empty.is_empty

    projected = adapter.project_skills(("commons-start",))
    assert isinstance(projected, EphemeralSkillBundle)
    assert projected.skill_ids == ("commons-start",)
    assert projected.source_digest
    assert projected.projection_digest
    assert projected.installer_digest

    refusal = adapter.project_skills(("private-skill-name",))

    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE
    assert refusal.durable_effect == "none"
    assert refusal.safe_next_actions
    assert "private-skill-name" not in json.dumps(refusal.as_dict())


def test_registry_is_closed_allowlisted_and_returns_typed_unavailability() -> None:
    registry = default_adapter_registry()

    assert registry.providers == (Provider.CLAUDE, Provider.CODEX, Provider.GROK)
    assert isinstance(registry.get(Provider.CODEX), CodexProviderAdapter)
    assert isinstance(registry.get(Provider.CLAUDE), ClaudeProviderAdapter)
    assert isinstance(registry.get(Provider.GROK), GrokProviderAdapter)

    unknown = registry.get("other-provider")
    assert isinstance(unknown, TypedRefusal)
    assert unknown.code is ProviderRefusalCode.PROVIDER_UNAVAILABLE
    assert unknown.durable_effect == "none"

    codex_only = AdapterRegistry({Provider.CODEX: CodexProviderAdapter()})
    unavailable = codex_only.get(Provider.CLAUDE)
    assert isinstance(unavailable, TypedRefusal)
    assert unavailable.code is ProviderRefusalCode.PROVIDER_UNAVAILABLE
    assert unavailable.provider is Provider.CLAUDE

    class WorkspaceAdapter:
        provider = Provider.CODEX

    with pytest.raises(ConfigurationError, match="non-allowlisted codex implementation"):
        AdapterRegistry({Provider.CODEX: WorkspaceAdapter()})  # type: ignore[dict-item]


def test_adapter_rejects_a_profile_from_the_other_provider() -> None:
    profiles = {
        profile.provider: profile
        for profile in _profiles()
        if not profile.profile_id.independent_reviewer
    }

    with pytest.raises(ConfigurationError, match="Codex adapter requires"):
        CodexProviderAdapter().describe(profiles[Provider.CLAUDE])
    with pytest.raises(ConfigurationError, match="Claude adapter requires"):
        ClaudeProviderAdapter().capabilities(profiles[Provider.CODEX])
    with pytest.raises(ConfigurationError, match="Grok adapter requires"):
        GrokProviderAdapter().describe(profiles[Provider.CLAUDE])


def test_provider_records_and_adapters_are_immutable() -> None:
    profile = _profiles()[0]
    adapter = _adapter(profile)
    descriptor = adapter.describe(profile)
    capabilities = adapter.capabilities(profile)
    refusal = adapter.project_skills(("skill",))
    assert isinstance(refusal, TypedRefusal)

    with pytest.raises(FrozenInstanceError):
        descriptor.model = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        capabilities.skills = ("other",)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        refusal.capability = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        adapter.adapter_version = "other"  # type: ignore[misc]


def test_launch_plan_deeply_owns_inputs_and_has_only_a_safe_summary() -> None:
    skill_refs = ["private-skill"]
    required = [ProviderCapability.MCP]
    plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="secret ephemeral instruction",
        skill_refs=skill_refs,  # type: ignore[arg-type]
        required_capabilities=required,  # type: ignore[arg-type]
        budget_unit=BudgetUnit.MICRO_USD,
        budget_limit=500_000,
    )

    skill_refs.append("later-skill")
    required.append(ProviderCapability.RESUME)

    assert plan.skill_refs == ("private-skill",)
    assert plan.required_capabilities == (ProviderCapability.MCP,)
    assert "secret ephemeral instruction" not in json.dumps(plan.as_dict())
    assert "private-skill" not in json.dumps(plan.as_dict())
    assert not {"argv", "env", "environment", "executable"} & {item.name for item in fields(plan)}
    with pytest.raises(FrozenInstanceError):
        plan.skill_refs = ()  # type: ignore[misc]


def test_launch_plan_bounds_skill_refs_before_materialization() -> None:
    oversized = _MisreportingSequence(("commons-start",), actual_count=10_000, reported_count=1)
    with pytest.raises(ValidationError, match="collection-size limit"):
        LaunchPlan(
            profile_id=BuiltinProfileId.CLAUDE_BUILDER,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="Implement",
            skill_refs=oversized,  # type: ignore[arg-type]
        )
    assert oversized.access_count == 9

    class ExplosiveStringification:
        def __str__(self) -> str:
            raise AssertionError("skill refs must be type-checked, not stringified")

    with pytest.raises(ValidationError, match="invalid name"):
        LaunchPlan(
            profile_id=BuiltinProfileId.CLAUDE_BUILDER,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="Implement",
            skill_refs=(ExplosiveStringification(),),  # type: ignore[arg-type]
        )


def test_validate_returns_typed_refusals_for_budget_profile_skill_and_capability() -> None:
    profiles = {profile.profile_id: profile for profile in _profiles()}
    codex_profile = profiles[BuiltinProfileId.CODEX_BUILDER]
    claude_profile = profiles[BuiltinProfileId.CLAUDE_BUILDER]
    codex = CodexProviderAdapter()
    claude = ClaudeProviderAdapter()
    codex_capabilities = codex.capabilities(codex_profile)
    claude_capabilities = claude.capabilities(claude_profile)

    valid_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement",
        budget_limit=1,
    )
    assert claude.validate(valid_plan, claude_capabilities) is valid_plan

    monetary_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement",
        budget_unit=BudgetUnit.MICRO_USD,
        budget_limit=100_000,
    )
    monetary = codex.validate(monetary_plan, codex_capabilities)
    assert isinstance(monetary, TypedRefusal)
    assert monetary.code is ProviderRefusalCode.BUDGET_NOT_ENFORCEABLE
    assert monetary.capability is ProviderCapability.MONETARY_BUDGET

    profile_mismatch = codex.validate(monetary_plan, claude_capabilities)
    assert isinstance(profile_mismatch, TypedRefusal)
    assert profile_mismatch.code is ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED

    skill_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement",
        skill_refs=("commons-start",),
    )
    assert claude.validate(skill_plan, claude_capabilities) is skill_plan

    unknown_skill_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement",
        skill_refs=("unknown-skill",),
    )
    skill = claude.validate(unknown_skill_plan, claude_capabilities)
    assert isinstance(skill, TypedRefusal)
    assert skill.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE

    resume_plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Implement",
        required_capabilities=(ProviderCapability.RESUME,),
    )
    resume = claude.validate(resume_plan, claude_capabilities)
    assert isinstance(resume, TypedRefusal)
    assert resume.code is ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED
    assert resume.capability is ProviderCapability.RESUME


def test_compile_instruction_requires_the_exact_projected_skill_bundle() -> None:
    adapter = ClaudeProviderAdapter()
    plain = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Exact ephemeral instruction",
    )

    empty = adapter.project_skills(())
    assert isinstance(empty, EphemeralSkillBundle)
    assert adapter.compile_instruction(plain, empty) == "Exact ephemeral instruction"

    projected = adapter.project_skills(("commons-start",))
    assert isinstance(projected, EphemeralSkillBundle)
    wrong_plan = adapter.compile_instruction(plain, projected)
    assert isinstance(wrong_plan, TypedRefusal)
    assert wrong_plan.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE

    required = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Exact ephemeral instruction",
        skill_refs=("commons-start",),
    )
    missing_projection = adapter.compile_instruction(required, empty)
    assert isinstance(missing_projection, TypedRefusal)
    assert missing_projection.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE

    compiled = adapter.compile_instruction(required, projected)
    assert isinstance(compiled, str)
    assert compiled.startswith("Exact ephemeral instruction")
    assert 'provider="claude"' in compiled
    assert ".claude/skills/commons-start/SKILL.md" in compiled


def test_decode_result_returns_bounded_diagnostics_without_success_or_raw_output() -> None:
    raw_provider_output = b'{"type":"error","message":"please run /login secret-token"}\n'
    process = ProcessResult(
        outcome=RunOutcome.SUCCEEDED,
        reason=RunReason.COMPLETED,
        exit_code=0,
        pid=12345,
        duration_seconds=0.1,
        stdout=raw_provider_output,
        stderr=b"raw stderr secret",
        stdout_bytes_seen=len(raw_provider_output),
        stderr_bytes_seen=17,
        output_truncated=True,
    )

    outcome = ClaudeProviderAdapter().decode_result(process)
    serialized = json.dumps(outcome.as_dict())

    assert isinstance(outcome, ProviderOutcome)
    assert outcome.diagnostic_code is DiagnosticCode.PROVIDER_AUTH_FAILED
    assert outcome.event_shape_tags == (
        "process.succeeded",
        "reason.completed",
        "output.truncated",
    )
    assert outcome.terminal_tool_signal is None
    assert outcome.usage_totals is None
    assert "success" not in outcome.as_dict()
    assert "secret-token" not in serialized
    assert "raw stderr" not in serialized
    with pytest.raises(FrozenInstanceError):
        outcome.event_shape_tags = ()  # type: ignore[misc]


def test_grok_decode_maps_auth_markers_without_echoing_key_material() -> None:
    raw = b'Unauthorized: set XAI_API_KEY="xai-secret-do-not-log" and login\n'
    process = _initialization_result(
        outcome=RunOutcome.SUCCEEDED,
        stdout=raw,
    )

    outcome = GrokProviderAdapter().decode_result(process)
    rendered = json.dumps(outcome.as_dict())

    assert outcome.diagnostic_code is DiagnosticCode.PROVIDER_AUTH_FAILED
    assert "xai-secret" not in rendered
    assert "XAI_API_KEY" not in rendered


def _mutable_capability_inputs() -> dict[str, object]:
    return {
        "provider": "claude",
        "profile_id": "claude-builder",
        "adapter_version": PROVIDER_ADAPTER_VERSION,
        "mcp": True,
        "mcp_tool_names": ["commons_orient"],
        "skills": ["skill-one"],
        "input_modes": ["stdin"],
        "resume_mode": "none",
        "cancellation_mode": "broker",
        "usage_reporting": "none",
        "sandbox_boundary": "trusted_workspace",
        "budget_units": ["provider_units", "micro_usd"],
    }


def test_descriptor_and_capabilities_normalize_and_deeply_own_collections() -> None:
    descriptor_units = ["provider_units", "micro_usd"]
    descriptor = ProviderDescriptor(
        provider="claude",  # type: ignore[arg-type]
        adapter_version=PROVIDER_ADAPTER_VERSION,
        profile_id="claude-builder",  # type: ignore[arg-type]
        model=None,
        sandbox_boundary="trusted_workspace",  # type: ignore[arg-type]
        permission_mode="acceptEdits",
        budget_units=descriptor_units,  # type: ignore[arg-type]
    )
    inputs = _mutable_capability_inputs()
    capabilities = CapabilitySet(**inputs)  # type: ignore[arg-type]
    before = json.dumps(
        {"descriptor": descriptor.as_dict(), "capabilities": capabilities.as_dict()},
        sort_keys=True,
    )

    descriptor_units.append("secret-budget")
    for name in ("mcp_tool_names", "skills", "input_modes", "budget_units"):
        values = inputs[name]
        assert isinstance(values, list)
        values.append("secret-later")

    assert descriptor.provider is Provider.CLAUDE
    assert descriptor.profile_id is BuiltinProfileId.CLAUDE_BUILDER
    assert descriptor.budget_units == (BudgetUnit.MICRO_USD, BudgetUnit.PROVIDER_UNITS)
    assert capabilities.mcp_tool_names == ("commons_orient",)
    assert capabilities.skills == ("skill-one",)
    assert capabilities.input_modes == ("stdin",)
    assert capabilities.budget_units == (BudgetUnit.MICRO_USD, BudgetUnit.PROVIDER_UNITS)
    assert (
        json.dumps(
            {"descriptor": descriptor.as_dict(), "capabilities": capabilities.as_dict()},
            sort_keys=True,
        )
        == before
    )
    assert "secret-later" not in before


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("mcp_tool_names", ["duplicate", "duplicate"]),
        ("skills", ["duplicate", "duplicate"]),
        ("input_modes", ["stdin", "stdin"]),
        ("mcp_tool_names", ["valid", "secret\nraw"]),
        ("skills", ["secret\nraw"]),
        ("input_modes", ["secret\nraw"]),
    ),
)
def test_capabilities_reject_invalid_or_duplicate_names_without_echoing_them(
    field_name: str,
    invalid_value: list[str],
) -> None:
    inputs = _mutable_capability_inputs()
    inputs[field_name] = invalid_value

    with pytest.raises(ValidationError) as captured:
        CapabilitySet(**inputs)  # type: ignore[arg-type]

    assert "secret\nraw" not in str(captured.value)


def test_descriptor_and_capability_mismatches_fail_during_construction() -> None:
    with pytest.raises(ValidationError, match="profile/provider mismatch"):
        ProviderDescriptor(
            provider=Provider.CODEX,
            adapter_version=PROVIDER_ADAPTER_VERSION,
            profile_id=BuiltinProfileId.CLAUDE_BUILDER,
            model=None,
            sandbox_boundary=SandboxBoundary.OS_ENFORCED,
            permission_mode="workspace-write:never",
            budget_units=(BudgetUnit.PROVIDER_UNITS,),
        )

    inputs = _mutable_capability_inputs()
    inputs["provider"] = "codex"
    with pytest.raises(ValidationError, match="profile/provider mismatch"):
        CapabilitySet(**inputs)  # type: ignore[arg-type]

    inputs = _mutable_capability_inputs()
    inputs["budget_units"] = ["micro_usd", "micro_usd"]
    with pytest.raises(ValidationError, match="duplicate budget units"):
        CapabilitySet(**inputs)  # type: ignore[arg-type]


def test_provider_outcome_rejects_unbounded_fields_and_deeply_owns_tags() -> None:
    tags = ["process.failed", "reason.nonzero_exit"]
    outcome = ProviderOutcome(
        provider="claude",  # type: ignore[arg-type]
        diagnostic_code="provider_auth_failed",  # type: ignore[arg-type]
        event_shape_tags=tags,  # type: ignore[arg-type]
    )
    before = json.dumps(outcome.as_dict(), sort_keys=True)
    tags.append("secret.raw")

    assert outcome.provider is Provider.CLAUDE
    assert outcome.diagnostic_code is DiagnosticCode.PROVIDER_AUTH_FAILED
    assert outcome.event_shape_tags == ("process.failed", "reason.nonzero_exit")
    assert json.dumps(outcome.as_dict(), sort_keys=True) == before
    assert "secret.raw" not in before

    secret_mapping = {"raw_secret": "do-not-serialize"}
    with pytest.raises(ValidationError) as terminal_error:
        ProviderOutcome(
            provider="claude",  # type: ignore[arg-type]
            diagnostic_code="none",  # type: ignore[arg-type]
            event_shape_tags=(),
            terminal_tool_signal=True,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError) as usage_error:
        ProviderOutcome(
            provider="claude",  # type: ignore[arg-type]
            diagnostic_code="none",  # type: ignore[arg-type]
            event_shape_tags=(),
            usage_totals=secret_mapping,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError) as provider_error:
        ProviderOutcome(
            provider=secret_mapping,  # type: ignore[arg-type]
            diagnostic_code="none",  # type: ignore[arg-type]
            event_shape_tags=(),
        )

    for error in (terminal_error.value, usage_error.value, provider_error.value):
        assert "do-not-serialize" not in str(error)
        assert "raw_secret" not in str(error)


def test_typed_refusal_normalizes_enums_and_rejects_incoherent_identity() -> None:
    refusal = TypedRefusal(
        code="provider_capability_unsupported",  # type: ignore[arg-type]
        provider="claude",  # type: ignore[arg-type]
        profile_id="claude-builder",  # type: ignore[arg-type]
        capability="resume",  # type: ignore[arg-type]
    )

    assert refusal.code is ProviderRefusalCode.PROVIDER_CAPABILITY_UNSUPPORTED
    assert refusal.provider is Provider.CLAUDE
    assert refusal.profile_id is BuiltinProfileId.CLAUDE_BUILDER
    assert refusal.capability is ProviderCapability.RESUME
    assert refusal.as_dict()["provider"] == "claude"

    with pytest.raises(ValidationError, match="profile/provider mismatch"):
        TypedRefusal(
            code="provider_unavailable",  # type: ignore[arg-type]
            provider="codex",  # type: ignore[arg-type]
            profile_id="claude-builder",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError) as captured:
        TypedRefusal(
            code="provider_capability_unsupported",  # type: ignore[arg-type]
            capability="secret\nraw",  # type: ignore[arg-type]
        )
    assert "secret\nraw" not in str(captured.value)


@pytest.mark.parametrize("field_name", ("mcp_tool_names", "skills"))
def test_capability_name_collections_accept_exact_count_limit_and_refuse_one_more(
    field_name: str,
) -> None:
    names = [
        f"{field_name.removesuffix('s')}-{index:03d}"
        for index in range(PROVIDER_CAPABILITY_COLLECTION_LIMIT)
    ]
    inputs = _mutable_capability_inputs()
    inputs[field_name] = names

    accepted = CapabilitySet(**inputs)  # type: ignore[arg-type]
    assert len(getattr(accepted, field_name)) == PROVIDER_CAPABILITY_COLLECTION_LIMIT

    names.append(f"{field_name.removesuffix('s')}-over-limit")
    with pytest.raises(ValidationError, match="collection-size limit") as captured:
        CapabilitySet(**inputs)  # type: ignore[arg-type]
    assert "over-limit" not in str(captured.value)


def _compact_capability_input_size(inputs: dict[str, object]) -> int:
    return len(
        json.dumps(
            inputs,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _capability_inputs_at_serialized_boundary() -> dict[str, object]:
    inputs = _mutable_capability_inputs()
    names: list[str] = []
    inputs["mcp_tool_names"] = names
    while True:
        remaining = PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT - _compact_capability_input_size(
            inputs
        )
        if remaining <= 259:
            # Adding to a non-empty JSON array costs comma + two quotes in
            # addition to the ASCII name bytes.
            name_length = remaining - 3
            prefix = f"tool-{len(names):03d}-"
            assert len(prefix) <= name_length <= 256
            names.append(prefix + "x" * (name_length - len(prefix)))
            assert (
                _compact_capability_input_size(inputs) == PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT
            )
            return inputs
        prefix = f"tool-{len(names):03d}-"
        names.append(prefix + "x" * (200 - len(prefix)))
        assert len(names) < PROVIDER_CAPABILITY_COLLECTION_LIMIT


def test_capability_serialized_size_accepts_exact_boundary_and_refuses_over_bound() -> None:
    inputs = _capability_inputs_at_serialized_boundary()
    accepted = CapabilitySet(**inputs)  # type: ignore[arg-type]
    before = json.dumps(
        accepted.as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(before.encode("utf-8")) == PROVIDER_CAPABILITY_SERIALIZED_BYTES_LIMIT

    names = inputs["mcp_tool_names"]
    assert isinstance(names, list)
    names.append("z")
    assert "z" not in accepted.mcp_tool_names
    assert (
        json.dumps(
            accepted.as_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        == before
    )
    with pytest.raises(ValidationError, match="serialized-size limit"):
        CapabilitySet(**inputs)  # type: ignore[arg-type]


def test_huge_malicious_capability_collections_fail_closed_without_echo() -> None:
    inputs = _mutable_capability_inputs()
    inputs["mcp_tool_names"] = [f"secret-tool-{index:05d}" for index in range(10_000)]
    inputs["skills"] = [f"secret-skill-{index:05d}" for index in range(10_000)]

    with pytest.raises(ValidationError, match="collection-size limit") as captured:
        CapabilitySet(**inputs)  # type: ignore[arg-type]

    assert "secret-tool" not in str(captured.value)
    assert "secret-skill" not in str(captured.value)


def test_capability_name_materialization_is_bounded_before_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = _MisreportingSequence(
        ("secret-tool",),
        actual_count=10_000,
        reported_count=0,
    )
    inputs = _mutable_capability_inputs()
    inputs["mcp_tool_names"] = names
    serialization_calls = 0

    def _forbidden_serialization(**_kwargs: object) -> int:
        nonlocal serialization_calls
        serialization_calls += 1
        raise AssertionError("serialization must not run after count overflow")

    monkeypatch.setattr(
        capability_contracts,
        "_capability_serialized_size",
        _forbidden_serialization,
    )

    with pytest.raises(ValidationError, match="collection-size limit") as captured:
        CapabilitySet(**inputs)  # type: ignore[arg-type]

    assert names.len_count == 0
    assert names.access_count == PROVIDER_CAPABILITY_COLLECTION_LIMIT + 1
    assert serialization_calls == 0
    assert "secret-tool" not in str(captured.value)


def test_provider_outcome_tag_materialization_is_bounded_without_echo() -> None:
    tags = _MisreportingSequence(
        ("secret.raw",),
        actual_count=10_000,
        reported_count=0,
    )

    with pytest.raises(ValidationError, match="collection-size limit") as captured:
        ProviderOutcome(
            provider=Provider.CLAUDE,
            diagnostic_code=DiagnosticCode.NONE,
            event_shape_tags=tags,  # type: ignore[arg-type]
        )

    assert tags.len_count == 0
    assert tags.access_count == 5
    assert "secret.raw" not in str(captured.value)


def test_budget_unit_materialization_is_bounded_without_echo() -> None:
    units = _MisreportingSequence(
        ("micro_usd", "provider_units", "secret-budget"),
        actual_count=10_000,
        reported_count=0,
    )

    with pytest.raises(ValidationError, match="collection-size limit") as captured:
        ProviderDescriptor(
            provider=Provider.CLAUDE,
            adapter_version=PROVIDER_ADAPTER_VERSION,
            profile_id=BuiltinProfileId.CLAUDE_BUILDER,
            model=None,
            sandbox_boundary=SandboxBoundary.TRUSTED_WORKSPACE,
            permission_mode="acceptEdits",
            budget_units=units,  # type: ignore[arg-type]
        )

    assert units.len_count == 0
    assert units.access_count == len(BudgetUnit) + 1
    assert "secret-budget" not in str(captured.value)
