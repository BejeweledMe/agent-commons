from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from agent_commons.errors import LifecycleConflictError, ValidationError

from .agent_projection import apply_agent_record
from .agent_role_envelopes import AgentEnvelope, AgentLinkEnvelope, AgentReconfiguredEnvelope
from .artifact_projection import apply_artifact_record
from .collections import collection_for
from .decision_projection import apply_decision_record
from .delegation_projection import apply_delegation_record
from .envelopes import DelegationEnvelope, TypedEventEnvelope, parse_event_envelope
from .finding_projection import apply_finding_record
from .handoff_projection import apply_handoff_record
from .invalidations import derive_invalidation_state
from .objective_projection import apply_objective_record
from .review_projection import apply_review_record
from .revisions import resolve_revision, structural_correction_changes
from .snapshot import ProjectionIssue, ProjectSnapshot
from .task_projection import apply_task_record
from .task_review_envelopes import ReviewEnvelope, TaskEnvelope
from .thread_handoff_envelopes import HandoffEnvelope, ThreadEnvelope
from .thread_projection import apply_thread_record
from .truth_evidence_envelopes import (
    ArtifactEnvelope,
    DecisionEnvelope,
    FindingEnvelope,
    VerificationEnvelope,
)
from .validation import EVENT_SPECS, validate_payload
from .verification_projection import apply_verification_record, refresh_verification_staleness

TASK_STATES = {
    "task.created": "ready",
    "task.taken": "assigned",
    "task.started": "active",
    "task.blocked": "blocked",
    "task.unblocked": "active",
    "task.completed": "completed",
    "task.submitted": "review",
    "task.accepted": "accepted",
    "task.cancelled": "cancelled",
    "task.reopened": "ready",
}

TASK_AUTHORING_EVENTS = {
    "task.revised",
    "task.taken",
    "task.started",
    "task.blocked",
    "task.unblocked",
    "task.completed",
}

THREAD_STATES = {"thread.opened": "open", "thread.replied": "open", "thread.resolved": "resolved"}
FINDING_STATES = {
    "finding.reported": "reported",
    "finding.promoted": "verified",
    "finding.contested": "contested",
    "finding.resolved": "resolved",
}
DECISION_STATES = {
    "decision.proposed": "proposed",
    "decision.accepted": "accepted",
    "decision.rejected": "rejected",
    "decision.deferred": "deferred",
    "decision.superseded": "superseded",
}
DELEGATION_STATES = {
    "delegation.requested": "requested",
    "delegation.started": "active",
    "delegation.input_needed": "input_needed",
    "delegation.resumed": "active",
    "delegation.succeeded": "succeeded",
    "delegation.failed": "failed",
    "delegation.cancelled": "cancelled",
    "delegation.recovered": "cancelled",
    "delegation.timed_out": "timed_out",
    "delegation.needs_operator": "needs_operator",
}
AGENT_STATES = {
    "agent.created": "active",
    "agent.reconfigured": "active",
    "agent.retired": "retired",
}
AGENT_LINK_STATES = {
    "agent.link_opened": "open",
    "agent.link_closed": "closed",
}

#: A task-scoped role leaves service when its task does.  Deriving that beats
#: recording it: there is no writer to forget, and no path that can skip it.
_LIFETIME_CLOSING_TASK_STATES = frozenset({"accepted", "cancelled"})

#: The semantics version THIS build replays with.  Bumped only when replay
#: semantics change such that an older reader would misjudge a healthy
#: history — v2 is the causal acceptance guard: a v1 reader replays a
#: twice-accepted chain into a false integrity failure
#: (finding.026GYJFW71EAK7QTWDA0E1T6PR, seen live on 17 Aug 2026 when an old
#: checkout's CLI read a newer ledger red).  A ledger stamped above this
#: number makes the projection say "update agent-commons" instead of
#: reporting integrity findings it cannot judge.
LEDGER_SEMANTICS_VERSION = 2

#: Event types whose replay depends on newer-than-v1 semantics, and the
#: version each one requires.  Writers consult this to stamp the ledger
#: exactly when a write starts depending on the newer behaviour — never
#: earlier, so an untouched workspace stays readable by old code.
SEMANTICS_SENSITIVE_EVENTS = {"task.accepted": 2}


def _record_issue(
    snapshot: ProjectSnapshot,
    code: str,
    message: str,
    *,
    event_ids: Iterable[str] = (),
    repairable: bool = True,
) -> None:
    normalized_ids = tuple(sorted({str(event_id) for event_id in event_ids if event_id}))
    snapshot.issues.append(
        ProjectionIssue(
            code=code,
            severity="error",
            message=message,
            event_ids=normalized_ids,
            repairable=repairable,
        )
    )
    snapshot.warnings.append(message)


def _actor_identity(actor: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): deepcopy(value) for key, value in actor.items() if str(key) != "session_id"}


def _apply(
    collection: dict[str, dict[str, Any]], identifier: str, event: Mapping[str, Any], state: str
) -> None:
    current = deepcopy(collection.get(identifier, {}))
    payload = deepcopy(dict(event.get("payload") or {}))
    # Every session that ever recorded an event for this entity, accumulated
    # rather than overwritten.  `actor` is the *last* actor by construction, so
    # any check that read it for authorship credited whoever touched the record
    # most recently -- one unrelated event by anyone else made the real author
    # look independent of its own subject (H2, 2026-08-10 review).  This set is
    # the authorship of record for every kind that has no curated author list of
    # its own.
    authors = {
        str(session_id) for session_id in current.get("author_session_ids", []) if str(session_id)
    }
    actor_session = str((event.get("actor") or {}).get("session_id", ""))
    if actor_session:
        authors.add(actor_session)
    collection[identifier] = {
        **current,
        **payload,
        "id": identifier,
        "state": state,
        "revision": str(event["event_id"]),
        "effective_revision": str(event.get("_effective_correction_id") or event["event_id"]),
        "recorded_at": event.get("recorded_at"),
        "actor": event.get("actor"),
        "author_session_ids": sorted(authors),
    }


def _relation_object(
    event: Mapping[str, Any], subject_id: str, predicate: str, object_kind: str
) -> str | None:
    """Read one typed relation off the immutable envelope."""

    for relation in event.get("relations") or ():
        if not isinstance(relation, Mapping) or relation.get("predicate") != predicate:
            continue
        subject = relation.get("subject")
        target = relation.get("object")
        if not isinstance(subject, Mapping) or not isinstance(target, Mapping):
            continue
        if subject.get("id") == subject_id and target.get("kind") == object_kind:
            return str(target.get("id"))
    return None


def _retire_lifetime_roles_for_task(snapshot: ProjectSnapshot, task_id: str) -> None:
    """Retire the active task-scoped roles bound to a task that just closed.

    Applied inline, at the event that closes the task, rather than in a pass
    over final state.  Two things depended on that:

    * `task.reopened` used to resurrect a lifetime-retired role, because a
      post-pass reads the task's *current* state and a reopened task is active
      again.  Retiring at the closing event and never un-retiring makes the
      removal terminal, matching "there is no transition out of retired".
    * A post-pass runs after the replay loop, so a lifetime-expired role was
      `active` for every `validate_transition` during replay while the write
      path (a fresh snapshot with the pass already run) saw it `retired`.
      Doing it in the loop makes replay re-apply the same rule the write saw.

    Derived, not recorded: there is still no `agent.retired` event and no writer
    to forget, so `retired_by: lifetime` records carry no such event by design.
    """

    for identifier, record in snapshot.agents.items():
        if record.get("state") != "active":
            continue
        lifetime = record.get("lifetime")
        if not isinstance(lifetime, Mapping) or lifetime.get("kind") != "task_scoped":
            continue
        if str(lifetime.get("task_id", "")) == task_id:
            snapshot.agents[identifier] = record.with_lifetime_retirement(task_id)


def _retire_lifetime_role_if_task_closed(snapshot: ProjectSnapshot, agent_id: str) -> None:
    """Retire a task-scoped role created after its task already closed.

    The closing-event path covers a role that exists when its task closes; this
    covers the reverse order -- a role created while its task is already
    accepted or cancelled -- so a late role is not born collecting work forever.
    A role bound to a task that is currently active (including one reopened
    since an earlier close) lives on, retiring at that task's next close.
    """

    record = snapshot.agents.get(agent_id)
    if record is None or record.get("state") != "active":
        return
    lifetime = record.get("lifetime")
    if not isinstance(lifetime, Mapping) or lifetime.get("kind") != "task_scoped":
        return
    task = snapshot.tasks.get(str(lifetime.get("task_id", "")))
    if task is not None and task.get("state") in _LIFETIME_CLOSING_TASK_STATES:
        snapshot.agents[agent_id] = record.with_lifetime_retirement(str(task.get("id", "")))


def _annotate_review_producer(
    snapshot: ProjectSnapshot, review_id: str, event: Mapping[str, Any]
) -> None:
    """Carry the producing role's context mode and prior-verdict count on a review.

    Requirement P7.3, delivered at the consumer: a review from an
    accumulated-context role reads differently from a clean-slate one, and a
    re-review shows how many times this role has judged this subject before.
    The count was dead code with a type bug (M7, 2026-08-10 review); it is wired
    here so every surface -- the entity view, `agent-commons review list`, the
    graph node -- carries it without a second computation.

    Derived at completion and recomputed on replay, never hidden: the ledger
    knows which verdicts a role recorded against a target.
    """

    from .agents import prior_verdicts, session_agent_map

    review = snapshot.reviews.get(review_id)
    if review is None:
        return
    actor_session = str((event.get("actor") or {}).get("session_id", ""))
    bindings = session_agent_map(snapshot.delegations)
    producer_roles = sorted(bindings.get(actor_session, frozenset()))
    modes = [
        str(snapshot.agents[role].get("context_mode"))
        for role in producer_roles
        if role in snapshot.agents and snapshot.agents[role].get("context_mode")
    ]
    # A run acting for no role is a plain window; there is no producing role to
    # attribute a context mode to, and the surface says so rather than guessing.
    target_ref = review.get("target_ref") or {}
    earlier: set[str] = set()
    for role in producer_roles:
        earlier.update(
            prior_verdicts(snapshot.reviews, bindings, agent_id=role, target_ref=target_ref)
        )
    earlier.discard(review_id)
    snapshot.reviews[review_id] = review.with_producer_annotation(
        producer_agent_ids=producer_roles,
        producer_context_mode=modes[0] if modes else None,
        producer_prior_verdict_count=len(earlier),
    )


def _apply_effective_event(
    snapshot: ProjectSnapshot,
    event: Mapping[str, Any],
    *,
    typed_envelope: TypedEventEnvelope | None = None,
) -> None:
    event_type = str(event["event_type"])
    payload = event["payload"]
    if event_type == "workspace.semantics_required":
        # A monotone floor, not an entity: the ledger remembers the newest
        # semantics any write has depended on, and readers compare themselves
        # against it before trusting their own replay.
        snapshot.semantics_required = max(
            snapshot.semantics_required, int(payload.get("semantics_version", 0) or 0)
        )
        return
    if event_type == "objective.created":
        apply_objective_record(snapshot.objectives, str(payload["objective_id"]), event, "active")
    elif event_type == "objective.revised":
        revised_payload = {
            **dict(payload),
            **deepcopy(dict(payload["changes"])),
        }
        revised_payload.pop("changes", None)
        apply_objective_record(
            snapshot.objectives,
            str(payload["objective_id"]),
            {**event, "payload": revised_payload},
            "active",
        )
    elif event_type == "objective.closed":
        apply_objective_record(snapshot.objectives, str(payload["objective_id"]), event, "closed")
    elif event_type == "task.revised":
        if not isinstance(typed_envelope, TaskEnvelope):
            raise ValidationError(f"missing typed task envelope for {event_type}")
        if typed_envelope.changes is None:
            raise ValidationError("task.revised has no typed changes")
        task_id = typed_envelope.task_id
        current = snapshot.tasks.get(task_id) or {}
        work_author_session_ids = {
            str(session_id)
            for session_id in current.get("work_author_session_ids", [])
            if str(session_id)
        }
        actor_session_id = str((event.get("actor") or {}).get("session_id", ""))
        if actor_session_id:
            work_author_session_ids.add(actor_session_id)
        revised_payload = {
            **typed_envelope.to_payload(),
            **deepcopy(typed_envelope.changes.to_payload()),
            "work_author_session_ids": sorted(work_author_session_ids),
        }
        revised_payload.pop("changes", None)
        apply_task_record(
            snapshot.tasks,
            task_id,
            {**event, "payload": revised_payload},
            str(current.get("state", "ready")),
        )
    elif event_type in TASK_STATES:
        if not isinstance(typed_envelope, TaskEnvelope):
            raise ValidationError(f"missing typed task envelope for {event_type}")
        task_id = typed_envelope.task_id
        current = snapshot.tasks.get(task_id) or {}
        work_author_session_ids = {
            str(session_id)
            for session_id in current.get("work_author_session_ids", [])
            if str(session_id)
        }
        if event_type in TASK_AUTHORING_EVENTS:
            actor_session_id = str((event.get("actor") or {}).get("session_id", ""))
            if actor_session_id:
                work_author_session_ids.add(actor_session_id)
        task_payload = {
            **typed_envelope.to_payload(),
            "work_author_session_ids": sorted(work_author_session_ids),
        }
        if event_type == "task.accepted":
            accepted_payload = {
                **task_payload,
                "accepted_subject_revision": current.get(
                    "effective_revision", current.get("revision")
                ),
            }
            apply_task_record(
                snapshot.tasks,
                task_id,
                {**event, "payload": accepted_payload},
                TASK_STATES[event_type],
            )
        else:
            apply_task_record(
                snapshot.tasks,
                task_id,
                {**event, "payload": task_payload},
                TASK_STATES[event_type],
            )
        if TASK_STATES[event_type] in _LIFETIME_CLOSING_TASK_STATES:
            # Terminal for the roles it scopes: retire them here, at the closing
            # event, so a later task.reopened cannot bring them back and replay
            # sees the same state the write path did.
            _retire_lifetime_roles_for_task(snapshot, task_id)
    elif event_type in THREAD_STATES:
        if not isinstance(typed_envelope, ThreadEnvelope):
            raise ValidationError(f"missing typed thread envelope for {event_type}")
        thread_id = typed_envelope.thread_id
        thread_payload = typed_envelope.to_payload()
        apply_thread_record(
            snapshot.threads,
            thread_id,
            event,
            thread_payload,
            str(typed_envelope.resolution or THREAD_STATES[event_type]),
        )
        if event_type == "thread.replied":
            snapshot.threads[thread_id] = snapshot.threads[thread_id].with_message(
                message_id=typed_envelope.message_id,
                body=typed_envelope.body,
                actor=deepcopy(event.get("actor")),
                recorded_at=event.get("recorded_at"),
            )
    elif event_type in {"artifact.registered", "artifact.revised"}:
        if not isinstance(typed_envelope, ArtifactEnvelope):
            raise ValidationError(f"missing typed artifact envelope for {event_type}")
        artifact_payload = typed_envelope.to_payload()
        artifact_payload["content_revision"] = artifact_payload.pop("revision")
        artifact_id = typed_envelope.artifact_id
        # Every session that produced a revision of this evidence, not just the
        # most recent one.  Independence is decided against this set, so losing
        # earlier authors would let the original writer review its own work.
        evidence_authors = {
            str(session_id)
            for session_id in (snapshot.artifacts.get(artifact_id) or {}).get(
                "evidence_author_session_ids", []
            )
            if str(session_id)
        }
        actor_session_id = str((event.get("actor") or {}).get("session_id", ""))
        if actor_session_id:
            evidence_authors.add(actor_session_id)
        artifact_payload["evidence_author_session_ids"] = sorted(evidence_authors)
        apply_artifact_record(
            snapshot.artifacts,
            artifact_id,
            event,
            artifact_payload,
            "registered",
        )
    elif event_type.startswith("review."):
        if not isinstance(typed_envelope, ReviewEnvelope):
            raise ValidationError(f"missing typed review envelope for {event_type}")
        review_id = typed_envelope.review_id
        review_payload = typed_envelope.to_payload()
        apply_review_record(
            snapshot.reviews,
            review_id,
            event,
            review_payload,
            "requested" if event_type == "review.requested" else str(typed_envelope.verdict),
        )
        if event_type == "review.completed":
            _annotate_review_producer(snapshot, review_id, event)
    elif event_type == "verification.recorded":
        if not isinstance(typed_envelope, VerificationEnvelope):
            raise ValidationError(f"missing typed verification envelope for {event_type}")
        apply_verification_record(snapshot.verifications, typed_envelope, event)
    elif event_type in FINDING_STATES:
        if not isinstance(typed_envelope, FindingEnvelope):
            raise ValidationError(f"missing typed finding envelope for {event_type}")
        apply_finding_record(
            snapshot.findings,
            typed_envelope.finding_id,
            event,
            FINDING_STATES[event_type],
        )
    elif event_type in DECISION_STATES:
        if not isinstance(typed_envelope, DecisionEnvelope):
            raise ValidationError(f"missing typed decision envelope for {event_type}")
        apply_decision_record(
            snapshot.decisions,
            typed_envelope.decision_id,
            event,
            DECISION_STATES[event_type],
        )
    elif event_type.startswith("handoff."):
        if not isinstance(typed_envelope, HandoffEnvelope):
            raise ValidationError(f"missing typed handoff envelope for {event_type}")
        apply_handoff_record(
            snapshot.handoffs,
            typed_envelope.handoff_id,
            event,
            typed_envelope.to_payload(),
            "acknowledged" if event_type == "handoff.acknowledged" else "open",
        )
    elif event_type in DELEGATION_STATES:
        if not isinstance(typed_envelope, DelegationEnvelope):
            raise ValidationError(f"missing typed delegation envelope for {event_type}")
        delegation_id = typed_envelope.delegation_id
        # The immediate parent projected the validated typed payload.  Keep
        # that order as the public mapping/wire contract while the record
        # layer takes its private recursive copy below.
        delegation_payload = typed_envelope.to_payload()
        # The role a run acts for is carried as an event relation, not a payload
        # field: `relation.predicate` and `typedRef.kind` are open patterns, so
        # this binding costs no schema change and an older reader ignores it.
        # Widening the delegation payload would have made every delegation event
        # in every workspace unreadable to the previous binary.
        #
        # Read the binding ONLY on the requested event, the one point
        # `validate_transition` authorises it (`_validate_role_binding`).  Reading
        # it on later events let an `on_behalf_of` relation attached to, say,
        # `delegation.started` rebind the run to any role during replay with no
        # authority check -- the write path never validates it there (round 2,
        # architecture).  On later events `_apply` carries the requested
        # binding forward, so the role a run acts for is fixed at request time.
        if event_type == "delegation.requested":
            bound_agent = _relation_object(event, delegation_id, "on_behalf_of", "agent")
            if bound_agent:
                delegation_payload["agent_id"] = bound_agent
        apply_delegation_record(
            snapshot.delegations,
            delegation_id,
            {**event, "payload": delegation_payload},
            DELEGATION_STATES[event_type],
        )
    elif event_type in AGENT_STATES:
        if not isinstance(typed_envelope, AgentEnvelope):
            raise ValidationError(f"missing typed agent envelope for {event_type}")
        agent_id = typed_envelope.agent_id
        agent_payload = dict(typed_envelope.to_payload())
        if isinstance(typed_envelope, AgentReconfiguredEnvelope):
            agent_payload = {**agent_payload, **deepcopy(typed_envelope.changes.to_payload())}
            agent_payload.pop("changes", None)
        apply_agent_record(
            snapshot.agents,
            agent_id,
            event,
            agent_payload,
            AGENT_STATES[event_type],
        )
        if event_type == "agent.created":
            snapshot.agents[agent_id] = snapshot.agents[agent_id].with_created_defaults(
                str(event["event_id"])
            )
            # A role born after its task already closed is retired at once, so
            # the two event orders -- task-then-role and role-then-task -- reach
            # the same terminal state.
            _retire_lifetime_role_if_task_closed(snapshot, agent_id)
    elif event_type in AGENT_LINK_STATES:
        if not isinstance(typed_envelope, AgentLinkEnvelope):
            raise ValidationError(f"missing typed agent link envelope for {event_type}")
        _apply(
            snapshot.agent_links,
            typed_envelope.link_id,
            {**event, "payload": typed_envelope.to_payload()},
            AGENT_LINK_STATES[event_type],
        )
    else:  # pragma: no cover - kept defensive if the registry is extended incorrectly
        raise ValidationError(f"unsupported projection event type: {event_type}")


def _cas_conflicts(
    events: Iterable[Mapping[str, Any]],
) -> tuple[set[str], list[ProjectionIssue]]:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        try:
            spec = validate_payload(event_type, payload)
        except ValidationError:
            continue
        expected = payload.get("expected_revision")
        if not spec.entity_kind or not spec.entity_id_field or not isinstance(expected, str):
            continue
        identifier = payload.get(spec.entity_id_field)
        if not isinstance(identifier, str):
            continue
        groups[(spec.entity_kind, identifier, expected)].append(str(event.get("event_id", "")))

    conflicted: set[str] = set()
    issues: list[ProjectionIssue] = []
    for (kind, identifier, revision), event_ids in sorted(groups.items()):
        unique_ids = sorted(set(event_ids))
        if len(unique_ids) < 2:
            continue
        conflicted.update(unique_ids)
        issues.append(
            ProjectionIssue(
                code="concurrent_transition_conflict",
                severity="error",
                message=(
                    f"conflicting concurrent {kind} transitions for {identifier} at {revision}: "
                    + ", ".join(unique_ids)
                ),
                event_ids=tuple(unique_ids),
            )
        )
    return conflicted, issues


def _built_upon_event_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    """Event ids some later event already used as its expected revision.

    An acceptance that a reopen (or any successor) built upon is history, not
    a live claim: stripping it retroactively rejects that already-applied
    successor as stale-expected and truncates the rest of the chain — the
    collapse recorded as finding.026GYJFW71EAK7QTWDA0E1T6PR.  Staleness
    guards may only ever aim at an acceptance nothing was built upon; a
    stripped acceptance that a later re-acceptance deliberately bypassed
    (binding the submit again) has no successor and stays strippable.

    Callers must feed this APPLIED events only.  Counting every recorded
    event lets a write that never applied — say, two conflicting reopens
    naming the same acceptance — shield a live acceptance from its own
    staleness guard (finding.1J0VT9597NVS6SKRMFQTSQSH3E).
    """

    built_upon: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        expected = payload.get("expected_revision")
        if isinstance(expected, str) and expected:
            built_upon.add(expected)
    return built_upon


def _stale_task_acceptance_ids(
    events: Iterable[Mapping[str, Any]],
    *,
    exempt_acceptance_ids: frozenset[str] = frozenset(),
) -> set[str]:
    """Identify acceptances bound to a superseded review revision before CAS grouping.

    ``exempt_acceptance_ids`` names the acceptances a successor demonstrably
    built upon — computed by ``project_events`` from a lenient probe pass, so
    only successors that actually APPLIED count.  Deriving the exemption here,
    from every recorded event, let a write that never applied shield a live
    acceptance from this guard (finding.1J0VT9597NVS6SKRMFQTSQSH3E).
    """

    current_review_revisions: dict[str, str] = {}
    materialized = list(events)
    for event in materialized:
        if str(event.get("event_type", "")) not in {
            "review.requested",
            "review.completed",
        }:
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        review_id = payload.get("review_id")
        event_id = event.get("event_id")
        if isinstance(review_id, str) and isinstance(event_id, str):
            current_review_revisions[review_id] = str(
                event.get("_effective_correction_id") or event_id
            )

    stale: set[str] = set()
    for event in materialized:
        if event.get("event_type") != "task.accepted":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        binding = payload.get("acceptance_review") or {}
        if not isinstance(binding, Mapping):
            continue
        review_ref = binding.get("ref") or {}
        if not isinstance(review_ref, Mapping):
            continue
        event_id = str(event.get("event_id", ""))
        # An acceptance an APPLIED successor built upon is history: the reopen
        # after it already took the claim back, so a review revision that
        # moved on cannot make it retroactively wrong without rejecting that
        # successor.
        if event_id in exempt_acceptance_ids:
            continue
        review_id = review_ref.get("id")
        revision = binding.get("revision")
        if (
            isinstance(review_id, str)
            and isinstance(revision, str)
            and current_review_revisions.get(review_id) != revision
        ):
            stale.add(event_id)
    return stale


def _current_evidence_revision(snapshot: ProjectSnapshot, ref: Mapping[str, Any]) -> str | None:
    kind = str(ref.get("kind", ""))
    identifier = str(ref.get("id", ""))
    if kind == "event":
        if identifier not in snapshot.known_event_ids:
            return None
        if (
            identifier in snapshot.invalid_event_ids
            or (
                "event",
                identifier,
            )
            in snapshot.stale_refs
        ):
            return None
        return snapshot.effective_event_revisions.get(identifier, identifier)
    if kind == "manifest":
        return identifier if identifier in snapshot.known_manifest_ids else None
    collection_name = collection_for(kind)
    if collection_name is None:
        return None
    item = getattr(snapshot, collection_name).get(identifier)
    if not item:
        return None
    if kind == "artifact" and item.get("manifest_ref") not in snapshot.known_manifest_ids:
        return None
    return str(item.get("effective_revision") or item.get("revision"))


def _has_stale_evidence(snapshot: ProjectSnapshot, item: Mapping[str, Any]) -> bool:
    for bound in item.get("evidence_refs") or []:
        if not isinstance(bound, Mapping) or set(bound) != {"ref", "revision"}:
            return True
        ref = bound.get("ref")
        if not isinstance(ref, Mapping):
            return True
        if _is_accepted_task_subject_evidence(snapshot, item, ref, bound.get("revision")):
            continue
        if _current_evidence_revision(snapshot, ref) != bound.get("revision"):
            return True
    return False


def _is_accepted_task_subject_evidence(
    snapshot: ProjectSnapshot,
    item: Mapping[str, Any],
    evidence_ref: Mapping[str, Any],
    evidence_revision: object,
) -> bool:
    """Recognize evidence on the accepted subject, not its acceptance event.

    A review or verification can bind its own task at the submitted revision.
    Acceptance advances that task's record, so comparing the binding with the
    post-acceptance revision would make the acceptance invalidate itself during
    replay.  The exception is deliberately limited to that self-reference and
    only to the revision captured as the accepted subject.
    """

    target = item.get("target_ref")
    if (
        not isinstance(target, Mapping)
        or target.get("kind") != "task"
        or evidence_ref.get("kind") != "task"
        or evidence_ref.get("id") != target.get("id")
    ):
        return False
    task = snapshot.tasks.get(str(target.get("id", "")))
    return (
        task is not None
        and task.get("state") == "accepted"
        and evidence_revision == task.get("accepted_subject_revision")
    )


def _has_stale_artifacts(snapshot: ProjectSnapshot, item: Mapping[str, Any]) -> bool:
    for bound in item.get("artifact_bindings") or []:
        if not isinstance(bound, Mapping) or set(bound) != {"ref", "revision"}:
            return True
        ref = bound.get("ref")
        if not isinstance(ref, Mapping) or ref.get("kind") != "artifact":
            return True
        if _current_evidence_revision(snapshot, ref) != bound.get("revision"):
            return True
    return False


def _mark_bound_evidence_stale(snapshot: ProjectSnapshot) -> None:
    for identifier, task in snapshot.tasks.items():
        stale = _has_stale_artifacts(snapshot, task)
        snapshot.tasks[identifier] = task.with_artifact_stale(stale)
        if stale:
            snapshot.warnings.append(f"task {identifier} has stale revision-bound artifacts")
    for identifier, item in snapshot.reviews.items():
        target = item.get("target_ref") or {}
        target_kind = str(target.get("kind", ""))
        target_id = str(target.get("id", ""))
        current = _current_evidence_revision(snapshot, target)
        if target_kind == "task":
            task = snapshot.tasks.get(target_id)
            if task and task.get("state") == "accepted":
                accepted_subject_revision = task.get("accepted_subject_revision")
                if isinstance(accepted_subject_revision, str):
                    current = accepted_subject_revision
        stale = (
            current is None
            or item.get("target_revision") != current
            or _has_stale_evidence(snapshot, item)
            or (
                target_kind == "task"
                and bool((snapshot.tasks.get(target_id) or {}).get("artifact_stale"))
            )
        )
        snapshot.reviews[identifier] = item.with_stale(stale)
        if stale:
            snapshot.warnings.append(f"review {identifier} is stale for current target revision")
    refresh_verification_staleness(
        snapshot,
        current_evidence_revision=_current_evidence_revision,
        has_stale_evidence=lambda item: _has_stale_evidence(snapshot, item),
    )
    for identifier, finding in snapshot.findings.items():
        stale = _has_stale_evidence(snapshot, finding)
        snapshot.findings[identifier] = finding.with_stale(stale)
        if stale and finding.get("state") == "verified":
            snapshot.warnings.append(f"finding {identifier} has stale revision-bound evidence")
    for identifier, decision in snapshot.decisions.items():
        stale = _has_stale_evidence(snapshot, decision)
        snapshot.decisions[identifier] = decision.with_stale(stale)
        if stale and decision.get("state") == "accepted":
            snapshot.warnings.append(f"decision {identifier} has stale revision-bound evidence")


def _fail_closed_decision_conflicts(snapshot: ProjectSnapshot) -> None:
    accepted_by_scope: dict[str, list[str]] = defaultdict(list)
    for identifier, decision in snapshot.decisions.items():
        scope = decision.get("scope")
        if (
            decision.get("state") == "accepted"
            and decision.get("stale") is not True
            and isinstance(scope, str)
            and scope
        ):
            accepted_by_scope[scope].append(identifier)
    for scope, identifiers in sorted(accepted_by_scope.items()):
        if len(identifiers) < 2:
            continue
        ordered = sorted(identifiers)
        _record_issue(
            snapshot,
            "decision_scope_conflict",
            f"conflicting accepted decisions for scope {scope}: {', '.join(ordered)}",
            event_ids=(snapshot.decisions[identifier]["revision"] for identifier in ordered),
        )
        for identifier in ordered:
            snapshot.decisions[identifier] = snapshot.decisions[identifier].with_conflict()


def _project_events_once(
    events: Iterable[Mapping[str, Any]],
    *,
    known_manifest_ids: Iterable[str] | None = None,
    forced_stale_acceptance_ids: frozenset[str] = frozenset(),
    exempt_acceptance_ids: frozenset[str] = frozenset(),
) -> ProjectSnapshot:
    raw = sorted(
        (dict(event) for event in events),
        key=lambda item: (str(item.get("recorded_at", "")), str(item.get("event_id", ""))),
    )
    relations = [relation for event in raw for relation in (event.get("relations") or [])]
    invalidation = derive_invalidation_state(raw, relations)
    snapshot = ProjectSnapshot()
    snapshot.known_event_ids = {
        str(event.get("event_id")) for event in raw if event.get("event_id")
    }
    snapshot.known_manifest_ids = (
        set(map(str, known_manifest_ids))
        if known_manifest_ids is not None
        else {
            str((event.get("payload") or {}).get("manifest_ref"))
            for event in raw
            if event.get("event_type") in {"artifact.registered", "artifact.revised"}
            and isinstance(event.get("payload"), Mapping)
            and (event.get("payload") or {}).get("manifest_ref")
        }
    )
    snapshot.stale_refs = set(invalidation.stale_targets)
    snapshot.invalid_event_ids = {
        identifier for kind, identifier in invalidation.invalid_targets if kind == "event"
    }
    corrections = [
        event
        for event in raw
        if event.get("event_type") == "event.corrected"
        and ("event", str(event.get("event_id", ""))) not in invalidation.invalid_targets
        and ("event", str(event.get("event_id", ""))) not in invalidation.stale_targets
    ]
    corrections_by_root: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for correction in corrections:
        target_id = str((correction.get("payload") or {}).get("target_event_id", ""))
        corrections_by_root[target_id].append(correction)
    correction_candidates_examined = 0
    root_event_ids = {
        str(event.get("event_id", ""))
        for event in raw
        if event.get("event_type")
        not in {"event.corrected", "event.invalidated", "event.invalidation_revoked"}
    }
    for correction in corrections:
        target_id = str((correction.get("payload") or {}).get("target_event_id", ""))
        if target_id not in root_event_ids:
            _record_issue(
                snapshot,
                "correction_unknown_root",
                f"correction {correction.get('event_id')} targets an unknown root event",
                event_ids=(str(correction.get("event_id", "")),),
            )

    effective: list[Mapping[str, Any]] = []
    for event in raw:
        event_id = str(event.get("event_id", ""))
        event_type = str(event.get("event_type", ""))
        if event_type in {"event.corrected", "event.invalidated", "event.invalidation_revoked"}:
            continue
        if ("event", event_id) in invalidation.invalid_targets or (
            "event",
            event_id,
        ) in invalidation.stale_targets:
            continue
        candidates = corrections_by_root.get(event_id, ())
        correction_candidates_examined += len(candidates)
        revision = resolve_revision(event, candidates)
        correction_event_ids = (event_id, *revision.active_heads)
        for issue in revision.issues:
            _record_issue(
                snapshot,
                "correction_revision_invalid",
                issue,
                event_ids=correction_event_ids,
            )
        if revision.conflict or revision.effective_event is None:
            _record_issue(
                snapshot,
                "correction_conflict",
                f"event {event_id} has conflicting corrections",
                event_ids=correction_event_ids,
            )
            continue
        event_type = str(revision.effective_event.get("event_type", ""))
        spec = EVENT_SPECS.get(event_type)
        original_payload = event.get("payload") or {}
        replacement_payload = revision.effective_event.get("payload") or {}
        if isinstance(original_payload, Mapping) and isinstance(replacement_payload, Mapping):
            structural_changes = structural_correction_changes(
                original_payload, replacement_payload
            )
            if structural_changes:
                _record_issue(
                    snapshot,
                    "correction_structural_change",
                    f"event {event_id} correction cannot change structural fields: "
                    + ", ".join(structural_changes),
                    event_ids=correction_event_ids,
                )
                continue
        if (
            spec
            and spec.entity_id_field
            and (
                original_payload.get(spec.entity_id_field)
                != replacement_payload.get(spec.entity_id_field)
            )
        ):
            _record_issue(
                snapshot,
                "correction_identity_change",
                f"event {event_id} correction cannot change {spec.entity_id_field}",
                event_ids=correction_event_ids,
            )
            continue
        effective.append(revision.effective_event)

    stale_acceptance_ids = _stale_task_acceptance_ids(
        effective, exempt_acceptance_ids=exempt_acceptance_ids
    ) | set(forced_stale_acceptance_ids)
    conflicted_event_ids, conflict_issues = _cas_conflicts(
        event for event in effective if str(event.get("event_id", "")) not in stale_acceptance_ids
    )
    snapshot.issues.extend(conflict_issues)
    snapshot.warnings.extend(issue.message for issue in conflict_issues)

    from .lifecycle import validate_transition

    for event in effective:
        event_id = str(event.get("event_id", ""))
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        if event_id in stale_acceptance_ids:
            snapshot.stale_refs.add(("event", event_id))
            snapshot.warnings.append(
                f"task acceptance event {event_id} is stale and was not applied"
            )
            continue
        if event_id in conflicted_event_ids:
            continue
        try:
            if not isinstance(payload, Mapping):
                raise ValidationError("event payload must be an object")
            actor = event.get("actor")
            if not isinstance(actor, Mapping):
                raise ValidationError("event actor must be an object")
            actor_session_id = str(actor.get("session_id", ""))
            actor_identity = _actor_identity(actor)
            known_identity = snapshot.session_identities.get(actor_session_id)
            if known_identity is not None and known_identity != actor_identity:
                raise LifecycleConflictError("session actor identity changed during replay")
            validate_payload(event_type, payload)
            typed_envelope = parse_event_envelope(event_type, payload)
            workspace_id = event.get("workspace_id")
            if snapshot.workspace_id is not None and workspace_id != snapshot.workspace_id:
                raise LifecycleConflictError(
                    f"workspace changed from {snapshot.workspace_id} to {workspace_id}"
                )
            validate_transition(
                snapshot,
                event_type,
                payload,
                actor_session_id=actor_session_id,
                actor=actor,
                # Relations carry the run/role binding on `delegation.requested`,
                # so replay revalidates who was allowed to staff that role there
                # rather than trusting that the write path checked it once.  The
                # binding is read from the relation only on the requested event
                # (see the delegation branch above), so a relation on a later
                # delegation event cannot silently rebind the run on replay.
                relations=event.get("relations") or (),
            )
            _apply_effective_event(snapshot, event, typed_envelope=typed_envelope)
            snapshot.session_identities.setdefault(actor_session_id, actor_identity)
            snapshot.effective_event_revisions[event_id] = str(
                event.get("_effective_correction_id") or event_id
            )
            snapshot.workspace_id = snapshot.workspace_id or str(workspace_id)
        except LifecycleConflictError as exc:
            if event_type == "decision.accepted" and str(exc).startswith(
                "conflicting accepted decisions for scope"
            ):
                _apply_effective_event(snapshot, event, typed_envelope=typed_envelope)
                snapshot.effective_event_revisions[event_id] = str(
                    event.get("_effective_correction_id") or event_id
                )
            else:
                _record_issue(
                    snapshot,
                    "lifecycle_rejected",
                    f"event {event_id} rejected by lifecycle: {exc}",
                    event_ids=(event_id,),
                )
        except (KeyError, TypeError, ValidationError) as exc:
            _record_issue(
                snapshot,
                "domain_validation_rejected",
                f"event {event_id} rejected by domain validation: {exc}",
                event_ids=(event_id,),
            )

    _mark_bound_evidence_stale(snapshot)
    _fail_closed_decision_conflicts(snapshot)
    if snapshot.semantics_required > LEDGER_SEMANTICS_VERSION:
        # This build replays with older semantics than some write in the
        # ledger depends on, so every integrity finding above is a possible
        # misdiagnosis — the 17 Aug incident read a healthy twice-accepted
        # chain as a lifecycle failure.  Say the one true thing instead.
        gate = ProjectionIssue(
            code="ledger_ahead_of_code",
            severity="error",
            message=(
                f"this ledger requires agent-commons semantics version "
                f"{snapshot.semantics_required}, but this build supports "
                f"{LEDGER_SEMANTICS_VERSION} — update agent-commons before "
                f"writing; integrity findings from an outdated reader are "
                f"unreliable and have been suppressed"
            ),
            repairable=False,
        )
        snapshot.issues = [gate]
        snapshot.warnings = [gate.message]
    snapshot.replay_metrics = {
        "events_replayed": len(raw),
        "corrections_indexed": len(corrections),
        "correction_targets": len(corrections_by_root),
        "correction_candidates_examined": correction_candidates_examined,
        "fixed_point_passes": 1,
    }
    return snapshot


def project_events(
    events: Iterable[Mapping[str, Any]],
    *,
    known_manifest_ids: Iterable[str] | None = None,
) -> ProjectSnapshot:
    """Project to a fixed point where stale evidence cannot preserve acceptance."""

    materialized = list(events)
    manifests = tuple(known_manifest_ids) if known_manifest_ids is not None else None
    passes = 0

    # Only an acceptance a successor demonstrably built upon is exempt from
    # the staleness guards: stripping such an acceptance rejects the
    # already-applied reopen as stale-expected and truncates the whole chain
    # after it (finding.026GYJFW71EAK7QTWDA0E1T6PR).  "Demonstrably" means
    # the successor APPLIED — counted from a lenient probe pass in which
    # every acceptance stands, because counting merely-recorded events let
    # two conflicting reopens that never applied shield a live acceptance
    # from its own guard (finding.1J0VT9597NVS6SKRMFQTSQSH3E).  The probe
    # runs only when some event names an acceptance as its expected
    # revision; most histories never do.
    acceptance_ids = {
        str(event.get("event_id", ""))
        for event in materialized
        if event.get("event_type") == "task.accepted"
    }
    pattern_present = any(
        isinstance(event.get("payload"), Mapping)
        and str((event.get("payload") or {}).get("expected_revision", "")) in acceptance_ids
        for event in materialized
    )
    exempt: frozenset[str] = frozenset()
    if pattern_present:
        probe = _project_events_once(
            materialized,
            known_manifest_ids=manifests,
            exempt_acceptance_ids=frozenset(acceptance_ids),
        )
        passes += 1
        applied_successor_targets = _built_upon_event_ids(
            event
            for event in materialized
            if str(event.get("event_id", "")) in probe.effective_event_revisions
        )
        exempt = frozenset(applied_successor_targets & acceptance_ids)

    snapshot = _project_events_once(
        materialized, known_manifest_ids=manifests, exempt_acceptance_ids=exempt
    )
    passes += 1
    stale_review_ids = {
        identifier for identifier, review in snapshot.reviews.items() if review.get("stale") is True
    }
    stale_acceptance_ids: set[str] = set()
    for event in materialized:
        if event.get("event_type") != "task.accepted":
            continue
        payload = event.get("payload") or {}
        binding = payload.get("acceptance_review") if isinstance(payload, Mapping) else None
        ref = binding.get("ref") if isinstance(binding, Mapping) else None
        task_id = payload.get("task_id") if isinstance(payload, Mapping) else None
        task = snapshot.tasks.get(str(task_id)) if isinstance(task_id, str) else None
        if (
            isinstance(ref, Mapping)
            and ref.get("id") in stale_review_ids
            and task is not None
            and task.get("state") == "accepted"
            and str(event.get("event_id", "")) not in exempt
        ):
            stale_acceptance_ids.add(str(event.get("event_id", "")))
    if stale_acceptance_ids:
        final = _project_events_once(
            materialized,
            known_manifest_ids=manifests,
            forced_stale_acceptance_ids=frozenset(stale_acceptance_ids),
            exempt_acceptance_ids=exempt,
        )
        passes += 1
        final.replay_metrics["fixed_point_passes"] = passes
        return final
    snapshot.replay_metrics["fixed_point_passes"] = passes
    return snapshot
