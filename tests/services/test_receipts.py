from __future__ import annotations

import inspect

from agent_commons.services.manager import CommonsManager
from agent_commons.services.receipts import ReceiptCommands

_PUBLIC_RECEIPT_METHODS = {
    "abandon_idempotency_receipt",
    "receipt_status",
    "reconcile_idempotency_receipts",
}

_SIGNATURES = {
    "abandon_idempotency_receipt": (
        "(self, key_digest: 'str', *, reason: 'str') -> 'dict[str, Any]'"
    ),
    "receipt_status": "(self) -> 'dict[str, Any]'",
    "reconcile_idempotency_receipts": (
        "(self, *, adopt_legacy_orphans: 'Sequence[str]' = (), prepare_rollback: 'bool' = False) "
        "-> 'dict[str, Any]'"
    ),
}


def test_receipt_commands_are_the_exact_manager_surface_without_proxies() -> None:
    public = {
        name
        for name, value in ReceiptCommands.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public == _PUBLIC_RECEIPT_METHODS

    for name, expected in _SIGNATURES.items():
        assert name not in CommonsManager.__dict__
        assert getattr(CommonsManager, name) is getattr(ReceiptCommands, name)
        assert str(inspect.signature(getattr(CommonsManager, name))) == expected
