from __future__ import annotations

from dataclasses import asdict

from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.ui.tracker_reads import build_tracker_snapshot, loading_tracker_snapshot

NOW = "2026-08-30T10:01:00Z"
CAPACITY = {"active": 1, "limit": 4, "queued": 0, "queue_capacity": 8}


def _snapshot(*, delegation_state: str = "active") -> ProjectSnapshot:
    snapshot = ProjectSnapshot(workspace_id="workspace.1")
    snapshot.tasks["task.dep"] = {  # type: ignore[assignment]
        "id": "task.dep",
        "task_id": "task.dep",
        "title": "Prepare foundation",
        "state": "accepted",
        "revision": "evt.task.dep.1",
        "effective_revision": "evt.task.dep.1",
        "recorded_at": "2026-08-30T10:00:50Z",
        "dependencies": [],
        "acceptance_review": {
            "ref": {"kind": "review", "id": "review.dep"},
            "revision": "evt.review.dep.1",
        },
    }
    snapshot.reviews["review.dep"] = {  # type: ignore[assignment]
        "id": "review.dep",
        "review_id": "review.dep",
        "state": "approved",
        "revision": "evt.review.dep.1",
        "effective_revision": "evt.review.dep.1",
        "recorded_at": "2026-08-30T10:00:51Z",
        "target_ref": {"kind": "task", "id": "task.dep"},
        "target_revision": "evt.task.dep.1",
        "independent": True,
        "stale": False,
    }
    snapshot.tasks["task.build"] = {  # type: ignore[assignment]
        "id": "task.build",
        "task_id": "task.build",
        "title": "Build tracker\x00 safely",
        "state": "active",
        "revision": "evt.task.build.1",
        "effective_revision": "evt.task.build.1",
        "recorded_at": "2026-08-30T10:00:55Z",
        "dependencies": ["task.dep"],
        "owner_session_id": "session.builder",
    }
    snapshot.tasks["task.next"] = {  # type: ignore[assignment]
        "id": "task.next",
        "task_id": "task.next",
        "title": "Ship UI",
        "state": "ready",
        "revision": "evt.task.next.1",
        "effective_revision": "evt.task.next.1",
        "recorded_at": "2026-08-30T10:00:55Z",
        "dependencies": ["task.build"],
    }
    snapshot.agents["agent.builder"] = {  # type: ignore[assignment]
        "id": "agent.builder",
        "agent_id": "agent.builder",
        "name": "Tracker builder",
        "profile_id": "claude-builder",
        "recorded_at": "2026-08-30T10:00:55Z",
    }
    snapshot.delegations["delegation.1"] = {  # type: ignore[assignment]
        "id": "delegation.1",
        "delegation_id": "delegation.1",
        "state": delegation_state,
        "revision": "evt.delegation.1",
        "effective_revision": "evt.delegation.1",
        "recorded_at": "2026-08-30T10:00:55Z",
        "target_ref": {"kind": "task", "id": "task.build"},
        "target_profile": "claude-builder",
        "agent_id": "agent.builder",
    }
    return snapshot


def _attempt(*, state: str = "running") -> dict[str, object]:
    return {
        "attempt_id": "attempt.1",
        "number": 1,
        "profile_id": "claude-builder",
        "provider": "claude",
        "state": state,
        "created_at": "2026-08-30T10:00:30Z",
        "updated_at": "2026-08-30T10:00:55Z",
        "correlation": {"delegation_id": "delegation.1"},
        "stderr_diagnostic_tail": "secret stderr",
        "argv": ["--private-tool-argument"],
        "transcript": "private transcript",
        "token_count": 9001,
        "cost": 42,
        "eta": "soon",
        "progress": 99,
    }


def test_tracker_composes_focused_dag_run_timeline_and_observed_capacity() -> None:
    dto = build_tracker_snapshot(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        sequence=7,
        focus_task_ids=["task.next"],
        capacity=CAPACITY,
    )
    wire = dto.to_wire()

    assert dto.state == "ready"
    assert wire["sequence"] == 7
    assert [task["task_id"] for task in wire["tasks"]] == [
        "task.build",
        "task.dep",
        "task.next",
    ]
    assert wire["critical_path_task_ids"] == ["task.dep", "task.build", "task.next"]
    assert wire["critical_path_predictive"] is False
    assert wire["capacity"] == {
        "state": "available",
        "active": 1,
        "limit": 4,
        "queued": 0,
        "queue_capacity": 8,
    }
    run = wire["runs"][0]
    assert run["provider"] == "claude"
    assert run["profile_id"] == "claude-builder"
    assert run["phase"] == "running"
    assert run["duration_seconds"] == 30
    build_title = next(task["title"] for task in wire["tasks"] if task["task_id"] == "task.build")
    assert "\x00" not in build_title


def test_tracker_has_no_raw_provider_or_fake_metric_fields() -> None:
    wire = build_tracker_snapshot(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        sequence=1,
        capacity=CAPACITY,
    ).to_wire()
    rendered = repr(wire)

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for child in value.values() for key in keys(child)}
        if isinstance(value, list):
            return {key for child in value for key in keys(child)}
        return set()

    for forbidden in (
        "secret stderr",
        "private-tool-argument",
        "private transcript",
    ):
        assert forbidden not in rendered
    assert {"token_count", "cost", "eta", "progress", "percentage"}.isdisjoint(keys(wire))


def test_human_attention_is_closed_vocabulary_not_provider_summary() -> None:
    dto = build_tracker_snapshot(
        _snapshot(delegation_state="needs_operator"),
        [_attempt(state="needs_operator")],
        generated_at=NOW,
        sequence=2,
        capacity=CAPACITY,
    )

    assert dto.attention[0].kind == "run"
    assert dto.attention[0].reason_code == "needs_operator"
    assert dto.attention[0].next_action == "answer_operator_request"


def test_empty_loading_error_and_stale_states_are_explicit() -> None:
    empty = build_tracker_snapshot(
        ProjectSnapshot(), [], generated_at=NOW, sequence=0, capacity=CAPACITY
    )
    loading = loading_tracker_snapshot(generated_at=NOW)
    error = build_tracker_snapshot(None, None, generated_at=NOW, sequence=3)
    stale = build_tracker_snapshot(
        _snapshot(),
        [_attempt()],
        generated_at=NOW,
        sequence=4,
        resume_gap=True,
        capacity=CAPACITY,
    )

    assert empty.state == "empty"
    assert loading.state == "loading"
    assert error.state == "error"
    assert {"projection_missing", "projection_unavailable"} <= set(error.gaps)
    assert stale.state == "stale"
    assert stale.freshness.resume_gap is True
    assert "resume_gap" in stale.gaps


def test_dto_returns_fresh_wire_containers() -> None:
    dto = build_tracker_snapshot(
        _snapshot(), [_attempt()], generated_at=NOW, sequence=1, capacity=CAPACITY
    )
    first = dto.to_wire()
    first["tasks"].clear()
    first["gaps"].append("tampered")

    second = dto.to_wire()
    assert second["tasks"]
    assert "tampered" not in second["gaps"]
    assert "secret stderr" not in repr(asdict(dto))
