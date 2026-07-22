from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_commons.core.canonical import canonical_sha256

CORRECTION_IMMUTABLE_FIELDS = frozenset(
    {
        "artifact_refs",
        "artifact_bindings",
        "acceptance_review",
        "attempt",
        "classification",
        "child_session_id",
        "criteria",
        "depth",
        "dependencies",
        "evidence_refs",
        "expected_revision",
        "independent",
        "manifest_ref",
        "limits",
        "parent_delegation_id",
        "parent_session_id",
        "purpose",
        "related_refs",
        "replacement_decision_id",
        "result_refs",
        "revision",
        "scope",
        "target_ref",
        "target_profile",
        "target_revision",
        "root_delegation_id",
        "to",
        "verdict",
    }
)


def structural_correction_changes(
    original: Mapping[str, Any], replacement: Mapping[str, Any]
) -> tuple[str, ...]:
    """Return causal/reference fields that immutable-envelope correction cannot change."""

    return tuple(
        sorted(
            field
            for field in CORRECTION_IMMUTABLE_FIELDS
            if original.get(field) != replacement.get(field)
        )
    )


@dataclass(frozen=True)
class RevisionState:
    root_event_id: str
    effective_event: Mapping[str, Any] | None
    active_heads: tuple[str, ...]
    conflict: bool
    issues: tuple[str, ...]


def resolve_revision(
    root_event: Mapping[str, Any], correction_events: Iterable[Mapping[str, Any]]
) -> RevisionState:
    root_id = str(root_event["event_id"])
    corrections = [
        event
        for event in correction_events
        if (event.get("payload") or {}).get("target_event_id") == root_id
    ]
    if not corrections:
        return RevisionState(root_id, deepcopy(root_event), (), False, ())

    issues: list[str] = []
    correction_ids = [str(event.get("event_id", "")) for event in corrections]
    if any(not identifier for identifier in correction_ids):
        issues.append("a correction is missing event_id")
    if len(set(correction_ids)) != len(correction_ids):
        issues.append("duplicate correction event_id")
    by_id = {str(event["event_id"]): event for event in corrections if event.get("event_id")}
    superseded: set[str] = set()
    parent_graph: dict[str, set[str]] = {identifier: set() for identifier in by_id}
    for event in corrections:
        event_id = str(event.get("event_id", ""))
        raw_parents = (event.get("payload") or {}).get("superseded_correction_event_ids", [])
        if not isinstance(raw_parents, list):
            issues.append(f"correction {event_id} parents must be a list")
            continue
        if len(set(map(str, raw_parents))) != len(raw_parents):
            issues.append(f"correction {event_id} has duplicate parents")
        parents: list[str] = []
        for raw_parent in raw_parents:
            if not isinstance(raw_parent, str) or not raw_parent:
                issues.append(f"correction {event_id} has malformed parent")
                continue
            parents.append(raw_parent)
        for parent in parents:
            if parent == event_id:
                issues.append(f"correction {event_id} cannot supersede itself")
                continue
            if parent not in by_id:
                issues.append(f"correction {event_id} has unknown parent {parent}")
            else:
                superseded.add(str(parent))
                parent_graph.setdefault(event_id, set()).add(parent)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            issues.append("correction supersession graph contains a cycle")
            return
        visiting.add(identifier)
        for parent in parent_graph.get(identifier, ()):
            visit(parent)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in sorted(parent_graph):
        visit(identifier)

    heads = tuple(sorted(set(by_id) - superseded))
    if len(heads) != 1:
        issues.append(
            "corrections have no active head"
            if not heads
            else "corrections have multiple active heads"
        )
    if issues:
        return RevisionState(root_id, None, heads, True, tuple(dict.fromkeys(issues)))

    head = by_id[heads[0]]
    payload = head.get("payload") or {}
    expected = payload.get("expected_target_sha256")
    if expected != canonical_sha256(root_event):
        issues.append("expected_target_sha256 does not match immutable root event")
        return RevisionState(root_id, None, heads, True, tuple(issues))
    replacement = payload.get("replacement_payload")
    if not isinstance(replacement, Mapping):
        issues.append("replacement_payload must be an object")
        return RevisionState(root_id, None, heads, True, tuple(issues))
    effective = deepcopy(root_event)
    effective["payload"] = deepcopy(dict(replacement))
    effective["_effective_correction_id"] = heads[0]
    return RevisionState(root_id, effective, heads, bool(issues), tuple(issues))
