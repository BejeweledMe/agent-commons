from __future__ import annotations

from copy import deepcopy

import pytest

from agent_commons.domain.envelopes import parse_event_envelope, serialize_event_envelope
from agent_commons.domain.task_review_envelopes import parse_task_review_envelope
from agent_commons.domain.validation import EVENT_SPECS
from agent_commons.errors import ValidationError

ULID_0 = "0" * 26
ULID_1 = "0" * 25 + "1"
ULID_2 = "0" * 25 + "2"
TASK_ID = f"task.{ULID_0}"
REVIEW_ID = f"review.{ULID_0}"
EVENT_ID = f"evt.{ULID_1}"
TARGET_REVISION = f"evt.{ULID_2}"

_ARTIFACT_REF: dict[str, object] = {"kind": "artifact", "id": f"artifact.{ULID_0}"}
_REVIEW_BINDING: dict[str, object] = {
    "ref": {"kind": "review", "id": REVIEW_ID},
    "revision": TARGET_REVISION,
}

PAYLOADS: dict[str, dict[str, object]] = {
    "task.created": {
        "task_id": TASK_ID,
        "title": "Implement endpoint",
        "description": "Build the health endpoint",
        "acceptance_criteria": ["returns 200"],
        "priority": "normal",
    },
    "task.revised": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "changes": {"description": "Build the corrected health endpoint"},
    },
    "task.taken": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "owner_session_id": "session.builder-stable",
    },
    "task.started": {"task_id": TASK_ID, "expected_revision": EVENT_ID},
    "task.blocked": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "waiting for an interface",
    },
    "task.unblocked": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "resolution": "interface agreed",
    },
    "task.completed": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "implementation complete",
    },
    "task.submitted": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "ready for review",
    },
    "task.accepted": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "acceptance criteria met",
        "acceptance_review": _REVIEW_BINDING,
    },
    "task.cancelled": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "superseded",
    },
    "task.reopened": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "regression found",
    },
    "review.requested": {
        "review_id": REVIEW_ID,
        "target_ref": _ARTIFACT_REF,
        "target_revision": TARGET_REVISION,
        "criteria": ["correctness"],
        "independent": True,
    },
    "review.completed": {
        "review_id": REVIEW_ID,
        "expected_revision": EVENT_ID,
        "target_revision": TARGET_REVISION,
        "verdict": "approved",
        "summary": "criteria satisfied",
    },
}


@pytest.mark.parametrize("event_type", PAYLOADS)
def test_direct_parser_round_trips_valid_task_and_review_payloads(event_type: str) -> None:
    payload = deepcopy(PAYLOADS[event_type])

    envelope = parse_task_review_envelope(event_type, payload)

    assert envelope is not None
    assert serialize_event_envelope(envelope) == payload


@pytest.mark.parametrize(
    ("event_type", "required_field"),
    [(event_type, field) for event_type in PAYLOADS for field in EVENT_SPECS[event_type].required],
)
def test_direct_parser_rejects_each_missing_required_field(
    event_type: str, required_field: str
) -> None:
    payload = deepcopy(PAYLOADS[event_type])
    del payload[required_field]

    with pytest.raises(ValidationError):
        parse_task_review_envelope(event_type, payload)


@pytest.mark.parametrize(
    ("event_type", "updates"),
    [
        ("task.created", {"task_id": "task.invalid"}),
        ("task.created", {"priority": "urgent"}),
        ("task.created", {"dependencies": [TASK_ID, TASK_ID]}),
        ("task.revised", {"changes": {"acceptance_criteria": []}}),
        (
            "task.completed",
            {"artifact_bindings": [{"ref": _REVIEW_BINDING["ref"], "revision": TARGET_REVISION}]},
        ),
        (
            "task.accepted",
            {
                "acceptance_review": {
                    "ref": _ARTIFACT_REF,
                    "revision": TARGET_REVISION,
                }
            },
        ),
        ("review.requested", {"target_ref": {"kind": "task"}}),
        ("review.requested", {"criteria": []}),
        ("review.requested", {"independent": "true"}),
        ("review.requested", {"unexpected": "field"}),
        ("review.completed", {"verdict": "accept"}),
        (
            "review.completed",
            {"evidence_refs": [{"ref": _ARTIFACT_REF, "revision": ""}]},
        ),
    ],
)
def test_direct_parser_rejects_malformed_task_and_review_contracts(
    event_type: str, updates: dict[str, object]
) -> None:
    payload = deepcopy(PAYLOADS[event_type])
    payload.update(updates)

    with pytest.raises(ValidationError):
        parse_task_review_envelope(event_type, payload)


def test_event_dispatcher_is_fail_closed_for_direct_invalid_task_input() -> None:
    payload = deepcopy(PAYLOADS["task.created"])
    payload["priority"] = "urgent"

    with pytest.raises(ValidationError):
        parse_event_envelope("task.created", payload)


def test_direct_parser_keeps_unknown_event_families_at_the_existing_boundary() -> None:
    assert parse_task_review_envelope("not.a.known.event", {}) is None
