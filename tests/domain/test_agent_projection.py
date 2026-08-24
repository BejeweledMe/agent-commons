from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.agent_projection import AgentRecord
from agent_commons.domain.projection import project_events
from agent_commons.domain.roles import effective_grants, lineage, turnover_used


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
        "actor": {"session_id": "session.test", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": "agent", "id": subject_id}],
        "relations": [],
    }


def test_agent_record_is_frozen_and_preserves_replay_and_wire_shape() -> None:
    agent_id = "agent.00000000000000000000000001"
    created = _event(
        1,
        "agent.created",
        {
            "agent_id": agent_id,
            "name": "Design lead",
            "profile_id": "claude-builder",
            "grants": {"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
            "context_mode": "fresh",
            "origin": "human",
            "approval": "human",
            "rationale": "Own the design system boundary.",
            "lifetime": {"kind": "persistent"},
            "turnover_budget": 4,
            "extensions": {"model": {"name": "claude-opus", "tags": ["design"]}},
        },
        agent_id,
    )
    reconfigured = _event(
        2,
        "agent.reconfigured",
        {
            "agent_id": agent_id,
            "expected_revision": created["event_id"],
            "changes": {"name": "Staff design lead", "skills": ["typescript"]},
            "reason": "The role now owns the prototype implementation too.",
        },
        agent_id,
    )
    retired = _event(
        3,
        "agent.retired",
        {
            "agent_id": agent_id,
            "expected_revision": reconfigured["event_id"],
            "reason": "The programme completed.",
            "retired_by": "human",
        },
        agent_id,
    )
    correction = _event(
        4,
        "event.corrected",
        {
            "target_event_id": reconfigured["event_id"],
            "expected_target_sha256": canonical_sha256(reconfigured),
            "replacement_payload": {
                **reconfigured["payload"],
                "changes": {"name": "Principal design lead", "skills": ["typescript"]},
            },
        },
        reconfigured["event_id"],
    )

    snapshot = project_events([created, reconfigured, retired, correction])
    record = snapshot.agents[agent_id]

    assert isinstance(record, AgentRecord)
    assert record.agent_id == agent_id
    assert record.state == "retired"
    assert record.revision == retired["event_id"]
    assert record.effective_revision == retired["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "active"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "active"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    model = exposed_extensions["model"]
    assert isinstance(model, dict)
    model["name"] = "tampered"
    tags = model["tags"]
    assert isinstance(tags, list)
    tags.append("tampered")
    assert record["extensions"] == {"model": {"name": "claude-opus", "tags": ["design"]}}

    expected = {
        "agent_id": agent_id,
        "name": "Principal design lead",
        "profile_id": "claude-builder",
        "grants": {"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
        "context_mode": "fresh",
        "origin": "human",
        "approval": "human",
        "rationale": "Own the design system boundary.",
        "lifetime": {"kind": "persistent"},
        "turnover_budget": 4,
        "extensions": {"model": {"name": "claude-opus", "tags": ["design"]}},
        "id": agent_id,
        "state": "retired",
        "revision": retired["event_id"],
        "effective_revision": retired["event_id"],
        "recorded_at": retired["recorded_at"],
        "actor": {"session_id": "session.test", "role_id": "builder"},
        "author_session_ids": ["session.test"],
        "created_by_agent_id": None,
        "template": False,
        "created_event_id": created["event_id"],
        "expected_revision": reconfigured["event_id"],
        "reason": "The programme completed.",
        "skills": ["typescript"],
        "retired_by": "human",
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["agents"] == [expected]
    assert json.dumps(snapshot.to_dict()["agents"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.issues == []


def test_agent_record_remains_a_mapping_for_lineage_and_grant_consumers() -> None:
    root_id = "agent.00000000000000000000000001"
    child_id = "agent.00000000000000000000000002"
    root = AgentRecord.from_projected_data(
        {
            "agent_id": root_id,
            "id": root_id,
            "state": "active",
            "revision": "evt.00000000000000000000000001",
            "effective_revision": "evt.00000000000000000000000001",
            "grants": {"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
        }
    )
    child = AgentRecord.from_projected_data(
        {
            "agent_id": child_id,
            "id": child_id,
            "state": "active",
            "revision": "evt.00000000000000000000000002",
            "effective_revision": "evt.00000000000000000000000002",
            "created_by_agent_id": root_id,
            "grants": {"create_roles": "auto", "retire_roles": "ask", "open_links": "deny"},
        }
    )
    agents = {root_id: root, child_id: child}

    assert [record["id"] for record in lineage(agents, child_id)] == [child_id, root_id]
    assert effective_grants(agents, child_id) == {
        "create_roles": "ask",
        "retire_roles": "deny",
        "open_links": "deny",
    }
    assert turnover_used(agents, root_id) == 1
