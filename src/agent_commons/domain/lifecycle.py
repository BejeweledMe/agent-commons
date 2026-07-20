from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.errors import LifecycleConflictError, ValidationError

_COLLECTIONS = {
    "objective": "objectives",
    "task": "tasks",
    "thread": "threads",
    "review": "reviews",
    "verification": "verifications",
    "finding": "findings",
    "decision": "decisions",
    "artifact": "artifacts",
    "handoff": "handoffs",
}

_TASK_ALLOWED = {
    "task.taken": {"ready"},
    "task.started": {"ready", "assigned"},
    "task.blocked": {"assigned", "active"},
    "task.unblocked": {"blocked"},
    "task.completed": {"active"},
    "task.submitted": {"completed"},
    "task.accepted": {"review"},
    "task.cancelled": {"ready", "assigned", "active", "blocked"},
    "task.reopened": {"completed", "review", "accepted", "cancelled"},
}

_STATE_ALLOWED = {
    "objective.revised": {"active"},
    "objective.closed": {"active"},
    "thread.replied": {"open"},
    "thread.resolved": {"open"},
    "review.completed": {"requested"},
    "finding.promoted": {"reported", "contested"},
    "finding.contested": {"reported", "verified"},
    "finding.resolved": {"reported", "verified", "contested"},
    "decision.accepted": {"proposed", "deferred"},
    "decision.rejected": {"proposed", "deferred"},
    "decision.deferred": {"proposed"},
    "decision.superseded": {"accepted"},
    "handoff.acknowledged": {"open"},
    "artifact.revised": {"registered"},
}


def entity(snapshot: ProjectSnapshot, kind: str, identifier: str) -> dict[str, Any] | None:
    attribute = _COLLECTIONS.get(kind)
    if attribute is None:
        raise ValidationError(f"unknown entity kind: {kind}")
    collection = getattr(snapshot, attribute)
    return collection.get(identifier)


def require_entity(snapshot: ProjectSnapshot, kind: str, identifier: str) -> dict[str, Any]:
    current = entity(snapshot, kind, identifier)
    if current is None:
        raise LifecycleConflictError(f"{kind} does not exist: {identifier}")
    return current


def require_revision(current: Mapping[str, Any], expected_revision: str) -> None:
    if current.get("revision") != expected_revision:
        current_revision = current.get("revision")
        raise LifecycleConflictError(
            f"stale expected revision {expected_revision}; current revision is {current_revision}"
        )


def validate_transition(
    snapshot: ProjectSnapshot,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor_session_id: str,
) -> None:
    if event_type.endswith(".created") or event_type in {
        "thread.opened",
        "artifact.registered",
        "review.requested",
        "verification.recorded",
        "finding.reported",
        "decision.proposed",
        "handoff.created",
        "event.invalidated",
        "event.invalidation_revoked",
        "event.corrected",
    }:
        _validate_creation(snapshot, event_type, payload)
        return

    family, _ = event_type.split(".", 1)
    identifier = str(payload.get(f"{family}_id", ""))
    if not identifier:
        raise ValidationError(f"{event_type} has no {family}_id")
    current = require_entity(snapshot, family, identifier)
    require_revision(current, str(payload.get("expected_revision", "")))
    allowed = _TASK_ALLOWED.get(event_type) or _STATE_ALLOWED.get(event_type)
    if allowed is not None and current.get("state") not in allowed:
        raise LifecycleConflictError(
            f"{event_type} is not allowed from {family} state {current.get('state')}"
        )
    if event_type == "review.completed" and current.get("independent"):
        requester_session = (current.get("actor") or {}).get("session_id")
        if requester_session == actor_session_id:
            raise LifecycleConflictError(
                "an independent review cannot be completed by its requester session"
            )
        target_ref = current.get("target_ref") or {}
        if target_ref.get("kind") == "task":
            target_task = require_entity(snapshot, "task", str(target_ref.get("id", "")))
            work_author_sessions = {
                str(session_id)
                for session_id in target_task.get("work_author_session_ids", [])
                if str(session_id)
            }
            if actor_session_id in work_author_sessions:
                raise LifecycleConflictError(
                    "an independent task review cannot be completed by a work-author session"
                )
    if event_type == "review.completed" and payload.get("target_revision") != current.get(
        "target_revision"
    ):
        raise LifecycleConflictError("review result does not bind the requested target revision")
    if event_type == "thread.replied":
        message_id = payload.get("message_id")
        if any(
            item.get("message_id") == message_id
            for item in current.get("messages", [])
            if isinstance(item, Mapping)
        ):
            raise LifecycleConflictError(f"thread already contains message: {message_id}")
    if event_type == "task.accepted":
        acceptance_review = payload.get("acceptance_review") or {}
        review_ref = acceptance_review.get("ref") or {}
        review = require_entity(
            snapshot, str(review_ref.get("kind", "")), str(review_ref.get("id", ""))
        )
        review_revision = review.get("effective_revision", review.get("revision"))
        if acceptance_review.get("revision") != review_revision:
            raise LifecycleConflictError(
                "task acceptance is not bound to the current review revision"
            )
        if review.get("state") != "approved" or review.get("stale") is True:
            raise LifecycleConflictError("task acceptance requires a current approved review")
        if review.get("independent") is not True:
            raise LifecycleConflictError("task acceptance requires an independent review")
        if review.get("target_ref") != {"kind": "task", "id": identifier}:
            raise LifecycleConflictError("acceptance review targets a different task")
        subject_revision = current.get("effective_revision", current.get("revision"))
        if review.get("target_revision") != subject_revision:
            raise LifecycleConflictError(
                "acceptance review does not bind the current task revision"
            )
        review_actor_session = str((review.get("actor") or {}).get("session_id", ""))
        work_author_sessions = {
            str(session_id)
            for session_id in current.get("work_author_session_ids", [])
            if str(session_id)
        }
        if review_actor_session in work_author_sessions:
            raise LifecycleConflictError(
                "task acceptance requires a review completed outside the work-author sessions"
            )
    if event_type == "decision.accepted":
        scope = str(current.get("scope", ""))
        conflicts = [
            item
            for item in snapshot.decisions.values()
            if item.get("state") == "accepted"
            and item.get("stale") is not True
            and item.get("scope") == scope
            and item.get("id") != identifier
        ]
        if conflicts:
            raise LifecycleConflictError(f"conflicting accepted decisions for scope {scope}")
    if event_type == "decision.superseded":
        replacement_id = str(payload.get("replacement_decision_id", ""))
        if replacement_id == identifier:
            raise LifecycleConflictError("a decision cannot supersede itself")
        replacement = require_entity(snapshot, "decision", replacement_id)
        if replacement.get("scope") != current.get("scope"):
            raise LifecycleConflictError("a replacement decision must have the same scope")
        if replacement.get("state") not in {"proposed", "deferred", "accepted"}:
            raise LifecycleConflictError(
                "a replacement decision must still be eligible or already accepted"
            )


def _validate_creation(
    snapshot: ProjectSnapshot, event_type: str, payload: Mapping[str, Any]
) -> None:
    created_kind = {
        "objective.created": "objective",
        "task.created": "task",
        "thread.opened": "thread",
        "artifact.registered": "artifact",
        "review.requested": "review",
        "verification.recorded": "verification",
        "finding.reported": "finding",
        "decision.proposed": "decision",
        "handoff.created": "handoff",
    }.get(event_type)
    if created_kind:
        identifier = str(payload.get(f"{created_kind}_id", ""))
        if entity(snapshot, created_kind, identifier) is not None:
            raise LifecycleConflictError(f"{created_kind} already exists: {identifier}")
    if event_type in {"review.requested", "verification.recorded"}:
        target = payload.get("target_ref") or {}
        target_current = require_entity(snapshot, str(target.get("kind")), str(target.get("id")))
        allowed_target_revisions = {
            target_current.get("revision"),
            target_current.get("effective_revision", target_current.get("revision")),
        }
        if payload.get("target_revision") not in allowed_target_revisions:
            raise LifecycleConflictError(
                "target_revision is not the current immutable target revision"
            )
    if event_type == "task.created":
        for dependency in payload.get("dependencies") or []:
            require_entity(snapshot, "task", str(dependency))
