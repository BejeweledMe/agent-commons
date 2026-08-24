from __future__ import annotations

import inspect

from agent_commons.services.manager import CommonsManager
from agent_commons.services.objectives import ObjectiveCommands

_PUBLIC_OBJECTIVE_METHODS = {
    "close_objective",
    "create_objective",
    "list_objectives",
    "revise_objective",
}

_SIGNATURES = {
    "create_objective": (
        "(self, *, title: 'str', description: 'str', acceptance_criteria: 'Sequence[str]', "
        "idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "list_objectives": "(self) -> 'list[dict[str, Any]]'",
    "revise_objective": (
        "(self, objective_id: 'str', expected_revision: 'str', *, changes: 'Mapping[str, Any]', "
        "idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "close_objective": (
        "(self, objective_id: 'str', expected_revision: 'str', *, reason: 'str', "
        "idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
}


def test_objective_commands_are_the_exact_manager_surface_without_proxies() -> None:
    public = {
        name
        for name, value in ObjectiveCommands.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public == _PUBLIC_OBJECTIVE_METHODS

    for name, expected in _SIGNATURES.items():
        assert name not in CommonsManager.__dict__
        assert getattr(CommonsManager, name) is getattr(ObjectiveCommands, name)
        assert str(inspect.signature(getattr(CommonsManager, name))) == expected
