from __future__ import annotations

import pytest

from agent_commons.domain.attention import awaits_human
from agent_commons.domain.snapshot import ProjectSnapshot


@pytest.mark.parametrize("state", ["input_needed", "failed", "timed_out", "needs_operator"])
def test_terminal_or_blocked_runs_require_human_attention(state: str) -> None:
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"id": "task.1", "state": "active"}},
        delegations={
            "delegation.1": {
                "id": "delegation.1",
                "state": state,
                "agent_id": "agent.1",
                "child_session_id": "session.1",
                "target_ref": {"kind": "task", "id": "task.1"},
            }
        },
    )

    attention = awaits_human(snapshot)

    assert [(item.kind, item.identifier) for item in attention.items] == [
        ("run_blocked", "delegation.1")
    ]
    assert attention.node_ids == {
        "agent.1",
        "delegation.1",
        "session.1",
        "task.1",
    }


def test_returned_work_stops_waiting_after_task_acceptance() -> None:
    delegation = {
        "id": "delegation.1",
        "state": "succeeded",
        "agent_id": "agent.1",
        "target_ref": {"kind": "task", "id": "task.1"},
    }
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"id": "task.1", "state": "review"}},
        delegations={"delegation.1": delegation},
    )

    waiting = awaits_human(snapshot)
    assert [(item.kind, item.identifier) for item in waiting.items] == [
        ("work_returned", "delegation.1")
    ]
    assert {"task.1", "delegation.1", "agent.1"} <= waiting.node_ids

    snapshot.tasks["task.1"]["state"] = "accepted"
    assert awaits_human(snapshot).items == ()
    assert awaits_human(snapshot).node_ids == frozenset()
