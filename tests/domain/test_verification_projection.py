from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_commons.domain.projection import project_events
from agent_commons.domain.verification_projection import VerificationRecord


def _event(
    number: int, event_type: str, payload: dict[str, object], subject_kind: str, subject_id: str
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


def test_verification_projection_record_is_frozen_and_preserves_legacy_read_shape() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    verification_id = "verification.00000000000000000000000001"
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
    verification = _event(
        2,
        "verification.recorded",
        {
            "verification_id": verification_id,
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": registered["event_id"],
            "claim": "artifact is present",
            "evidence_refs": [
                {
                    "ref": {"kind": "artifact", "id": artifact_id},
                    "revision": registered["event_id"],
                }
            ],
            "method": "manifest inspection",
            "outcome": "passed",
        },
        "verification",
        verification_id,
    )

    snapshot = project_events([registered, verification])
    record = snapshot.verifications[verification_id]

    assert isinstance(record, VerificationRecord)
    assert record["claim"] == "artifact is present"
    assert record["stale"] is False
    with pytest.raises(FrozenInstanceError):
        record.stale = True  # type: ignore[misc]
    exposed_actor = record["actor"]
    assert isinstance(exposed_actor, dict)
    exposed_actor["role_id"] = "tampered"
    assert record["actor"] == {"session_id": "session.test", "role_id": "builder"}
    assert snapshot.to_dict()["verifications"] == [
        {
            "verification_id": verification_id,
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": registered["event_id"],
            "claim": "artifact is present",
            "evidence_refs": [
                {
                    "ref": {"kind": "artifact", "id": artifact_id},
                    "revision": registered["event_id"],
                }
            ],
            "method": "manifest inspection",
            "outcome": "passed",
            "id": verification_id,
            "state": "recorded",
            "revision": verification["event_id"],
            "effective_revision": verification["event_id"],
            "recorded_at": verification["recorded_at"],
            "actor": {"session_id": "session.test", "role_id": "builder"},
            "author_session_ids": ["session.test"],
            "stale": False,
        }
    ]
