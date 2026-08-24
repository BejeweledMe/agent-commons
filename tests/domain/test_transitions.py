from __future__ import annotations

import pytest

from agent_commons.domain.transitions import TRANSITION_SPECS, TransitionSpec, transition_spec

_PREEXISTING_ALLOWED_FROM_STATES = {
    "objective.revised": frozenset({"active"}),
    "objective.closed": frozenset({"active"}),
    "task.revised": frozenset({"ready", "assigned", "active", "blocked", "completed", "review"}),
    "task.taken": frozenset({"ready"}),
    "task.started": frozenset({"ready", "assigned"}),
    "task.blocked": frozenset({"assigned", "active"}),
    "task.unblocked": frozenset({"blocked"}),
    "task.completed": frozenset({"active"}),
    "task.submitted": frozenset({"completed"}),
    "task.accepted": frozenset({"review"}),
    "task.cancelled": frozenset({"ready", "assigned", "active", "blocked"}),
    "task.reopened": frozenset({"completed", "review", "accepted", "cancelled"}),
    "thread.replied": frozenset({"open"}),
    "thread.resolved": frozenset({"open"}),
    "review.completed": frozenset({"requested"}),
    "finding.promoted": frozenset({"reported", "contested"}),
    "finding.contested": frozenset({"reported", "verified"}),
    "finding.resolved": frozenset({"reported", "verified", "contested"}),
    "decision.accepted": frozenset({"proposed", "deferred"}),
    "decision.rejected": frozenset({"proposed", "deferred"}),
    "decision.deferred": frozenset({"proposed"}),
    "decision.superseded": frozenset({"accepted"}),
    "handoff.acknowledged": frozenset({"open"}),
    "artifact.revised": frozenset({"registered"}),
    "delegation.started": frozenset({"requested"}),
    "delegation.input_needed": frozenset({"active"}),
    "delegation.resumed": frozenset({"input_needed"}),
    "delegation.succeeded": frozenset({"active"}),
    "delegation.failed": frozenset({"requested", "active", "input_needed"}),
    "delegation.cancelled": frozenset({"requested"}),
    "delegation.recovered": frozenset({"requested"}),
    "delegation.timed_out": frozenset({"requested", "active", "input_needed"}),
    "delegation.needs_operator": frozenset({"requested", "active", "input_needed"}),
    "agent.reconfigured": frozenset({"active"}),
    "agent.retired": frozenset({"active"}),
    "agent.link_closed": frozenset({"open"}),
}


def test_transition_specs_match_the_preexisting_source_state_matrix() -> None:
    assert {
        event_type: spec.allowed_from_states for event_type, spec in TRANSITION_SPECS.items()
    } == _PREEXISTING_ALLOWED_FROM_STATES
    assert {spec.event_type for spec in TRANSITION_SPECS.values()} == set(
        _PREEXISTING_ALLOWED_FROM_STATES
    )


def test_transition_specs_are_frozen_and_expose_their_existing_event_keys() -> None:
    spec = transition_spec("task.taken")

    assert spec == TransitionSpec("task.taken", frozenset({"ready"}))
    assert spec.allows("ready")
    assert not spec.allows("active")
    assert transition_spec("task.created") is None
    with pytest.raises(TypeError):
        TRANSITION_SPECS["task.taken"] = spec  # type: ignore[index]
