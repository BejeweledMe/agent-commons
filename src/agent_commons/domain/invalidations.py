from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

DEPENDENCY_PREDICATES = frozenset(
    {"depends_on", "uses", "derived_from", "reviews", "verifies", "supersedes"}
)


def _node(ref: Mapping[str, Any]) -> tuple[str, str] | None:
    kind, identifier = ref.get("kind"), ref.get("id")
    if not kind or not identifier:
        return None
    return str(kind), str(identifier)


@dataclass(frozen=True)
class InvalidationState:
    invalid_targets: frozenset[tuple[str, str]]
    stale_targets: frozenset[tuple[str, str]]
    active_invalidation_ids: frozenset[str]


def _implicit_event_dependencies(
    events: Iterable[Mapping[str, Any]],
) -> Iterable[dict[str, Any]]:
    """Derive only protocol-defined causal edges, never suffix-inferred refs."""

    for event in events:
        event_id = event.get("event_id")
        payload = event.get("payload")
        if not isinstance(event_id, str) or not event_id or not isinstance(payload, Mapping):
            continue
        predecessors: list[str] = []
        expected_revision = payload.get("expected_revision")
        if isinstance(expected_revision, str) and expected_revision:
            predecessors.append(expected_revision)
        if event.get("event_type") in {"review.requested", "verification.recorded"}:
            target_revision = payload.get("target_revision")
            if isinstance(target_revision, str) and target_revision:
                predecessors.append(target_revision)
        if event.get("event_type") == "task.accepted":
            acceptance_review = payload.get("acceptance_review")
            if isinstance(acceptance_review, Mapping):
                review_revision = acceptance_review.get("revision")
                if isinstance(review_revision, str) and review_revision:
                    predecessors.append(review_revision)
        causation_event_id = event.get("causation_event_id")
        if isinstance(causation_event_id, str) and causation_event_id:
            predecessors.append(causation_event_id)
        if event.get("event_type") == "event.corrected":
            target_event_id = payload.get("target_event_id")
            if isinstance(target_event_id, str) and target_event_id:
                predecessors.append(target_event_id)
            parents = payload.get("superseded_correction_event_ids", [])
            if isinstance(parents, list):
                predecessors.extend(
                    parent for parent in parents if isinstance(parent, str) and parent
                )
        for predecessor in set(predecessors):
            if predecessor == event_id:
                continue
            yield {
                "predicate": "depends_on",
                "subject": {"kind": "event", "id": event_id},
                "object": {"kind": "event", "id": predecessor},
            }


def derive_invalidation_state(
    events: Iterable[Mapping[str, Any]], relations: Iterable[Mapping[str, Any]]
) -> InvalidationState:
    events = list(events)
    reverse_edges: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for relation in (*relations, *_implicit_event_dependencies(events)):
        if relation.get("predicate") not in DEPENDENCY_PREDICATES:
            continue
        source_node = _node(relation.get("subject") or {})
        target_node = _node(relation.get("object") or {})
        if source_node and target_node and source_node != target_node:
            reverse_edges[target_node].add(source_node)

    invalidations = {
        str(event["event_id"]): event
        for event in events
        if event.get("event_type") == "event.invalidated" and event.get("event_id")
    }
    revocations = [
        event
        for event in events
        if event.get("event_type") == "event.invalidation_revoked" and event.get("event_id")
    ]

    def impact(active_ids: set[str]) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        invalid: set[tuple[str, str]] = set()
        for event_id in active_ids:
            node = _node((invalidations[event_id].get("payload") or {}).get("target_ref") or {})
            if node:
                invalid.add(node)
        stale: set[tuple[str, str]] = set()
        queue = deque(invalid)
        while queue:
            current = queue.popleft()
            for descendant in reverse_edges.get(current, ()):
                if descendant not in invalid and descendant not in stale:
                    stale.add(descendant)
                    queue.append(descendant)
        return invalid, stale

    active_ids = set(invalidations)
    while True:
        invalid, stale = impact(active_ids)
        revoked = {
            str((event.get("payload") or {}).get("invalidation_event_id"))
            for event in revocations
            if ("event", str(event["event_id"])) not in invalid
            and ("event", str(event["event_id"])) not in stale
        }
        updated = set(invalidations).difference(revoked)
        if updated == active_ids:
            break
        active_ids = updated
    invalid, stale = impact(active_ids)
    return InvalidationState(frozenset(invalid), frozenset(stale), frozenset(active_ids))
