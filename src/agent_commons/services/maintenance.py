"""History-maintenance commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._validation import _optional_list


class MaintenanceCommands:
    """Commands for corrections and invalidation of canonical history."""

    def correct_event(
        self,
        target_event_id: str,
        *,
        expected_target_sha256: str,
        replacement_payload: Mapping[str, Any],
        superseded_correction_event_ids: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.corrected", idempotency_key)
        payload: dict[str, Any] = {
            "target_event_id": target_event_id,
            "expected_target_sha256": expected_target_sha256,
            "replacement_payload": dict(replacement_payload),
        }
        superseded = _optional_list(
            superseded_correction_event_ids,
            "superseded_correction_event_ids",
        )
        if superseded:
            payload["superseded_correction_event_ids"] = superseded
        return self.record_event(
            "event.corrected",
            payload,
            idempotency_key=key,
            tags=("maintenance", "correction"),
        )

    def show_event(self, event_id: str) -> dict[str, Any]:
        record = self.events.get(event_id)
        return {
            "event_id": record.event_id,
            "canonical_sha256": record.sha256,
            "event": dict(record.event),
        }

    def invalidate_event(
        self,
        target_event_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.invalidated", idempotency_key)
        target = {"kind": "event", "id": target_event_id}
        return self.record_event(
            "event.invalidated",
            {"target_ref": target, "reason": reason},
            idempotency_key=key,
            tags=("maintenance", "invalidation"),
        )

    def revoke_invalidation(
        self,
        invalidation_event_id: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("event.invalidation_revoked", idempotency_key)
        return self.record_event(
            "event.invalidation_revoked",
            {
                "invalidation_event_id": invalidation_event_id,
                "reason": reason,
            },
            idempotency_key=key,
            tags=("maintenance", "invalidation-revocation"),
        )
