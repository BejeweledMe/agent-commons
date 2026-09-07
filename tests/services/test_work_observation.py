"""Fresh canonical observations do not mistake immutable event age for staleness."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_commons.domain.execution_plan import PlanGap, PlanState
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import EvidenceState, FreshnessState, WorkSourceGap
from agent_commons.services.execution_plan import build_execution_plan
from agent_commons.services.work_metrics import WorkMetricsInputError, build_work_health
from tests.services.test_work_metrics import _attempt, _snapshot, _task

OLD = "2026-08-30T09:00:00Z"
NOW = "2026-08-30T11:00:00Z"
GRAPH = {"generated_at": NOW, "limits": {"truncated": False}}


def quiet_snapshot() -> ProjectSnapshot:
    snapshot = ProjectSnapshot()
    snapshot.tasks["task.1"] = _task(state="ready")  # type: ignore[assignment]
    snapshot.tasks["task.1"]["recorded_at"] = OLD  # type: ignore[index]
    return snapshot


def old_running_snapshot() -> ProjectSnapshot:
    snapshot = _snapshot()
    for collection in (snapshot.tasks, snapshot.agents, snapshot.delegations):
        for record in collection.values():
            record["recorded_at"] = OLD  # type: ignore[index]
    return snapshot


def test_fresh_observation_keeps_an_idle_ready_task_current_without_rewriting_it() -> None:
    snapshot = quiet_snapshot()
    original = deepcopy(snapshot.tasks)
    unobserved = build_work_health(snapshot, generated_at=NOW, graph=GRAPH)
    assert unobserved.freshness is FreshnessState.STALE
    for now in ("2026-08-30T09:00:59Z", "2026-08-30T09:01:01Z", NOW):
        plan = build_execution_plan(
            snapshot,
            (),
            generated_at=now,
            graph={**GRAPH, "generated_at": now},
            canonical_observed_at=now,
        )
        assert plan.freshness is FreshnessState.FRESH
        assert PlanGap.PROJECTION_STALE not in plan.gaps
        assert plan.nodes[0].readiness.value == "ready"
        assert plan.nodes[0].freshness is FreshnessState.FRESH
    assert snapshot.tasks == original


@pytest.mark.parametrize("attempt_time,expected", [(OLD, "stale"), (NOW, "fresh")])
def test_canonical_observation_never_refreshes_an_active_attempt_heartbeat(
    attempt_time: str, expected: str
) -> None:
    snapshot = old_running_snapshot()
    attempt = _attempt(created_at=OLD, updated_at=attempt_time)
    health = build_work_health(
        snapshot,
        [attempt],
        generated_at=NOW,
        graph=GRAPH,
        canonical_observed_at=NOW,
    )
    assert health.freshness.value == expected
    assert health.runs[0].freshness.value == expected
    if expected == "stale":
        assert health.runs[0].evidence_state is EvidenceState.STALE


def test_observed_coherent_terminal_history_does_not_expire_like_a_live_heartbeat() -> None:
    snapshot = old_running_snapshot()
    snapshot.delegations["delegation.1"]["state"] = "succeeded"  # type: ignore[index]
    attempt = _attempt(state="succeeded", created_at=OLD, updated_at=OLD)
    health = build_work_health(
        snapshot, [attempt], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert health.freshness is FreshnessState.FRESH
    assert health.runs[0].freshness is FreshnessState.FRESH
    snapshot.delegations["delegation.1"]["state"] = "active"  # type: ignore[index]
    mismatched = build_work_health(
        snapshot, [attempt], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert mismatched.freshness is FreshnessState.STALE
    assert "canonical_attempt_state_match" in mismatched.runs[0].missing_fields


def test_observation_ages_the_latest_attempt_and_still_validates_historical_evidence() -> None:
    snapshot = old_running_snapshot()
    prior = _attempt(state="failed", number=1, created_at=OLD, updated_at=OLD)
    current = _attempt(number=2, created_at=OLD, updated_at=NOW)
    health = build_work_health(
        snapshot, [prior, current], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert health.freshness is FreshnessState.FRESH
    prior["updated_at"] = "2027-01-01T00:00:00Z"
    invalid = build_work_health(
        snapshot, [prior, current], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert invalid.freshness is FreshnessState.UNKNOWN
    assert WorkSourceGap.SOURCE_TIMESTAMP_FUTURE in invalid.source_gaps


@pytest.mark.parametrize("value", [None, "bad-clock", "2027-01-01T00:00:00Z"])
def test_fresh_observation_cannot_cover_missing_invalid_or_future_canonical_timestamps(
    value: str | None,
) -> None:
    snapshot = quiet_snapshot()
    snapshot.tasks["task.1"]["recorded_at"] = value  # type: ignore[index]
    health = build_work_health(snapshot, generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW)
    assert health.freshness is FreshnessState.UNKNOWN
    assert health.source_gaps


def test_invalid_future_and_stale_observation_clocks_are_not_freshness_bypasses() -> None:
    snapshot = quiet_snapshot()
    for observed in ("bad-clock", "2027-01-01T00:00:00Z"):
        with pytest.raises(WorkMetricsInputError):
            build_work_health(snapshot, generated_at=NOW, canonical_observed_at=observed)
        plan = build_execution_plan(snapshot, (), generated_at=NOW, canonical_observed_at=observed)
        assert plan.state is PlanState.ERROR
    health = build_work_health(snapshot, generated_at=NOW, canonical_observed_at=OLD)
    assert health.freshness is FreshnessState.STALE


def test_observation_does_not_mask_unvalidated_graph_age_truncation_or_resume_gap() -> None:
    snapshot = quiet_snapshot()
    for graph, gap in (
        ({**GRAPH, "generated_at": OLD}, PlanGap.GRAPH_STALE),
        ({**GRAPH, "limits": {"truncated": True}}, PlanGap.GRAPH_TRUNCATED),
    ):
        plan = build_execution_plan(
            snapshot, (), generated_at=NOW, graph=graph, canonical_observed_at=NOW
        )
        assert gap in plan.gaps
    resumed = build_execution_plan(
        snapshot, (), generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW, resume_gap=True
    )
    assert resumed.freshness is FreshnessState.STALE
    assert PlanGap.RESUME_GAP in resumed.gaps
