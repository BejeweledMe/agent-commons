from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
from itertools import count, repeat

import pytest

from agent_commons.domain.execution_plan import (
    MAX_PLAN_TASKS,
    CapacitySignal,
    CapacityState,
    CriticalPathBasis,
    ExecutionPlanEdge,
    PlanGap,
    PlanState,
    ReadinessState,
)
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import EvidenceState, FreshnessState, NextAction, RunPhase
from agent_commons.services.execution_plan import build_execution_plan

NOW = "2026-08-30T10:01:00Z"
CAPACITY = {"active": 1, "limit": 4, "queued": 0, "queue_capacity": 8}


def _task(
    task_id: str,
    *,
    state: str = "ready",
    dependencies: list[str] | object | None = None,
    owner: str | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "task_id": task_id,
        "state": state,
        "revision": f"evt.{task_id}.1",
        "effective_revision": f"evt.{task_id}.1",
        "recorded_at": "2026-08-30T10:00:55Z",
        "dependencies": [] if dependencies is None else dependencies,
        "owner_session_id": owner,
    }


def _delegation(
    task_id: str,
    *,
    state: str = "active",
    delegation_id: str = "delegation.1",
    profile_id: str = "claude-builder",
    agent_id: str = "agent.builder",
) -> dict[str, object]:
    return {
        "id": delegation_id,
        "delegation_id": delegation_id,
        "state": state,
        "revision": f"evt.{delegation_id}.1",
        "effective_revision": f"evt.{delegation_id}.1",
        "recorded_at": "2026-08-30T10:00:55Z",
        "target_ref": {"kind": "task", "id": task_id},
        "target_profile": profile_id,
        "agent_id": agent_id,
    }


def _attempt(
    *,
    state: str = "running",
    delegation_id: str = "delegation.1",
    attempt_id: str = "attempt.1",
    profile_id: str = "claude-builder",
    provider: str = "claude",
    created_at: str = "2026-08-30T10:00:30Z",
    updated_at: str = "2026-08-30T10:00:55Z",
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "number": 1,
        "profile_id": profile_id,
        "provider": provider,
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "correlation": {"delegation_id": delegation_id},
        "stderr_diagnostic_tail": "secret-must-not-cross-read-boundary",
        "argv": ["--provider-secret"],
        "token_count": 999,
        "cost": 12.34,
    }


def _snapshot() -> ProjectSnapshot:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.dep"] = _task("task.dep", state="accepted")  # type: ignore[assignment]
    snapshot.tasks["task.dep"]["acceptance_review"] = {  # type: ignore[index]
        "ref": {"kind": "review", "id": "review.dep"},
        "revision": "evt.review.dep.1",
    }
    snapshot.reviews["review.dep"] = {  # type: ignore[assignment]
        "id": "review.dep",
        "review_id": "review.dep",
        "state": "approved",
        "revision": "evt.review.dep.1",
        "effective_revision": "evt.review.dep.1",
        "recorded_at": "2026-08-30T10:00:56Z",
        "target_ref": {"kind": "task", "id": "task.dep"},
        "target_revision": "evt.task.dep.1",
        "independent": True,
        "stale": False,
    }
    snapshot.tasks["task.mid"] = _task(
        "task.mid", state="active", dependencies=["task.dep"], owner="session.owner"
    )  # type: ignore[assignment]
    snapshot.tasks["task.final"] = _task("task.final", dependencies=["task.mid"])  # type: ignore[assignment]
    snapshot.tasks["task.unrelated"] = _task("task.unrelated")  # type: ignore[assignment]
    snapshot.delegations["delegation.1"] = _delegation("task.mid")  # type: ignore[assignment]
    snapshot.agents["agent.builder"] = {  # type: ignore[assignment]
        "id": "agent.builder",
        "agent_id": "agent.builder",
        "name": "Implementation builder",
        "profile_id": "claude-builder",
        "recorded_at": "2026-08-30T10:00:55Z",
    }
    return snapshot


def test_focused_plan_is_deterministic_and_exposes_exact_readiness() -> None:
    snapshot = _snapshot()

    first = build_execution_plan(
        snapshot,
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=CAPACITY,
    )
    second = build_execution_plan(
        snapshot,
        reversed([_attempt()]),
        generated_at=NOW,
        focus_task_ids=reversed(["task.final"]),
        capacity=dict(reversed(list(CAPACITY.items()))),
    )

    assert first == second
    assert first.state is PlanState.COMPLETE
    assert first.freshness is FreshnessState.FRESH
    assert tuple(node.task_id for node in first.nodes) == (
        "task.dep",
        "task.final",
        "task.mid",
    )
    assert "task.unrelated" not in {node.task_id for node in first.nodes}
    assert first.critical_path_task_ids == ("task.dep", "task.mid", "task.final")
    assert first.critical_path_basis is CriticalPathBasis.DEPENDENCY_DEPTH_ONLY
    assert first.critical_path_predictive is False
    assert first.capacity.state is CapacityState.AVAILABLE

    mid = next(node for node in first.nodes if node.task_id == "task.mid")
    assert mid.readiness is ReadinessState.IN_PROGRESS
    assert mid.blocking_dependency_ids == ()
    assert mid.owner_session_id == "session.owner"
    assert mid.role_name == "Implementation builder"
    assert mid.provider == "claude"
    assert mid.profile_id == "claude-builder"
    assert mid.phase is RunPhase.RUNNING
    assert mid.next_action is NextAction.WAIT_FOR_RUN

    final = next(node for node in first.nodes if node.task_id == "task.final")
    assert final.readiness is ReadinessState.BLOCKED
    assert final.dependency_task_ids == ("task.mid",)
    assert final.blocking_dependency_ids == ("task.mid",)
    assert final.next_action is NextAction.RESOLVE_DEPENDENCIES

    rendered = repr(asdict(first))
    assert "secret-must-not-cross-read-boundary" not in rendered
    assert "provider-secret" not in rendered
    assert all(word not in rendered for word in ("token_count", "cost", "percentage", "eta"))


def test_cancelled_prerequisite_is_terminal_failure_not_a_guessed_unlock() -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.dep"]["state"] = "cancelled"  # type: ignore[index]

    plan = build_execution_plan(
        snapshot,
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.mid"],
        capacity=CAPACITY,
    )

    mid = next(node for node in plan.nodes if node.task_id == "task.mid")
    assert mid.readiness is ReadinessState.TERMINAL_DEPENDENCY_FAILURE
    assert mid.blocking_dependency_ids == ("task.dep",)
    assert mid.terminal_dependency_failure_ids == ("task.dep",)
    assert mid.policy_unknown_dependency_ids == ()
    assert mid.awaits_human is True
    assert mid.next_action is NextAction.INSPECT_MISSING_EVIDENCE
    assert PlanGap.TERMINAL_DEPENDENCY_FAILURE in plan.gaps
    with pytest.raises(ValueError, match="require human inspection"):
        replace(mid, awaits_human=False)


@pytest.mark.parametrize("state", ["failed", "timed_out", "needs_operator", "input_needed"])
def test_failed_or_paused_run_requires_human_attention(state: str) -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.mid"]["dependencies"] = []  # type: ignore[index]
    snapshot.delegations["delegation.1"]["state"] = state  # type: ignore[index]

    plan = build_execution_plan(
        snapshot,
        [_attempt(state=state)],
        generated_at=NOW,
        focus_task_ids=["task.mid"],
        capacity=CAPACITY,
    )

    node = plan.nodes[0]
    assert node.readiness is ReadinessState.HUMAN_ATTENTION
    assert node.awaits_human is True
    assert node.phase.value == state


def test_missing_attempt_source_and_resume_gap_are_honest_partial_state() -> None:
    plan = build_execution_plan(
        _snapshot(),
        None,
        generated_at=NOW,
        focus_task_ids=["task.mid"],
        resume_gap=True,
        capacity=CAPACITY,
    )

    assert plan.state is PlanState.PARTIAL
    assert plan.freshness is FreshnessState.STALE
    assert {PlanGap.ATTEMPTS_MISSING, PlanGap.RESUME_GAP} <= set(plan.gaps)
    mid = next(node for node in plan.nodes if node.task_id == "task.mid")
    assert mid.evidence_state in {EvidenceState.PARTIAL, EvidenceState.STALE}


def test_malformed_attempt_source_is_typed_partial_without_provider_payload() -> None:
    malformed = {
        "correlation": {"delegation_id": "delegation.1"},
        "state": "running",
        "created_at": "not-a-time",
        "stderr_diagnostic_tail": "provider-secret-must-not-render",
    }

    plan = build_execution_plan(
        _snapshot(),
        [malformed],
        generated_at=NOW,
        focus_task_ids=["task.mid"],
        capacity=CAPACITY,
    )

    assert plan.state is PlanState.PARTIAL
    assert PlanGap.ATTEMPTS_PARTIAL in plan.gaps
    assert "provider-secret-must-not-render" not in repr(asdict(plan))


def test_capacity_backpressure_is_observed_not_predicted() -> None:
    plan = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity={"active": 4, "limit": 4, "queued": 2, "queue_capacity": 8},
    )

    assert plan.capacity.state is CapacityState.BACKPRESSURE
    assert plan.capacity.active == 4
    assert plan.capacity.queued == 2


def test_capacity_is_copied_into_an_exact_frozen_value() -> None:
    source = dict(CAPACITY)
    plan = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=source,
    )
    source["active"] = 4
    source["queued"] = 8

    assert plan.capacity.active == 1
    assert plan.capacity.queued == 0
    with pytest.raises(FrozenInstanceError):
        plan.capacity.active = 2  # type: ignore[misc]
    with pytest.raises(TypeError, match="exact immutable CapacitySignal"):
        replace(plan, capacity=source)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="disagrees"):
        replace(plan.capacity, state=CapacityState.SATURATED)
    with pytest.raises(ValueError, match="cannot retain"):
        CapacitySignal(CapacityState.UNKNOWN, 1, 4, 0, 8)


def test_plan_enforces_edge_focus_and_path_membership() -> None:
    plan = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=CAPACITY,
    )

    with pytest.raises(ValueError, match="focus_task_ids must reference plan nodes"):
        replace(plan, focus_task_ids=("task.absent",))
    with pytest.raises(ValueError, match="critical path must follow dependency edges"):
        replace(plan, critical_path_task_ids=("task.final", "task.dep"))
    with pytest.raises(ValueError, match="edge dependent must reference a plan node"):
        replace(
            plan,
            edges=(
                *plan.edges,
                ExecutionPlanEdge("task.dep", "task.absent"),
            ),
        )


def test_missing_and_malformed_capacity_are_typed_without_invention() -> None:
    missing = build_execution_plan(
        _snapshot(), [_attempt()], generated_at=NOW, focus_task_ids=["task.final"]
    )
    malformed = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity={"active": 9, "limit": 2, "queued": 0, "queue_capacity": 1},
    )

    assert missing.capacity.state is CapacityState.UNKNOWN
    assert PlanGap.CAPACITY_MISSING in missing.gaps
    assert malformed.capacity.state is CapacityState.UNKNOWN
    assert PlanGap.CAPACITY_MALFORMED in malformed.gaps

    wrong_shape = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=[],  # type: ignore[arg-type]
    )
    assert wrong_shape.state is PlanState.PARTIAL
    assert PlanGap.CAPACITY_MALFORMED in wrong_shape.gaps


def test_cycle_returns_bounded_typed_error_and_no_critical_path() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.a"] = _task("task.a", dependencies=["task.b"])  # type: ignore[assignment]
    snapshot.tasks["task.b"] = _task("task.b", dependencies=["task.a"])  # type: ignore[assignment]

    plan = build_execution_plan(
        snapshot,
        [],
        generated_at=NOW,
        focus_task_ids=["task.a"],
        capacity=CAPACITY,
    )

    assert plan.state is PlanState.ERROR
    assert PlanGap.CYCLE_DETECTED in plan.gaps
    assert plan.critical_path_task_ids == ()
    assert len(plan.nodes) == 2
    assert len(plan.edges) == 2


def test_missing_dependency_stays_an_exact_blocker_and_partial_edge() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.a"] = _task("task.a", dependencies=["task.absent"])  # type: ignore[assignment]

    plan = build_execution_plan(
        snapshot,
        [],
        generated_at=NOW,
        focus_task_ids=["task.a"],
        capacity=CAPACITY,
    )

    assert plan.state is PlanState.PARTIAL
    assert PlanGap.DEPENDENCY_MISSING in plan.gaps
    assert plan.nodes[0].dependency_task_ids == ("task.absent",)
    assert plan.nodes[0].blocking_dependency_ids == ("task.absent",)
    assert plan.nodes[0].readiness is ReadinessState.POLICY_UNKNOWN
    assert plan.nodes[0].policy_unknown_dependency_ids == ("task.absent",)
    assert plan.nodes[0].awaits_human is True
    assert plan.nodes[0].next_action is NextAction.INSPECT_MISSING_EVIDENCE
    assert PlanGap.DEPENDENCY_POLICY_UNKNOWN in plan.gaps
    assert plan.edges[0].prerequisite_missing is True
    with pytest.raises(ValueError, match="require human inspection"):
        replace(plan.nodes[0], awaits_human=False)


@pytest.mark.parametrize(
    "dependencies",
    [
        "task.not-a-list",
        ["bad\nidentifier"],
        [f"task.{index}" for index in range(MAX_PLAN_TASKS + 1)],
    ],
)
def test_hostile_dependency_inputs_fail_bounded_with_typed_error(dependencies: object) -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.a"] = _task("task.a", dependencies=dependencies)  # type: ignore[assignment]

    plan = build_execution_plan(
        snapshot,
        [],
        generated_at=NOW,
        focus_task_ids=["task.a"],
        capacity=CAPACITY,
    )

    assert plan.state is PlanState.ERROR
    assert {PlanGap.TASK_MALFORMED, PlanGap.DEPENDENCIES_TRUNCATED} & set(plan.gaps)
    assert len(plan.nodes) <= MAX_PLAN_TASKS
    assert len(plan.edges) <= 4_096
    node = next(value for value in plan.nodes if value.task_id == "task.a")
    assert node.readiness in {ReadinessState.UNKNOWN, ReadinessState.POLICY_UNKNOWN}
    assert node.readiness is not ReadinessState.READY
    assert node.next_action is NextAction.INSPECT_MISSING_EVIDENCE
    assert node.next_action is not NextAction.START_READY_WORK
    assert node.awaits_human is True


class _CountingForever:
    def __init__(self, prefix: str, hard_limit: int) -> None:
        self.prefix = prefix
        self.hard_limit = hard_limit
        self.seen = 0

    def __iter__(self) -> _CountingForever:
        return self

    def __next__(self) -> str:
        self.seen += 1
        if self.seen > self.hard_limit:
            raise AssertionError("bounded reader consumed beyond max+1")
        return f"{self.prefix}.{self.seen}"


class _SecretRuntimeError:
    def __iter__(self):  # type: ignore[no-untyped-def]
        yield "task.safe"
        raise RuntimeError("SECRET_ITERATOR_DETAIL_MUST_NOT_ECHO")


def test_infinite_focus_attempt_and_domain_iterables_stop_at_max_plus_one() -> None:
    focus = _CountingForever("task", 65)
    focus_plan = build_execution_plan(
        _snapshot(),
        [],
        generated_at=NOW,
        focus_task_ids=focus,
        capacity=CAPACITY,
    )
    assert focus.seen == 65
    assert focus_plan.state is PlanState.ERROR
    assert PlanGap.PLAN_TRUNCATED in focus_plan.gaps

    attempts = count()
    attempt_plan = build_execution_plan(_snapshot(), attempts, generated_at=NOW, capacity=CAPACITY)
    assert attempt_plan.state is PlanState.PARTIAL
    assert PlanGap.ATTEMPTS_PARTIAL in attempt_plan.gaps
    assert next(attempts) == 2_049

    dependency_snapshot = _snapshot()
    dependency_snapshot.tasks["task.final"]["dependencies"] = repeat("task.mid")  # type: ignore[index]
    dependency_plan = build_execution_plan(
        dependency_snapshot,
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=CAPACITY,
    )
    assert dependency_plan.state is PlanState.ERROR
    assert PlanGap.DEPENDENCIES_TRUNCATED in dependency_plan.gaps

    ordinary = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        capacity=CAPACITY,
    )
    with pytest.raises(ValueError, match="dependency_task_ids exceeds"):
        replace(ordinary.nodes[0], dependency_task_ids=repeat("task.dep"))  # type: ignore[arg-type]


@pytest.mark.parametrize("source", ["focus", "attempts", "dependencies"])
def test_secret_iterator_errors_become_no_echo_typed_states(source: str) -> None:
    snapshot = _snapshot()
    focus: object = ["task.final"]
    attempts: object = [_attempt()]
    if source == "focus":
        focus = _SecretRuntimeError()
    elif source == "attempts":
        attempts = _SecretRuntimeError()
    else:
        snapshot.tasks["task.final"]["dependencies"] = _SecretRuntimeError()  # type: ignore[index]

    plan = build_execution_plan(
        snapshot,
        attempts,  # type: ignore[arg-type]
        generated_at=NOW,
        focus_task_ids=focus,  # type: ignore[arg-type]
        capacity=CAPACITY,
    )

    if source == "attempts":
        assert PlanGap.ATTEMPTS_PARTIAL in plan.gaps
    elif source == "focus":
        assert PlanGap.FOCUS_INPUT_MALFORMED in plan.gaps
    else:
        assert PlanGap.TASK_MALFORMED in plan.gaps
    assert "SECRET_ITERATOR_DETAIL_MUST_NOT_ECHO" not in repr(asdict(plan))


def test_newest_run_uses_rfc3339_instant_then_delegation_id_tie_break() -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.mid"]["dependencies"] = []  # type: ignore[index]
    snapshot.delegations.clear()
    snapshot.agents["agent.codex"] = {  # type: ignore[assignment]
        "id": "agent.codex",
        "agent_id": "agent.codex",
        "name": "Codex builder",
        "profile_id": "codex-builder",
        "recorded_at": "2026-08-30T10:00:55Z",
    }
    snapshot.agents["agent.codex-z"] = {  # type: ignore[assignment]
        "id": "agent.codex-z",
        "agent_id": "agent.codex-z",
        "name": "Codex Z builder",
        "profile_id": "codex-builder",
        "recorded_at": "2026-08-30T10:00:55Z",
    }
    snapshot.delegations["delegation.a"] = _delegation(  # type: ignore[assignment]
        "task.mid", delegation_id="delegation.a"
    )
    snapshot.delegations["delegation.y"] = _delegation(  # type: ignore[assignment]
        "task.mid",
        delegation_id="delegation.y",
        profile_id="codex-builder",
        agent_id="agent.codex",
    )
    snapshot.delegations["delegation.z"] = _delegation(  # type: ignore[assignment]
        "task.mid",
        delegation_id="delegation.z",
        profile_id="codex-builder",
        agent_id="agent.codex-z",
    )
    attempts = [
        _attempt(
            delegation_id="delegation.a",
            attempt_id="attempt.a",
            created_at="2026-08-30T07:59:00Z",
            updated_at="2026-08-30T10:00:00+02:00",
        ),
        _attempt(
            delegation_id="delegation.y",
            attempt_id="attempt.y",
            profile_id="codex-builder",
            provider="codex",
            created_at="2026-08-30T09:00:00Z",
            updated_at="2026-08-30T09:30:00Z",
        ),
        _attempt(
            delegation_id="delegation.z",
            attempt_id="attempt.z",
            profile_id="codex-builder",
            provider="codex",
            created_at="2026-08-30T09:00:00Z",
            updated_at="2026-08-30T11:30:00+02:00",
        ),
    ]

    plan = build_execution_plan(
        snapshot,
        attempts,
        generated_at=NOW,
        focus_task_ids=["task.mid"],
        stale_after_seconds=10_000,
        capacity=CAPACITY,
    )

    node = next(value for value in plan.nodes if value.task_id == "task.mid")
    assert node.provider == "codex"
    assert node.profile_id == "codex-builder"
    assert node.role_name == "Codex Z builder"


def test_oversized_projection_and_missing_projection_return_empty_typed_errors() -> None:
    oversized = ProjectSnapshot(workspace_id="workspace.1")
    for index in range(MAX_PLAN_TASKS + 1):
        task_id = f"task.{index}"
        oversized.tasks[task_id] = _task(task_id)  # type: ignore[assignment]

    too_large = build_execution_plan(oversized, [], generated_at=NOW, capacity=CAPACITY)
    absent = build_execution_plan(None, None, generated_at=NOW, capacity=CAPACITY)

    assert too_large.state is PlanState.ERROR
    assert too_large.nodes == ()
    assert PlanGap.PLAN_TRUNCATED in too_large.gaps
    assert absent.state is PlanState.ERROR
    assert PlanGap.PROJECTION_MISSING in absent.gaps


def test_malformed_and_stale_graph_sources_are_typed() -> None:
    malformed = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        graph={"generated_at": "tomorrow", "limits": {}},
        capacity=CAPACITY,
    )
    stale = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        focus_task_ids=["task.final"],
        graph={"generated_at": "2026-08-30T09:00:00Z", "limits": {"truncated": False}},
        capacity=CAPACITY,
    )

    assert malformed.state is PlanState.ERROR
    assert PlanGap.GRAPH_MALFORMED in malformed.gaps
    assert stale.state is PlanState.PARTIAL
    assert stale.freshness is FreshnessState.STALE
    assert PlanGap.GRAPH_STALE in stale.gaps

    wrong_limit_type = build_execution_plan(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        graph={"generated_at": NOW, "limits": {"truncated": "false"}},
        capacity=CAPACITY,
    )
    assert wrong_limit_type.state is PlanState.ERROR
    assert PlanGap.GRAPH_MALFORMED in wrong_limit_type.gaps
