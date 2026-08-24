from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.attention import awaits_human
from agent_commons.domain.projection import project_events
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.thread_projection import ThreadRecord
from agent_commons.services.threads import ThreadCommands
from agent_commons.ui.graph import blocked_on_human


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
        "subject_refs": [{"kind": "thread", "id": subject_id}],
        "relations": [],
    }


def test_thread_record_is_frozen_and_preserves_replay_and_wire_shape() -> None:
    thread_id = "thread.00000000000000000000000001"
    opened = _event(
        1,
        "thread.opened",
        {
            "thread_id": thread_id,
            "thread_type": "question",
            "subject": "Which release environment should we use?",
            "desired_outcome": "Choose one environment.",
            "to": ["operator", "agent.architect"],
            "related_refs": [{"kind": "task", "id": "task.00000000000000000000000001"}],
            "extensions": {"routing": {"priority": "high"}},
        },
        thread_id,
    )
    replied = _event(
        2,
        "thread.replied",
        {
            "thread_id": thread_id,
            "expected_revision": opened["event_id"],
            "message_id": "message.00000000000000000000000001",
            "body": "Use staging first.",
        },
        thread_id,
    )
    replied["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    resolved = _event(
        3,
        "thread.resolved",
        {
            "thread_id": thread_id,
            "expected_revision": replied["event_id"],
            "resolution": "accepted",
            "summary": "Staging is selected.",
        },
        thread_id,
    )
    correction = _event(
        4,
        "event.corrected",
        {
            "target_event_id": replied["event_id"],
            "expected_target_sha256": canonical_sha256(replied),
            "replacement_payload": {
                **replied["payload"],
                "body": "Use staging first, then production.",
            },
        },
        replied["event_id"],
    )

    snapshot = project_events([opened, replied, resolved, correction])
    record = snapshot.threads[thread_id]

    assert isinstance(record, ThreadRecord)
    assert record.thread_id == thread_id
    assert record.state == "accepted"
    assert record.revision == resolved["event_id"]
    assert record.effective_revision == resolved["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "open"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "open"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    routing = exposed_extensions["routing"]
    assert isinstance(routing, dict)
    routing["priority"] = "tampered"
    exposed_messages = record["messages"]
    assert isinstance(exposed_messages, list)
    message = exposed_messages[0]
    assert isinstance(message, dict)
    message_actor = message["actor"]
    assert isinstance(message_actor, dict)
    message_actor["role_id"] = "tampered"
    assert record["extensions"] == {"routing": {"priority": "high"}}
    assert record["messages"] == [
        {
            "message_id": "message.00000000000000000000000001",
            "body": "Use staging first, then production.",
            "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
            "recorded_at": replied["recorded_at"],
        }
    ]

    expected = {
        "thread_id": thread_id,
        "thread_type": "question",
        "subject": "Which release environment should we use?",
        "desired_outcome": "Choose one environment.",
        "to": ["operator", "agent.architect"],
        "related_refs": [{"kind": "task", "id": "task.00000000000000000000000001"}],
        "extensions": {"routing": {"priority": "high"}},
        "id": thread_id,
        "state": "accepted",
        "revision": resolved["event_id"],
        "effective_revision": resolved["event_id"],
        "recorded_at": resolved["recorded_at"],
        "actor": {"session_id": "session.builder", "role_id": "builder"},
        "author_session_ids": ["session.builder", "session.reviewer"],
        "message_id": "message.00000000000000000000000001",
        "body": "Use staging first, then production.",
        "expected_revision": replied["event_id"],
        "messages": [
            {
                "message_id": "message.00000000000000000000000001",
                "body": "Use staging first, then production.",
                "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
                "recorded_at": replied["recorded_at"],
            }
        ],
        "resolution": "accepted",
        "summary": "Staging is selected.",
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["threads"] == [expected]
    assert json.dumps(snapshot.to_dict()["threads"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.issues == []


def test_thread_record_remains_a_mapping_for_attention_graph_and_engagement() -> None:
    question_id = "thread.00000000000000000000000001"
    engagement_id = "thread.00000000000000000000000002"
    question = ThreadRecord.from_projected_data(
        {
            "thread_id": question_id,
            "id": question_id,
            "state": "open",
            "revision": "evt.00000000000000000000000001",
            "effective_revision": "evt.00000000000000000000000001",
            "thread_type": "question",
            "subject": "Which release environment?",
            "to": ["operator"],
            "actor": {"session_id": "session.builder"},
        }
    )
    engagement = ThreadRecord.from_projected_data(
        {
            "thread_id": engagement_id,
            "id": engagement_id,
            "state": "open",
            "revision": "evt.00000000000000000000000002",
            "effective_revision": "evt.00000000000000000000000002",
            "thread_type": "engagement",
            "subject": "Coordinate release",
            "to": ["agent.architect", "operator"],
            "messages": [{"message_id": "message.1", "body": "Start with staging."}],
            "recorded_at": "2026-01-01T00:00:02Z",
        }
    )
    snapshot = ProjectSnapshot(
        agents={"agent.architect": {"id": "agent.architect", "state": "active"}},
        threads={question_id: question, engagement_id: engagement},
    )

    attention = awaits_human(snapshot)
    assert [(item.kind, item.identifier) for item in attention.items] == [("thread", question_id)]
    assert blocked_on_human(snapshot) == {question_id, "session.builder"}

    class EngagementReader:
        def snapshot(self) -> ProjectSnapshot:
            return snapshot

    assert ThreadCommands.list_engagements(EngagementReader()) == [  # type: ignore[arg-type]
        {
            "thread_id": engagement_id,
            "revision": "evt.00000000000000000000000002",
            "state": "open",
            "subject": "Coordinate release",
            "addressed_roles": ["agent.architect"],
            "unaddressed_roles": [],
            "messages": [{"message_id": "message.1", "body": "Start with staging."}],
            "recorded_at": "2026-01-01T00:00:02Z",
        }
    ]
