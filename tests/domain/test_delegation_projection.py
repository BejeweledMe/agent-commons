from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from agent_commons.core.canonical import canonical_json_bytes, canonical_sha256, loads_json_strict
from agent_commons.domain.delegation_projection import DelegationRecord
from agent_commons.domain.envelopes import DelegationEnvelope, parse_event_envelope
from agent_commons.domain.projection import project_events

WORKSPACE_ID = "workspace.00000000000000000000000001"
TASK_ID = "task.00000000000000000000000001"
DELEGATION_ID = "delegation.00000000000000000000000001"
PARENT_SESSION_ID = "session." + "a" * 32
CHILD_SESSION_ID = "session." + "b" * 32


def _event(
    number: int,
    event_type: str,
    payload: dict[str, object],
    *,
    actor_session_id: str,
    subject_kind: str,
    subject_id: str,
) -> dict[str, object]:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:00:{number:02d}Z",
        "actor": {"session_id": actor_session_id, "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "relations": [],
    }


def _persisted(event: dict[str, object]) -> dict[str, object]:
    """Round-trip canonical bytes to make persisted event order the oracle."""

    persisted = loads_json_strict(canonical_json_bytes(event))
    assert isinstance(persisted, dict)
    return persisted


def _legacy_apply(
    current: dict[str, object], event: dict[str, object], state: str
) -> dict[str, object]:
    """Apply a delegation exactly as the immediate pre-slice projection did."""

    raw_payload = event["payload"]
    assert isinstance(raw_payload, dict)
    envelope = parse_event_envelope(str(event["event_type"]), raw_payload)
    assert isinstance(envelope, DelegationEnvelope)
    # This is the pre-slice delegation branch's actual payload source.  It is
    # intentionally not the event's raw mapping: that would change public
    # insertion order when source events order equivalent keys differently.
    payload = envelope.to_payload()
    actor = event["actor"]
    assert isinstance(actor, dict)
    authors = {
        str(session_id) for session_id in current.get("author_session_ids", []) if str(session_id)
    }
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    return {
        **current,
        **deepcopy(payload),
        "id": str(event["payload"]["delegation_id"]),
        "state": state,
        "revision": str(event["event_id"]),
        "effective_revision": str(event.get("_effective_correction_id") or event["event_id"]),
        "recorded_at": event["recorded_at"],
        "actor": deepcopy(actor),
        "author_session_ids": sorted(authors),
    }


def test_delegation_record_preserves_canonical_replay_bytes_through_correction() -> None:
    """The frozen projection exactly retains the old raw replay wire contract."""

    task = _persisted(
        _event(
            1,
            "task.created",
            {
                "task_id": TASK_ID,
                "title": "Prepare an immutable delegation projection.",
                "description": "Replay the historical delegation unchanged.",
                "acceptance_criteria": ["Wire output preserves canonical order."],
                "priority": "high",
            },
            actor_session_id=PARENT_SESSION_ID,
            subject_kind="task",
            subject_id=TASK_ID,
        )
    )
    requested = _persisted(
        _event(
            2,
            "delegation.requested",
            {
                "target_profile": "codex-builder",
                "delegation_id": DELEGATION_ID,
                "limits": {
                    "max_concurrency": 1,
                    "budget": {"limit": 10000, "unit": "tokens"},
                    "max_attempts": 2,
                    "wall_time_seconds": 900,
                    "max_depth": 1,
                },
                "target_ref": {"id": TASK_ID, "kind": "task"},
                "purpose": "implementation",
                "target_revision": task["event_id"],
                "root_delegation_id": DELEGATION_ID,
                "parent_session_id": PARENT_SESSION_ID,
                "depth": 0,
            },
            actor_session_id=PARENT_SESSION_ID,
            subject_kind="delegation",
            subject_id=DELEGATION_ID,
        )
    )
    started = _persisted(
        _event(
            3,
            "delegation.started",
            {
                "child_session_id": CHILD_SESSION_ID,
                "delegation_id": DELEGATION_ID,
                "attempt": 1,
                "expected_revision": requested["event_id"],
            },
            actor_session_id=PARENT_SESSION_ID,
            subject_kind="delegation",
            subject_id=DELEGATION_ID,
        )
    )
    succeeded = _persisted(
        _event(
            4,
            "delegation.succeeded",
            {
                "summary": "The initial outcome had a wording defect.",
                "result_refs": [{"id": TASK_ID, "kind": "task"}],
                "delegation_id": DELEGATION_ID,
                "expected_revision": started["event_id"],
            },
            actor_session_id=CHILD_SESSION_ID,
            subject_kind="delegation",
            subject_id=DELEGATION_ID,
        )
    )
    succeeded_payload = succeeded["payload"]
    assert isinstance(succeeded_payload, dict)
    correction = _persisted(
        _event(
            5,
            "event.corrected",
            {
                "replacement_payload": {
                    **succeeded_payload,
                    "summary": "The corrected outcome preserves replay order.",
                },
                "target_event_id": succeeded["event_id"],
                "expected_target_sha256": canonical_sha256(succeeded),
            },
            actor_session_id=PARENT_SESSION_ID,
            subject_kind="event",
            subject_id=str(succeeded["event_id"]),
        )
    )

    correction_payload = correction["payload"]
    assert isinstance(correction_payload, dict)
    replacement_payload = correction_payload["replacement_payload"]
    assert isinstance(replacement_payload, dict)
    corrected_succeeded: dict[str, object] = {
        **succeeded,
        "payload": replacement_payload,
        "_effective_correction_id": correction["event_id"],
    }
    legacy_wire: dict[str, object] = {}
    for event, state in (
        (requested, "requested"),
        (started, "active"),
        (corrected_succeeded, "succeeded"),
    ):
        legacy_wire = _legacy_apply(legacy_wire, event, state)

    snapshot = project_events([task, requested, started, succeeded, correction])
    record = snapshot.delegations[DELEGATION_ID]

    assert isinstance(record, DelegationRecord)
    assert record.delegation_id == DELEGATION_ID
    assert record.state == "succeeded"
    assert record.revision == succeeded["event_id"]
    assert record.effective_revision == correction["event_id"]
    with pytest.raises(FrozenInstanceError):
        record.state = "failed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        record["state"] = "failed"  # type: ignore[index]

    exposed_limits = record["limits"]
    assert isinstance(exposed_limits, dict)
    exposed_budget = exposed_limits["budget"]
    assert isinstance(exposed_budget, dict)
    exposed_budget["limit"] = 1
    exposed_results = record["result_refs"]
    assert isinstance(exposed_results, list)
    exposed_result = exposed_results[0]
    assert isinstance(exposed_result, dict)
    exposed_result["id"] = "task.tampered"
    assert record["limits"] == legacy_wire["limits"]
    assert record["result_refs"] == legacy_wire["result_refs"]

    legacy_target_ref = legacy_wire["target_ref"]
    actual_target_ref = record["target_ref"]
    assert isinstance(legacy_target_ref, dict)
    assert isinstance(actual_target_ref, dict)
    assert list(actual_target_ref) == list(legacy_target_ref)
    assert json.dumps(actual_target_ref, separators=(",", ":")) == json.dumps(
        legacy_target_ref, separators=(",", ":")
    )

    legacy_limits = legacy_wire["limits"]
    actual_limits = record["limits"]
    assert isinstance(legacy_limits, dict)
    assert isinstance(actual_limits, dict)
    assert list(actual_limits) == list(legacy_limits)
    assert json.dumps(actual_limits, separators=(",", ":")) == json.dumps(
        legacy_limits, separators=(",", ":")
    )

    assert list(record) == list(legacy_wire)
    assert record.to_dict() == legacy_wire
    assert json.dumps(record.to_dict(), separators=(",", ":")) == json.dumps(
        legacy_wire, separators=(",", ":")
    )
    assert snapshot.to_dict()["delegations"] == [legacy_wire]
    assert snapshot.issues == []
