"""Review commands mixed into the universal workspace manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.errors import LifecycleConflictError

from ._validation import _nonempty_list


class ReviewCommands:
    """Commands for independent reviews and their recorded verdicts."""

    def list_reviews(self, *, state: str | None = None) -> list[dict[str, Any]]:
        return self._list("review", state=state)

    def request_review(
        self,
        *,
        target_ref: Mapping[str, str],
        target_revision: str,
        criteria: Sequence[str],
        independent: bool = True,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("review.requested", idempotency_key)
        review_id = self._new_entity_id("review", "review.requested", key)
        snapshot = self.snapshot()
        target_binding = self._bind_evidence_refs((target_ref,), snapshot)[0]
        target = target_binding["ref"]
        if target_binding["revision"] != target_revision:
            raise LifecycleConflictError(
                "target_revision is not the current effective target revision"
            )
        subject = {"kind": "review", "id": review_id}
        return self.record_event(
            "review.requested",
            {
                "review_id": review_id,
                "target_ref": target,
                "target_revision": target_revision,
                "criteria": _nonempty_list(criteria, "criteria"),
                "independent": bool(independent),
            },
            idempotency_key=key,
            relations=(self._relation(subject, "reviews", target),),
            tags=("review",),
        )

    def complete_review(
        self,
        review_id: str,
        expected_revision: str,
        *,
        target_revision: str,
        verdict: str,
        summary: str,
        evidence_refs: Sequence[Mapping[str, str]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        refs = self._bind_evidence_refs(evidence_refs)
        key = self._idempotency_key("review.completed", idempotency_key)
        return self.record_event(
            "review.completed",
            {
                "review_id": review_id,
                "expected_revision": expected_revision,
                "target_revision": target_revision,
                "verdict": verdict,
                "summary": summary,
                "evidence_refs": refs,
            },
            idempotency_key=key,
            tags=("review",),
        )
