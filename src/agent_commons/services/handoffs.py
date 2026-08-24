"""Handoff commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._validation import _nonempty_list, _optional_list


class HandoffCommands:
    """Commands for durable handoffs between sessions."""

    def list_handoffs(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("handoff", state=state)

    def create_handoff(
        self,
        *,
        to: Sequence[str],
        completed: Sequence[str] = (),
        active: Sequence[str] = (),
        next_actions: Sequence[str],
        blockers: Sequence[str] = (),
        risks: Sequence[str] = (),
        open_questions: Sequence[str] = (),
        related_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("handoff.created", idempotency_key)
        handoff_id = self._new_entity_id("handoff", "handoff.created", key)
        refs = self._assert_refs_exist(related_refs)
        subject = {"kind": "handoff", "id": handoff_id}
        return self.record_event(
            "handoff.created",
            {
                "handoff_id": handoff_id,
                "to": sorted(set(_nonempty_list(to, "to"))),
                "completed": _optional_list(completed, "completed"),
                "active": _optional_list(active, "active"),
                "next_actions": _nonempty_list(next_actions, "next_actions"),
                "blockers": _optional_list(blockers, "blockers"),
                "risks": _optional_list(risks, "risks"),
                "open_questions": _optional_list(open_questions, "open_questions"),
                "related_refs": refs,
            },
            idempotency_key=key,
            relations=[self._relation(subject, "depends_on", value) for value in refs],
            tags=("handoff",),
        )

    def acknowledge_handoff(
        self,
        handoff_id: str,
        expected_revision: str,
        *,
        note: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("handoff.acknowledged", idempotency_key)
        return self.record_event(
            "handoff.acknowledged",
            {"handoff_id": handoff_id, "expected_revision": expected_revision, "note": note},
            idempotency_key=key,
            tags=("handoff",),
        )
