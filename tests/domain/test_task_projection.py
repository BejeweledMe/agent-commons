from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.projection import project_events
from agent_commons.domain.task_projection import TaskRecord


def _event(
    number: int,
    event_type: str,
    payload: dict[str, object],
    subject_kind: str,
    subject_id: str,
) -> dict[str, object]:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": "workspace.00000000000000000000000001",
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:00:{number:02d}Z",
        "actor": {"session_id": "session.test", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "relations": [],
    }


def test_task_record_is_frozen_and_preserves_replay_and_wire_shape() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    task_id = "task.00000000000000000000000001"
    registered = _event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    created = _event(
        2,
        "task.created",
        {
            "task_id": task_id,
            "title": "Initial task",
            "description": "Keep the existing task wire shape.",
            "acceptance_criteria": ["replay succeeds"],
            "priority": "normal",
            "dependencies": [],
            "extensions": {"source": {"path": "docs/task.md"}},
        },
        "task",
        task_id,
    )
    started = _event(
        3,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    revised = _event(
        4,
        "task.revised",
        {
            "task_id": task_id,
            "expected_revision": started["event_id"],
            "changes": {"title": "Revised task"},
        },
        "task",
        task_id,
    )
    binding = {
        "ref": {"kind": "artifact", "id": artifact_id},
        "revision": registered["event_id"],
    }
    completed = _event(
        5,
        "task.completed",
        {
            "task_id": task_id,
            "expected_revision": revised["event_id"],
            "summary": "finished",
            "artifact_refs": [binding["ref"]],
            "artifact_bindings": [binding],
        },
        "task",
        task_id,
    )
    revised_artifact = _event(
        6,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": registered["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    correction = _event(
        7,
        "event.corrected",
        {
            "target_event_id": revised["event_id"],
            "expected_target_sha256": canonical_sha256(revised),
            "replacement_payload": {
                **revised["payload"],
                "changes": {"title": "Corrected task"},
            },
        },
        "event",
        revised["event_id"],
    )

    snapshot = project_events(
        [registered, created, started, revised, completed, revised_artifact, correction]
    )
    record = snapshot.tasks[task_id]

    assert isinstance(record, TaskRecord)
    assert record.task_id == task_id
    assert record.state == "completed"
    assert record.revision == completed["event_id"]
    assert record.effective_revision == completed["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "cancelled"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "cancelled"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    source = exposed_extensions["source"]
    assert isinstance(source, dict)
    source["path"] = "tampered"
    exposed_bindings = record["artifact_bindings"]
    assert isinstance(exposed_bindings, list)
    bound_ref = exposed_bindings[0]["ref"]
    assert isinstance(bound_ref, dict)
    bound_ref["id"] = "artifact.tampered"
    assert record["extensions"] == {"source": {"path": "docs/task.md"}}
    assert record["artifact_bindings"] == [binding]

    expected = {
        "task_id": task_id,
        "title": "Corrected task",
        "description": "Keep the existing task wire shape.",
        "acceptance_criteria": ["replay succeeds"],
        "priority": "normal",
        "dependencies": [],
        "extensions": {"source": {"path": "docs/task.md"}},
        "work_author_session_ids": ["session.test"],
        "id": task_id,
        "state": "completed",
        "revision": completed["event_id"],
        "effective_revision": completed["event_id"],
        "recorded_at": completed["recorded_at"],
        "actor": {"session_id": "session.test", "role_id": "builder"},
        "author_session_ids": ["session.test"],
        "expected_revision": revised["event_id"],
        "summary": "finished",
        "artifact_refs": [binding["ref"]],
        "artifact_bindings": [binding],
        "artifact_stale": True,
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["tasks"] == [expected]
    assert json.dumps(snapshot.to_dict()["tasks"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.issues == []
