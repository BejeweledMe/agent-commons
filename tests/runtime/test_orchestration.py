from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError, LifecycleConflictError, ValidationError
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    DiagnosticCode,
    ProcessResult,
    ProfileRegistry,
    RunOutcome,
    RunReason,
    TelemetryEvent,
    TelemetryKind,
    default_profile_registry,
)
from agent_commons.runtime.diagnostics import workflow_diagnostic_code
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


def _workspace(
    tmp_path: Path, *, parent_ttl_seconds: int = 8 * 60 * 60
) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="runtime-orchestration")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="runtime-parent-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
        ttl_seconds=parent_ttl_seconds,
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Review bounded runtime orchestration",
        description="Exercise one exact target without provider content persistence.",
        acceptance_criteria=("independent review is canonical",),
        priority="high",
        idempotency_key="runtime-target-task",
    )
    return manager, task


def _delegation(
    manager: CommonsManager,
    task: dict[str, Any],
    *,
    max_attempts: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key=f"runtime-review-{max_attempts}",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": max_attempts,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        idempotency_key=f"runtime-delegation-{max_attempts}",
    )
    return review, delegation


class FakeRunner:
    def __init__(
        self,
        *,
        outcome: RunOutcome = RunOutcome.SUCCEEDED,
        reason: RunReason = RunReason.COMPLETED,
        after_start: Callable[[str], None] | None = None,
        crash_after_start: bool = False,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.after_start = after_start
        self.crash_after_start = crash_after_start
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation
        self.calls += 1
        if self.reason is not RunReason.START_FAILED:
            values["on_started"](7000 + self.calls)
            if self.crash_after_start:
                raise RuntimeError("simulated broker crash")
            if self.after_start is not None:
                self.after_start(values["child_session_id"])
        return ProcessResult(
            outcome=self.outcome,
            reason=self.reason,
            exit_code=0 if self.outcome is RunOutcome.SUCCEEDED else 1,
            pid=None if self.reason is RunReason.START_FAILED else 7000 + self.calls,
            duration_seconds=0.25,
            stdout=b"provider content must remain ephemeral",
            stderr=b"",
            stdout_bytes_seen=38,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def test_launch_pins_child_state_when_ambient_root_belongs_to_another_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]

    foreign_repo = tmp_path / "foreign-repo"
    foreign_repo.mkdir()
    CommonsManager.initialize(foreign_repo, integrations=(), workspace_name="foreign")
    foreign_state_root = tmp_path / "foreign-state"
    CommonsManager(foreign_repo, state_root=foreign_state_root)
    monkeypatch.setenv("AGENT_COMMONS_STATE_ROOT", str(foreign_state_root))

    class StateResolvingRunner(FakeRunner):
        resolved_state_root: Path | None = None
        resolution_error: ConfigurationError | None = None

        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            values["on_started"](7001)
            try:
                child = CommonsManager(
                    manager.repo_root,
                    session_id=values["child_session_id"],
                    state_root=values.get("state_root"),
                )
            except ConfigurationError as exc:
                self.resolution_error = exc
                self.resolved_state_root = foreign_state_root
            else:
                child.sessions.require_active(values["child_session_id"])
                self.resolved_state_root = child.paths.state_root
            return ProcessResult(
                outcome=RunOutcome.SUCCEEDED,
                reason=RunReason.COMPLETED,
                exit_code=0,
                pid=7001,
                duration_seconds=0.25,
                stdout=b"",
                stderr=b"",
                stdout_bytes_seen=0,
                stderr_bytes_seen=0,
                output_truncated=False,
            )

    runner = StateResolvingRunner()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    result = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-foreign-ambient-state-launch",
    )

    assert result["attempt"]["state"] == "succeeded"
    assert runner.resolution_error is None
    assert runner.resolved_state_root == manager.paths.state_root
    notice = result["child_state_resolution"]
    assert notice.count("\n") == 0
    assert "launching workspace" in notice
    assert "AGENT_COMMONS_STATE_ROOT" in notice
    assert "AGENT_COMMONS_STATE_BASE" in notice


class CollectingTelemetry:
    capture_content = False

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


def test_child_review_and_delegation_result_are_canonical_but_output_is_not(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    review, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        completed = child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="runtime-child-review-complete",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="runtime-child-delegation-succeed",
        )
        assert completed["revision"]

    runner = FakeRunner(after_start=complete_as_child)
    telemetry = CollectingTelemetry()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
        telemetry=telemetry,
    )
    result = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-success",
    )

    assert result["delegation"]["state"] == "succeeded"
    assert result["delegation"]["result_refs"] == [review["entity_ref"]]
    assert manager.list_reviews(state="approved")[0]["id"] == review["entity_ref"]["id"]
    assert "provider content" not in json.dumps(result)
    operational = "".join(
        path.read_text()
        for path in (manager.paths.state_root / "runtime").rglob("*.json")
        if path.is_file()
    )
    assert "provider content" not in operational
    child_session = result["attempt"]["correlation"]["child_session_id"]
    assert len(result["attempt"]["correlation"]["trace_id"]) == 32
    assert manager.show_session(child_session)["status"] == "closed"
    assert [event.kind for event in telemetry.events][-2:] == [
        TelemetryKind.CANONICAL_FINALIZATION_STARTED,
        TelemetryKind.CANONICAL_FINALIZATION_COMPLETED,
    ]
    final = telemetry.events[-1]
    assert final.canonical_state == "succeeded"
    assert final.canonical_reason_code == "succeeded"
    assert final.process_canonical_mismatch is False
    assert final.terminal_tool_calls == 0


def test_a_roles_tool_narrowing_reaches_the_launched_process(tmp_path: Path) -> None:
    """From `agent create` to argv, through the path a broker run really takes.

    A narrowing verified only against the profile object would leave the run
    itself unnarrowed and the test green, which is the failure mode this branch
    has hit four times.
    """

    manager, task = _workspace(tmp_path)
    role = manager.create_agent(
        name="Scoped reviewer",
        profile_id="claude-independent-reviewer",
        rationale="reads the repository but does not grep it",
        tool_allowlist=("commons_repo_read", "commons_complete_review"),
        idempotency_key="runtime-role",
    )
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key="runtime-role-review",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="runtime-role-delegation",
    )
    delegation_id = delegation["entity_ref"]["id"]
    seen: list[tuple[str, ...]] = []

    class CapturingRunner(FakeRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            seen.append(tuple(invocation.argv))
            return super().run(invocation, **values)

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="runtime-role-child-review",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="runtime-role-child-succeed",
        )

    service = DelegationRuntimeService(
        manager,
        runner=CapturingRunner(after_start=complete_as_child),  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
        telemetry=CollectingTelemetry(),
    )
    result = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-role-launch",
    )

    assert result["delegation"]["state"] == "succeeded"
    argv = seen[0]
    allowed = set(argv[argv.index("--allowed-tools") + 1].split(","))
    assert "mcp__agent-commons__commons_repo_read" in allowed
    assert "mcp__agent-commons__commons_repo_search" not in allowed
    assert "mcp__agent-commons__commons_succeed_delegation" in allowed


def test_a_required_skill_reaches_the_instruction_the_provider_receives(
    tmp_path: Path,
) -> None:
    """A catalogue nothing reads would be a settings screen over nothing."""

    manager, task = _workspace(tmp_path)
    role = manager.create_agent(
        name="Backend",
        profile_id="claude-independent-reviewer",
        rationale="requires the house review checklist",
        skills=("house-checklist",),
        idempotency_key="skill-role",
    )
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key="skill-review",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="skill-delegation",
    )
    delegation_id = delegation["entity_ref"]["id"]
    seen: list[bytes] = []

    class CapturingRunner(FakeRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            seen.append(invocation.stdin)
            return super().run(invocation, **values)

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="skill-child-review",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="skill-child-succeed",
        )

    def service(catalog: dict[str, Any]) -> DelegationRuntimeService:
        return DelegationRuntimeService(
            manager,
            runner=CapturingRunner(after_start=complete_as_child),  # type: ignore[arg-type]
            profiles=default_profile_registry(
                claude_executable="/bin/echo", mcp_executable="/bin/echo"
            ),
            catalog=catalog,
            telemetry=CollectingTelemetry(),
        )

    # A role requiring a skill the operator has not defined fails closed rather
    # than running as a different agent than the one that was configured.
    with pytest.raises(ValidationError, match="catalogue does not define"):
        service({"skills": [], "tools": []}).run(
            delegation_id, delegation["revision"], idempotency_key="skill-launch-missing"
        )

    result = service(
        {
            "skills": [
                {
                    "id": "house-checklist",
                    "title": "House checklist",
                    "instruction": "Check error handling before style.",
                }
            ],
            "tools": [],
        }
    ).run(delegation_id, delegation["revision"], idempotency_key="skill-launch")

    assert result["delegation"]["state"] == "succeeded"
    instruction = seen[-1].decode("utf-8")
    assert "Check error handling before style." in instruction
    assert "house-checklist" in instruction


def test_the_model_a_role_was_hired_on_reaches_the_launched_process(tmp_path: Path) -> None:
    """From the hire form to argv, through the path a broker run really takes.

    The same failure mode as the tool narrowing above, and a worse one to miss:
    a model verified only against the delegation event, or only against the
    profile object the service holds, would leave the operator's choice in the
    ledger while the provider ran the profile's model -- the panel reporting
    one thing and the subscription billing another.

    The broker resolves the profile from the registry it is *built* with, so
    the substitution has to reach that registry and not merely the object the
    service checked. That is exactly what this asserts, from the argv the
    runner was handed.
    """

    manager, task = _workspace(tmp_path)
    role = manager.create_agent(
        name="Chosen-model reviewer",
        profile_id="claude-independent-reviewer",
        rationale="hired to run on a model the operator picked",
        model="claude-opus-4-6",
        idempotency_key="runtime-model-role",
    )
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key="runtime-model-review",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="runtime-model-delegation",
    )
    delegation_id = delegation["entity_ref"]["id"]
    seen: list[tuple[str, ...]] = []

    class CapturingRunner(FakeRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            seen.append(tuple(invocation.argv))
            return super().run(invocation, **values)

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="runtime-model-child-review",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="runtime-model-child-succeed",
        )

    # The operator config names no model at all, so the only place this one can
    # have come from is the role record.
    profiles = default_profile_registry(claude_executable="/bin/echo", mcp_executable="/bin/echo")
    assert profiles.get("claude-independent-reviewer").model is None

    service = DelegationRuntimeService(
        manager,
        runner=CapturingRunner(after_start=complete_as_child),  # type: ignore[arg-type]
        profiles=profiles,
        telemetry=CollectingTelemetry(),
    )
    result = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-model-launch",
    )

    assert result["delegation"]["state"] == "succeeded"
    argv = seen[0]
    assert argv[argv.index("--model") + 1] == "claude-opus-4-6"
    # The service's own registry is untouched: the substitution is per launch,
    # never a mutation of operator configuration held for the process's life.
    assert profiles.get("claude-independent-reviewer").model is None
    # And the independent reviewer is still an independent reviewer. Replacing
    # a field re-runs __post_init__, so the fixed permission mode and the fixed
    # tool set are re-validated rather than inherited from an object that was
    # checked once, long ago.
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"


def test_a_role_hired_on_no_model_still_runs_the_profiles_own(tmp_path: Path) -> None:
    """The other half, and the one a bug would make silent: a role that named
    no model must be launched exactly as before, with the operator config's
    model reaching argv untouched.  Nothing may substitute an empty choice."""

    manager, task = _workspace(tmp_path)
    role = manager.create_agent(
        name="Profile-model reviewer",
        profile_id="claude-independent-reviewer",
        rationale="hired without naming a model",
        idempotency_key="runtime-nomodel-role",
    )
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key="runtime-nomodel-review",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="runtime-nomodel-delegation",
    )
    delegation_id = delegation["entity_ref"]["id"]
    seen: list[tuple[str, ...]] = []

    class CapturingRunner(FakeRunner):
        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            seen.append(tuple(invocation.argv))
            return super().run(invocation, **values)

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="runtime-nomodel-child-review",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="runtime-nomodel-child-succeed",
        )

    configured = ProfileRegistry(
        {
            BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER: ClaudeRunnerProfile(
                profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
                executable="/bin/echo",
                mcp_executable="/bin/echo",
                model="claude-sonnet-4-5",
                permission_mode=ClaudePermissionMode.DONT_ASK,
            )
        }
    )
    result = DelegationRuntimeService(
        manager,
        runner=CapturingRunner(after_start=complete_as_child),  # type: ignore[arg-type]
        profiles=configured,
        telemetry=CollectingTelemetry(),
    ).run(delegation_id, delegation["revision"], idempotency_key="runtime-nomodel-launch")

    assert result["delegation"]["state"] == "succeeded"
    argv = seen[0]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"


def test_prestart_failure_can_retry_only_until_attempt_limit(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task, max_attempts=2)
    delegation_id = delegation["entity_ref"]["id"]
    runner = FakeRunner(outcome=RunOutcome.FAILED, reason=RunReason.START_FAILED)
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    first = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert first["delegation"]["state"] == "requested"
    assert first["attempt"]["number"] == 1

    replay = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert replay["reused"] is True
    assert runner.calls == 1

    exhausted = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
        retry=True,
    )
    assert exhausted["attempt"]["number"] == 2
    assert exhausted["delegation"]["state"] == "failed"
    assert exhausted["delegation"]["reason_code"] == "launch_failed"

    terminal_replay = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert terminal_replay["reused"] is True
    assert terminal_replay["delegation"]["state"] == "failed"
    with pytest.raises(LifecycleConflictError, match="requested state"):
        service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="runtime-launch-retry",
            retry=True,
        )


def test_independent_review_instruction_requires_both_canonical_terminal_calls(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    service = DelegationRuntimeService(manager)

    instruction = service._instruction(
        manager.get_delegation(delegation["entity_ref"]["id"]),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
    )

    complete = instruction.index("commons_complete_review")
    succeed = instruction.index("commons_succeed_delegation")
    assert complete < succeed
    assert "review:<id>" in instruction
    assert "Delegation revision at launch" not in instruction
    compact_instruction = instruction.replace("\n", " ")
    assert "immediately before every delegation outcome call" in compact_instruction
    assert "fetch the delegation again with commons_show_delegation" in compact_instruction
    assert "prose-only answer or successful process exit" in instruction
    assert "commons_delegation_needs_operator" in instruction
    assert "commons_delegation_input_needed" in instruction


def test_missing_terminal_audit_does_not_claim_no_tool_was_called() -> None:
    code = workflow_diagnostic_code(
        {
            "diagnostic_code": "none",
            "process_canonical_mismatch": True,
            "terminal_tool_calls": 0,
            "terminal_tool_rejections": 0,
            "terminal_tool_completions": 0,
            "terminal_tool_audit_available": False,
        }
    )

    assert code is DiagnosticCode.PROCESS_CANONICAL_MISMATCH


def test_successful_process_without_terminal_tool_gets_actionable_workflow_diagnostic(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    service = DelegationRuntimeService(
        manager,
        runner=FakeRunner(),  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="runtime-missing-terminal-tool",
    )
    diagnostic = service.list_attempts(diagnostic=True)[0]

    assert result["delegation"]["state"] == "needs_operator"
    assert result["workflow_diagnostic_code"] == "terminal_tool_not_called"
    assert result["safe_next_actions"]
    assert diagnostic["diagnostic_code"] == "none"
    assert diagnostic["workflow_diagnostic_code"] == "terminal_tool_not_called"
    assert diagnostic["safe_next_actions"]


def test_reconcile_maps_ambiguous_running_attempt_to_canonical_needs_operator(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]
    runner = FakeRunner(crash_after_start=True)
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    with pytest.raises(RuntimeError, match="simulated broker crash"):
        service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="runtime-launch-crash",
        )
    assert manager.get_delegation(delegation_id)["state"] == "active"

    reconciled = service.reconcile()
    assert reconciled[0]["reconciled"] is True
    assert reconciled[0]["attempt"]["state"] == "needs_operator"
    assert reconciled[0]["delegation"]["state"] == "needs_operator"
    assert reconciled[0]["delegation"]["reason_code"] == "orphaned"


def test_parent_session_ttl_must_cover_provider_and_finalization(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path, parent_ttl_seconds=60)
    _, delegation = _delegation(manager, task)
    runner = FakeRunner()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    with pytest.raises(LifecycleConflictError, match="TTL must cover"):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="runtime-parent-ttl-too-short",
        )

    assert runner.calls == 0
    assert service.list_attempts() == []


def test_parent_ttl_check_uses_the_session_registry_clock(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path, parent_ttl_seconds=60)
    _, delegation = _delegation(manager, task)
    service = DelegationRuntimeService(
        manager,
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )
    parent = manager.sessions.require_active(manager.session_id)
    parent_expiry = datetime.fromisoformat(parent.expires_at.replace("Z", "+00:00"))
    manager.sessions.clock = lambda: parent_expiry.timestamp() - 180

    child = service._open_child_session(
        manager.get_delegation(delegation["entity_ref"]["id"]),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
    )

    child_expiry = datetime.fromisoformat(child["expires_at"].replace("Z", "+00:00"))
    assert child_expiry > datetime.fromtimestamp(manager.sessions.clock(), tz=UTC)
