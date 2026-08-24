from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.acceptance import select_qualifying_review
from agent_commons.domain.projection import project_events
from agent_commons.domain.review_projection import ReviewRecord
from agent_commons.domain.snapshot import ProjectSnapshot


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


def test_review_record_is_frozen_and_preserves_replay_wire_and_staleness() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    artifact_ref = {"kind": "artifact", "id": artifact_id}
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
    requested = _event(
        2,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": artifact_ref,
            "target_revision": registered["event_id"],
            "criteria": ["correctness"],
            "independent": False,
        },
        "review",
        review_id,
    )
    completed = _event(
        3,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": requested["event_id"],
            "target_revision": registered["event_id"],
            "verdict": "approved",
            "summary": "looks good",
            "evidence_refs": [{"ref": artifact_ref, "revision": registered["event_id"]}],
        },
        "review",
        review_id,
    )
    completed["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    correction = _event(
        4,
        "event.corrected",
        {
            "target_event_id": completed["event_id"],
            "expected_target_sha256": canonical_sha256(completed),
            "replacement_payload": {**completed["payload"], "summary": "clarified approval"},
        },
        "event",
        completed["event_id"],
    )

    snapshot = project_events([registered, requested, completed, correction])
    record = snapshot.reviews[review_id]

    assert isinstance(record, ReviewRecord)
    assert record.review_id == review_id
    assert record.state == "approved"
    assert record.revision == completed["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "requested"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "requested"  # type: ignore[index]

    exposed_criteria = record["criteria"]
    assert isinstance(exposed_criteria, list)
    exposed_criteria.append("tampered")
    exposed_evidence = record["evidence_refs"]
    assert isinstance(exposed_evidence, list)
    exposed_ref = exposed_evidence[0]["ref"]
    assert isinstance(exposed_ref, dict)
    exposed_ref["id"] = "artifact.tampered"
    assert record["criteria"] == ["correctness"]
    assert record["evidence_refs"] == [{"ref": artifact_ref, "revision": registered["event_id"]}]

    expected = {
        "review_id": review_id,
        "target_ref": artifact_ref,
        "target_revision": registered["event_id"],
        "criteria": ["correctness"],
        "independent": False,
        "id": review_id,
        "state": "approved",
        "revision": completed["event_id"],
        "effective_revision": correction["event_id"],
        "recorded_at": completed["recorded_at"],
        "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
        "author_session_ids": ["session.reviewer", "session.test"],
        "expected_revision": requested["event_id"],
        "verdict": "approved",
        "summary": "clarified approval",
        "evidence_refs": [{"ref": artifact_ref, "revision": registered["event_id"]}],
        "producer_agent_ids": [],
        "producer_context_mode": None,
        "producer_prior_verdict_count": 0,
        "stale": False,
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["reviews"] == [expected]
    assert json.dumps(snapshot.to_dict()["reviews"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.issues == []

    revised = _event(
        5,
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
    stale_snapshot = project_events([registered, requested, completed, correction, revised])
    assert stale_snapshot.reviews[review_id]["stale"] is True


def test_review_record_remains_a_mapping_for_task_acceptance() -> None:
    review_id = "review.00000000000000000000000001"
    review = ReviewRecord.from_projected_data(
        {
            "review_id": review_id,
            "id": review_id,
            "state": "approved",
            "revision": "evt.00000000000000000000000001",
            "effective_revision": "evt.00000000000000000000000001",
            "independent": True,
            "stale": False,
            "target_ref": {"kind": "task", "id": "task.1"},
            "target_revision": "evt.current",
            "actor": {"session_id": "session.reviewer"},
            "recorded_at": "2026-08-19T00:00:00Z",
        }
    )
    snapshot = ProjectSnapshot(
        tasks={"task.1": {"id": "task.1", "revision": "evt.current"}},
        reviews={review_id: review},
    )

    assert select_qualifying_review(snapshot, "task.1") is review
