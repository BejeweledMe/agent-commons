"""Decision commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.domain.lifecycle import entity
from agent_commons.errors import ValidationError

from ._validation import _optional_list


class DecisionCommands:
    """Commands for decisions and their truth transitions."""

    def list_decisions(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("decision", state=state)

    def propose_decision(
        self,
        *,
        scope: str,
        proposal: str,
        alternatives: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.proposed", idempotency_key)
        decision_id = self._new_entity_id("decision", "decision.proposed", key)
        return self.record_event(
            "decision.proposed",
            {
                "decision_id": decision_id,
                "scope": scope,
                "proposal": proposal,
                "alternatives": _optional_list(alternatives, "alternatives"),
            },
            idempotency_key=key,
            tags=("decision",),
        )

    def accept_decision(
        self,
        decision_id: str,
        expected_revision: str,
        *,
        rationale: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        dissent: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._bind_evidence_refs(evidence_refs)
        key = self._idempotency_key("decision.accepted", idempotency_key)
        return self.record_event(
            "decision.accepted",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "rationale": rationale,
                "evidence_refs": evidence,
                "dissent": _optional_list(dissent, "dissent"),
            },
            idempotency_key=key,
            tags=("decision", "truth"),
        )

    def reject_decision(
        self, decision_id: str, expected_revision: str, *, rationale: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.rejected", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "decision.rejected",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "rationale": rationale,
            },
            idempotency_key=key,
            tags=("decision", "truth"),
        )

    def defer_decision(
        self, decision_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("decision.deferred", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "decision.deferred",
            {"decision_id": decision_id, "expected_revision": expected_revision, "reason": reason},
            idempotency_key=key,
            tags=("decision",),
        )

    def supersede_decision(
        self,
        decision_id: str,
        expected_revision: str,
        *,
        replacement_decision_id: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot()
        if entity(snapshot, "decision", replacement_decision_id) is None:
            raise ValidationError("replacement decision does not exist")
        key = self._idempotency_key("decision.superseded", idempotency_key)
        replacement = {"kind": "decision", "id": replacement_decision_id}
        return self.record_event(
            "decision.superseded",
            {
                "decision_id": decision_id,
                "expected_revision": expected_revision,
                "replacement_decision_id": replacement_decision_id,
                "reason": reason,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    replacement,
                    "supersedes",
                    {"kind": "decision", "id": decision_id},
                ),
            ),
            tags=("decision", "truth"),
        )
