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

from agent_commons.domain.agents import effective_grants, session_agent_map
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
    "agent": 1,
    "session": 1,
    "task": 2,
    "artifact": 2,
    "delegation": 3,
    "review": 4,
    "verification": 4,
    "agent_link": 4,
}

#: A permanent edge is standing structure: who reports to whom, who owns what.
#: A temporary edge is one exchange bound to a single attempt.  The distinction
#: is real in the ledger, not a visual convention, so the projection carries it
#: rather than leaving the frontend to guess from edge kinds.
_PERMANENT_EDGES = frozenset(
    {"spawned", "requested_by", "runs_as", "owns", "depends_on", "reports_to"}
)

#: Nodes are dropped in this order when the graph exceeds its bounds; terminal
#: work is the least useful thing on screen when there is too much to draw.
#: Roles are last: they are the standing structure the rest of the graph hangs
#: off, so shedding one orphans everything below it.
_SHED_ORDER = (
    "delegation",
    "task",
    "session",
    "review",
    "verification",
    "artifact",
    "agent_link",
    "objective",
    "agent",
)

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
    # `name` first for a role: it is the one display field agent.created
    # guarantees, and leaving it out rendered every role as its ULID, twice per
    # card and in the inspector heading (M2, 2026-08-10 review).
    for key in ("name", "title", "subject", "summary", "proposal", "path", "role_id"):
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
    *,
    awaits_human: bool = False,
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
        "profile_id",
        "origin",
        "context_mode",
        "template",
        "agent_id",
        "allowed_action",
        "retired_by",
        # A review carries the producing role's context mode and how many times
        # it has judged this subject before, so an accumulated-context verdict
        # does not read as a clean-slate one.
        "producer_context_mode",
        "producer_prior_verdict_count",
    ):
        value = record.get(key)
        if value is not None and isinstance(value, (str, int, float, bool)):
            attrs[key] = _clean(value) if isinstance(value, str) else value
    if kind == "objective":
        # The ledger has no objective->task link; say so instead of drawing one.
        attrs["attached"] = False
    if kind == "agent":
        grants = record.get("effective_grants")
        if isinstance(grants, Mapping):
            attrs["effective_grants"] = {
                str(name): _clean(level) for name, level in sorted(grants.items())
            }
    return {
        "id": identifier,
        "kind": kind,
        "label": _label(kind, record),
        "state": _state(kind, record),
        "stale": bool(record.get("stale") or record.get("artifact_stale")),
        # A node the human has to answer before anything moves.  Carried on the
        # node so a blocker is visible on the graph itself rather than only to
        # someone who opened the right list.
        "awaits_human": awaits_human,
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


#: Thread kinds that are an agent asking a human to decide something, rather
#: than agents talking among themselves.
_HUMAN_DECISION_THREADS = frozenset({"decision_request", "question", "help_request", "proposal"})

#: A recipient that means the human is being asked, not a role.
_HUMAN_RECIPIENTS = frozenset({"operator", "*"})


def thread_awaits_human(record: Mapping[str, Any]) -> bool:
    """Whether an open thread is waiting on a person, not on a role.

    A human-decision thread counts only when it is addressed to the human: a
    role asking the operator to decide.  A directive the operator sends *to* a
    role is the same thread kind but waits on the role, and counting it made the
    operator's own messages inflate their own attention queue and ring their own
    session (round 2, design).  The ring and the list read this one predicate,
    so they cannot disagree about which threads are waiting on you.
    """

    if record.get("state") != "open":
        return False
    if str(record.get("thread_type", "")) not in _HUMAN_DECISION_THREADS:
        return False
    recipients = {str(item) for item in record.get("to") or ()}
    return bool(recipients & _HUMAN_RECIPIENTS)


def blocked_on_human(snapshot: ProjectSnapshot) -> set[str]:
    """Node identifiers waiting on a person, derived from real ledger state.

    Two producers exist today and both are already wired: a delegation enters
    `input_needed` when a worker asks for input, and an open decision-request
    thread is a role addressing a human directly.  Nothing here is speculative;
    a source with no producer would be a yellow ring that never lights.
    """

    blocked: set[str] = set()
    for identifier, record in snapshot.delegations.items():
        if record.get("state") != "input_needed":
            continue
        blocked.add(identifier)
        agent_id = record.get("agent_id")
        if agent_id:
            blocked.add(str(agent_id))
        session_id = record.get("child_session_id")
        if session_id:
            blocked.add(str(session_id))
    for identifier, record in snapshot.threads.items():
        if not thread_awaits_human(record):
            continue
        blocked.add(identifier)
        session_id = str((record.get("actor") or {}).get("session_id", ""))
        if session_id:
            blocked.add(session_id)
    bindings = session_agent_map(snapshot.delegations)
    for session_id in list(blocked):
        blocked.update(bindings.get(session_id, ()))
    return blocked


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

    # Standing roles are the chain of command itself: a role a human created
    # answers to the human, and a role an agent created sits under its creator.
    for _ in range(len(snapshot.agents) + 1):
        settled = False
        for identifier, record in snapshot.agents.items():
            if identifier in ranks:
                continue
            creator = record.get("created_by_agent_id")
            if not creator:
                ranks[identifier] = 0
                settled = True
            elif str(creator) in ranks:
                ranks[identifier] = ranks[str(creator)] + 1
                settled = True
        if not settled:
            break

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
            # A run performed for a role hangs under that role, not under the
            # session that happened to commission it.
            role = str(record.get("agent_id", ""))
            requester = str(record.get("parent_session_id", ""))
            anchor = role if role in ranks else requester
            if anchor in ranks:
                ranks[identifier] = ranks[anchor] + 1
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
    blocked = blocked_on_human(snapshot)

    def add(kind: str, identifier: str, record: Mapping[str, Any]) -> None:
        nodes.append(
            _node(
                kind,
                identifier,
                record,
                ranks.get(identifier),
                awaits_human=identifier in blocked,
            )
        )

    for identifier, record in sorted(snapshot.objectives.items()):
        add("objective", identifier, record)
    for identifier, record in sorted(snapshot.agents.items()):
        add(
            "agent",
            identifier,
            {**record, "effective_grants": effective_grants(snapshot.agents, identifier)},
        )
    for session in sessions:
        identifier = str(session.get("session_id", ""))
        if identifier:
            add("session", identifier, session)
    for identifier, record in sorted(snapshot.tasks.items()):
        add("task", identifier, record)
    for identifier, record in sorted(snapshot.artifacts.items()):
        add("artifact", identifier, record)
    for identifier, record in sorted(snapshot.delegations.items()):
        add("delegation", identifier, record)
    for identifier, record in sorted(snapshot.reviews.items()):
        add("review", identifier, record)
    for identifier, record in sorted(snapshot.verifications.items()):
        add("verification", identifier, record)
    for identifier, record in sorted(snapshot.agent_links.items()):
        add("agent_link", identifier, record)

    known = {node["id"] for node in nodes}

    for identifier, record in sorted(snapshot.agents.items()):
        creator = record.get("created_by_agent_id")
        if creator and str(creator) in known:
            edges.append(_edge("reports_to", identifier, str(creator)))
    for identifier, record in sorted(snapshot.agent_links.items()):
        for predicate, key in (("link_from", "from_agent_id"), ("link_to", "to_agent_id")):
            other = record.get(key)
            if other and str(other) in known:
                edges.append(
                    _edge(
                        predicate,
                        identifier,
                        str(other),
                        allowed_action=record.get("allowed_action"),
                    )
                )

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
        role = record.get("agent_id")
        if role and str(role) in known:
            # A situational run acting for a standing role.  Temporary by
            # construction: the run ends, the role does not.
            edges.append(_edge("acts_for", identifier, str(role)))

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

    # A template is shelf stock, not a live hire; the board's footer must not
    # count it as an agent (round 2 PM finding: creating a template grew the
    # agents counter).  Only the tally splits — nodes above stay the full
    # ledger projection, and hiding shelf stock is the renderer's decision.
    live_agents = {
        identifier: record
        for identifier, record in snapshot.agents.items()
        if not record.get("template")
    }
    template_agents = {
        identifier: record
        for identifier, record in snapshot.agents.items()
        if record.get("template")
    }

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
            "agents": _counts(live_agents, "agent"),
            "templates": _counts(template_agents, "agent"),
        },
        "awaiting_human": sorted(node["id"] for node in nodes if node["awaits_human"]),
        "issues": [issue.as_dict() for issue in snapshot.issues],
        "warnings": [truncate_utf8(str(warning), 300) for warning in snapshot.warnings],
        "read_diagnostics": dict(read_diagnostics or {}),
        "limits": {
            "max_nodes": MAX_NODES,
            "max_edges": MAX_EDGES,
            "truncated": truncated,
        },
    }
