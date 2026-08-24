"""Objective commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._validation import _nonempty_list


class ObjectiveCommands:
    """Commands for workspace objectives."""

    def create_objective(
        self,
        *,
        title: str,
        description: str,
        acceptance_criteria: Sequence[str],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.created", idempotency_key)
        objective_id = self._new_entity_id("objective", "objective.created", key)
        return self.record_event(
            "objective.created",
            {
                "objective_id": objective_id,
                "title": title,
                "description": description,
                "acceptance_criteria": _nonempty_list(acceptance_criteria, "acceptance_criteria"),
            },
            idempotency_key=key,
            tags=("objective",),
        )

    def list_objectives(self) -> list[dict[str, Any]]:
        return self._list("objective")

    def revise_objective(
        self,
        objective_id: str,
        expected_revision: str,
        *,
        changes: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.revised", idempotency_key)
        return self.record_event(
            "objective.revised",
            {
                "objective_id": objective_id,
                "expected_revision": expected_revision,
                "changes": dict(changes),
            },
            idempotency_key=key,
            tags=("objective",),
        )

    def close_objective(
        self,
        objective_id: str,
        expected_revision: str,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("objective.closed", idempotency_key)
        return self.record_event(
            "objective.closed",
            {
                "objective_id": objective_id,
                "expected_revision": expected_revision,
                "reason": reason,
            },
            idempotency_key=key,
            tags=("objective",),
        )
