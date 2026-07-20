from __future__ import annotations

import pytest

from agent_commons.domain.lifecycle import validate_transition
from agent_commons.domain.projection import ProjectSnapshot, project_events
from agent_commons.errors import LifecycleConflictError
from agent_commons.views import orientation, render_views

WORKSPACE_ID = "workspace.00000000000000000000000001"
TASK_ID = "task.00000000000000000000000001"
DELEGATION_ID = "delegation.00000000000000000000000001"
CHILD_DELEGATION_ID = "delegation.00000000000000000000000002"
PARENT_SESSION_ID = "session." + "a" * 32
CHILD_SESSION_ID = "session." + "b" * 32

LIMITS = {
    "max_depth": 1,
    "wall_time_seconds": 900,
    "max_attempts": 2,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 10000},
}


def _event(number: int, event_type: str, payload: dict, *, actor: str) -> dict:
    family = event_type.split(".", 1)[0]
    identifier = payload.get(f"{family}_id", "event.unknown")
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:00:{number:02d}Z",
        "actor": {"session_id": actor, "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": family, "id": identifier}],
        "relations": [],
    }


def _task_event() -> dict:
    return _event(
        1,
        "task.created",
        {
            "task_id": TASK_ID,
            "title": "Implement runtime",
            "description": "Build one bounded delegation slice",
            "acceptance_criteria": ["reviewed"],
            "priority": "high",
        },
        actor=PARENT_SESSION_ID,
    )


def _request(task_revision: str, *, max_depth: int = 1) -> dict:
    return _event(
        2,
        "delegation.requested",
        {
            "delegation_id": DELEGATION_ID,
            "target_ref": {"kind": "task", "id": TASK_ID},
            "target_revision": task_revision,
            "target_profile": "codex-builder",
            "purpose": "implementation",
            "parent_session_id": PARENT_SESSION_ID,
            "root_delegation_id": DELEGATION_ID,
            "depth": 0,
            "limits": {**LIMITS, "max_depth": max_depth},
        },
        actor=PARENT_SESSION_ID,
    )


def test_projected_delegation_lifecycle_preserves_lineage_and_results() -> None:
    task = _task_event()
    requested = _request(task["event_id"])
    started = _event(
        3,
        "delegation.started",
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": requested["event_id"],
            "child_session_id": CHILD_SESSION_ID,
            "attempt": 1,
        },
        actor=PARENT_SESSION_ID,
    )
    input_needed = _event(
        4,
        "delegation.input_needed",
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": started["event_id"],
            "summary": "A bounded operator choice is required.",
        },
        actor=CHILD_SESSION_ID,
    )
    resumed = _event(
        5,
        "delegation.resumed",
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": input_needed["event_id"],
            "resolution": "The operator selected the current task revision.",
        },
        actor=PARENT_SESSION_ID,
    )
    succeeded = _event(
        6,
        "delegation.succeeded",
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": resumed["event_id"],
            "summary": "The independent review completed.",
            "result_refs": [{"kind": "task", "id": TASK_ID}],
        },
        actor=CHILD_SESSION_ID,
    )

    snapshot = project_events([succeeded, requested, task, input_needed, started, resumed])

    delegation = snapshot.delegations[DELEGATION_ID]
    assert delegation["state"] == "succeeded"
    assert delegation["root_delegation_id"] == DELEGATION_ID
    assert delegation["parent_session_id"] == PARENT_SESSION_ID
    assert delegation["child_session_id"] == CHILD_SESSION_ID
    assert delegation["result_refs"] == [{"kind": "task", "id": TASK_ID}]
    assert snapshot.warnings == []


def test_invalid_terminal_transition_is_rejected_during_replay() -> None:
    task = _task_event()
    requested = _request(task["event_id"])
    succeeded_without_start = _event(
        3,
        "delegation.succeeded",
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": requested["event_id"],
            "summary": "This must not apply.",
            "result_refs": [{"kind": "task", "id": TASK_ID}],
        },
        actor=CHILD_SESSION_ID,
    )

    snapshot = project_events([task, requested, succeeded_without_start])

    assert snapshot.delegations[DELEGATION_ID]["state"] == "requested"
    assert any("not allowed" in warning for warning in snapshot.warnings)


@pytest.mark.parametrize(
    ("event_type", "fields", "state", "requires_start"),
    [
        (
            "delegation.succeeded",
            {
                "summary": "Completed.",
                "result_refs": [{"kind": "task", "id": TASK_ID}],
            },
            "succeeded",
            True,
        ),
        (
            "delegation.failed",
            {"reason_code": "runtime_error", "summary": "Failed safely."},
            "failed",
            False,
        ),
        ("delegation.cancelled", {"reason": "Cancelled."}, "cancelled", False),
        (
            "delegation.timed_out",
            {"summary": "Time limit elapsed."},
            "timed_out",
            False,
        ),
        (
            "delegation.needs_operator",
            {"reason_code": "orphaned", "summary": "Recovery is ambiguous."},
            "needs_operator",
            False,
        ),
    ],
)
def test_every_terminal_outcome_projects_explicitly(
    event_type: str,
    fields: dict,
    state: str,
    requires_start: bool,
) -> None:
    task = _task_event()
    requested = _request(task["event_id"])
    events = [task, requested]
    expected_revision = requested["event_id"]
    if requires_start:
        started = _event(
            3,
            "delegation.started",
            {
                "delegation_id": DELEGATION_ID,
                "expected_revision": requested["event_id"],
                "child_session_id": CHILD_SESSION_ID,
                "attempt": 1,
            },
            actor=PARENT_SESSION_ID,
        )
        events.append(started)
        expected_revision = started["event_id"]
    terminal = _event(
        4 if requires_start else 3,
        event_type,
        {
            "delegation_id": DELEGATION_ID,
            "expected_revision": expected_revision,
            **fields,
        },
        actor=CHILD_SESSION_ID if requires_start else PARENT_SESSION_ID,
    )
    events.append(terminal)

    snapshot = project_events(events)

    assert snapshot.delegations[DELEGATION_ID]["state"] == state
    assert snapshot.warnings == []


def test_child_session_must_be_distinct_and_attempt_is_hard_bounded() -> None:
    snapshot = ProjectSnapshot(
        tasks={
            TASK_ID: {
                "id": TASK_ID,
                "state": "ready",
                "revision": "evt.00000000000000000000000001",
            }
        },
        delegations={
            DELEGATION_ID: {
                "id": DELEGATION_ID,
                "state": "requested",
                "revision": "evt.00000000000000000000000002",
                "target_ref": {"kind": "task", "id": TASK_ID},
                "target_revision": "evt.00000000000000000000000001",
                "parent_session_id": PARENT_SESSION_ID,
                "limits": LIMITS,
            }
        },
    )
    payload = {
        "delegation_id": DELEGATION_ID,
        "expected_revision": "evt.00000000000000000000000002",
        "child_session_id": PARENT_SESSION_ID,
        "attempt": 1,
    }
    with pytest.raises(LifecycleConflictError, match="distinct"):
        validate_transition(
            snapshot,
            "delegation.started",
            payload,
            actor_session_id=PARENT_SESSION_ID,
        )

    with pytest.raises(LifecycleConflictError, match="max_attempts"):
        validate_transition(
            snapshot,
            "delegation.started",
            {**payload, "child_session_id": CHILD_SESSION_ID, "attempt": 3},
            actor_session_id=PARENT_SESSION_ID,
        )


def test_child_delegation_rejects_ancestor_cycles_and_depth_growth() -> None:
    task_revision = "evt.00000000000000000000000001"
    parent_revision = "evt.00000000000000000000000002"
    snapshot = ProjectSnapshot(
        tasks={TASK_ID: {"id": TASK_ID, "state": "ready", "revision": task_revision}},
        delegations={
            DELEGATION_ID: {
                "id": DELEGATION_ID,
                "state": "active",
                "revision": parent_revision,
                "effective_revision": parent_revision,
                "root_delegation_id": DELEGATION_ID,
                "depth": 0,
                "child_session_id": CHILD_SESSION_ID,
                "limits": LIMITS,
            }
        },
    )
    base = {
        "delegation_id": CHILD_DELEGATION_ID,
        "target_ref": {"kind": "delegation", "id": DELEGATION_ID},
        "target_revision": parent_revision,
        "target_profile": "codex-builder",
        "purpose": "implementation",
        "parent_session_id": CHILD_SESSION_ID,
        "parent_delegation_id": DELEGATION_ID,
        "root_delegation_id": DELEGATION_ID,
        "depth": 1,
        "limits": LIMITS,
    }
    with pytest.raises(LifecycleConflictError, match="ancestor cycle"):
        validate_transition(
            snapshot,
            "delegation.requested",
            base,
            actor_session_id=CHILD_SESSION_ID,
        )

    with pytest.raises(LifecycleConflictError, match="max_depth"):
        validate_transition(
            snapshot,
            "delegation.requested",
            {
                **base,
                "target_ref": {"kind": "task", "id": TASK_ID},
                "target_revision": task_revision,
                "limits": {**LIMITS, "max_depth": 0},
            },
            actor_session_id=CHILD_SESSION_ID,
        )

    snapshot.delegations[CHILD_DELEGATION_ID] = {
        "id": CHILD_DELEGATION_ID,
        "state": "requested",
        "parent_delegation_id": DELEGATION_ID,
    }
    with pytest.raises(LifecycleConflictError, match="max_concurrency"):
        validate_transition(
            snapshot,
            "delegation.requested",
            {
                **base,
                "delegation_id": "delegation.00000000000000000000000003",
                "target_ref": {"kind": "task", "id": TASK_ID},
                "target_revision": task_revision,
            },
            actor_session_id=CHILD_SESSION_ID,
        )


def test_delegations_are_bounded_in_orientation_and_generated_views(tmp_path) -> None:
    snapshot = ProjectSnapshot(
        workspace_id=WORKSPACE_ID,
        delegations={
            DELEGATION_ID: {
                "id": DELEGATION_ID,
                "state": "input_needed",
                "target_profile": "claude-independent-reviewer",
                "summary": "Operator input is required.",
            }
        },
    )

    brief = orientation(snapshot, max_items=1)
    assert brief["delegations"]["input_needed"][0]["id"] == DELEGATION_ID
    assert brief["delegations"]["active"] == []

    paths = render_views(snapshot, tmp_path / "views")
    delegation_view = next(path for path in paths if path.name == "DELEGATIONS.md")
    rendered = delegation_view.read_text(encoding="utf-8")
    assert DELEGATION_ID in rendered
    assert "input_needed" in rendered
