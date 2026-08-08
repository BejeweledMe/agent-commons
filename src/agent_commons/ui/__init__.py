"""Local read-only view of an Agent Commons workspace.

This package deliberately does not import FastAPI at module level so that the
CLI can report a missing optional dependency instead of failing to start.
"""

from __future__ import annotations

from importlib import resources

GRAPH_SCHEMA = "agent_commons.ui.graph.v1"
META_SCHEMA = "agent_commons.ui.meta.v1"
ENTITY_SCHEMA = "agent_commons.ui.entity.v1"
STARTED_SCHEMA = "agent_commons.ui.started.v1"

TRUST_NOTE = "loopback reachability alone is not authentication"

#: The three layers of decreasing authority a reader must be able to tell apart.
TRUTH_LAYERS = ("CANONICAL", "COORDINATION", "OPERATIONAL")


def read_spa() -> str:
    """Read the packaged single-file frontend from the wheel, not from disk."""

    return resources.files("agent_commons.ui").joinpath("static/index.html").read_text("utf-8")


__all__ = [
    "ENTITY_SCHEMA",
    "GRAPH_SCHEMA",
    "META_SCHEMA",
    "STARTED_SCHEMA",
    "TRUST_NOTE",
    "TRUTH_LAYERS",
    "read_spa",
]
