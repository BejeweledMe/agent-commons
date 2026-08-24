"""Typed, immutable lifecycle transition specifications.

These specifications describe only the allowed *source* states for events
whose lifecycle validation has an existing entity.  Entity-specific checks
remain in ``lifecycle.py``: authorization, reference binding, and payload
relationships are not state-table concerns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True)
class TransitionSpec:
    """The existing source-state rule for one lifecycle event type."""

    event_type: str
    allowed_from_states: frozenset[str]

    def allows(self, state: object) -> bool:
        """Return whether this event accepts the projected source state."""

        return state in self.allowed_from_states


def _transition(event_type: str, *allowed_from_states: str) -> TransitionSpec:
    return TransitionSpec(event_type, frozenset(allowed_from_states))


# This is deliberately one immutable mapping rather than task and non-task
# tables.  The event registry still supplies entity kind and identifier; this
# table owns only the stable existing rule of which current states can advance.
TRANSITION_SPECS: Final[Mapping[str, TransitionSpec]] = MappingProxyType(
    {
        "objective.revised": _transition("objective.revised", "active"),
        "objective.closed": _transition("objective.closed", "active"),
        "task.revised": _transition(
            "task.revised", "ready", "assigned", "active", "blocked", "completed", "review"
        ),
        "task.taken": _transition("task.taken", "ready"),
        "task.started": _transition("task.started", "ready", "assigned"),
        "task.blocked": _transition("task.blocked", "assigned", "active"),
        "task.unblocked": _transition("task.unblocked", "blocked"),
        "task.completed": _transition("task.completed", "active"),
        "task.submitted": _transition("task.submitted", "completed"),
        "task.accepted": _transition("task.accepted", "review"),
        "task.cancelled": _transition("task.cancelled", "ready", "assigned", "active", "blocked"),
        "task.reopened": _transition(
            "task.reopened", "completed", "review", "accepted", "cancelled"
        ),
        "thread.replied": _transition("thread.replied", "open"),
        "thread.resolved": _transition("thread.resolved", "open"),
        "review.completed": _transition("review.completed", "requested"),
        "finding.promoted": _transition("finding.promoted", "reported", "contested"),
        "finding.contested": _transition("finding.contested", "reported", "verified"),
        "finding.resolved": _transition("finding.resolved", "reported", "verified", "contested"),
        "decision.accepted": _transition("decision.accepted", "proposed", "deferred"),
        "decision.rejected": _transition("decision.rejected", "proposed", "deferred"),
        "decision.deferred": _transition("decision.deferred", "proposed"),
        "decision.superseded": _transition("decision.superseded", "accepted"),
        "handoff.acknowledged": _transition("handoff.acknowledged", "open"),
        "artifact.revised": _transition("artifact.revised", "registered"),
        "delegation.started": _transition("delegation.started", "requested"),
        "delegation.input_needed": _transition("delegation.input_needed", "active"),
        "delegation.resumed": _transition("delegation.resumed", "input_needed"),
        "delegation.succeeded": _transition("delegation.succeeded", "active"),
        "delegation.failed": _transition(
            "delegation.failed", "requested", "active", "input_needed"
        ),
        # The current runtime has no authenticated stop/kill acknowledgement in
        # canonical history.  Cancellation therefore remains safe only before
        # launch; live work is classified through reconciliation instead.
        "delegation.cancelled": _transition("delegation.cancelled", "requested"),
        # Recovery is distinct from requester-owned cancellation and can only
        # terminalize work that never reached the provider-start boundary.
        "delegation.recovered": _transition("delegation.recovered", "requested"),
        "delegation.timed_out": _transition(
            "delegation.timed_out", "requested", "active", "input_needed"
        ),
        "delegation.needs_operator": _transition(
            "delegation.needs_operator", "requested", "active", "input_needed"
        ),
        # A role has no transition out of retired: the ledger keeps its work.
        "agent.reconfigured": _transition("agent.reconfigured", "active"),
        "agent.retired": _transition("agent.retired", "active"),
        "agent.link_closed": _transition("agent.link_closed", "open"),
    }
)


def transition_spec(event_type: str) -> TransitionSpec | None:
    """Return the source-state specification for a lifecycle event, if any."""

    return TRANSITION_SPECS.get(event_type)
