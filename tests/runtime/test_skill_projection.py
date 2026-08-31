from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace

import pytest

import agent_commons.runtime.skill_projection as projection_module
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BudgetUnit,
    BuiltinProfileId,
    ClaudeProviderAdapter,
    CodexProviderAdapter,
    EphemeralSkillBundle,
    LaunchPlan,
    LaunchPlanner,
    LaunchPurpose,
    Provider,
    ProviderRefusalCode,
    TypedRefusal,
    default_profile_registry,
    project_builtin_skills,
)


class _CountingSequence(Sequence[object]):
    def __init__(self, value: object, count: int) -> None:
        self.value = value
        self.count = count
        self.access_count = 0

    def __getitem__(self, index: int) -> object:
        if index < 0 or index >= self.count:
            raise IndexError(index)
        self.access_count += 1
        return self.value

    def __len__(self) -> int:
        return 1


@pytest.mark.parametrize(
    ("adapter", "profile_id", "installed_root"),
    (
        (CodexProviderAdapter(), BuiltinProfileId.CODEX_BUILDER, ".agents/skills"),
        (ClaudeProviderAdapter(), BuiltinProfileId.CLAUDE_BUILDER, ".claude/skills"),
    ),
)
def test_each_provider_projects_allowlisted_source_deterministically(
    adapter: CodexProviderAdapter | ClaudeProviderAdapter,
    profile_id: BuiltinProfileId,
    installed_root: str,
) -> None:
    plan = LaunchPlan(
        profile_id=profile_id,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Exact task instruction",
        skill_refs=("commons-start", "commons-coordinate"),
    )

    first = adapter.project_skills(plan.skill_refs)
    second = adapter.project_skills(plan.skill_refs)
    assert isinstance(first, EphemeralSkillBundle)
    assert first == second
    assert first.provider is profile_id.provider
    assert len({first.source_digest, first.projection_digest, first.installer_digest}) == 3

    compiled = adapter.compile_instruction(plan, first)
    assert isinstance(compiled, str)
    assert compiled.startswith("Exact task instruction")
    assert f"{installed_root}/commons-start/SKILL.md" in compiled
    assert "name: commons-start" in compiled
    assert "name: commons-coordinate" in compiled


def test_provider_projection_and_installer_digests_are_provider_specific() -> None:
    codex = project_builtin_skills(Provider.CODEX, ("commons-start",))
    claude = project_builtin_skills(Provider.CLAUDE, ("commons-start",))

    assert codex.source_digest == claude.source_digest
    assert codex.projection_digest != claude.projection_digest
    assert codex.installer_digest != claude.installer_digest


@pytest.mark.parametrize("field", ("projection_digest", "installer_digest", "source_digest"))
def test_digest_tampering_is_typed_refusal(field: str) -> None:
    adapter = ClaudeProviderAdapter()
    plan = LaunchPlan(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Exact task instruction",
        skill_refs=("commons-start",),
    )
    bundle = adapter.project_skills(plan.skill_refs)
    assert isinstance(bundle, EphemeralSkillBundle)
    altered = replace(bundle, **{field: "0" * 64})

    refusal = adapter.compile_instruction(plan, altered)
    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE
    assert refusal.durable_effect == "none"


def test_source_drift_after_projection_is_typed_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CodexProviderAdapter()
    plan = LaunchPlan(
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="Exact task instruction",
        skill_refs=("commons-start",),
    )
    bundle = adapter.project_skills(plan.skill_refs)
    assert isinstance(bundle, EphemeralSkillBundle)
    original_source = projection_module._source

    def changed_source(skill_id: str) -> bytes:
        return original_source(skill_id) + b"\nchanged-after-validation"

    monkeypatch.setattr(projection_module, "_source", changed_source)
    refusal = adapter.compile_instruction(plan, bundle)
    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE


def test_bundle_is_immutable_and_safe_metadata_never_contains_skill_text(tmp_path) -> None:
    planner = LaunchPlanner.default()
    profile = default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    ).get(BuiltinProfileId.CLAUDE_BUILDER)
    plan = LaunchPlan(
        profile_id=profile.profile_id,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction="PRIVATE TASK INSTRUCTION",
        skill_refs=("commons-start",),
        budget_unit=BudgetUnit.MICRO_USD,
        budget_limit=1,
    )
    validation = planner.validate_static(plan, profile, workspace_root=tmp_path)
    assert not isinstance(validation, TypedRefusal)
    built = planner.build(
        validation,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        delegation_id="delegation.01M1P4SKILLBUNDLE000000",
        child_session_id="session.01M1P4SKILLBUNDLE00000000",
        max_budget_microusd=1,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )

    bundle = built.skill_bundle
    assert bundle is not None
    with pytest.raises(FrozenInstanceError):
        bundle.projection_digest = "0" * 64  # type: ignore[misc]
    metadata = json.dumps(built.as_dict())
    assert "PRIVATE TASK INSTRUCTION" not in metadata
    assert "name: commons-start" not in metadata
    assert "commons-start" not in metadata
    assert built.invocation_fingerprint == built.as_dict()["invocation_fingerprint"]
    assert bundle.source_digest in built.invocation.stdin.decode()
    assert bundle.projection_digest in built.invocation.stdin.decode()
    assert bundle.installer_digest in built.invocation.stdin.decode()


def test_unknown_skill_refuses_without_echo() -> None:
    secret_identity = "private-workspace-skill-secret"
    refusal = CodexProviderAdapter().project_skills((secret_identity,))

    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.SKILL_PROJECTION_UNAVAILABLE
    assert secret_identity not in json.dumps(refusal.as_dict())


def test_projection_collections_are_bounded_before_materialization() -> None:
    refs = _CountingSequence("commons-start", 10_000)
    with pytest.raises(ConfigurationError, match="collection-size limit"):
        project_builtin_skills(Provider.CODEX, refs)  # type: ignore[arg-type]
    assert refs.access_count == 9

    projected = _CountingSequence(b"projected", 10_000)
    with pytest.raises(ConfigurationError, match="collection-size limit"):
        EphemeralSkillBundle(
            provider=Provider.CODEX,
            skill_ids=("commons-start",),
            projected_instructions=projected,  # type: ignore[arg-type]
            source_digest="0" * 64,
            projection_digest="0" * 64,
            installer_digest="0" * 64,
        )
    assert projected.access_count == 9

    def hostile_iterable():  # type: ignore[no-untyped-def]
        raise AssertionError("non-sequence skill refs must not be consumed")
        yield "commons-start"

    with pytest.raises(ConfigurationError, match="bounded collection"):
        project_builtin_skills(Provider.CLAUDE, hostile_iterable())  # type: ignore[arg-type]
