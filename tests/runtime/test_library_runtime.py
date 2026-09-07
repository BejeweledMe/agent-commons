from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.library import LibraryStore
from agent_commons.runtime import (
    BudgetUnit,
    BuiltinProfileId,
    LaunchPlan,
    LaunchPlanner,
    LaunchPurpose,
    TypedRefusal,
    ValidatedLaunchPlan,
    default_profile_registry,
)
from agent_commons.runtime.model import invocation_instruction_bytes
from agent_commons.services.delegation_instruction import (
    DelegationInstructionInput,
    compose_delegation_instruction,
)


@pytest.mark.parametrize(
    "profile_id",
    [
        BuiltinProfileId.CLAUDE_BUILDER,
        BuiltinProfileId.CODEX_BUILDER,
        BuiltinProfileId.GROK_BUILDER,
    ],
)
def test_specialization_enters_every_provider_exactly_once_and_is_fingerprinted(
    tmp_path: Path, profile_id: BuiltinProfileId
) -> None:
    store = LibraryStore(tmp_path / "library")
    role = next(
        item["ref"] for item in store.catalog()["roles"] if item["ref"]["id"] == "frontend-engineer"
    )
    bundle = store.compose_role(role)
    prompt = store.detail(role, editable=True)["content"]["system_prompt"]
    profile = default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        grok_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    ).get(profile_id)
    planner = LaunchPlanner.default()
    validation = planner.validate_static(
        LaunchPlan(
            profile_id=profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="Mandatory broker contract" + bundle.instruction,
            budget_unit=BudgetUnit.PROVIDER_UNITS,
            budget_limit=1,
        ),
        profile,
        workspace_root=tmp_path,
    )
    assert not isinstance(validation, TypedRefusal)
    built = planner.build(
        validation,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        delegation_id="delegation.01M19P2EXACTBUILD000000000",
        child_session_id="session.01M19P2EXACTBUILD00000000000",
        max_budget_microusd=None,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )
    actual = invocation_instruction_bytes(built.invocation).decode()
    assert actual.count(prompt) == 1
    assert "Service skill web-frontend-engineering / SKILL.md" in actual
    assert "commons_read_skill" in actual
    assert "Mandatory broker contract" in actual
    assert prompt not in json.dumps(built.as_dict())
    if profile_id is not BuiltinProfileId.GROK_BUILDER:
        changed = replace(built.invocation, stdin=b"Methods silently stripped")
        with pytest.raises(ConfigurationError):
            ValidatedLaunchPlan.create(validation=validation, invocation=changed)


def test_companion_versions_change_bundle_digest_without_loading_all_methods(
    tmp_path: Path,
) -> None:
    store = LibraryStore(tmp_path / "library")
    role = next(
        item["ref"] for item in store.catalog()["roles"] if item["ref"]["id"] == "frontend-engineer"
    )
    first = store.compose_role(role)
    content = store.detail(role, editable=True)["content"]
    companion = content["conditional_skills"][0]["ref"]
    skill = store.detail(companion, editable=True)["content"]
    edited = store.save(
        kind="skill",
        id="custom-companion",
        content={**skill, "instruction": "Unique conditional method text"},
        expected_version=None,
        idempotency_key="companion",
    )
    content["conditional_skills"][0]["ref"] = edited["ref"]
    changed = store.save(
        kind="role",
        id="custom-frontend",
        content=content,
        expected_version=None,
        idempotency_key="role",
    )
    second = store.compose_role(changed["ref"])
    assert first.source_digest != second.source_digest
    assert "Unique conditional method text" not in second.instruction
    assert store.read_file(edited["ref"])["text"] == "Unique conditional method text"


def test_every_builtin_role_fits_all_three_provider_plans_without_live_providers(
    tmp_path: Path,
) -> None:
    store = LibraryStore(tmp_path / "library")
    profiles = default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        grok_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    )
    planner = LaunchPlanner.default()
    checked = 0
    for role in store.catalog()["roles"]:
        bundle = store.compose_role(role["ref"])
        primary = store.resolve(bundle.skill_refs[0].as_dict())
        assert primary["content"]["instruction"] in bundle.instruction
        for path, text in primary["files"].items():
            if path != "SKILL.md" and len(text) > 100:
                assert text not in bundle.instruction
        for identifier in (
            BuiltinProfileId.CODEX_BUILDER,
            BuiltinProfileId.CLAUDE_BUILDER,
            BuiltinProfileId.GROK_BUILDER,
        ):
            broker_instruction = compose_delegation_instruction(
                DelegationInstructionInput(
                    delegation_id="delegation.01M19P2EXACTBUILD000000000",
                    target_kind="task",
                    target_id="task.01M19P2EXACTBUILD000000000",
                    target_revision="evt.01M19P2EXACTBUILD000000000",
                    purpose="implementation",
                    target_profile=identifier.value,
                    max_depth=0,
                    wall_time_seconds=600,
                    max_attempts=1,
                    max_concurrency=1,
                    budget_limit=1,
                    budget_unit="provider_units",
                ),
                profile_id=identifier,
            )
            validation = planner.validate_static(
                LaunchPlan(
                    profile_id=identifier,
                    purpose=LaunchPurpose.IMPLEMENTATION,
                    instruction=broker_instruction + bundle.instruction,
                    budget_unit=BudgetUnit.PROVIDER_UNITS,
                    budget_limit=1,
                ),
                profiles.get(identifier),
                workspace_root=tmp_path,
            )
            assert not isinstance(validation, TypedRefusal), (role["ref"], identifier)
            assert planner.validate_instruction_size(validation) is None
            checked += 1
    assert checked == 90
