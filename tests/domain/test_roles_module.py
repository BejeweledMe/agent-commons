"""A3 compatibility for the canonical standing-role domain module."""

from __future__ import annotations

from agent_commons.domain import agents, roles


def test_legacy_agents_module_reexports_canonical_role_rules() -> None:
    assert agents.__all__ == roles.__all__
    for name in roles.__all__:
        assert getattr(agents, name) is getattr(roles, name)
