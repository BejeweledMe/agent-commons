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
    "delegation": "delegations",
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
    "delegation.started": {"requested"},
    "delegation.input_needed": {"active"},
    "delegation.resumed": {"input_needed"},
    "delegation.succeeded": {"active"},
    "delegation.failed": {"requested", "active", "input_needed"},
    # The current runtime has no authenticated stop/kill acknowledgement in a
    # canonical event.  Cancellation is therefore safe only before launch;
    # started work must be stopped and classified through timeout/failure/
    # needs_operator reconciliation instead of merely changing ledger state.
    "delegation.cancelled": {"requested"},
    "delegation.timed_out": {"requested", "active", "input_needed"},
    "delegation.needs_operator": {"requested", "active", "input_needed"},
}

_DELEGATION_MONOTONIC_LIMITS = (
    "max_depth",
    "wall_time_seconds",
    "max_attempts",
    "max_concurrency",
)


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
        "delegation.requested",
    }:
        _validate_creation(
            snapshot,
            event_type,
            payload,
            actor_session_id=actor_session_id,
        )
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
    if event_type == "review.completed":
        bound = _bound_delegations(snapshot, actor_session_id)
        if bound and not any(
            delegation.get("purpose") == "independent_review"
            and _delegation_matches_review(delegation, current)
            for delegation in bound
        ):
            raise LifecycleConflictError(
                "a delegated reviewer may complete only its exact bound review target"
            )
    if event_type == "delegation.started":
        child_session_id = str(payload.get("child_session_id", ""))
        if actor_session_id != str(current.get("parent_session_id", "")):
            raise LifecycleConflictError(
                "only the delegation requester session may start its provider child"
            )
        if child_session_id == actor_session_id:
            raise LifecycleConflictError(
                "a delegation child session must be distinct from its parent and starter"
            )
        attempt = int(payload.get("attempt", 0))
        maximum = int((current.get("limits") or {}).get("max_attempts", 0))
        if attempt > maximum:
            raise LifecycleConflictError("delegation attempt exceeds its hard max_attempts limit")
        _validate_target_binding(snapshot, current)
    if event_type in {"delegation.input_needed", "delegation.succeeded"}:
        if actor_session_id != str(current.get("child_session_id", "")):
            raise LifecycleConflictError(
                "only the delegation's bound child session may report this outcome"
            )
    if event_type in {"delegation.resumed", "delegation.cancelled", "delegation.timed_out"}:
        if actor_session_id != str(current.get("parent_session_id", "")):
            raise LifecycleConflictError(
                "only the delegation requester session may control this transition"
            )
    if event_type in {"delegation.failed", "delegation.needs_operator"}:
        if actor_session_id not in {
            str(current.get("parent_session_id", "")),
            str(current.get("child_session_id", "")),
        }:
            raise LifecycleConflictError(
                "delegation failure classification requires its parent or bound child session"
            )
    if event_type == "delegation.succeeded":
        result_refs = payload.get("result_refs") or []
        for result_ref in result_refs:
            _require_ref_exists(snapshot, result_ref)
            if result_ref == {"kind": "delegation", "id": identifier}:
                raise LifecycleConflictError("a delegation cannot return itself as a result")
        purpose = str(current.get("purpose", ""))
        if purpose == "independent_review":
            if len(result_refs) != 1 or result_refs[0].get("kind") != "review":
                raise LifecycleConflictError(
                    "an independent-review delegation must return exactly one review"
                )
            review = require_entity(snapshot, "review", str(result_refs[0].get("id", "")))
            if (
                review.get("state") == "requested"
                or (review.get("actor") or {}).get("session_id") != actor_session_id
                or not _delegation_matches_review(current, review)
            ):
                raise LifecycleConflictError(
                    "delegation result review is not the bound child's exact completed review"
                )
        if purpose == "verification":
            if len(result_refs) != 1 or result_refs[0].get("kind") != "verification":
                raise LifecycleConflictError(
                    "a verification delegation must return exactly one verification"
                )
            verification = require_entity(
                snapshot, "verification", str(result_refs[0].get("id", ""))
            )
            if (
                (verification.get("actor") or {}).get("session_id") != actor_session_id
                or verification.get("target_ref") != current.get("target_ref")
                or verification.get("target_revision") != current.get("target_revision")
            ):
                raise LifecycleConflictError(
                    "delegation result verification is not the bound child's exact verification"
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
    snapshot: ProjectSnapshot,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor_session_id: str,
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
        "delegation.requested": "delegation",
    }.get(event_type)
    if created_kind:
        identifier = str(payload.get(f"{created_kind}_id", ""))
        if entity(snapshot, created_kind, identifier) is not None:
            raise LifecycleConflictError(f"{created_kind} already exists: {identifier}")
    if event_type in {"review.requested", "verification.recorded"}:
        target = payload.get("target_ref") or {}
        target_current = require_entity(
            snapshot,
            str(target.get("kind")),
            str(target.get("id")),
        )
        allowed_target_revisions = {
            target_current.get("revision"),
            target_current.get("effective_revision", target_current.get("revision")),
        }
        if payload.get("target_revision") not in allowed_target_revisions:
            raise LifecycleConflictError(
                "target_revision is not the current immutable target revision"
            )
    if event_type == "verification.recorded":
        bound = _bound_delegations(snapshot, actor_session_id)
        if bound and not any(
            _delegation_allows_verification(snapshot, delegation, payload) for delegation in bound
        ):
            raise LifecycleConflictError(
                "a delegated child may verify only its exact delegation or review target"
            )
    if event_type == "delegation.requested":
        _validate_target_binding(snapshot, payload)
    if event_type == "task.created":
        for dependency in payload.get("dependencies") or []:
            require_entity(snapshot, "task", str(dependency))
    if event_type == "delegation.requested":
        _validate_delegation_request(snapshot, payload, actor_session_id=actor_session_id)


def _current_ref_revision(snapshot: ProjectSnapshot, ref: Mapping[str, Any]) -> str | None:
    kind = str(ref.get("kind", ""))
    identifier = str(ref.get("id", ""))
    if kind == "event":
        return snapshot.effective_event_revisions.get(identifier)
    if kind == "manifest":
        return identifier if identifier in snapshot.known_manifest_ids else None
    current = require_entity(snapshot, kind, identifier)
    return str(current.get("effective_revision") or current.get("revision"))


def _require_ref_exists(snapshot: ProjectSnapshot, ref: Mapping[str, Any]) -> None:
    if _current_ref_revision(snapshot, ref) is None:
        raise LifecycleConflictError(
            f"{ref.get('kind')} does not exist or is not effective: {ref.get('id')}"
        )


def _validate_target_binding(snapshot: ProjectSnapshot, value: Mapping[str, Any]) -> None:
    target = value.get("target_ref") or {}
    current_revision = _current_ref_revision(snapshot, target)
    if value.get("target_revision") != current_revision:
        raise LifecycleConflictError("target_revision is not the current immutable target revision")


def _delegation_ancestor_ids(
    snapshot: ProjectSnapshot, parent_delegation_id: str
) -> tuple[str, ...]:
    ancestors: list[str] = []
    current_id = parent_delegation_id
    while current_id:
        if current_id in ancestors:
            raise LifecycleConflictError("delegation parent lineage contains a cycle")
        ancestors.append(current_id)
        current = require_entity(snapshot, "delegation", current_id)
        current_id = str(current.get("parent_delegation_id") or "")
    return tuple(ancestors)


def _bound_delegations(
    snapshot: ProjectSnapshot, actor_session_id: str
) -> tuple[Mapping[str, Any], ...]:
    """Return non-terminal delegations whose worker is the current actor."""

    return tuple(
        delegation
        for delegation in snapshot.delegations.values()
        if delegation.get("child_session_id") == actor_session_id
        and delegation.get("state") in {"active", "input_needed"}
    )


def _delegation_matches_review(delegation: Mapping[str, Any], review: Mapping[str, Any]) -> bool:
    target = delegation.get("target_ref") or {}
    review_ref = {"kind": "review", "id": review.get("id")}
    if target == review_ref:
        return delegation.get("target_revision") in {
            review.get("revision"),
            review.get("effective_revision", review.get("revision")),
            review.get("expected_revision"),
        }
    return target == review.get("target_ref") and delegation.get("target_revision") == review.get(
        "target_revision"
    )


def _delegation_allows_verification(
    snapshot: ProjectSnapshot,
    delegation: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    """Keep reviewer-produced facts bound to the exact review subject."""

    target_ref = payload.get("target_ref")
    target_revision = payload.get("target_revision")
    if delegation.get("purpose") == "verification":
        return (
            delegation.get("target_ref") == target_ref
            and delegation.get("target_revision") == target_revision
        )
    if delegation.get("purpose") != "independent_review":
        return False
    return any(
        _delegation_matches_review(delegation, review)
        and review.get("target_ref") == target_ref
        and review.get("target_revision") == target_revision
        for review in snapshot.reviews.values()
    )


def _validate_delegation_request(
    snapshot: ProjectSnapshot,
    payload: Mapping[str, Any],
    *,
    actor_session_id: str,
) -> None:
    delegation_id = str(payload["delegation_id"])
    if payload.get("parent_session_id") != actor_session_id:
        raise LifecycleConflictError("delegation parent_session_id must match its requester")
    depth = int(payload["depth"])
    limits = payload["limits"]
    if depth > int(limits["max_depth"]):
        raise LifecycleConflictError("delegation depth exceeds its hard max_depth limit")

    parent_id = str(payload.get("parent_delegation_id") or "")
    if not parent_id:
        if _bound_delegations(snapshot, actor_session_id):
            raise LifecycleConflictError(
                "a bound delegation child cannot escape its lineage with a new root delegation"
            )
        if depth != 0 or payload.get("root_delegation_id") != delegation_id:
            raise LifecycleConflictError(
                "a root delegation must have depth zero and identify itself as root"
            )
        return

    if parent_id == delegation_id:
        raise LifecycleConflictError("a delegation cannot be its own parent")
    parent = require_entity(snapshot, "delegation", parent_id)
    if parent.get("state") != "active":
        raise LifecycleConflictError("a child delegation requires an active parent delegation")
    if parent.get("child_session_id") != actor_session_id:
        raise LifecycleConflictError(
            "a child delegation must be requested by its parent's bound child session"
        )
    if depth != int(parent.get("depth", -1)) + 1:
        raise LifecycleConflictError("delegation depth does not extend its parent by one")
    if payload.get("root_delegation_id") != parent.get("root_delegation_id"):
        raise LifecycleConflictError("delegation root does not match its parent lineage")

    ancestors = _delegation_ancestor_ids(snapshot, parent_id)
    target_ref = payload.get("target_ref") or {}
    if target_ref.get("kind") == "delegation" and target_ref.get("id") in ancestors:
        raise LifecycleConflictError("delegation target would create an ancestor cycle")

    parent_limits = parent.get("limits") or {}
    nonterminal_children = sum(
        1
        for delegation in snapshot.delegations.values()
        if delegation.get("parent_delegation_id") == parent_id
        and delegation.get("state") in {"requested", "active", "input_needed"}
    )
    if nonterminal_children >= int(parent_limits["max_concurrency"]):
        raise LifecycleConflictError(
            "child delegation exceeds its parent's hard max_concurrency limit"
        )
    for name in _DELEGATION_MONOTONIC_LIMITS:
        if int(limits[name]) > int(parent_limits[name]):
            raise LifecycleConflictError(f"child delegation cannot increase {name}")
    parent_budget = parent_limits.get("budget") or {}
    budget = limits.get("budget") or {}
    if budget.get("unit") != parent_budget.get("unit"):
        raise LifecycleConflictError("child delegation cannot change its budget unit")
    if int(budget.get("limit", 0)) > int(parent_budget.get("limit", 0)):
        raise LifecycleConflictError("child delegation cannot increase its budget limit")
