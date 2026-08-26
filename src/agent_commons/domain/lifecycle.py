from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.domain.collections import collection_for
from agent_commons.domain.roles import (
    RoleTransitionContext,
    principals,
    session_agent_map,
    validate_role_transition,
)
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import (
    LIVE_WORKER_DELEGATION_STATES,
    NON_TERMINAL_DELEGATION_STATES,
)
from agent_commons.domain.transitions import transition_spec
from agent_commons.domain.validation import EVENT_SPECS
from agent_commons.errors import LifecycleConflictError, ValidationError

_DELEGATION_MONOTONIC_LIMITS = (
    "max_depth",
    "wall_time_seconds",
    "max_attempts",
    "max_concurrency",
)


def entity(snapshot: ProjectSnapshot, kind: str, identifier: str) -> dict[str, Any] | None:
    attribute = collection_for(kind)
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
    actor: Mapping[str, Any] | None = None,
    relations: Sequence[Mapping[str, Any]] = (),
) -> None:
    if event_type == "workspace.semantics_required":
        # The floor only rises.  A stamp at or below the current requirement
        # is refused at write time, so replay never meets a redundant one and
        # the ledger carries exactly one stamp per version step.
        if int(payload.get("semantics_version", 0) or 0) <= snapshot.semantics_required:
            raise LifecycleConflictError(
                "the ledger already requires this semantics version or newer"
            )
        return
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
        "agent.created",
        "agent.link_opened",
    }:
        _validate_creation(
            snapshot,
            event_type,
            payload,
            actor_session_id=actor_session_id,
            relations=relations,
        )
        return

    # Kind and identity come from the event registry rather than from the text
    # before the dot: `agent.link_closed` is an `agent_link`, and guessing from
    # the prefix would silently look up the wrong collection.
    spec = EVENT_SPECS.get(event_type)
    family = spec.entity_kind if spec and spec.entity_kind else event_type.split(".", 1)[0]
    id_field = spec.entity_id_field if spec and spec.entity_id_field else f"{family}_id"
    identifier = str(payload.get(id_field, ""))
    if not identifier:
        raise ValidationError(f"{event_type} has no {id_field}")
    current = require_entity(snapshot, family, identifier)
    require_revision(current, str(payload.get("expected_revision", "")))
    transition = transition_spec(event_type)
    if transition is not None and not transition.allows(current.get("state")):
        raise LifecycleConflictError(
            f"{event_type} is not allowed from {family} state {current.get('state')}"
        )
    if event_type == "review.completed" and current.get("independent"):
        bindings = session_agent_map(snapshot.delegations)
        completer = principals(bindings, {actor_session_id})
        # Requester and completer are compared as principals, not sessions: a
        # standing role that requested a review in one run and approved it in
        # the next used two different session ids and passed a raw comparison
        # (H2, 2026-08-10 review).  The requester principal set comes from the
        # authors of the review record itself, which accumulate across its
        # events rather than tracking the last actor.
        requester = _record_author_principals(snapshot, current)
        if requester & completer:
            raise LifecycleConflictError(
                "an independent review cannot be completed by a principal that requested it: "
                + ", ".join(sorted(requester & completer))
            )
        target_ref = current.get("target_ref") or {}
        target_kind = str(target_ref.get("kind", ""))
        overlap = _subject_author_principals(snapshot, target_ref) & completer
        if overlap:
            raise LifecycleConflictError(
                f"an independent {target_kind or 'subject'} review cannot be completed "
                "by a principal that authored the subject: " + ", ".join(sorted(overlap))
            )
    if event_type == "review.completed":
        bound = _child_delegations(snapshot, actor_session_id)
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
    if event_type == "delegation.resumed":
        parent_session_id = str(current.get("parent_session_id", ""))
        actor_identity = (
            {str(key): value for key, value in actor.items() if str(key) != "session_id"}
            if isinstance(actor, Mapping)
            else None
        )
        same_identity_replacement = (
            actor_identity is not None
            and snapshot.session_identities.get(parent_session_id) == actor_identity
        )
        if actor_session_id != parent_session_id and not same_identity_replacement:
            raise LifecycleConflictError(
                "only the delegation requester session may control this transition"
            )
    if event_type in {"delegation.cancelled", "delegation.timed_out"}:
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
        # A delegated worker speaks where it was spoken to.  Without this, the
        # reply tool it now carries would let one bounded run write into every
        # conversation in the workspace.  Terminal bindings keep the rule: a
        # worker that already reported its outcome does not graduate into a
        # session that may write anywhere.
        bound = _child_delegations(snapshot, actor_session_id)
        if bound:
            addressed = {str(item) for item in current.get("to") or ()}
            reachable = {"*", actor_session_id} | {
                str(delegation.get("agent_id"))
                for delegation in bound
                if delegation.get("agent_id")
            }
            if not addressed & reachable:
                raise LifecycleConflictError(
                    "a delegated worker may reply only to a thread it is addressed in"
                )
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
        # A later non-structural correction folds into the review's replay
        # position and rewrites its effective revision — an id that did not
        # exist when the acceptance was written.  The binding therefore may
        # name either the current effective head or the raw event it
        # corrected: corrections cannot change structural fields, so both
        # spell the same verdict against the same target.  Without the root
        # id here, correcting a review's wording retroactively rejected every
        # acceptance bound to it (finding.026GYJFW71EAK7QTWDA0E1T6PR).
        if acceptance_review.get("revision") not in {review_revision, review.get("revision")}:
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
        # The same correction tolerance, mirrored: a corrected task event moves
        # the subject's effective revision under a review that bound the raw id.
        if review.get("target_revision") not in {subject_revision, current.get("revision")}:
            raise LifecycleConflictError(
                "acceptance review does not bind the current task revision"
            )
        review_actor_session = str((review.get("actor") or {}).get("session_id", ""))
        bindings = session_agent_map(snapshot.delegations)
        work_author_principals = principals(
            bindings,
            (
                str(session_id)
                for session_id in current.get("work_author_session_ids", [])
                if str(session_id)
            ),
        )
        if work_author_principals & principals(bindings, {review_actor_session}):
            raise LifecycleConflictError(
                "task acceptance requires a review completed outside the work-author principals"
            )
    if event_type == "agent.reconfigured":
        validate_role_transition(
            event_type,
            _role_transition_context(snapshot, actor_session_id),
            payload,
            current=current,
        )
    if event_type == "agent.retired":
        validate_role_transition(
            event_type,
            _role_transition_context(snapshot, actor_session_id),
            payload,
            current=current,
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
    relations: Sequence[Mapping[str, Any]] = (),
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
        "agent.created": "agent",
        "agent.link_opened": "agent_link",
    }.get(event_type)
    if created_kind:
        spec = EVENT_SPECS.get(event_type)
        id_field = spec.entity_id_field if spec and spec.entity_id_field else f"{created_kind}_id"
        identifier = str(payload.get(id_field, ""))
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
        bound = _child_delegations(snapshot, actor_session_id)
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
        validate_role_transition(
            event_type,
            _role_transition_context(snapshot, actor_session_id),
            payload,
            relations=relations,
        )
    if event_type == "agent.created":
        validate_role_transition(
            event_type,
            _role_transition_context(snapshot, actor_session_id),
            payload,
        )
    if event_type == "agent.link_opened":
        validate_role_transition(
            event_type,
            _role_transition_context(snapshot, actor_session_id),
            payload,
        )


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


def acting_agent_id(snapshot: ProjectSnapshot, actor_session_id: str) -> str | None:
    """The standing role a session runs as -- for the life of the session.

    Only a delegated child session acts for a role.  A parent that requested the
    delegation keeps its own identity: it commissioned the work, it did not
    perform it.

    Terminal delegations count.  The child process outlives its own
    `delegation.succeeded` until the parent reaps it, and a session that ran as
    a role must not become an unbound human window in that gap: independence
    already treats it as the role forever (`session_agent_map`), so authority
    does too, or the same session is "was role R" for one check and "nobody"
    for the other -- the C1 escape.  A live binding wins over a finished one;
    among equals the most recently opened delegation (ULID order) decides,
    which replay derives from the ledger alone.
    """

    bound = [
        delegation
        for delegation in _child_delegations(snapshot, actor_session_id)
        if delegation.get("agent_id")
    ]
    if not bound:
        return None
    live = [
        delegation
        for delegation in bound
        if delegation.get("state") in LIVE_WORKER_DELEGATION_STATES
    ]
    latest = max(live or bound, key=lambda delegation: str(delegation.get("id", "")))
    return str(latest["agent_id"])


def _role_transition_context(
    snapshot: ProjectSnapshot, actor_session_id: str
) -> RoleTransitionContext:
    def role_entity(kind: str, identifier: str) -> Mapping[str, Any]:
        return require_entity(snapshot, kind, identifier)

    return RoleTransitionContext(
        snapshot=snapshot,
        actor_session_id=actor_session_id,
        acting_agent_id=acting_agent_id(snapshot, actor_session_id),
        require_entity=role_entity,
    )


def _subject_author_principals(
    snapshot: ProjectSnapshot, target_ref: Mapping[str, Any]
) -> set[str]:
    """Author identity for independence, expressed once over principals.

    A session identifier stopped being the unit the moment a role could outlive
    a run: the same standing role can author work in one session and approve it
    in the next, and both identifiers differ.  Every independence check reads
    this function, so a principal kind added later is covered everywhere at
    once rather than in whichever call site someone remembers.
    """

    bindings = session_agent_map(snapshot.delegations)
    return principals(bindings, _subject_author_sessions(snapshot, target_ref))


def _record_author_principals(snapshot: ProjectSnapshot, record: Mapping[str, Any]) -> set[str]:
    """Principals that authored one record, from its accumulated author set.

    The projection keeps every session that recorded an event for an entity in
    `author_session_ids`, so this survives a later unrelated event overwriting
    `actor`.
    """

    bindings = session_agent_map(snapshot.delegations)
    sessions = {
        str(session_id) for session_id in record.get("author_session_ids", []) if str(session_id)
    }
    if not sessions:
        actor_session = str((record.get("actor") or {}).get("session_id", ""))
        if actor_session:
            sessions.add(actor_session)
    return principals(bindings, sessions)


def _subject_author_sessions(snapshot: ProjectSnapshot, target_ref: Mapping[str, Any]) -> set[str]:
    """Every session that authored the subject of a review, whatever its kind.

    Independence is a property of the subject, not of one target kind.  Closing
    this per kind has already failed twice: a task review was refused while the
    same work reviewed straight as an artifact was approved, and once artifacts
    were covered a decision or a finding was still open.  The default here is
    the recording actor, so a kind added later is covered before anyone
    remembers to extend this function.
    """

    kind = str(target_ref.get("kind", ""))
    identifier = str(target_ref.get("id", ""))
    attribute = collection_for(kind)
    if attribute is None:
        return set()
    record = getattr(snapshot, attribute).get(identifier)
    if not record:
        return set()

    authors: set[str] = set()
    for field in ("work_author_session_ids", "evidence_author_session_ids"):
        authors.update(str(session_id) for session_id in record.get(field, []) if str(session_id))
    if kind == "task":
        # A task's evidence is authored elsewhere: the session that produced the
        # bound artifacts never has to touch a task event.  Recording the task
        # is not authoring its work, so the actor alone does not count here.
        authors.update(_evidence_author_sessions(snapshot, record))
        return authors
    # Every session that recorded an event for this subject, not the last one.
    # `actor` is overwritten on each event, so reading it credited whoever
    # touched the record most recently and let the real author review its own
    # decision, finding, or thread after any unrelated event (H2).
    authors.update(
        str(session_id) for session_id in record.get("author_session_ids", []) if str(session_id)
    )
    if not authors:
        actor_session = str((record.get("actor") or {}).get("session_id", ""))
        if actor_session:
            authors.add(actor_session)
    return authors


def _evidence_author_sessions(snapshot: ProjectSnapshot, task: Mapping[str, Any]) -> set[str]:
    """Sessions that produced the artifacts bound to a task.

    Task lifecycle events are not the only way to author work: a session can
    write all of the code, register it as the artifact under review, and never
    touch a task event.  Without this, that session counts as independent.
    """

    authors: set[str] = set()
    for bound in task.get("artifact_bindings") or []:
        if not isinstance(bound, Mapping):
            continue
        ref = bound.get("ref")
        if not isinstance(ref, Mapping) or ref.get("kind") != "artifact":
            continue
        artifact = snapshot.artifacts.get(str(ref.get("id", "")))
        if not artifact:
            continue
        authors.update(
            str(session_id)
            for session_id in artifact.get("evidence_author_session_ids", [])
            if str(session_id)
        )
    return authors


def _child_delegations(
    snapshot: ProjectSnapshot, actor_session_id: str
) -> tuple[Mapping[str, Any], ...]:
    """Every delegation, in any state, whose worker is the current actor.

    A binding does not end with the run: the worker process outlives its own
    terminal event, and its session keeps the identity and the restrictions it
    worked under.  Checks that specifically need a *live* run -- a child
    delegation under an active parent -- filter on state themselves.
    """

    return tuple(
        delegation
        for delegation in snapshot.delegations.values()
        if delegation.get("child_session_id") == actor_session_id
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
    if payload.get("purpose") == "independent_review":
        # A delegated reviewer can only record its verdict into an existing open
        # independent review request; without one the child burns its attempt
        # and budget, then exits input_needed (finding.3XK0XP1RGRJSNFQJKKRT4TX9FF).
        # The target may be the open review itself, or an entity that an open
        # review is bound to at this exact revision.
        target = dict(payload.get("target_ref") or {})
        target_revision = payload.get("target_revision")
        if target.get("kind") == "review":
            review = snapshot.reviews.get(str(target.get("id"))) or {}
            has_open_independent_review = (
                review.get("state") == "requested" and review.get("independent") is True
            )
        else:
            has_open_independent_review = any(
                review.get("state") == "requested"
                and review.get("independent") is True
                and review.get("target_ref") == target
                and review.get("target_revision") == target_revision
                for review in snapshot.reviews.values()
            )
        if not has_open_independent_review:
            raise LifecycleConflictError(
                "an independent_review delegation requires an open independent review"
                " request bound to its exact target revision; create one with"
                " review request first"
            )
    depth = int(payload["depth"])
    limits = payload["limits"]
    if depth > int(limits["max_depth"]):
        raise LifecycleConflictError("delegation depth exceeds its hard max_depth limit")

    parent_id = str(payload.get("parent_delegation_id") or "")
    if not parent_id:
        # Ever bound, not currently bound: a worker whose run just terminalized
        # still holds its lineage, or reporting success would be the one step
        # that turns a bounded child into an unbounded commissioner.
        if _child_delegations(snapshot, actor_session_id):
            raise LifecycleConflictError(
                "a delegation child session cannot escape its lineage with a new root delegation"
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
        and delegation.get("state") in NON_TERMINAL_DELEGATION_STATES
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
