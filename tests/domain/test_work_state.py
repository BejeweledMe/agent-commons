from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from agent_commons.domain.work_state import (
    MAX_BLOCKING_DEPENDENCIES,
    MAX_RUNS,
    AcceptanceState,
    AcceptanceView,
    EvidenceState,
    FreshnessState,
    NextAction,
    ReviewGapKind,
    ReviewLoopGap,
    RunPhase,
    RunView,
    WorkHealth,
    WorkHealthState,
    WorkSourceGap,
)


def _run(**changes: object) -> RunView:
    values: dict[str, object] = {
        "delegation_id": "delegation.1",
        "task_id": "task.1",
        "agent_id": "agent.1",
        "role_name": "Builder",
        "provider": "claude",
        "profile_id": "claude-builder",
        "canonical_state": "active",
        "phase": RunPhase.RUNNING,
        "attempt_id": "attempt.1",
        "attempt_number": 1,
        "started_at": "2026-08-30T10:00:00Z",
        "updated_at": "2026-08-30T10:00:10Z",
        "finished_at": None,
        "duration_seconds": 10,
        "awaits_human": False,
        "blocking_dependencies": (),
        "next_action": NextAction.WAIT_FOR_RUN,
        "freshness": FreshnessState.FRESH,
        "evidence_state": EvidenceState.COMPLETE,
    }
    values.update(changes)
    return RunView(**values)  # type: ignore[arg-type]


def _acceptance() -> AcceptanceView:
    return AcceptanceView(
        task_id="task.1",
        task_state="review",
        task_revision="evt.task.1",
        state=AcceptanceState.APPROVED,
        review_id="review.1",
        review_state="approved",
        review_revision="evt.review.2",
        review_target_revision="evt.task.1",
        independent=True,
        stale=False,
        next_action=NextAction.ACCEPT_TASK,
        evidence_state=EvidenceState.COMPLETE,
    )


def test_run_view_is_frozen_and_deeply_owns_sequences() -> None:
    dependencies = ["task.a"]
    missing = ["agent"]
    run = _run(
        blocking_dependencies=dependencies,
        missing_fields=missing,
        evidence_state=EvidenceState.PARTIAL,
    )

    dependencies.append("task.b")
    missing.append("profile_id")

    assert run.blocking_dependencies == ("task.a",)
    assert run.missing_fields == ("agent",)
    with pytest.raises(FrozenInstanceError):
        run.phase = RunPhase.FAILED  # type: ignore[misc]


def test_work_health_deeply_owns_nested_collections() -> None:
    runs = [_run()]
    acceptances = [
        replace(
            _acceptance(),
            state=AcceptanceState.REVIEW_REQUIRED,
            review_id=None,
            review_state=None,
            review_revision=None,
            review_target_revision=None,
            independent=None,
            stale=None,
            next_action=NextAction.REQUEST_REVIEW,
        )
    ]
    gaps = [
        ReviewLoopGap(
            task_id="task.1",
            task_revision="evt.task.1",
            kind=ReviewGapKind.MISSING_REVIEW,
            review_id=None,
            review_revision=None,
            next_action=NextAction.REQUEST_REVIEW,
        )
    ]
    source_gaps = [WorkSourceGap.GRAPH_TRUNCATED]
    expected_acceptance = acceptances[0]
    health = WorkHealth(
        generated_at="2026-08-30T10:00:10Z",
        source_updated_at="2026-08-30T10:00:10Z",
        state=WorkHealthState.PARTIAL,
        freshness=FreshnessState.FRESH,
        evidence_state=EvidenceState.PARTIAL,
        task_count=1,
        run_count=1,
        attention_count=1,
        blocked_task_count=0,
        stale_evidence_count=0,
        terminal_failure_count=0,
        runs=runs,
        acceptances=acceptances,
        review_gaps=gaps,
        source_gaps=source_gaps,
    )

    runs.clear()
    acceptances.clear()
    gaps.clear()
    source_gaps.clear()

    assert health.runs == (_run(),)
    assert health.acceptances == (expected_acceptance,)
    assert len(health.review_gaps) == 1
    assert health.source_gaps == (WorkSourceGap.GRAPH_TRUNCATED,)


def test_models_enforce_closed_states_and_bounds() -> None:
    with pytest.raises(ValueError, match="RunPhase"):
        _run(phase="provider_says_done")
    with pytest.raises(ValueError, match="blocking_dependencies"):
        _run(
            blocking_dependencies=tuple(
                f"task.{index}" for index in range(MAX_BLOCKING_DEPENDENCIES + 1)
            )
        )
    with pytest.raises(ValueError, match="duration_seconds"):
        _run(duration_seconds=-1)
    with pytest.raises(ValueError, match="control characters"):
        _run(role_name="bad\nrole")
    with pytest.raises(TypeError, match="awaits_human"):
        _run(awaits_human=1)
    with pytest.raises(ValueError, match="RFC 3339"):
        _run(started_at="not-a-time")
    with pytest.raises(ValueError, match="canonical_state"):
        _run(canonical_state="provider_complete")
    with pytest.raises(ValueError, match="delegation_id is required"):
        _run(delegation_id=None)
    with pytest.raises(ValueError, match="EvidenceGap"):
        _run(missing_fields=("stderr secret should not cross",))
    with pytest.raises(TypeError, match="independent"):
        replace(_acceptance(), independent=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source_updated_at"):
        WorkHealth(
            generated_at="2026-08-30T10:00:00Z",
            source_updated_at="2026-08-30T10:00:01Z",
            state=WorkHealthState.EMPTY,
            freshness=FreshnessState.UNKNOWN,
            evidence_state=EvidenceState.MISSING,
            task_count=0,
            run_count=0,
            attention_count=0,
            blocked_task_count=0,
            stale_evidence_count=0,
            terminal_failure_count=0,
            runs=(),
            acceptances=(),
            review_gaps=(),
        )


def test_work_health_rejects_untyped_nested_data_and_incoherent_counts() -> None:
    base: dict[str, object] = {
        "generated_at": "2026-08-30T10:00:10Z",
        "source_updated_at": None,
        "state": WorkHealthState.PARTIAL,
        "freshness": FreshnessState.UNKNOWN,
        "evidence_state": EvidenceState.PARTIAL,
        "task_count": 0,
        "run_count": 1,
        "attention_count": 0,
        "blocked_task_count": 0,
        "stale_evidence_count": 0,
        "terminal_failure_count": 0,
        "runs": [{"stderr": "secret", "env": {"TOKEN": "secret"}}],
        "acceptances": (),
        "review_gaps": (),
    }
    with pytest.raises(TypeError, match="exact RunView"):
        WorkHealth(**base)  # type: ignore[arg-type]

    base["runs"] = (_run(),)
    base["run_count"] = 2
    with pytest.raises(ValueError, match="run_count"):
        WorkHealth(**base)  # type: ignore[arg-type]

    base["run_count"] = True
    with pytest.raises(TypeError, match="run_count"):
        WorkHealth(**base)  # type: ignore[arg-type]


def test_work_health_bounds_hostile_nested_iterable_at_limit_plus_one() -> None:
    class HostileRuns:
        def __init__(self) -> None:
            self.calls = 0

        def __iter__(self) -> HostileRuns:
            return self

        def __next__(self) -> RunView:
            self.calls += 1
            if self.calls > MAX_RUNS + 1:
                raise AssertionError("nested iterable consumed beyond bounded probe")
            return _run()

    runs = HostileRuns()
    with pytest.raises(ValueError, match="runs exceeds"):
        WorkHealth(
            generated_at="2026-08-30T10:00:10Z",
            source_updated_at=None,
            state=WorkHealthState.PARTIAL,
            freshness=FreshnessState.UNKNOWN,
            evidence_state=EvidenceState.PARTIAL,
            task_count=0,
            run_count=0,
            attention_count=0,
            blocked_task_count=0,
            stale_evidence_count=0,
            terminal_failure_count=0,
            runs=runs,  # type: ignore[arg-type]
            acceptances=(),
            review_gaps=(),
        )
    assert runs.calls == MAX_RUNS + 1


def test_provider_sensitive_fields_cannot_be_represented() -> None:
    sensitive = {
        "argv",
        "env",
        "stdout",
        "stderr",
        "prompt",
        "transcript",
        "reasoning",
        "token",
        "oauth_code",
    }
    for model in (RunView, AcceptanceView, ReviewLoopGap, WorkHealth):
        assert sensitive.isdisjoint(field.name for field in fields(model))
