from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from agent_commons.errors import LifecycleConflictError, ValidationError

from .invalidations import derive_invalidation_state
from .revisions import resolve_revision, structural_correction_changes
from .validation import EVENT_SPECS, validate_payload

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
    "delegation.timed_out": "timed_out",
    "delegation.needs_operator": "needs_operator",
}


@dataclass
class ProjectSnapshot:
    workspace_id: str | None = None
    objectives: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    threads: dict[str, dict[str, Any]] = field(default_factory=dict)
    reviews: dict[str, dict[str, Any]] = field(default_factory=dict)
    verifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    handoffs: dict[str, dict[str, Any]] = field(default_factory=dict)
    delegations: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    invalid_event_ids: set[str] = field(default_factory=set)
    stale_refs: set[tuple[str, str]] = field(default_factory=set)
    effective_event_revisions: dict[str, str] = field(default_factory=dict)
    known_event_ids: set[str] = field(default_factory=set)
    known_manifest_ids: set[str] = field(default_factory=set)

    def entity_revision(self, kind: str, identifier: str) -> str | None:
        collection = getattr(
            self,
            {
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
            }.get(kind, ""),
            None,
        )
        if not isinstance(collection, dict) or identifier not in collection:
            return None
        return str(collection[identifier]["revision"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "objectives": list(self.objectives.values()),
            "tasks": list(self.tasks.values()),
            "threads": list(self.threads.values()),
            "reviews": list(self.reviews.values()),
            "verifications": list(self.verifications.values()),
            "findings": list(self.findings.values()),
            "decisions": list(self.decisions.values()),
            "artifacts": list(self.artifacts.values()),
            "handoffs": list(self.handoffs.values()),
            "delegations": list(self.delegations.values()),
            "warnings": sorted(set(self.warnings)),
            "invalid_event_ids": sorted(self.invalid_event_ids),
            "stale_refs": [
                {"kind": kind, "id": identifier} for kind, identifier in sorted(self.stale_refs)
            ],
        }


def _apply(
    collection: dict[str, dict[str, Any]], identifier: str, event: Mapping[str, Any], state: str
) -> None:
    current = deepcopy(collection.get(identifier, {}))
    payload = deepcopy(dict(event.get("payload") or {}))
    collection[identifier] = {
        **current,
        **payload,
        "id": identifier,
        "state": state,
        "revision": str(event["event_id"]),
        "effective_revision": str(event.get("_effective_correction_id") or event["event_id"]),
        "recorded_at": event.get("recorded_at"),
        "actor": event.get("actor"),
    }


def _apply_effective_event(snapshot: ProjectSnapshot, event: Mapping[str, Any]) -> None:
    event_type = str(event["event_type"])
    payload = event["payload"]
    if event_type == "objective.created":
        _apply(snapshot.objectives, str(payload["objective_id"]), event, "active")
    elif event_type == "objective.revised":
        revised_payload = {
            **dict(payload),
            **deepcopy(dict(payload["changes"])),
        }
        revised_payload.pop("changes", None)
        _apply(
            snapshot.objectives,
            str(payload["objective_id"]),
            {**event, "payload": revised_payload},
            "active",
        )
    elif event_type == "objective.closed":
        _apply(snapshot.objectives, str(payload["objective_id"]), event, "closed")
    elif event_type in TASK_STATES:
        task_id = str(payload["task_id"])
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
            **dict(payload),
            "work_author_session_ids": sorted(work_author_session_ids),
        }
        if event_type == "task.accepted":
            accepted_payload = {
                **task_payload,
                "accepted_subject_revision": current.get(
                    "effective_revision", current.get("revision")
                ),
            }
            _apply(
                snapshot.tasks,
                task_id,
                {**event, "payload": accepted_payload},
                TASK_STATES[event_type],
            )
        else:
            _apply(
                snapshot.tasks,
                task_id,
                {**event, "payload": task_payload},
                TASK_STATES[event_type],
            )
    elif event_type in THREAD_STATES:
        thread_id = str(payload["thread_id"])
        _apply(
            snapshot.threads,
            thread_id,
            event,
            str(payload.get("resolution") or THREAD_STATES[event_type]),
        )
        if event_type == "thread.replied":
            snapshot.threads[thread_id].setdefault("messages", []).append(
                {
                    "message_id": payload["message_id"],
                    "body": payload["body"],
                    "actor": deepcopy(event.get("actor")),
                    "recorded_at": event.get("recorded_at"),
                }
            )
    elif event_type in {"artifact.registered", "artifact.revised"}:
        artifact_payload = deepcopy(dict(payload))
        artifact_payload["content_revision"] = artifact_payload.pop("revision")
        _apply(
            snapshot.artifacts,
            str(payload["artifact_id"]),
            {**event, "payload": artifact_payload},
            "registered",
        )
    elif event_type.startswith("review."):
        _apply(
            snapshot.reviews,
            str(payload["review_id"]),
            event,
            "requested" if event_type == "review.requested" else str(payload["verdict"]),
        )
    elif event_type == "verification.recorded":
        _apply(snapshot.verifications, str(payload["verification_id"]), event, "recorded")
    elif event_type in FINDING_STATES:
        _apply(snapshot.findings, str(payload["finding_id"]), event, FINDING_STATES[event_type])
    elif event_type in DECISION_STATES:
        _apply(
            snapshot.decisions,
            str(payload["decision_id"]),
            event,
            DECISION_STATES[event_type],
        )
    elif event_type.startswith("handoff."):
        _apply(
            snapshot.handoffs,
            str(payload["handoff_id"]),
            event,
            "acknowledged" if event_type == "handoff.acknowledged" else "open",
        )
    elif event_type in DELEGATION_STATES:
        _apply(
            snapshot.delegations,
            str(payload["delegation_id"]),
            event,
            DELEGATION_STATES[event_type],
        )
    else:  # pragma: no cover - kept defensive if the registry is extended incorrectly
        raise ValidationError(f"unsupported projection event type: {event_type}")


def _cas_conflicts(
    events: Iterable[Mapping[str, Any]],
) -> tuple[set[str], list[str]]:
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
    warnings: list[str] = []
    for (kind, identifier, revision), event_ids in sorted(groups.items()):
        unique_ids = sorted(set(event_ids))
        if len(unique_ids) < 2:
            continue
        conflicted.update(unique_ids)
        warnings.append(
            f"conflicting concurrent {kind} transitions for {identifier} at {revision}: "
            + ", ".join(unique_ids)
        )
    return conflicted, warnings


def _stale_task_acceptance_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    """Identify acceptances bound to a superseded review revision before CAS grouping."""

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
        review_id = review_ref.get("id")
        revision = binding.get("revision")
        if (
            isinstance(review_id, str)
            and isinstance(revision, str)
            and current_review_revisions.get(review_id) != revision
        ):
            stale.add(str(event.get("event_id", "")))
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
    collection_name = {
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
    }.get(kind)
    if collection_name is None:
        return None
    item = getattr(snapshot, collection_name).get(identifier)
    if not item:
        return None
    return str(item.get("effective_revision") or item.get("revision"))


def _has_stale_evidence(snapshot: ProjectSnapshot, item: Mapping[str, Any]) -> bool:
    for bound in item.get("evidence_refs") or []:
        if not isinstance(bound, Mapping) or set(bound) != {"ref", "revision"}:
            return True
        ref = bound.get("ref")
        if not isinstance(ref, Mapping):
            return True
        if _current_evidence_revision(snapshot, ref) != bound.get("revision"):
            return True
    return False


def _mark_bound_evidence_stale(snapshot: ProjectSnapshot) -> None:
    for label, collection in (
        ("review", snapshot.reviews),
        ("verification", snapshot.verifications),
    ):
        for identifier, item in collection.items():
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
            )
            item["stale"] = stale
            if stale:
                snapshot.warnings.append(
                    f"{label} {identifier} is stale for current target revision"
                )
    for label, collection, effective_state in (
        ("finding", snapshot.findings, "verified"),
        ("decision", snapshot.decisions, "accepted"),
    ):
        for identifier, item in collection.items():
            stale = _has_stale_evidence(snapshot, item)
            item["stale"] = stale
            if stale and item.get("state") == effective_state:
                snapshot.warnings.append(f"{label} {identifier} has stale revision-bound evidence")


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
        snapshot.warnings.append(
            f"conflicting accepted decisions for scope {scope}: {', '.join(ordered)}"
        )
        for identifier in ordered:
            snapshot.decisions[identifier]["state"] = "conflicted"
            snapshot.decisions[identifier]["conflict"] = True


def project_events(
    events: Iterable[Mapping[str, Any]],
    *,
    known_manifest_ids: Iterable[str] | None = None,
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
    root_event_ids = {
        str(event.get("event_id", ""))
        for event in raw
        if event.get("event_type")
        not in {"event.corrected", "event.invalidated", "event.invalidation_revoked"}
    }
    for correction in corrections:
        target_id = str((correction.get("payload") or {}).get("target_event_id", ""))
        if target_id not in root_event_ids:
            snapshot.warnings.append(
                f"correction {correction.get('event_id')} targets an unknown root event"
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
        revision = resolve_revision(event, corrections)
        snapshot.warnings.extend(revision.issues)
        if revision.conflict or revision.effective_event is None:
            snapshot.warnings.append(f"event {event_id} has conflicting corrections")
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
                snapshot.warnings.append(
                    f"event {event_id} correction cannot change structural fields: "
                    + ", ".join(structural_changes)
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
            snapshot.warnings.append(
                f"event {event_id} correction cannot change {spec.entity_id_field}"
            )
            continue
        effective.append(revision.effective_event)

    stale_acceptance_ids = _stale_task_acceptance_ids(effective)
    conflicted_event_ids, conflict_warnings = _cas_conflicts(
        event for event in effective if str(event.get("event_id", "")) not in stale_acceptance_ids
    )
    snapshot.warnings.extend(conflict_warnings)

    from .lifecycle import validate_transition

    for event in effective:
        event_id = str(event.get("event_id", ""))
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        if event_id in conflicted_event_ids:
            continue
        try:
            if not isinstance(payload, Mapping):
                raise ValidationError("event payload must be an object")
            validate_payload(event_type, payload)
            workspace_id = event.get("workspace_id")
            if snapshot.workspace_id is not None and workspace_id != snapshot.workspace_id:
                raise LifecycleConflictError(
                    f"workspace changed from {snapshot.workspace_id} to {workspace_id}"
                )
            validate_transition(
                snapshot,
                event_type,
                payload,
                actor_session_id=str((event.get("actor") or {}).get("session_id", "")),
            )
            _apply_effective_event(snapshot, event)
            snapshot.effective_event_revisions[event_id] = str(
                event.get("_effective_correction_id") or event_id
            )
            snapshot.workspace_id = snapshot.workspace_id or str(workspace_id)
        except LifecycleConflictError as exc:
            if event_type == "decision.accepted" and str(exc).startswith(
                "conflicting accepted decisions for scope"
            ):
                _apply_effective_event(snapshot, event)
                snapshot.effective_event_revisions[event_id] = str(
                    event.get("_effective_correction_id") or event_id
                )
            elif event_type == "task.accepted":
                snapshot.stale_refs.add(("event", event_id))
                snapshot.warnings.append(
                    f"task acceptance event {event_id} is stale and was not applied: {exc}"
                )
            else:
                snapshot.warnings.append(f"event {event_id} rejected by lifecycle: {exc}")
        except (KeyError, TypeError, ValidationError) as exc:
            snapshot.warnings.append(f"event {event_id} rejected by domain validation: {exc}")

    _mark_bound_evidence_stale(snapshot)
    _fail_closed_decision_conflicts(snapshot)
    return snapshot
