from __future__ import annotations

import pytest

from agent_commons.domain.lifecycle import validate_transition
from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.domain.validation import EVENT_SPECS, validate_payload
from agent_commons.errors import LifecycleConflictError, ValidationError

EVENT_ID = "evt.00000000000000000000000001"
TASK_ID = "task.00000000000000000000000001"
THREAD_ID = "thread.00000000000000000000000001"
MESSAGE_ID = "message.00000000000000000000000001"
OBJECTIVE_ID = "objective.00000000000000000000000001"
DECISION_ID = "decision.00000000000000000000000001"
REPLACEMENT_ID = "decision.00000000000000000000000002"
REVIEW_ID = "review.00000000000000000000000001"
REVIEW_REVISION = "evt.00000000000000000000000002"
AGENT_ID = "agent.00000000000000000000000001"
ACTOR_AGENT_ID = "agent.00000000000000000000000002"
DELEGATION_ID = "delegation.00000000000000000000000001"


def _human_created_role_payload() -> dict[str, object]:
    return {
        "agent_id": AGENT_ID,
        "name": "Backend engineer",
        "profile_id": "codex-builder",
        "grants": {"create_roles": "deny", "retire_roles": "deny", "open_links": "deny"},
        "context_mode": "fresh",
        "origin": "human",
        "approval": "human",
        "rationale": "Own the implementation boundary.",
        "lifetime": {"kind": "persistent"},
    }


def test_thread_reply_spec_requires_cas_revision() -> None:
    assert "expected_revision" in EVENT_SPECS["thread.replied"].required
    with pytest.raises(ValidationError, match="expected_revision"):
        validate_payload(
            "thread.replied",
            {
                "thread_id": THREAD_ID,
                "message_id": MESSAGE_ID,
                "body": "reply",
            },
        )


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        (
            "task.created",
            {
                "task_id": TASK_ID,
                "title": "Task",
                "description": "Description",
                "acceptance_criteria": ["valid", 3],
                "priority": "normal",
            },
            r"acceptance_criteria\[1\]",
        ),
        (
            "review.requested",
            {
                "review_id": "review.00000000000000000000000001",
                "target_ref": {"kind": "artifact", "id": "artifact.1", "extra": True},
                "target_revision": EVENT_ID,
                "criteria": ["correctness"],
                "independent": True,
            },
            "valid typed reference",
        ),
        (
            "finding.reported",
            {
                "finding_id": "finding.00000000000000000000000001",
                "summary": "Finding",
                "severity": "high",
                "evidence_refs": ["artifact:not-a-ref-object"],
            },
            r"evidence_refs\[0\]",
        ),
    ],
)
def test_payload_validation_rejects_malformed_list_children_and_refs(
    event_type: str, payload: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_payload(event_type, payload)


def test_objective_changes_are_nonempty_and_closed_world() -> None:
    base = {"objective_id": OBJECTIVE_ID, "expected_revision": EVENT_ID}
    with pytest.raises(ValidationError, match="non-empty object"):
        validate_payload("objective.revised", {**base, "changes": {}})
    with pytest.raises(ValidationError, match="unsupported objective fields"):
        validate_payload(
            "objective.revised",
            {**base, "changes": {"objective_id": "objective.other"}},
        )
    with pytest.raises(ValidationError, match=r"changes.acceptance_criteria\[0\]"):
        validate_payload(
            "objective.revised",
            {**base, "changes": {"acceptance_criteria": [""]}},
        )


def test_correction_hash_and_parent_ids_are_strict() -> None:
    base = {
        "target_event_id": EVENT_ID,
        "replacement_payload": {"summary": "replacement"},
    }
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        validate_payload(
            "event.corrected",
            {**base, "expected_target_sha256": "A" * 64},
        )
    with pytest.raises(ValidationError, match=r"evt\.<ULID>"):
        validate_payload(
            "event.corrected",
            {
                **base,
                "expected_target_sha256": "a" * 64,
                "superseded_correction_event_ids": ["evt.bad"],
            },
        )


def test_role_payload_validation_preserves_specific_error_order() -> None:
    created = _human_created_role_payload()
    created["grants"] = {"create_roles": "invalid"}
    created["profile_id"] = "invalid-profile"
    with pytest.raises(ValidationError, match="grants must contain exactly"):
        validate_payload("agent.created", created)

    created = _human_created_role_payload()
    created["profile_id"] = "invalid-profile"
    created["origin"] = "invalid-origin"
    with pytest.raises(ValidationError, match="invalid agent profile_id"):
        validate_payload("agent.created", created)

    with pytest.raises(ValidationError, match="changes contains immutable agent fields: immutable"):
        validate_payload(
            "agent.reconfigured",
            {
                "agent_id": AGENT_ID,
                "expected_revision": EVENT_ID,
                "changes": {"immutable": "value", "context_mode": "invalid-mode"},
                "reason": "Correct configuration.",
            },
        )

    with pytest.raises(ValidationError, match="retired_by must be human, agent, or cascade"):
        validate_payload(
            "agent.retired",
            {
                "agent_id": AGENT_ID,
                "expected_revision": EVENT_ID,
                "reason": "The role is no longer needed.",
                "retired_by": "invalid-retirer",
                "cascade_of": "agent.invalid",
            },
        )

    with pytest.raises(ValidationError, match="invalid link allowed_action"):
        validate_payload(
            "agent.link_opened",
            {
                "link_id": "link.00000000000000000000000001",
                "from_agent_id": AGENT_ID,
                "to_agent_id": AGENT_ID,
                "allowed_action": "invalid-action",
                "reason": "Coordinate the implementation.",
            },
        )


def test_role_lifecycle_validation_preserves_guard_order() -> None:
    actor = {
        "id": ACTOR_AGENT_ID,
        "state": "active",
        "revision": EVENT_ID,
        "origin": "human",
    }
    actor_binding = {
        "id": DELEGATION_ID,
        "agent_id": ACTOR_AGENT_ID,
        "child_session_id": "session.role",
        "state": "succeeded",
    }
    with pytest.raises(
        LifecycleConflictError,
        match="a session running as a role cannot record a human-created role",
    ):
        validate_transition(
            ProjectSnapshot(
                agents={ACTOR_AGENT_ID: actor},
                delegations={DELEGATION_ID: actor_binding},
            ),
            "agent.created",
            _human_created_role_payload(),
            actor_session_id="session.role",
        )

    retiring_role = {
        "id": AGENT_ID,
        "state": "active",
        "revision": EVENT_ID,
        "origin": "human",
    }
    with pytest.raises(LifecycleConflictError, match="a role owing live work cannot be retired"):
        validate_transition(
            ProjectSnapshot(
                agents={AGENT_ID: retiring_role, ACTOR_AGENT_ID: actor},
                delegations={
                    DELEGATION_ID: actor_binding,
                    "delegation.00000000000000000000000002": {
                        "id": "delegation.00000000000000000000000002",
                        "agent_id": AGENT_ID,
                        "state": "active",
                    },
                },
            ),
            "agent.retired",
            {
                "agent_id": AGENT_ID,
                "expected_revision": EVENT_ID,
                "reason": "The service is no longer needed.",
                "retired_by": "agent",
            },
            actor_session_id="session.role",
        )

    with pytest.raises(
        LifecycleConflictError,
        match="agent does not exist: agent.00000000000000000000000003",
    ):
        validate_transition(
            ProjectSnapshot(
                agents={ACTOR_AGENT_ID: actor},
                delegations={DELEGATION_ID: actor_binding},
            ),
            "agent.link_opened",
            {
                "link_id": "link.00000000000000000000000001",
                "from_agent_id": "agent.00000000000000000000000003",
                "to_agent_id": ACTOR_AGENT_ID,
                "allowed_action": "ask",
                "reason": "Coordinate the implementation.",
            },
            actor_session_id="session.role",
        )


def test_task_acceptance_requires_explicit_review_state() -> None:
    payload = {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "accepted",
        "acceptance_review": {
            "ref": {"kind": "review", "id": REVIEW_ID},
            "revision": REVIEW_REVISION,
        },
    }
    completed = ProjectSnapshot(
        tasks={TASK_ID: {"id": TASK_ID, "state": "completed", "revision": EVENT_ID}}
    )
    with pytest.raises(LifecycleConflictError, match="not allowed"):
        validate_transition(
            completed,
            "task.accepted",
            payload,
            actor_session_id="session.reviewer",
        )

    under_review = ProjectSnapshot(
        tasks={TASK_ID: {"id": TASK_ID, "state": "review", "revision": EVENT_ID}},
        reviews={
            REVIEW_ID: {
                "id": REVIEW_ID,
                "state": "approved",
                "revision": REVIEW_REVISION,
                "effective_revision": REVIEW_REVISION,
                "independent": True,
                "stale": False,
                "target_ref": {"kind": "task", "id": TASK_ID},
                "target_revision": EVENT_ID,
            }
        },
    )
    validate_transition(
        under_review,
        "task.accepted",
        payload,
        actor_session_id="session.reviewer",
    )


def test_duplicate_thread_message_is_rejected() -> None:
    snapshot = ProjectSnapshot(
        threads={
            THREAD_ID: {
                "id": THREAD_ID,
                "state": "open",
                "revision": EVENT_ID,
                "messages": [{"message_id": MESSAGE_ID, "body": "first"}],
            }
        }
    )
    with pytest.raises(LifecycleConflictError, match="already contains message"):
        validate_transition(
            snapshot,
            "thread.replied",
            {
                "thread_id": THREAD_ID,
                "expected_revision": EVENT_ID,
                "message_id": MESSAGE_ID,
                "body": "duplicate",
            },
            actor_session_id="session.writer",
        )


def test_decision_supersession_requires_compatible_replacement() -> None:
    current = {
        "id": DECISION_ID,
        "state": "accepted",
        "revision": EVENT_ID,
        "scope": "architecture.storage",
    }
    payload = {
        "decision_id": DECISION_ID,
        "expected_revision": EVENT_ID,
        "replacement_decision_id": REPLACEMENT_ID,
        "reason": "new constraints",
    }
    with pytest.raises(LifecycleConflictError, match="does not exist"):
        validate_transition(
            ProjectSnapshot(decisions={DECISION_ID: current}),
            "decision.superseded",
            payload,
            actor_session_id="session.owner",
        )

    replacement = {
        "id": REPLACEMENT_ID,
        "state": "proposed",
        "revision": "evt.00000000000000000000000002",
        "scope": "architecture.other",
    }
    with pytest.raises(LifecycleConflictError, match="same scope"):
        validate_transition(
            ProjectSnapshot(decisions={DECISION_ID: current, REPLACEMENT_ID: replacement}),
            "decision.superseded",
            payload,
            actor_session_id="session.owner",
        )
