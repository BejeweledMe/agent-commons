"""The conservative Grok instruction bound is retained for the stdin transport."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import agent_commons.runtime.model as model
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BuiltinProfileId,
    EphemeralSkillBundle,
    GrokProviderAdapter,
    LaunchPlan,
    LaunchPlanner,
    LaunchPurpose,
    ProviderRefusalCode,
    TypedRefusal,
)
from tests.runtime.test_launch_plan import _build, _context_binding, _profile


def sized_instruction(size: int, glyph: str = "x") -> str:
    count, remainder = divmod(size, len(glyph.encode("utf-8")))
    return glyph * count + "x" * remainder


def plan(instruction: str, **kwargs: Any) -> LaunchPlan:
    return LaunchPlan(
        profile_id=BuiltinProfileId.GROK_BUILDER,
        purpose=LaunchPurpose.IMPLEMENTATION,
        instruction=instruction,
        **kwargs,
    )


@pytest.mark.parametrize("glyph", ["x", "界", "🙂"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_grok_argument_limit_counts_utf8_and_reserves_terminating_nul(
    glyph: str, offset: int
) -> None:
    instruction = sized_instruction(model.GROK_PROMPT_ARGUMENT_MAX_BYTES + offset, glyph)
    adapter = GrokProviderAdapter()
    bundle = adapter.project_skills(())
    assert isinstance(bundle, EphemeralSkillBundle)
    compiled = adapter.compile_instruction(plan(instruction), bundle)
    assert model.GROK_PROMPT_ARGUMENT_MAX_BYTES + 1 == 128 * 1024
    if offset <= 0:
        assert compiled == instruction
        assert len(compiled.encode("utf-8")) + 1 <= 128 * 1024
    else:
        assert isinstance(compiled, TypedRefusal)
        assert compiled.code is ProviderRefusalCode.INSTRUCTION_TOO_LARGE
        assert instruction not in str(compiled.as_dict())


def test_skill_projection_is_counted_before_static_launch_validation(tmp_path: Path) -> None:
    instruction = sized_instruction(model.GROK_PROMPT_ARGUMENT_MAX_BYTES)
    refusal = LaunchPlanner.default().validate_static(
        plan(instruction, skill_refs=("commons-start",)),
        _profile(BuiltinProfileId.GROK_BUILDER),
        workspace_root=tmp_path,
    )
    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.INSTRUCTION_TOO_LARGE


def test_composed_context_is_counted_before_building_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = LaunchPlanner.default()
    context = _context_binding()
    assert context.compiled_context_bytes is not None
    size = model.GROK_PROMPT_ARGUMENT_MAX_BYTES - len(context.compiled_context_bytes) - 2
    validation = planner.validate_static(
        plan(sized_instruction(size)),
        _profile(BuiltinProfileId.GROK_BUILDER),
        workspace_root=tmp_path,
    )
    assert not isinstance(validation, TypedRefusal)
    assert planner.validate_instruction_size(validation, context) is None
    oversized = replace(validation, instruction=validation.instruction + "x")
    refusal = planner.validate_instruction_size(oversized, context)
    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.INSTRUCTION_TOO_LARGE

    def no_invocation(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("an oversized prompt must be refused before constructing argv")

    monkeypatch.setattr(GrokProviderAdapter, "build_invocation", no_invocation)
    # Preserve exact skill composition so the size guard is the refusal.
    oversized = replace(oversized, plan=replace(oversized.plan, instruction=oversized.instruction))
    with pytest.raises(ConfigurationError) as caught:
        _build(planner, oversized, tmp_path, context=context)
    assert caught.value.code == "instruction_too_large"
    assert oversized.instruction not in str(caught.value)


@pytest.mark.parametrize(
    "profile_id", [BuiltinProfileId.CODEX_BUILDER, BuiltinProfileId.CLAUDE_BUILDER]
)
def test_stdin_providers_keep_their_existing_instruction_bound(
    tmp_path: Path, profile_id: BuiltinProfileId
) -> None:
    planner = LaunchPlanner.default()
    validation = planner.validate_static(
        replace(
            plan(sized_instruction(model.GROK_PROMPT_ARGUMENT_MAX_BYTES + 1)), profile_id=profile_id
        ),
        _profile(profile_id),
        workspace_root=tmp_path,
    )
    assert not isinstance(validation, TypedRefusal)
    assert planner.validate_instruction_size(validation, _context_binding()) is None


def test_direct_grok_profile_refuses_before_executable_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_resolution(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("oversized direct profile instruction reached host resolution")

    monkeypatch.setattr(model, "resolve_trusted_executable", no_resolution)
    instruction = "SYNTHETIC_OVERSIZED_INSTRUCTION" + sized_instruction(
        model.GROK_PROMPT_ARGUMENT_MAX_BYTES
    )
    with pytest.raises(ConfigurationError) as caught:
        _profile(BuiltinProfileId.GROK_BUILDER).build_invocation(
            instruction,
            workspace_root=tmp_path,
            delegation_id="delegation.synthetic-size-guard",
        )
    assert str(caught.value) == "Grok instruction exceeds the supported instruction byte limit"
    assert "SYNTHETIC_OVERSIZED_INSTRUCTION" not in str(caught.value)


def test_direct_grok_profile_accepts_exact_bound_over_fixed_stdin(
    tmp_path: Path,
) -> None:
    instruction = sized_instruction(model.GROK_PROMPT_ARGUMENT_MAX_BYTES, "界")
    invocation = _profile(BuiltinProfileId.GROK_BUILDER).build_invocation(
        instruction,
        workspace_root=tmp_path,
        delegation_id="delegation.synthetic-size-guard",
    )
    assert invocation.argv[-2:] == ("--prompt-file", "/dev/stdin")
    assert invocation.stdin == instruction.encode("utf-8")
