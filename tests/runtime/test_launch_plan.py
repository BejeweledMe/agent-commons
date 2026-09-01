from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

import agent_commons.runtime.launch as launch_module
from agent_commons.domain.context_pack import ContextPackDraft, ContextPackRecord
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BudgetUnit,
    BuiltinProfileId,
    CodexProviderAdapter,
    ContextBinding,
    LaunchPlan,
    LaunchPlanner,
    LaunchPurpose,
    ProcessResult,
    ProviderInitializationProbe,
    ProviderInitializationState,
    ProviderRefusalCode,
    RunnerInvocation,
    RunOutcome,
    RunReason,
    TypedRefusal,
    ValidatedLaunchPlan,
    default_profile_registry,
    invocation_fingerprint,
)
from agent_commons.services.context_compiler import ContextCompiler


def _result() -> ProcessResult:
    return ProcessResult(
        outcome=RunOutcome.SUCCEEDED,
        reason=RunReason.COMPLETED,
        exit_code=0,
        pid=42,
        duration_seconds=0.01,
        stdout=b"",
        stderr=b"",
        stdout_bytes_seen=0,
        stderr_bytes_seen=0,
        output_truncated=False,
    )


def _profile(profile_id: BuiltinProfileId):
    return default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        grok_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    ).get(profile_id)


def _static(planner: LaunchPlanner, profile: Any, tmp_path: Path):
    purpose = (
        LaunchPurpose.INDEPENDENT_REVIEW
        if profile.profile_id.independent_reviewer
        else LaunchPurpose.IMPLEMENTATION
    )
    result = planner.validate_static(
        LaunchPlan(
            profile_id=profile.profile_id,
            purpose=purpose,
            instruction="bound instruction",
            budget_unit=(
                BudgetUnit.MICRO_USD if profile.supports_budget else BudgetUnit.PROVIDER_UNITS
            ),
            budget_limit=1,
        ),
        profile,
        workspace_root=tmp_path,
    )
    assert not isinstance(result, TypedRefusal)
    return result


def test_planner_builds_exactly_once_and_plan_is_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CODEX_BUILDER)
    validation = _static(planner, profile, tmp_path)
    original = CodexProviderAdapter.build_invocation
    calls: list[RunnerInvocation] = []

    def counted(self: CodexProviderAdapter, *args: Any, **kwargs: Any) -> RunnerInvocation:
        invocation = original(self, *args, **kwargs)
        calls.append(invocation)
        return invocation

    monkeypatch.setattr(CodexProviderAdapter, "build_invocation", counted)
    validated = planner.build(
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

    assert len(calls) == 1
    assert validated.invocation is calls[0]
    assert validated.invocation_fingerprint == validated.as_dict()["invocation_fingerprint"]
    assert "bound instruction" not in str(validated.as_dict())
    assert validated.invocation.argv not in validated.as_dict().values()
    with pytest.raises(FrozenInstanceError):
        validated.invocation_fingerprint = "0" * 64  # type: ignore[misc]


def test_fingerprint_covers_actual_stdin_bytes(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CLAUDE_BUILDER)
    validation = _static(planner, profile, tmp_path)
    first = planner.build(
        validation,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        delegation_id="delegation.01M19P2FINGERPRINT0000000",
        child_session_id="session.01M19P2FINGERPRINT000000000",
        max_budget_microusd=1,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )
    changed_invocation = replace(first.invocation, stdin=first.invocation.stdin + b"x")
    assert invocation_fingerprint(changed_invocation) != first.invocation_fingerprint
    with pytest.raises(ConfigurationError, match="skill/context composition"):
        ValidatedLaunchPlan.create(validation=validation, invocation=changed_invocation)


def test_grok_plan_proves_prompt_argument_and_fingerprints_it(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.GROK_BUILDER)
    validation = _static(planner, profile, tmp_path)
    built = planner.build(
        validation,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        delegation_id="delegation.01M19P2GROKFINGERPRINT00",
        child_session_id="session.01M19P2GROKFINGERPRINT000",
        max_budget_microusd=None,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )
    prompt_index = built.invocation.argv.index("-p") + 1
    assert built.invocation.stdin == b""
    assert built.invocation.argv[prompt_index] == "bound instruction"

    changed_argv = list(built.invocation.argv)
    changed_argv[prompt_index] = "different instruction"
    changed = replace(built.invocation, argv=tuple(changed_argv))
    assert invocation_fingerprint(changed) != built.invocation_fingerprint
    with pytest.raises(ConfigurationError, match="skill/context composition"):
        ValidatedLaunchPlan.create(validation=validation, invocation=changed)


def test_skill_and_context_composition_is_proven_before_fingerprinting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CLAUDE_BUILDER)
    validation = planner.validate_static(
        LaunchPlan(
            profile_id=profile.profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="Exact role instruction",
            skill_refs=("commons-start",),
            budget_unit=BudgetUnit.MICRO_USD,
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
        delegation_id="delegation.01M1P4COMPOSITION00000",
        child_session_id="session.01M1P4COMPOSITION0000000",
        max_budget_microusd=1,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )
    stripped = replace(built.invocation, stdin=b"Exact role instruction")

    def forbidden_fingerprint(_invocation: RunnerInvocation) -> str:
        raise AssertionError("invalid composition must fail before fingerprinting")

    monkeypatch.setattr(launch_module, "invocation_fingerprint", forbidden_fingerprint)
    with pytest.raises(ConfigurationError, match="skill/context composition"):
        ValidatedLaunchPlan.create(
            validation=validation,
            invocation=stripped,
            context=built.context,
        )


@pytest.mark.parametrize(
    ("profile_changes", "expected_code"),
    (
        ({"executable": "/definitely/missing-provider"}, ProviderRefusalCode.PROVIDER_START_FAILED),
        (
            {"mcp_executable": "/definitely/missing-mcp"},
            ProviderRefusalCode.MCP_EXECUTABLE_UNAVAILABLE,
        ),
        (
            {"git_executable": "/definitely/missing-git"},
            ProviderRefusalCode.GIT_EXECUTABLE_UNAVAILABLE,
        ),
        ({"trusted_workspace": False}, ProviderRefusalCode.TRUSTED_WORKSPACE_REQUIRED),
    ),
)
def test_static_validation_preserves_specific_prestart_refusals(
    tmp_path: Path,
    profile_changes: dict[str, object],
    expected_code: ProviderRefusalCode,
) -> None:
    planner = LaunchPlanner.default()
    profile = replace(_profile(BuiltinProfileId.CODEX_BUILDER), **profile_changes)

    refusal = planner.validate_static(
        LaunchPlan(
            profile_id=profile.profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="bound instruction",
            budget_unit=BudgetUnit.PROVIDER_UNITS,
            budget_limit=1,
        ),
        profile,
        workspace_root=tmp_path,
    )

    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is expected_code
    assert refusal.durable_effect == "none"
    assert "preflight" in " ".join(refusal.safe_next_actions)


def test_trust_refusal_precedes_all_executable_resolution_without_echo(tmp_path: Path) -> None:
    secret = "SUPERSECRET-untrusted-and-missing"
    profile = replace(
        _profile(BuiltinProfileId.CODEX_BUILDER),
        executable=f"/{secret}-provider",
        mcp_executable=f"/{secret}-mcp",
        git_executable=f"/{secret}-git",
        trusted_workspace=False,
    )

    refusal = LaunchPlanner.default().validate_static(
        LaunchPlan(
            profile_id=profile.profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="bound instruction",
            budget_unit=BudgetUnit.PROVIDER_UNITS,
            budget_limit=1,
        ),
        profile,
        workspace_root=tmp_path,
    )

    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is ProviderRefusalCode.TRUSTED_WORKSPACE_REQUIRED
    assert secret not in json.dumps(refusal.as_dict())


@pytest.mark.parametrize(
    ("profile_id", "expected_code"),
    (
        (BuiltinProfileId.CODEX_BUILDER, ProviderRefusalCode.MCP_EXECUTABLE_UNAVAILABLE),
        (BuiltinProfileId.CLAUDE_BUILDER, ProviderRefusalCode.PROVIDER_START_FAILED),
    ),
)
def test_multi_missing_resolution_preserves_provider_specific_first_failure(
    tmp_path: Path,
    profile_id: BuiltinProfileId,
    expected_code: ProviderRefusalCode,
) -> None:
    secret = f"SUPERSECRET-{profile_id.value}-multi-missing"
    profile = replace(
        _profile(profile_id),
        executable=f"/{secret}-provider",
        mcp_executable=f"/{secret}-mcp",
        git_executable=f"/{secret}-git",
    )

    refusal = LaunchPlanner.default().validate_static(
        LaunchPlan(
            profile_id=profile.profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction="bound instruction",
            budget_unit=BudgetUnit.PROVIDER_UNITS,
            budget_limit=1,
        ),
        profile,
        workspace_root=tmp_path,
    )

    assert isinstance(refusal, TypedRefusal)
    assert refusal.code is expected_code
    assert secret not in json.dumps(refusal.as_dict())


def test_generic_probe_layer_contains_no_provider_policy() -> None:
    source = inspect.getsource(ProviderInitializationProbe).casefold()
    assert "codex" not in source
    assert "app-server" not in source
    assert "sqlite state runtime" not in source
    assert "provider.code" not in source


def test_claude_probe_uses_only_fixed_no_model_argv_eof_and_bounds(tmp_path: Path) -> None:
    class CapturingRunner:
        invocation: RunnerInvocation | None = None
        values: dict[str, Any] = {}

        def run(self, invocation: RunnerInvocation, **values: Any) -> ProcessResult:
            self.invocation = invocation
            self.values = values
            return _result()

    runner = CapturingRunner()
    status = ProviderInitializationProbe(
        runner=runner,  # type: ignore[arg-type]
        timeout_seconds=3,
        max_output_bytes=4096,
    ).probe(
        _profile(BuiltinProfileId.CLAUDE_BUILDER),
        workspace_root=tmp_path,
    )
    assert status.state is ProviderInitializationState.READY
    assert runner.invocation is not None
    # The trusted executable resolver normalizes a symlinked configured path
    # before it becomes provider argv.  Linux commonly exposes /bin -> /usr/bin.
    assert runner.invocation.argv == (str(Path("/bin/echo").resolve()), "mcp", "list")
    assert runner.invocation.stdin == b""
    assert runner.values["timeout_seconds"] == 3
    assert runner.values["max_output_bytes"] == 4096


def test_codex_probe_uses_only_fixed_no_model_argv_eof_and_bounds(tmp_path: Path) -> None:
    class CapturingRunner:
        invocation: RunnerInvocation | None = None
        values: dict[str, Any] = {}

        def run(self, invocation: RunnerInvocation, **values: Any) -> ProcessResult:
            self.invocation = invocation
            self.values = values
            return _result()

    runner = CapturingRunner()
    status = ProviderInitializationProbe(
        runner=runner,  # type: ignore[arg-type]
        timeout_seconds=3,
        max_output_bytes=4096,
    ).probe(
        _profile(BuiltinProfileId.CODEX_BUILDER),
        workspace_root=tmp_path,
    )
    assert status.state is ProviderInitializationState.READY
    assert runner.invocation is not None
    assert runner.invocation.argv[-2:] == ("app-server", "--stdio")
    assert runner.invocation.stdin == b""
    assert runner.values["timeout_seconds"] == 3
    assert runner.values["max_output_bytes"] == 4096


def test_grok_probe_default_bound_covers_bounded_inspect_metadata(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "projectTrusted": True,
            "mcpServers": [{"name": "agent-commons"}],
            "hooks": [],
            "plugins": [],
            "lspServers": [],
            "mcpConfigProblems": [],
            "boundedMetadata": "x" * (32 * 1024),
        }
    ).encode()

    class CapturingRunner:
        values: dict[str, Any] = {}

        def run(self, invocation: RunnerInvocation, **values: Any) -> ProcessResult:
            self.values = values
            return replace(
                _result(),
                stdout=payload,
                stdout_bytes_seen=len(payload),
            )

    runner = CapturingRunner()
    status = ProviderInitializationProbe(runner=runner).probe(  # type: ignore[arg-type]
        _profile(BuiltinProfileId.GROK_BUILDER),
        workspace_root=tmp_path,
    )

    assert status.state is ProviderInitializationState.READY
    assert runner.values["max_output_bytes"] == 64 * 1024


def _context_binding(summary: str = "Stable baseline") -> ContextBinding:
    record = ContextPackRecord.create(
        context_pack_id="context_pack." + "0" * 25 + "7",
        revision="evt." + "0" * 25 + "7",
        source_event_id="evt." + "0" * 25 + "7",
        draft=ContextPackDraft.from_payload(
            {
                "summary": summary,
                "facts": [],
                "decision_refs": [],
                "open_questions": ["What remains open?"],
            }
        ),
        recorded_at="2026-08-30T00:00:00Z",
        author_session_ids=("session.context-author",),
    )
    compiled = ContextCompiler().compile(record)
    return ContextBinding(
        binding=compiled.binding,
        compiled_context_bytes=compiled.text.encode("utf-8"),
    )


def _build(
    planner: LaunchPlanner,
    validation: Any,
    tmp_path: Path,
    *,
    context: ContextBinding | None = None,
) -> ValidatedLaunchPlan:
    return planner.build(
        validation,
        workspace_root=tmp_path,
        state_root=tmp_path / "state",
        delegation_id="delegation.01M19C2CONTEXTBIND0000000",
        child_session_id="session.01M19C2CONTEXTBIND000000000",
        max_budget_microusd=None,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
        context=context,
    )


def test_fresh_plan_keeps_invocation_bytes_and_exposes_no_fingerprint(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CLAUDE_BUILDER)
    validation = _static(planner, profile, tmp_path)
    implicit = _build(planner, validation, tmp_path)
    explicit = _build(planner, validation, tmp_path, context=ContextBinding.fresh())

    assert implicit.context.binding is None
    assert implicit.invocation.stdin == explicit.invocation.stdin
    assert b"# Agent Commons Context Pack" not in implicit.invocation.stdin
    metadata = implicit.as_dict()
    assert metadata["context_mode"] == "fresh"
    assert metadata["compiled_context_fingerprint"] is None


def test_accumulated_plan_binds_baseline_bytes_and_fingerprint(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CLAUDE_BUILDER)
    validation = _static(planner, profile, tmp_path)
    context = _context_binding()
    validated = _build(planner, validation, tmp_path, context=context)

    assert validated.context is context
    assert validated.invocation.stdin.endswith(b"\n\n" + context.compiled_context_bytes)
    metadata = validated.as_dict()
    assert metadata["context_mode"] == "accumulated"
    assert metadata["compiled_context_fingerprint"] == context.compiled_context_fingerprint
    assert "Stable baseline" not in str(metadata)


def test_two_plans_share_identical_baseline_with_separate_instructions(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    context = _context_binding()
    codex_base = _static(planner, _profile(BuiltinProfileId.CODEX_BUILDER), tmp_path)
    codex_validation = replace(
        codex_base,
        plan=replace(codex_base.plan, instruction="codex role instruction"),
        instruction="codex role instruction",
    )
    claude_base = _static(planner, _profile(BuiltinProfileId.CLAUDE_BUILDER), tmp_path)
    claude_validation = replace(
        claude_base,
        plan=replace(claude_base.plan, instruction="claude role instruction"),
        instruction="claude role instruction",
    )
    codex = _build(
        planner,
        codex_validation,
        tmp_path,
        context=context,
    )
    claude = _build(
        planner,
        claude_validation,
        tmp_path,
        context=context,
    )

    assert codex.invocation.stdin != claude.invocation.stdin
    assert codex.invocation.stdin.endswith(b"\n\n" + context.compiled_context_bytes)
    assert claude.invocation.stdin.endswith(b"\n\n" + context.compiled_context_bytes)
    assert (
        codex.as_dict()["compiled_context_fingerprint"]
        == claude.as_dict()["compiled_context_fingerprint"]
    )


def test_plan_refuses_context_it_does_not_actually_carry(tmp_path: Path) -> None:
    planner = LaunchPlanner.default()
    profile = _profile(BuiltinProfileId.CLAUDE_BUILDER)
    validation = _static(planner, profile, tmp_path)
    fresh = _build(planner, validation, tmp_path)
    context = _context_binding()

    with pytest.raises(ConfigurationError):
        ValidatedLaunchPlan(
            plan=fresh.plan,
            descriptor=fresh.descriptor,
            capabilities=fresh.capabilities,
            invocation=fresh.invocation,
            invocation_fingerprint=fresh.invocation_fingerprint,
            context=context,
        )
