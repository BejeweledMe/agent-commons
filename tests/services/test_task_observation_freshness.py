"""Canonical task observation and provider progress age independently."""

from copy import deepcopy

import pytest

from agent_commons.domain.work_state import FreshnessState
from agent_commons.services.execution_plan import build_execution_plan
from agent_commons.services.work_metrics import build_work_health
from tests.services.test_work_metrics import _attempt, _task
from tests.services.test_work_observation import GRAPH, NOW, OLD, old_running_snapshot


@pytest.mark.parametrize(
    "canonical_state,attempt_state", [("active", "running"), ("needs_operator", "succeeded")]
)
def test_observed_tasks_remain_current_while_old_run_stays_stale(canonical_state, attempt_state):
    snapshot = old_running_snapshot()
    snapshot.delegations["delegation.1"]["state"] = canonical_state
    snapshot.tasks["task.other"] = _task("task.other", state="ready")
    attempt = _attempt(state=attempt_state, created_at=OLD, updated_at=OLD)
    before = deepcopy((snapshot.tasks, snapshot.delegations, attempt))
    plan = build_execution_plan(
        snapshot, [attempt], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert plan.freshness is FreshnessState.STALE
    assert {item.task_id: item.freshness for item in plan.nodes} == {
        "task.1": FreshnessState.FRESH,
        "task.dep": FreshnessState.FRESH,
        "task.other": FreshnessState.FRESH,
    }
    health = build_work_health(
        snapshot, [attempt], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert health.runs[0].freshness is FreshnessState.STALE
    assert (snapshot.tasks, snapshot.delegations, attempt) == before


def test_old_orphan_does_not_age_a_fresh_task_observation():
    snapshot = old_running_snapshot()
    snapshot.delegations.clear()
    attempt = _attempt(created_at=OLD, updated_at=OLD)
    plan = build_execution_plan(
        snapshot, [attempt], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert plan.freshness is FreshnessState.STALE
    assert plan.nodes[0].freshness is FreshnessState.FRESH
    assert "attempts_partial" in plan.gaps


@pytest.mark.parametrize("timestamp", [None, "not-a-time", "2027-01-01T00:00:00Z"])
def test_bad_subject_timestamp_never_becomes_a_current_task(timestamp):
    snapshot = old_running_snapshot()
    snapshot.tasks["task.1"]["recorded_at"] = timestamp
    plan = build_execution_plan(
        snapshot, [], generated_at=NOW, graph=GRAPH, canonical_observed_at=NOW
    )
    assert plan.nodes[0].freshness is FreshnessState.UNKNOWN


def test_old_or_missing_observation_does_not_bypass_age_budget():
    snapshot = old_running_snapshot()
    for observation in (None, OLD):
        plan = build_execution_plan(
            snapshot,
            [_attempt(created_at=OLD, updated_at=OLD)],
            generated_at=NOW,
            graph=GRAPH,
            canonical_observed_at=observation,
        )
        assert plan.nodes[0].freshness is FreshnessState.STALE
