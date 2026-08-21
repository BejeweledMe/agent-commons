from __future__ import annotations

import inspect

from agent_commons.services.manager import CommonsManager
from agent_commons.services.roles import RoleCommands

_PUBLIC_ROLE_METHODS = {
    "approve_agent_proposal",
    "close_agent_link",
    "create_agent",
    "decline_agent_proposal",
    "get_agent",
    "list_agent_proposals",
    "list_agents",
    "open_agent_link",
    "propose_agent",
    "reconfigure_agent",
    "retire_agent",
}

_SIGNATURES = {
    "list_agents": "(self, *, include_retired: 'bool' = False) -> 'list[dict[str, Any]]'",
    "get_agent": "(self, agent_id: 'str') -> 'dict[str, Any]'",
    "create_agent": (
        "(self, *, name: 'str', profile_id: 'str', grants: 'Mapping[str, str] | None' = "
        "None, context_mode: 'str' = 'fresh', rationale: 'str', lifetime: 'Mapping[str, Any] "
        "| None' = None, skills: 'Sequence[str]' = (), tool_allowlist: 'Sequence[str]' = (), "
        "turnover_budget: 'int | None' = None, template: 'bool' = False, model: 'str | None' = "
        "None, created_by_agent_id: 'str | None' = None, approval: 'str | None' = None, "
        "proposal_ref: 'Mapping[str, str] | None' = None, idempotency_key: 'str | None' = None) "
        "-> 'dict[str, Any]'"
    ),
    "propose_agent": (
        "(self, *, name: 'str', profile_id: 'str', rationale: 'str', grants: 'Mapping[str, str] "
        "| None' = None, context_mode: 'str' = 'fresh', turnover_budget: 'int | None' = None, "
        "lifetime: 'Mapping[str, Any] | None' = None, idempotency_key: 'str | None' = None) -> "
        "'dict[str, Any]'"
    ),
    "list_agent_proposals": "(self) -> 'list[dict[str, Any]]'",
    "approve_agent_proposal": (
        "(self, thread_id: 'str', *, idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "decline_agent_proposal": (
        "(self, thread_id: 'str', *, reason: 'str', idempotency_key: 'str | None' = None) -> "
        "'dict[str, Any]'"
    ),
    "reconfigure_agent": (
        "(self, agent_id: 'str', expected_revision: 'str', *, changes: 'Mapping[str, Any]', "
        "reason: 'str', isolation_downgrade_reason: 'str | None' = None, idempotency_key: 'str | "
        "None' = None) -> 'dict[str, Any]'"
    ),
    "retire_agent": (
        "(self, agent_id: 'str', expected_revision: 'str | None' = None, *, reason: 'str', "
        "cascade: 'bool' = False, idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "open_agent_link": (
        "(self, *, from_agent_id: 'str', to_agent_id: 'str', allowed_action: 'str' = 'ask', "
        "deadline_seconds: 'int | None' = None, reason: 'str', idempotency_key: 'str | None' = "
        "None) -> 'dict[str, Any]'"
    ),
    "close_agent_link": (
        "(self, link_id: 'str', expected_revision: 'str', *, reason: 'str', idempotency_key: 'str "
        "| None' = None) -> 'dict[str, Any]'"
    ),
    "_agent_view": (
        "(snapshot: 'ProjectSnapshot', record: 'Mapping[str, Any]') -> 'dict[str, Any]'"
    ),
}


def test_role_commands_are_the_exact_manager_surface_without_proxies() -> None:
    public = {
        name
        for name, value in RoleCommands.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public == _PUBLIC_ROLE_METHODS
    assert "_agent_view" in RoleCommands.__dict__

    for name, expected in _SIGNATURES.items():
        assert name not in CommonsManager.__dict__
        assert getattr(CommonsManager, name) is getattr(RoleCommands, name)
        assert str(inspect.signature(getattr(CommonsManager, name))) == expected
