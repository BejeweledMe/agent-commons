from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.decision_projection import DecisionRecord
from agent_commons.domain.projection import project_events
from agent_commons.views import orientation, render_views


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
        "actor": {"session_id": "session.builder", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "relations": [],
    }


def test_decision_record_is_frozen_and_preserves_replay_wire_and_truth_views(tmp_path) -> None:
    decision_id = "decision.00000000000000000000000001"
    proposed = _event(
        1,
        "decision.proposed",
        {
            "decision_id": decision_id,
            "scope": "architecture.projection",
            "proposal": "Freeze the projected decision record.",
            "alternatives": ["Leave mutable dictionaries."],
            "extensions": {"source": {"kind": "audit", "priority": "high"}},
        },
        "decision",
        decision_id,
    )
    accepted = _event(
        2,
        "decision.accepted",
        {
            "decision_id": decision_id,
            "expected_revision": proposed["event_id"],
            "rationale": "It gives decision consumers an immutable boundary.",
            "evidence_refs": [],
            "dissent": ["Keep the mapping read shape."],
        },
        "decision",
        decision_id,
    )
    accepted["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    correction = _event(
        3,
        "event.corrected",
        {
            "target_event_id": accepted["event_id"],
            "expected_target_sha256": canonical_sha256(accepted),
            "replacement_payload": {
                **accepted["payload"],
                "rationale": "It gives truth consumers an immutable boundary.",
            },
        },
        "event",
        accepted["event_id"],
    )

    snapshot = project_events([proposed, accepted, correction])
    record = snapshot.decisions[decision_id]

    assert isinstance(record, DecisionRecord)
    assert record.decision_id == decision_id
    assert record.state == "accepted"
    assert record.revision == accepted["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "rejected"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "rejected"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    source = exposed_extensions["source"]
    assert isinstance(source, dict)
    source["priority"] = "tampered"
    exposed_alternatives = record["alternatives"]
    assert isinstance(exposed_alternatives, list)
    exposed_alternatives.append("tampered")
    assert record["extensions"] == {"source": {"kind": "audit", "priority": "high"}}
    assert record["alternatives"] == ["Leave mutable dictionaries."]

    expected = {
        "decision_id": decision_id,
        "scope": "architecture.projection",
        "proposal": "Freeze the projected decision record.",
        "alternatives": ["Leave mutable dictionaries."],
        "extensions": {"source": {"kind": "audit", "priority": "high"}},
        "id": decision_id,
        "state": "accepted",
        "revision": accepted["event_id"],
        "effective_revision": correction["event_id"],
        "recorded_at": accepted["recorded_at"],
        "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
        "author_session_ids": ["session.builder", "session.reviewer"],
        "expected_revision": proposed["event_id"],
        "rationale": "It gives truth consumers an immutable boundary.",
        "evidence_refs": [],
        "dissent": ["Keep the mapping read shape."],
        "stale": False,
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["decisions"] == [expected]
    assert json.dumps(snapshot.to_dict()["decisions"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert [item["id"] for item in orientation(snapshot)["effective_truth"]["decisions"]] == [
        decision_id
    ]
    render_views(snapshot, tmp_path / "views")
    decisions = (tmp_path / "views" / "DECISIONS.md").read_text(encoding="utf-8")
    current = (tmp_path / "views" / "CURRENT.md").read_text(encoding="utf-8")
    assert decision_id in decisions
    assert decision_id in current
    assert snapshot.issues == []


def test_decision_record_preserves_terminal_transitions_and_fail_closed_conflicts() -> None:
    deferred_id = "decision.00000000000000000000000001"
    rejected_id = "decision.00000000000000000000000002"
    superseded_id = "decision.00000000000000000000000003"
    replacement_id = "decision.00000000000000000000000004"
    conflict_one = "decision.00000000000000000000000005"
    conflict_two = "decision.00000000000000000000000006"
    events = [
        _event(
            1,
            "decision.proposed",
            {
                "decision_id": deferred_id,
                "scope": "architecture.deferred",
                "proposal": "Defer this choice.",
                "alternatives": [],
            },
            "decision",
            deferred_id,
        ),
        _event(
            2,
            "decision.deferred",
            {
                "decision_id": deferred_id,
                "expected_revision": "evt.00000000000000000000000001",
                "reason": "Need more evidence.",
            },
            "decision",
            deferred_id,
        ),
        _event(
            3,
            "decision.proposed",
            {
                "decision_id": rejected_id,
                "scope": "architecture.rejected",
                "proposal": "Reject this choice.",
                "alternatives": [],
            },
            "decision",
            rejected_id,
        ),
        _event(
            4,
            "decision.rejected",
            {
                "decision_id": rejected_id,
                "expected_revision": "evt.00000000000000000000000003",
                "rationale": "The alternative is safer.",
            },
            "decision",
            rejected_id,
        ),
        _event(
            5,
            "decision.proposed",
            {
                "decision_id": superseded_id,
                "scope": "architecture.replacement",
                "proposal": "Original choice.",
                "alternatives": [],
            },
            "decision",
            superseded_id,
        ),
        _event(
            6,
            "decision.accepted",
            {
                "decision_id": superseded_id,
                "expected_revision": "evt.00000000000000000000000005",
                "rationale": "Selected initially.",
                "evidence_refs": [],
                "dissent": [],
            },
            "decision",
            superseded_id,
        ),
        _event(
            7,
            "decision.proposed",
            {
                "decision_id": replacement_id,
                "scope": "architecture.replacement",
                "proposal": "Replacement choice.",
                "alternatives": [],
            },
            "decision",
            replacement_id,
        ),
        _event(
            8,
            "decision.superseded",
            {
                "decision_id": superseded_id,
                "expected_revision": "evt.00000000000000000000000006",
                "replacement_decision_id": replacement_id,
                "reason": "A better choice is ready.",
            },
            "decision",
            superseded_id,
        ),
    ]
    for number, decision_id in ((9, conflict_one), (11, conflict_two)):
        events.append(
            _event(
                number,
                "decision.proposed",
                {
                    "decision_id": decision_id,
                    "scope": "architecture.conflict",
                    "proposal": f"Choice {number}.",
                    "alternatives": [],
                },
                "decision",
                decision_id,
            )
        )
        events.append(
            _event(
                number + 1,
                "decision.accepted",
                {
                    "decision_id": decision_id,
                    "expected_revision": f"evt.{number:026d}",
                    "rationale": "Selected on a separate branch.",
                    "evidence_refs": [],
                    "dissent": [],
                },
                "decision",
                decision_id,
            )
        )

    snapshot = project_events(events)

    assert snapshot.decisions[deferred_id]["state"] == "deferred"
    assert snapshot.decisions[rejected_id]["state"] == "rejected"
    assert snapshot.decisions[superseded_id]["state"] == "superseded"
    for decision_id in (conflict_one, conflict_two):
        record = snapshot.decisions[decision_id]
        assert isinstance(record, DecisionRecord)
        assert record.state == "conflicted"
        assert record["conflict"] is True
    assert any("conflicting accepted decisions" in warning for warning in snapshot.warnings)
