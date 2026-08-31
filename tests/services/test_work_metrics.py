from __future__ import annotations

from dataclasses import asdict

import pytest

from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import (
    MAX_BLOCKING_DEPENDENCIES,
    AcceptanceState,
    EvidenceState,
    FreshnessState,
    NextAction,
    ReviewGapKind,
    RunPhase,
    WorkHealthState,
    WorkSourceGap,
)
from agent_commons.services.work_metrics import (
    MAX_ATTEMPT_INPUTS,
    WorkMetricsInputError,
    build_work_health,
)

NOW = "2026-08-30T10:01:00Z"


def _task(
    task_id: str = "task.1",
    *,
    state: str = "active",
    revision: str = "evt.task.1",
    dependencies: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "task_id": task_id,
        "state": state,
        "revision": revision,
        "effective_revision": revision,
        "recorded_at": "2026-08-30T10:00:55Z",
        "dependencies": dependencies or [],
    }


def _delegation(
    delegation_id: str = "delegation.1",
    *,
    task_id: str = "task.1",
    state: str = "active",
) -> dict[str, object]:
    return {
        "id": delegation_id,
        "delegation_id": delegation_id,
        "state": state,
        "revision": "evt.delegation.2",
        "effective_revision": "evt.delegation.2",
        "recorded_at": "2026-08-30T10:00:55Z",
        "target_ref": {"kind": "task", "id": task_id},
        "target_profile": "claude-builder",
        "agent_id": "agent.1",
    }


def _attempt(
    *,
    state: str = "running",
    number: int = 1,
    updated_at: str = "2026-08-30T10:00:55Z",
    created_at: str = "2026-08-30T10:00:30Z",
    delegation_id: str = "delegation.1",
) -> dict[str, object]:
    return {
        "attempt_id": f"attempt.{number}",
        "number": number,
        "profile_id": "claude-builder",
        "provider": "claude",
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "correlation": {"delegation_id": delegation_id},
        # These values must never cross into a Work view.
        "stderr_diagnostic_tail": "secret-token-should-not-render",
        "argv": ["--dangerous-provider-flag"],
        "env": {"API_KEY": "secret"},
    }


def _snapshot() -> ProjectSnapshot:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.dep"] = _task("task.dep", state="active", revision="evt.dep.1")  # type: ignore[assignment]
    snapshot.tasks["task.1"] = _task(dependencies=["task.dep"])  # type: ignore[assignment]
    snapshot.delegations["delegation.1"] = _delegation()  # type: ignore[assignment]
    snapshot.agents["agent.1"] = {  # type: ignore[assignment]
        "id": "agent.1",
        "agent_id": "agent.1",
        "name": "Backend builder",
        "profile_id": "claude-builder",
        "recorded_at": "2026-08-30T10:00:55Z",
    }
    return snapshot


def _review(
    *,
    state: str = "approved",
    target_revision: str = "evt.task.1",
    independent: bool = True,
    stale: bool = False,
    review_id: str = "review.1",
) -> dict[str, object]:
    return {
        "id": review_id,
        "review_id": review_id,
        "state": state,
        "revision": "evt.review.2",
        "effective_revision": "evt.review.2",
        "recorded_at": "2026-08-30T10:00:58Z",
        "target_ref": {"kind": "task", "id": "task.1"},
        "target_revision": target_revision,
        "independent": independent,
        "stale": stale,
    }


def test_empty_snapshot_is_honestly_empty_and_not_fresh() -> None:
    health = build_work_health(ProjectSnapshot(), generated_at=NOW)

    assert health.state is WorkHealthState.EMPTY
    assert health.evidence_state is EvidenceState.MISSING
    assert health.freshness is FreshnessState.UNKNOWN
    assert health.source_updated_at is None
    assert health.runs == ()
    assert health.acceptances == ()


def test_run_join_is_deterministic_bounded_and_provider_safe() -> None:
    snapshot = _snapshot()
    attempts = [
        _attempt(number=2, state="running"),
        _attempt(number=1, state="failed", updated_at="2026-08-30T10:00:50Z"),
    ]

    first = build_work_health(snapshot, attempts, generated_at=NOW)
    second = build_work_health(snapshot, reversed(attempts), generated_at=NOW)

    assert first == second
    run = first.runs[0]
    assert run.agent_id == "agent.1"
    assert run.role_name == "Backend builder"
    assert run.provider == "claude"
    assert run.profile_id == "claude-builder"
    assert run.phase is RunPhase.RUNNING
    assert run.attempt_id == "attempt.2"
    assert run.duration_seconds == 30
    assert run.blocking_dependencies == ("task.dep",)
    assert run.next_action is NextAction.RESOLVE_DEPENDENCIES
    assert "secret-token-should-not-render" not in repr(asdict(first))
    assert "dangerous-provider-flag" not in repr(asdict(first))
    assert "API_KEY" not in repr(asdict(first))


@pytest.mark.parametrize(
    ("state", "phase", "awaits_human", "action"),
    [
        ("failed", RunPhase.FAILED, True, NextAction.INSPECT_FAILURE),
        ("timed_out", RunPhase.TIMED_OUT, True, NextAction.INSPECT_FAILURE),
        (
            "needs_operator",
            RunPhase.NEEDS_OPERATOR,
            True,
            NextAction.ANSWER_OPERATOR_REQUEST,
        ),
    ],
)
def test_terminal_failures_are_explicit(
    state: str, phase: RunPhase, awaits_human: bool, action: NextAction
) -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.1"]["dependencies"] = []  # type: ignore[index]
    snapshot.delegations["delegation.1"]["state"] = state  # type: ignore[index]

    health = build_work_health(snapshot, [_attempt(state=state)], generated_at=NOW)

    run = health.runs[0]
    assert run.phase is phase
    assert run.awaits_human is awaits_human
    assert run.next_action is action
    assert run.finished_at == "2026-08-30T10:00:55Z"
    assert health.terminal_failure_count == 1


def test_operational_success_cannot_outrun_canonical_active_state() -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.1"]["dependencies"] = []  # type: ignore[index]

    health = build_work_health(snapshot, [_attempt(state="succeeded")], generated_at=NOW)

    run = health.runs[0]
    assert run.canonical_state == "active"
    assert run.phase is RunPhase.RUNNING
    assert run.phase is not RunPhase.SUCCEEDED
    assert run.finished_at is None
    assert "canonical_attempt_state_match" in run.missing_fields
    assert run.awaits_human is True
    assert run.next_action is NextAction.INSPECT_MISSING_EVIDENCE
    assert health.terminal_failure_count == 0
    assert health.state is WorkHealthState.PARTIAL


def test_partial_join_and_stale_snapshot_are_typed() -> None:
    snapshot = _snapshot()
    snapshot.agents.clear()
    snapshot.delegations["delegation.1"]["target_profile"] = "unknown-provider"  # type: ignore[index]
    stale_attempt = _attempt(
        created_at="2026-08-30T08:59:30Z",
        updated_at="2026-08-30T09:00:00Z",
    )
    for collection in (snapshot.tasks, snapshot.delegations):
        for record in collection.values():
            record["recorded_at"] = "2026-08-30T09:00:00Z"  # type: ignore[index]

    health = build_work_health(snapshot, [stale_attempt], generated_at=NOW)

    run = health.runs[0]
    assert run.freshness is FreshnessState.STALE
    assert run.evidence_state is EvidenceState.STALE
    assert {"agent", "attempt_profile_match"} <= set(run.missing_fields)
    assert health.state is WorkHealthState.PARTIAL
    assert health.evidence_state is EvidenceState.PARTIAL


def test_fresh_graph_does_not_mask_stale_canonical_and_attempt_evidence() -> None:
    snapshot = _snapshot()
    for collection in (snapshot.tasks, snapshot.delegations, snapshot.agents):
        for record in collection.values():
            record["recorded_at"] = "2026-08-30T09:00:00Z"  # type: ignore[index]
    attempt = _attempt(
        created_at="2026-08-30T09:00:00Z",
        updated_at="2026-08-30T09:00:10Z",
    )

    health = build_work_health(
        snapshot,
        [attempt],
        generated_at=NOW,
        graph={"generated_at": NOW, "limits": {"truncated": False}},
    )

    assert health.freshness is FreshnessState.STALE
    assert health.runs[0].freshness is FreshnessState.STALE
    assert health.runs[0].evidence_state is EvidenceState.STALE
    assert health.state is WorkHealthState.STALE


def test_malformed_attempt_is_partial_without_canonical_fallback_masking() -> None:
    snapshot = _snapshot()
    malformed = {
        "correlation": {"delegation_id": "delegation.1"},
        "created_at": "2026-08-30T10:00:30Z",
        "updated_at": "2026-08-30T10:00:55Z",
    }

    health = build_work_health(snapshot, [malformed], generated_at=NOW)

    run = health.runs[0]
    assert run.phase is RunPhase.RUNNING
    assert run.profile_id == "claude-builder"
    assert {
        "attempt_id",
        "attempt_number",
        "attempt_state",
        "attempt_profile_id",
        "attempt_provider",
    } <= set(run.missing_fields)
    assert run.evidence_state is EvidenceState.PARTIAL
    assert run.next_action is NextAction.INSPECT_MISSING_EVIDENCE
    assert WorkSourceGap.ATTEMPT_EVIDENCE_MISSING in health.source_gaps
    assert health.state is WorkHealthState.PARTIAL


@pytest.mark.parametrize("bad_dependencies", ["task.dep", {"task.dep": True}])
def test_invalid_dependency_collections_do_not_create_edges(bad_dependencies: object) -> None:
    snapshot = _snapshot()
    snapshot.tasks["task.1"]["dependencies"] = bad_dependencies  # type: ignore[index]

    health = build_work_health(snapshot, [_attempt()], generated_at=NOW)

    run = health.runs[0]
    assert run.blocking_dependencies == ()
    assert "task_dependencies" in run.missing_fields
    assert WorkSourceGap.TASK_DEPENDENCIES_INVALID in health.source_gaps
    assert health.state is WorkHealthState.PARTIAL


@pytest.mark.parametrize(
    ("created_at", "updated_at", "source_gap"),
    [
        (
            "2026-08-30T10:00:50Z",
            "2026-08-30T10:00:40Z",
            WorkSourceGap.SOURCE_TIMESTAMP_ORDER_INVALID,
        ),
        (
            "2026-08-30T10:02:00Z",
            "2026-08-30T10:03:00Z",
            WorkSourceGap.SOURCE_TIMESTAMP_FUTURE,
        ),
    ],
)
def test_impossible_attempt_time_has_unknown_duration_and_typed_gap(
    created_at: str, updated_at: str, source_gap: WorkSourceGap
) -> None:
    snapshot = _snapshot()
    snapshot.delegations["delegation.1"]["state"] = "failed"  # type: ignore[index]

    health = build_work_health(
        snapshot,
        [_attempt(state="failed", created_at=created_at, updated_at=updated_at)],
        generated_at=NOW,
    )

    run = health.runs[0]
    assert run.duration_seconds is None
    assert "attempt_timestamp_order" in run.missing_fields
    assert run.freshness is FreshnessState.UNKNOWN
    assert health.freshness is FreshnessState.UNKNOWN
    assert source_gap in health.source_gaps
    assert run.evidence_state is EvidenceState.PARTIAL
    assert health.state is WorkHealthState.PARTIAL


def test_unknown_profile_and_wide_dependencies_degrade_without_breaking_view() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    dependencies = [f"task.dep.{index:04d}" for index in range(MAX_BLOCKING_DEPENDENCIES + 3)]
    snapshot.tasks["task.1"] = _task(dependencies=dependencies)  # type: ignore[assignment]
    snapshot.delegations["delegation.1"] = _delegation(state="requested")  # type: ignore[assignment]
    snapshot.delegations["delegation.1"]["target_profile"] = "corrupt-profile"  # type: ignore[index]

    health = build_work_health(snapshot, generated_at=NOW)

    run = health.runs[0]
    assert run.profile_id is None
    assert run.provider is None
    assert len(run.blocking_dependencies) == MAX_BLOCKING_DEPENDENCIES
    assert {"known_profile", "blocking_dependencies_truncated"} <= set(run.missing_fields)
    assert health.state is WorkHealthState.PARTIAL


def test_dependency_iterable_is_consumed_only_through_limit_plus_one() -> None:
    class HostileDependencies:
        def __init__(self) -> None:
            self.calls = 0

        def __iter__(self) -> HostileDependencies:
            return self

        def __next__(self) -> str:
            self.calls += 1
            if self.calls > MAX_BLOCKING_DEPENDENCIES + 1:
                raise AssertionError("dependencies consumed beyond bounded probe")
            return f"task.hostile.{self.calls:04d}"

    dependencies = HostileDependencies()
    snapshot = _snapshot()
    snapshot.tasks["task.1"]["dependencies"] = dependencies  # type: ignore[index]

    health = build_work_health(snapshot, [_attempt()], generated_at=NOW)

    run = health.runs[0]
    assert dependencies.calls == MAX_BLOCKING_DEPENDENCIES + 1
    assert len(run.blocking_dependencies) == MAX_BLOCKING_DEPENDENCIES
    assert "blocking_dependencies_truncated" in run.missing_fields
    assert WorkSourceGap.TASK_DEPENDENCIES_TRUNCATED in health.source_gaps
    assert health.state is WorkHealthState.PARTIAL


def test_unsafe_attempt_and_dependency_ids_degrade_without_echo_or_abort() -> None:
    unsafe_attempt_id = "x" * 300 + "\nprovider-secret"
    unsafe_dependency = "y" * 300
    control_dependency = "task.bad\nsecret"
    snapshot = _snapshot()
    snapshot.tasks["task.1"]["dependencies"] = [  # type: ignore[index]
        "task.dep",
        unsafe_dependency,
        control_dependency,
    ]
    attempt = _attempt()
    attempt["attempt_id"] = unsafe_attempt_id

    health = build_work_health(snapshot, [attempt], generated_at=NOW)

    run = health.runs[0]
    assert run.attempt_id is None
    assert run.blocking_dependencies == ("task.dep",)
    assert {"attempt_id", "task_dependencies"} <= set(run.missing_fields)
    assert WorkSourceGap.ATTEMPT_EVIDENCE_MISSING in health.source_gaps
    assert WorkSourceGap.TASK_DEPENDENCIES_INVALID in health.source_gaps
    rendered = repr(asdict(health))
    assert unsafe_attempt_id not in rendered
    assert unsafe_dependency not in rendered
    assert control_dependency not in rendered
    assert health.state is WorkHealthState.PARTIAL


@pytest.mark.parametrize(
    ("review", "kind", "action", "evidence"),
    [
        (
            _review(stale=True),
            ReviewGapKind.STALE_REVIEW,
            NextAction.REQUEST_REVIEW,
            EvidenceState.STALE,
        ),
        (
            _review(target_revision="evt.old"),
            ReviewGapKind.TARGET_REVISION_MISMATCH,
            NextAction.REQUEST_REVIEW,
            EvidenceState.COMPLETE,
        ),
        (
            _review(independent=False),
            ReviewGapKind.NON_INDEPENDENT_REVIEW,
            NextAction.REQUEST_REVIEW,
            EvidenceState.COMPLETE,
        ),
        (
            _review(state="changes_requested"),
            ReviewGapKind.CHANGES_REQUESTED,
            NextAction.REVISE_WORK,
            EvidenceState.COMPLETE,
        ),
    ],
)
def test_review_loop_gaps_are_revision_bound(
    review: dict[str, object],
    kind: ReviewGapKind,
    action: NextAction,
    evidence: EvidenceState,
) -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.1"] = _task(state="review")  # type: ignore[assignment]
    snapshot.reviews["review.1"] = review  # type: ignore[assignment]

    health = build_work_health(snapshot, generated_at=NOW)

    acceptance = health.acceptances[0]
    assert acceptance.next_action is action
    assert acceptance.evidence_state is evidence
    assert health.review_gaps[0].kind is kind
    assert health.review_gaps[0].task_revision == "evt.task.1"


def test_exact_current_approval_and_accepted_binding() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.1"] = _task(state="review")  # type: ignore[assignment]
    snapshot.reviews["review.1"] = _review()  # type: ignore[assignment]

    approved = build_work_health(snapshot, generated_at=NOW).acceptances[0]

    assert approved.state is AcceptanceState.APPROVED
    assert approved.next_action is NextAction.ACCEPT_TASK
    snapshot.tasks["task.1"]["state"] = "accepted"  # type: ignore[index]
    snapshot.tasks["task.1"]["acceptance_review"] = {  # type: ignore[index]
        "ref": {"kind": "review", "id": "review.1"},
        "revision": "evt.review.2",
    }
    accepted = build_work_health(snapshot, generated_at=NOW).acceptances[0]
    assert accepted.state is AcceptanceState.ACCEPTED
    assert accepted.next_action is NextAction.NONE


def test_missing_review_and_missing_accepted_binding_are_not_conflated() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.1"] = _task(state="review")  # type: ignore[assignment]

    pending = build_work_health(snapshot, generated_at=NOW)
    assert pending.acceptances[0].state is AcceptanceState.REVIEW_REQUIRED
    assert pending.review_gaps[0].kind is ReviewGapKind.MISSING_REVIEW

    snapshot.tasks["task.1"]["state"] = "accepted"  # type: ignore[index]
    incomplete = build_work_health(snapshot, generated_at=NOW)
    assert incomplete.acceptances[0].state is AcceptanceState.EVIDENCE_INCOMPLETE
    assert incomplete.acceptances[0].evidence_state is EvidenceState.MISSING
    assert incomplete.review_gaps[0].kind is ReviewGapKind.REVIEW_EVIDENCE_MISSING


def test_graph_truncation_marks_aggregate_partial_without_becoming_truth() -> None:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.1"] = _task()  # type: ignore[assignment]
    graph = {
        "generated_at": "2026-08-30T10:00:59Z",
        "limits": {"truncated": True},
        "nodes": [{"provider_output": "must be ignored"}],
    }

    health = build_work_health(snapshot, generated_at=NOW, graph=graph)

    assert health.state is WorkHealthState.PARTIAL
    assert health.evidence_state is EvidenceState.PARTIAL
    assert health.source_gaps == (WorkSourceGap.GRAPH_TRUNCATED,)
    assert "must be ignored" not in repr(asdict(health))


def test_orphan_attempt_marks_missing_projection_without_creating_a_run() -> None:
    health = build_work_health(
        ProjectSnapshot(),
        [_attempt(delegation_id="delegation.orphan")],
        generated_at=NOW,
    )

    assert health.runs == ()
    assert health.state is WorkHealthState.PARTIAL
    assert health.evidence_state is EvidenceState.PARTIAL
    assert health.source_gaps == (WorkSourceGap.ORPHAN_OPERATIONAL_ATTEMPT,)


def test_invalid_clock_and_unbounded_input_are_refused() -> None:
    with pytest.raises(WorkMetricsInputError, match="RFC 3339"):
        build_work_health(ProjectSnapshot(), generated_at="not-a-clock")
    with pytest.raises(WorkMetricsInputError, match="cannot be negative"):
        build_work_health(ProjectSnapshot(), generated_at=NOW, stale_after_seconds=-1)


def test_attempt_iterable_is_consumed_only_through_limit_plus_one() -> None:
    class HostileAttempts:
        def __init__(self) -> None:
            self.calls = 0

        def __iter__(self) -> HostileAttempts:
            return self

        def __next__(self) -> dict[str, object]:
            self.calls += 1
            if self.calls > MAX_ATTEMPT_INPUTS + 1:
                raise AssertionError("attempt iterable was consumed past the bounded probe")
            return {}

    attempts = HostileAttempts()

    with pytest.raises(WorkMetricsInputError, match="bounded join limit"):
        build_work_health(ProjectSnapshot(), attempts, generated_at=NOW)

    assert attempts.calls == MAX_ATTEMPT_INPUTS + 1
