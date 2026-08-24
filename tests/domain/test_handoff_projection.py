from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.handoff_projection import HandoffRecord
from agent_commons.domain.projection import project_events
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.views import inbox_view, render_views


def _event(
    number: int,
    event_type: str,
    payload: dict[str, object],
    subject_id: str,
) -> dict[str, object]:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": "workspace.00000000000000000000000001",
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:00:{number:02d}Z",
        "actor": {"session_id": "session.builder", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": "handoff", "id": subject_id}],
        "relations": [],
    }


def test_handoff_record_is_frozen_and_preserves_replay_and_wire_shape() -> None:
    handoff_id = "handoff.00000000000000000000000001"
    created = _event(
        1,
        "handoff.created",
        {
            "handoff_id": handoff_id,
            "to": ["operator", "role:reviewer"],
            "completed": ["projection reducer"],
            "active": ["wire characterization"],
            "next_actions": ["review the handoff"],
            "blockers": ["none"],
            "risks": ["preserve wire order"],
            "open_questions": ["none"],
            "related_refs": [{"kind": "task", "id": "task.00000000000000000000000001"}],
        },
        handoff_id,
    )
    acknowledged = _event(
        2,
        "handoff.acknowledged",
        {
            "handoff_id": handoff_id,
            "expected_revision": created["event_id"],
            "note": "Received for review.",
        },
        handoff_id,
    )
    acknowledged["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    correction = _event(
        3,
        "event.corrected",
        {
            "target_event_id": acknowledged["event_id"],
            "expected_target_sha256": canonical_sha256(acknowledged),
            "replacement_payload": {**acknowledged["payload"], "note": "Received and queued."},
        },
        acknowledged["event_id"],
    )

    snapshot = project_events([created, acknowledged, correction])
    record = snapshot.handoffs[handoff_id]

    assert isinstance(record, HandoffRecord)
    assert record.handoff_id == handoff_id
    assert record.state == "acknowledged"
    assert record.revision == acknowledged["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "open"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "open"  # type: ignore[index]

    exposed_actions = record["next_actions"]
    assert isinstance(exposed_actions, list)
    exposed_actions.append("tampered")
    exposed_refs = record["related_refs"]
    assert isinstance(exposed_refs, list)
    related_ref = exposed_refs[0]
    assert isinstance(related_ref, dict)
    related_ref["id"] = "task.tampered"
    assert record["next_actions"] == ["review the handoff"]
    assert record["related_refs"] == [{"kind": "task", "id": "task.00000000000000000000000001"}]

    expected = {
        "handoff_id": handoff_id,
        "to": ["operator", "role:reviewer"],
        "completed": ["projection reducer"],
        "active": ["wire characterization"],
        "next_actions": ["review the handoff"],
        "blockers": ["none"],
        "risks": ["preserve wire order"],
        "open_questions": ["none"],
        "related_refs": [{"kind": "task", "id": "task.00000000000000000000000001"}],
        "id": handoff_id,
        "state": "acknowledged",
        "revision": acknowledged["event_id"],
        "effective_revision": correction["event_id"],
        "recorded_at": acknowledged["recorded_at"],
        "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
        "author_session_ids": ["session.builder", "session.reviewer"],
        "expected_revision": created["event_id"],
        "note": "Received and queued.",
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["handoffs"] == [expected]
    assert json.dumps(snapshot.to_dict()["handoffs"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.issues == []


def test_handoff_record_remains_a_mapping_for_inbox_and_rendered_views(tmp_path) -> None:
    handoff_id = "handoff.00000000000000000000000001"
    record = HandoffRecord.from_projected_data(
        {
            "handoff_id": handoff_id,
            "id": handoff_id,
            "state": "open",
            "revision": "evt.00000000000000000000000001",
            "effective_revision": "evt.00000000000000000000000001",
            "to": ["role:reviewer"],
            "next_actions": ["read the handoff"],
            "actor": {"session_id": "session.builder"},
        }
    )
    snapshot = ProjectSnapshot(handoffs={handoff_id: record})

    inbox = inbox_view(snapshot, session={"role_id": "reviewer"}, verbose=True)
    assert inbox["counts"] == {"threads": 0, "handoffs": 1}
    assert inbox["handoffs"] == [record.to_dict()]

    render_views(snapshot, tmp_path / "views")
    handoffs = (tmp_path / "views" / "HANDOFFS.md").read_text(encoding="utf-8")
    assert handoff_id in handoffs
