from __future__ import annotations

import inspect

from agent_commons.services.maintenance import MaintenanceCommands
from agent_commons.services.manager import CommonsManager

_PUBLIC_MAINTENANCE_METHODS = {
    "correct_event",
    "invalidate_event",
    "revoke_invalidation",
    "show_event",
}

_SIGNATURES = {
    "correct_event": (
        "(self, target_event_id: 'str', *, expected_target_sha256: 'str', "
        "replacement_payload: 'Mapping[str, Any]', superseded_correction_event_ids: "
        "'Sequence[str]' = (), idempotency_key: 'str | None' = None) -> 'dict[str, Any]'"
    ),
    "show_event": "(self, event_id: 'str') -> 'dict[str, Any]'",
    "invalidate_event": (
        "(self, target_event_id: 'str', *, reason: 'str', idempotency_key: 'str | None' = "
        "None) -> 'dict[str, Any]'"
    ),
    "revoke_invalidation": (
        "(self, invalidation_event_id: 'str', *, reason: 'str', idempotency_key: 'str | None' "
        "= None) -> 'dict[str, Any]'"
    ),
}


def test_maintenance_commands_are_the_exact_manager_surface_without_proxies() -> None:
    public = {
        name
        for name, value in MaintenanceCommands.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public == _PUBLIC_MAINTENANCE_METHODS

    for name, expected in _SIGNATURES.items():
        assert name not in CommonsManager.__dict__
        assert getattr(CommonsManager, name) is getattr(MaintenanceCommands, name)
        assert str(inspect.signature(getattr(CommonsManager, name))) == expected
