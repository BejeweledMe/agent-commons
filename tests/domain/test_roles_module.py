"""A3 compatibility for the canonical standing-role domain module."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_commons.domain import agents, roles, states
from agent_commons.domain.roles import RoleTransitionContext
from agent_commons.domain.snapshot import ProjectSnapshot


def test_legacy_agents_module_reexports_canonical_role_rules() -> None:
    assert agents.__all__ == roles.__all__
    for name in roles.__all__:
        assert getattr(agents, name) is getattr(roles, name)


def test_legacy_agents_module_retains_the_prior_delegation_state_export() -> None:
    assert agents.NON_TERMINAL_DELEGATION_STATES is states.NON_TERMINAL_DELEGATION_STATES


def test_role_transition_context_is_frozen() -> None:
    context = RoleTransitionContext(
        snapshot=ProjectSnapshot(),
        actor_session_id="session.writer",
        acting_agent_id=None,
        require_entity=lambda _kind, _identifier: {},
    )

    with pytest.raises(FrozenInstanceError):
        context.actor_session_id = "session.other"  # type: ignore[misc]
