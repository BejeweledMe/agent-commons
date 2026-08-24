from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.artifact_projection import ArtifactRecord
from agent_commons.domain.lifecycle import entity
from agent_commons.domain.projection import _current_evidence_revision, project_events
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.services.artifacts import ArtifactCommands


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


def test_artifact_record_is_frozen_and_preserves_replay_wire_and_staleness() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    registered = _event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
            "extensions": {"preview": {"media_type": "image/png", "width": 640}},
        },
        "artifact",
        artifact_id,
    )
    revised = _event(
        2,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": registered["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
            "extensions": {"preview": {"media_type": "image/png", "width": 800}},
        },
        "artifact",
        artifact_id,
    )
    revised["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    requested = _event(
        3,
        "review.requested",
        {
            "review_id": "review.00000000000000000000000001",
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": revised["event_id"],
            "criteria": ["manifest current"],
            "independent": False,
        },
        "review",
        "review.00000000000000000000000001",
    )
    correction = _event(
        4,
        "event.corrected",
        {
            "target_event_id": revised["event_id"],
            "expected_target_sha256": canonical_sha256(revised),
            "replacement_payload": {
                **revised["payload"],
                "extensions": {"preview": {"media_type": "image/png", "width": 1024}},
            },
        },
        "event",
        revised["event_id"],
    )

    snapshot = project_events([registered, revised, requested, correction])
    record = snapshot.artifacts[artifact_id]

    assert isinstance(record, ArtifactRecord)
    assert record.artifact_id == artifact_id
    assert record.state == "registered"
    assert record.revision == revised["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "retired"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "retired"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    preview = exposed_extensions["preview"]
    assert isinstance(preview, dict)
    preview["width"] = 1
    exposed_actor = record["actor"]
    assert isinstance(exposed_actor, dict)
    exposed_actor["role_id"] = "tampered"
    assert record["extensions"] == {"preview": {"media_type": "image/png", "width": 1024}}
    assert record["actor"] == {"session_id": "session.reviewer", "role_id": "reviewer"}

    expected = {
        "artifact_id": artifact_id,
        "manifest_ref": "mft.artifact.sha256." + "2" * 64,
        "classification": "internal",
        "extensions": {"preview": {"media_type": "image/png", "width": 1024}},
        "content_revision": "sha256:" + "2" * 64,
        "evidence_author_session_ids": ["session.builder", "session.reviewer"],
        "id": artifact_id,
        "state": "registered",
        "revision": revised["event_id"],
        "effective_revision": correction["event_id"],
        "recorded_at": revised["recorded_at"],
        "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
        "author_session_ids": ["session.builder", "session.reviewer"],
        "expected_revision": registered["event_id"],
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["artifacts"] == [expected]
    assert json.dumps(snapshot.to_dict()["artifacts"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert snapshot.reviews["review.00000000000000000000000001"]["stale"] is True
    assert snapshot.issues == []


class _PreviewBundleSubject:
    def __init__(self, snapshot: ProjectSnapshot, manifest: dict[str, object]) -> None:
        self._snapshot = snapshot
        self.manifests = SimpleNamespace(
            get=lambda _manifest_ref: SimpleNamespace(manifest=manifest)
        )

    def snapshot(self) -> ProjectSnapshot:
        return self._snapshot


def test_artifact_record_remains_a_mapping_for_evidence_and_preview_bundle() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    manifest_ref = "mft.artifact.sha256." + "1" * 64
    content_revision = "sha256:" + "1" * 64
    record = ArtifactRecord.from_projected_data(
        {
            "artifact_id": artifact_id,
            "manifest_ref": manifest_ref,
            "content_revision": content_revision,
            "classification": "internal",
            "id": artifact_id,
            "state": "registered",
            "revision": "evt.00000000000000000000000001",
            "effective_revision": "evt.00000000000000000000000002",
            "recorded_at": "2026-08-24T00:00:00Z",
            "actor": {"session_id": "session.builder"},
        }
    )
    snapshot = ProjectSnapshot(
        artifacts={artifact_id: record},
        known_manifest_ids={manifest_ref},
    )
    manifest = {
        "artifact_id": artifact_id,
        "revision": content_revision,
        "source": {"path": "docs/screen.png"},
        "media_type": "image/png",
        "size_bytes": 1,
        "classification": "internal",
    }
    subject = _PreviewBundleSubject(snapshot, manifest)

    assert entity(snapshot, "artifact", artifact_id) is record
    assert _current_evidence_revision(snapshot, {"kind": "artifact", "id": artifact_id}) == (
        "evt.00000000000000000000000002"
    )
    bundle = ArtifactCommands.get_artifact_bundle(subject, artifact_id)
    assert bundle == {"artifact": record.to_dict(), "manifest": manifest}
