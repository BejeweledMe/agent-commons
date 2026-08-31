"""Canonical mapping from entity kinds to projected snapshot collections."""

from __future__ import annotations

COLLECTIONS: dict[str, str] = {
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
    "agent": "agents",
    "agent_link": "agent_links",
    "context_pack": "context_packs",
    "design_package": "design_packages",
}


def collection_for(kind: str) -> str | None:
    """Return the ProjectSnapshot attribute for one canonical entity kind."""

    return COLLECTIONS.get(kind)
