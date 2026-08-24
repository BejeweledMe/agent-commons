from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict
from agent_commons.domain.objective_projection import ObjectiveRecord
from agent_commons.domain.projection import project_events
from agent_commons.views import orientation, render_views


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
        "subject_refs": [{"kind": "objective", "id": subject_id}],
        "relations": [],
    }


def test_objective_record_preserves_raw_canonical_created_payload_order(tmp_path) -> None:
    """Replay persisted bytes, rather than a hand-ordered fixture, as the oracle."""

    objective_id = "objective.00000000000000000000000001"
    source_event = _event(
        1,
        "objective.created",
        {
            "title": "Preserve the persisted wire order.",
            "objective_id": objective_id,
            "extensions": {"audit": {"source": "A5", "priority": "high"}},
            "acceptance_criteria": ["Replay stays byte-compatible."],
            "description": "Freeze the read model, not the canonical payload.",
        },
        objective_id,
    )
    persisted = loads_json_strict(canonical_json_bytes(source_event))
    assert isinstance(persisted, dict)
    raw_payload = persisted["payload"]
    assert isinstance(raw_payload, dict)
    legacy_wire = {
        **raw_payload,
        "id": objective_id,
        "state": "active",
        "revision": persisted["event_id"],
        "effective_revision": persisted["event_id"],
        "recorded_at": persisted["recorded_at"],
        "actor": persisted["actor"],
        "author_session_ids": ["session.builder"],
    }

    snapshot = project_events([persisted])
    record = snapshot.objectives[objective_id]

    assert isinstance(record, ObjectiveRecord)
    assert record.objective_id == objective_id
    with pytest.raises(FrozenInstanceError):
        record.state = "closed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "closed"  # type: ignore[index]
    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    audit = exposed_extensions["audit"]
    assert isinstance(audit, dict)
    audit["priority"] = "tampered"
    exposed_criteria = record["acceptance_criteria"]
    assert isinstance(exposed_criteria, list)
    exposed_criteria.append("tampered")
    assert record["extensions"] == {"audit": {"source": "A5", "priority": "high"}}
    assert record["acceptance_criteria"] == ["Replay stays byte-compatible."]
    assert list(record) == list(legacy_wire)
    assert record.to_dict() == legacy_wire
    assert json.dumps(record.to_dict(), separators=(",", ":")) == json.dumps(
        legacy_wire, separators=(",", ":")
    )
    assert snapshot.to_dict()["objectives"] == [legacy_wire]
    assert orientation(snapshot)["objectives"] == [
        {
            "id": objective_id,
            "state": "active",
            "title": raw_payload["title"],
            "revision": persisted["event_id"],
            "effective_revision": persisted["event_id"],
        }
    ]
    render_views(snapshot, tmp_path / "views")
    assert objective_id in (tmp_path / "views" / "CURRENT.md").read_text(encoding="utf-8")
    assert snapshot.issues == []


def test_objective_record_preserves_revision_and_close_transitions() -> None:
    objective_id = "objective.00000000000000000000000001"
    created = _event(
        1,
        "objective.created",
        {
            "objective_id": objective_id,
            "title": "Initial title",
            "description": "Initial description",
            "acceptance_criteria": ["initial criterion"],
        },
        objective_id,
    )
    revised = _event(
        2,
        "objective.revised",
        {
            "objective_id": objective_id,
            "expected_revision": created["event_id"],
            "changes": {"title": "Revised title", "acceptance_criteria": ["revised criterion"]},
        },
        objective_id,
    )
    revised["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    closed = _event(
        3,
        "objective.closed",
        {
            "objective_id": objective_id,
            "expected_revision": revised["event_id"],
            "reason": "The objective is complete.",
        },
        objective_id,
    )

    snapshot = project_events([created, revised, closed])
    record = snapshot.objectives[objective_id]

    assert isinstance(record, ObjectiveRecord)
    assert record.state == "closed"
    assert record["title"] == "Revised title"
    assert record["acceptance_criteria"] == ["revised criterion"]
    assert record["reason"] == "The objective is complete."
    assert record["author_session_ids"] == ["session.builder", "session.reviewer"]
    assert record.to_dict()["revision"] == closed["event_id"]
    assert snapshot.issues == []
