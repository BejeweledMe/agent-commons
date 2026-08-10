"""Pure projection from a ledger snapshot to a renderable graph.

Every node and edge below is derived from a field that actually exists in the
canonical projection.  Where a relationship a reader might expect is absent from
the ledger -- most notably objective-to-task -- it is reported as absent rather
than inferred, because a fabricated edge is indistinguishable from a real one
once it is drawn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.ui import GRAPH_SCHEMA
from agent_commons.views import truncate_utf8

MAX_NODES = 2_000
MAX_EDGES = 4_000
_LABEL_BYTES = 120

#: Fallback layer for a node that has no place in the reporting hierarchy.
#: A band is a *rank in the chain of command*, not a category of record: a
#: session nobody delegated to sits at the top because it answers to the human,
#: and everything it started sits under it.  Grouping by record kind instead put
#: 300 unrelated nodes in one row and showed no hierarchy at all.
_BANDS = {
    "objective": 0,
    "session": 1,
    "task": 2,
    "artifact": 2,
    "delegation": 3,
    "review": 4,
    "verification": 4,
}

#: A permanent edge is standing structure: who reports to whom, who owns what.
#: A temporary edge is one exchange bound to a single attempt.  The distinction
#: is real in the ledger, not a visual convention, so the projection carries it
#: rather than leaving the frontend to guess from edge kinds.
_PERMANENT_EDGES = frozenset({"spawned", "requested_by", "runs_as", "owns", "depends_on"})

#: Nodes are dropped in this order when the graph exceeds its bounds; terminal
#: work is the least useful thing on screen when there is too much to draw.
_SHED_ORDER = ("delegation", "task", "session", "review", "verification", "artifact", "objective")

_TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "accepted", "completed", "closed", "expired"}
)


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    stripped = "".join(
        character for character in text if character.isprintable() or character == " "
    )
    return truncate_utf8(stripped.strip(), _LABEL_BYTES)


def _label(kind: str, record: Mapping[str, Any]) -> str:
    if kind == "delegation":
        profile = _clean(record.get("target_profile"))
        purpose = _clean(record.get("purpose"))
        return f"{profile} · {purpose}".strip(" ·")
    for key in ("title", "subject", "summary", "proposal", "path", "role_id"):
        value = record.get(key)
        if value:
            return _clean(value)
    return ""


def _state(kind: str, record: Mapping[str, Any]) -> str:
    state = record.get("state")
    if state:
        return _clean(state)
    if kind in {"review", "verification"}:
        return _clean(record.get("verdict") or "requested")
    if kind == "artifact":
        return "registered"
    return ""


def _node(
    kind: str,
    identifier: str,
    record: Mapping[str, Any],
    rank: int | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key in (
        "purpose",
        "target_profile",
        "depth",
        "attempt",
        "priority",
        "client",
        "role_id",
    ):
        value = record.get(key)
        if value is not None and isinstance(value, (str, int, float, bool)):
            attrs[key] = _clean(value) if isinstance(value, str) else value
    if kind == "objective":
        # The ledger has no objective->task link; say so instead of drawing one.
        attrs["attached"] = False
    return {
        "id": identifier,
        "kind": kind,
        "label": _label(kind, record),
        "state": _state(kind, record),
        "stale": bool(record.get("stale") or record.get("artifact_stale")),
        "revision": record.get("revision"),
        "effective_revision": record.get("effective_revision"),
        "recorded_at": record.get("recorded_at"),
        "band": rank if rank is not None else _BANDS.get(kind, 5),
        "reports_to_operator": rank == 0,
        "attrs": attrs,
    }


def _edge(kind: str, source: str, target: str, **attrs: Any) -> dict[str, Any]:
    return {
        "id": f"{kind}|{source}|{target}",
        "kind": kind,
        "from": source,
        "to": target,
        "relation": "permanent" if kind in _PERMANENT_EDGES else "temporary",
        "attrs": {key: value for key, value in attrs.items() if value is not None},
    }


def _counts(records: Mapping[str, Mapping[str, Any]], kind: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records.values():
        state = _state(kind, record) or "unknown"
        counts[state] = counts.get(state, 0) + 1
    return counts


def _shed(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Bound the graph, then drop edges whose endpoints no longer exist."""

    if len(nodes) <= MAX_NODES and len(edges) <= MAX_EDGES:
        return nodes, edges, False

    def priority(node: Mapping[str, Any]) -> tuple[int, int]:
        terminal = 0 if node.get("state") in _TERMINAL_STATES else 1
        try:
            position = _SHED_ORDER.index(str(node.get("kind")))
        except ValueError:
            position = len(_SHED_ORDER)
        return (terminal, -position)

    ordered = sorted(nodes, key=priority, reverse=True)
    kept = ordered[:MAX_NODES]
    identifiers = {node["id"] for node in kept}
    surviving = [
        edge for edge in edges if edge["from"] in identifiers and edge["to"] in identifiers
    ][:MAX_EDGES]
    # Preserve the caller's ordering for stable rendering between polls.
    order = {node["id"]: index for index, node in enumerate(nodes)}
    kept.sort(key=lambda node: order[node["id"]])
    return kept, surviving, True


def _reporting_ranks(snapshot: ProjectSnapshot, session_ids: set[str]) -> dict[str, int]:
    """Rank every node by its distance from the human, along real ledger links.

    A session nobody delegated to answers to the operator, so it ranks 0.  A
    delegation ranks one below the session that requested it, the child session
    it opened ranks below that, and so on down the tree.  Work hangs off the
    session that owns it.  This is the organisation chart the ledger already
    contains; nothing here is inferred.
    """

    ranks: dict[str, int] = {}
    delegations = snapshot.delegations

    # A session is delegated-to when some delegation opened it as its child.
    delegated_sessions = {
        str(record.get("child_session_id"))
        for record in delegations.values()
        if record.get("child_session_id")
    }
    for session_id in session_ids:
        if session_id not in delegated_sessions:
            ranks[session_id] = 0

    # Walk the delegation tree outward from the sessions that answer to a human.
    # Bounded by the node count: every pass must settle at least one node or the
    # remainder is unreachable and keeps its fallback band.
    for _ in range(len(delegations) + 1):
        settled = False
        for identifier, record in delegations.items():
            if identifier in ranks:
                continue
            requester = str(record.get("parent_session_id", ""))
            if requester in ranks:
                ranks[identifier] = ranks[requester] + 1
                child = str(record.get("child_session_id", ""))
                if child:
                    ranks[child] = ranks[identifier] + 1
                settled = True
        if not settled:
            break

    # Work sits one level below whoever owns it; unowned work stays at the top,
    # where an operator can see that nobody has picked it up.
    for identifier, record in snapshot.tasks.items():
        owner = str(record.get("owner_session_id", ""))
        ranks[identifier] = ranks.get(owner, 0) + 1 if owner in ranks else 0
    return ranks


def build_graph(
    snapshot: ProjectSnapshot,
    *,
    sessions: Sequence[Mapping[str, Any]] = (),
    workspace_id: str | None = None,
    generated_at: str,
    ledger_fingerprint: str,
    server_instance_id: str,
    seq: int,
    read_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    session_ids = {str(session.get("session_id", "")) for session in sessions}
    session_ids.discard("")
    ranks = _reporting_ranks(snapshot, session_ids)

    for identifier, record in sorted(snapshot.objectives.items()):
        nodes.append(_node("objective", identifier, record, ranks.get(identifier)))
    for session in sessions:
        identifier = str(session.get("session_id", ""))
        if identifier:
            nodes.append(_node("session", identifier, session, ranks.get(identifier)))
    for identifier, record in sorted(snapshot.tasks.items()):
        nodes.append(_node("task", identifier, record, ranks.get(identifier)))
    for identifier, record in sorted(snapshot.artifacts.items()):
        nodes.append(_node("artifact", identifier, record, ranks.get(identifier)))
    for identifier, record in sorted(snapshot.delegations.items()):
        nodes.append(_node("delegation", identifier, record, ranks.get(identifier)))
    for identifier, record in sorted(snapshot.reviews.items()):
        nodes.append(_node("review", identifier, record, ranks.get(identifier)))
    for identifier, record in sorted(snapshot.verifications.items()):
        nodes.append(_node("verification", identifier, record, ranks.get(identifier)))

    known = {node["id"] for node in nodes}

    for identifier, record in sorted(snapshot.delegations.items()):
        parent = record.get("parent_delegation_id")
        if parent and parent in known:
            edges.append(_edge("spawned", str(parent), identifier))
        target = record.get("target_ref")
        if isinstance(target, Mapping):
            target_id = str(target.get("id", ""))
            if target_id in known:
                edges.append(
                    _edge(
                        "targets",
                        identifier,
                        target_id,
                        revision=record.get("target_revision"),
                    )
                )
        requester = record.get("parent_session_id")
        if requester and requester in known:
            edges.append(_edge("requested_by", str(requester), identifier))
        child = record.get("child_session_id")
        if child and child in known:
            edges.append(_edge("runs_as", identifier, str(child)))

    for identifier, record in sorted(snapshot.tasks.items()):
        owner = record.get("owner_session_id")
        if owner and owner in known:
            edges.append(_edge("owns", str(owner), identifier))
        for dependency in record.get("dependencies") or ():
            if str(dependency) in known:
                edges.append(_edge("depends_on", identifier, str(dependency)))

    for collection in (snapshot.reviews, snapshot.verifications):
        for identifier, record in sorted(collection.items()):
            target = record.get("target_ref")
            if isinstance(target, Mapping):
                target_id = str(target.get("id", ""))
                if target_id in known:
                    edges.append(
                        _edge(
                            "reviews",
                            identifier,
                            target_id,
                            revision=record.get("target_revision"),
                            stale=bool(record.get("stale")),
                        )
                    )

    nodes, edges, truncated = _shed(nodes, edges)

    return {
        "schema": GRAPH_SCHEMA,
        "workspace_id": workspace_id or snapshot.workspace_id,
        "generated_at": generated_at,
        "ledger_fingerprint": ledger_fingerprint,
        "server_instance_id": server_instance_id,
        "seq": seq,
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "objectives": _counts(snapshot.objectives, "objective"),
            "tasks": _counts(snapshot.tasks, "task"),
            "delegations": _counts(snapshot.delegations, "delegation"),
            "reviews": _counts(snapshot.reviews, "review"),
            "verifications": _counts(snapshot.verifications, "verification"),
        },
        "issues": [issue.as_dict() for issue in snapshot.issues],
        "warnings": [truncate_utf8(str(warning), 300) for warning in snapshot.warnings],
        "read_diagnostics": dict(read_diagnostics or {}),
        "limits": {
            "max_nodes": MAX_NODES,
            "max_edges": MAX_EDGES,
            "truncated": truncated,
        },
    }
