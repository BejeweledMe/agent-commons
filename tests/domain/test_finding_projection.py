from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_json_bytes, canonical_sha256, loads_json_strict
from agent_commons.domain.finding_projection import FindingRecord
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


def test_finding_record_preserves_the_raw_canonical_reported_payload_order() -> None:
    """Replay the persisted event, not a hand-ordered fixture, as the oracle."""

    finding_id = "finding.00000000000000000000000001"
    source_event = _event(
        1,
        "finding.reported",
        {
            "summary": "The persisted payload order is the wire contract.",
            "finding_id": finding_id,
            "extensions": {"audit": {"source": "A5"}},
            "evidence_refs": [],
            "severity": "normal",
        },
        "finding",
        finding_id,
    )
    persisted = loads_json_strict(canonical_json_bytes(source_event))
    assert isinstance(persisted, dict)
    raw_payload = persisted["payload"]
    assert isinstance(raw_payload, dict)

    # This is the raw-dict projection shape before FindingRecord froze it.  Its
    # key order comes from canonical event bytes, not this test's source literal.
    legacy_wire = {
        **raw_payload,
        "id": finding_id,
        "state": "reported",
        "revision": persisted["event_id"],
        "effective_revision": persisted["event_id"],
        "recorded_at": persisted["recorded_at"],
        "actor": persisted["actor"],
        "author_session_ids": ["session.builder"],
        "stale": False,
    }

    snapshot = project_events([persisted])
    record = snapshot.findings[finding_id]

    assert list(record) == list(legacy_wire)
    assert record.to_dict() == legacy_wire
    assert json.dumps(record.to_dict(), separators=(",", ":")) == json.dumps(
        legacy_wire, separators=(",", ":")
    )


def test_finding_record_is_frozen_and_preserves_replay_wire_and_staleness(tmp_path) -> None:
    artifact_id = "artifact.00000000000000000000000001"
    finding_id = "finding.00000000000000000000000001"
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
    evidence = {"ref": {"kind": "artifact", "id": artifact_id}, "revision": registered["event_id"]}
    reported = _event(
        2,
        "finding.reported",
        {
            "finding_id": finding_id,
            "summary": "A projected finding must be immutable.",
            "severity": "high",
            "evidence_refs": [evidence],
            "extensions": {"audit": {"source": "A5", "priority": "high"}},
        },
        "finding",
        finding_id,
    )
    promoted = _event(
        3,
        "finding.promoted",
        {
            "finding_id": finding_id,
            "expected_revision": reported["event_id"],
            "summary": "A projected finding is immutable.",
            "evidence_refs": [evidence],
        },
        "finding",
        finding_id,
    )
    promoted["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    correction = _event(
        4,
        "event.corrected",
        {
            "target_event_id": promoted["event_id"],
            "expected_target_sha256": canonical_sha256(promoted),
            "replacement_payload": {
                **promoted["payload"],
                "summary": "A projected finding stays immutable.",
            },
        },
        "event",
        promoted["event_id"],
    )
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

    snapshot = project_events([registered, reported, promoted, correction, revised])
    record = snapshot.findings[finding_id]

    assert isinstance(record, FindingRecord)
    assert record.finding_id == finding_id
    assert record.state == "verified"
    assert record.revision == promoted["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "resolved"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "resolved"  # type: ignore[index]

    exposed_extensions = record["extensions"]
    assert isinstance(exposed_extensions, dict)
    audit = exposed_extensions["audit"]
    assert isinstance(audit, dict)
    audit["priority"] = "tampered"
    exposed_evidence = record["evidence_refs"]
    assert isinstance(exposed_evidence, list)
    exposed_ref = exposed_evidence[0]
    assert isinstance(exposed_ref, dict)
    exposed_ref["revision"] = "evt.tampered"
    assert record["extensions"] == {"audit": {"source": "A5", "priority": "high"}}
    assert record["evidence_refs"] == [evidence]

    expected = {
        "finding_id": finding_id,
        "summary": "A projected finding stays immutable.",
        "severity": "high",
        "evidence_refs": [evidence],
        "extensions": {"audit": {"source": "A5", "priority": "high"}},
        "id": finding_id,
        "state": "verified",
        "revision": promoted["event_id"],
        "effective_revision": correction["event_id"],
        "recorded_at": promoted["recorded_at"],
        "actor": {"session_id": "session.reviewer", "role_id": "reviewer"},
        "author_session_ids": ["session.builder", "session.reviewer"],
        "expected_revision": reported["event_id"],
        "stale": True,
    }
    assert record.to_dict() == expected
    assert snapshot.to_dict()["findings"] == [expected]
    assert json.dumps(snapshot.to_dict()["findings"], separators=(",", ":")) == json.dumps(
        [expected], separators=(",", ":")
    )
    assert orientation(snapshot)["effective_truth"] == {"decisions": [], "findings": []}
    render_views(snapshot, tmp_path / "views")
    risks = (tmp_path / "views" / "KNOWN_RISKS.md").read_text(encoding="utf-8")
    assert "## Reported or contested findings\n\n- None\n" in risks
    assert any(f"finding {finding_id} has stale" in warning for warning in snapshot.warnings)
    assert snapshot.issues == []
