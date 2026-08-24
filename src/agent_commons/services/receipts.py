"""Receipt recovery commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_commons.errors import LifecycleConflictError, ValidationError


class ReceiptCommands:
    """Commands for idempotency receipt recovery and abandonment."""

    def abandon_idempotency_receipt(
        self,
        key_digest: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Audit and tombstone an orphan receipt that cannot be retried."""

        normalized_reason = reason.strip()
        self.events.idempotency.refresh_scope()
        if (
            not normalized_reason
            or len(normalized_reason) > 2048
            or any(ord(character) < 32 for character in normalized_reason)
        ):
            raise ValidationError("receipt abandonment reason is invalid")
        with self._canonical_write_lock():
            try:
                self.sessions.require_active(
                    self.session_id,
                    capability="receipt:abandon",
                )
            except LifecycleConflictError as exc:
                raise LifecycleConflictError(
                    "receipt abandonment requires an active receipt:abandon capability"
                ) from exc
            actor = self._actor()
            self.policy.assert_safe(
                {
                    "key_digest": key_digest,
                    "reason": normalized_reason,
                    "actor": actor,
                },
                context="idempotency receipt abandonment",
            )
            reservation = self.events.idempotency.get_by_digest(key_digest)
            if reservation is None and self.events.idempotency.get_migration() is None:
                reservation = self.events.idempotency.get_legacy_by_digest(key_digest)
            existing = self.events.idempotency.get_abandonment(key_digest)
            if existing is None and self.events.idempotency.get_migration() is None:
                existing = self.events.idempotency.get_legacy_abandonment(key_digest)
            if reservation is None:
                if existing is not None:
                    return dict(existing)
                raise ValidationError("idempotency receipt does not exist")

            for record in self.events.iter_events():
                event = record.event
                event_digest = self.events.idempotency.key_digest(
                    str(event["idempotency_namespace"]),
                    str(event["idempotency_key"]),
                )
                if record.event_id == reservation.event_id or event_digest == key_digest:
                    raise LifecycleConflictError(
                        "a receipt with a canonical event cannot be abandoned"
                    )
            abandonment = self.events.idempotency.abandon(
                reservation,
                reason=normalized_reason,
                actor_session_id=str(actor["session_id"]),
                actor_principal_id=str(actor["principal_id"]),
            )
        return dict(abandonment)

    def receipt_status(self) -> dict[str, Any]:
        self.events.idempotency.refresh_scope()
        records, _ = self._records_and_snapshot()
        return self.receipt_recovery.status(records)

    def reconcile_idempotency_receipts(
        self,
        *,
        adopt_legacy_orphans: Sequence[str] = (),
        prepare_rollback: bool = False,
    ) -> dict[str, Any]:
        self.events.idempotency.refresh_scope()
        with self._canonical_write_lock():
            actor = self._actor()
            records, _ = self._records_and_snapshot()
            if prepare_rollback:
                return self.receipt_recovery.prepare_rollback(records, actor=actor)
            return self.receipt_recovery.reconcile(
                records,
                actor=actor,
                adopt_legacy_orphans=adopt_legacy_orphans,
            )
