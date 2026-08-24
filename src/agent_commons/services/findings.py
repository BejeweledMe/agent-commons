"""Finding commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.errors import ValidationError


class FindingCommands:
    """Commands for provisional findings and their truth transitions."""

    def list_findings(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("finding", state=state)

    def report_finding(
        self,
        *,
        summary: str,
        severity: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.reported", idempotency_key)
        finding_id = self._new_entity_id("finding", "finding.reported", key)
        evidence = self._bind_evidence_refs(evidence_refs)
        subject = {"kind": "finding", "id": finding_id}
        relations = [self._relation(subject, "derived_from", value["ref"]) for value in evidence]
        return self.record_event(
            "finding.reported",
            {
                "finding_id": finding_id,
                "summary": summary,
                "severity": severity,
                "evidence_refs": evidence,
            },
            idempotency_key=key,
            relations=relations,
            tags=("finding", severity),
        )

    def promote_finding(
        self,
        finding_id: str,
        expected_revision: str,
        *,
        summary: str,
        evidence_refs: Sequence[Mapping[str, str]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        evidence = self._bind_evidence_refs(evidence_refs)
        if not evidence:
            raise ValidationError("promoting a finding requires evidence")
        key = self._idempotency_key("finding.promoted", idempotency_key)
        return self.record_event(
            "finding.promoted",
            {
                "finding_id": finding_id,
                "expected_revision": expected_revision,
                "evidence_refs": evidence,
                "summary": summary,
            },
            idempotency_key=key,
            tags=("finding", "truth"),
        )

    def contest_finding(
        self, finding_id: str, expected_revision: str, *, reason: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.contested", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "finding.contested",
            {"finding_id": finding_id, "expected_revision": expected_revision, "reason": reason},
            idempotency_key=key,
            tags=("finding",),
        )

    def resolve_finding(
        self, finding_id: str, expected_revision: str, *, resolution: str, **kwargs: Any
    ) -> dict[str, Any]:
        key = self._idempotency_key("finding.resolved", kwargs.pop("idempotency_key", None))
        return self.record_event(
            "finding.resolved",
            {
                "finding_id": finding_id,
                "expected_revision": expected_revision,
                "resolution": resolution,
            },
            idempotency_key=key,
            tags=("finding", "truth"),
        )
