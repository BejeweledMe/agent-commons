"""Bounded, secret-free Work DTOs for canonical Context Packs."""

from __future__ import annotations

from typing import Any

from agent_commons.domain.context_pack import ContextPackRecord

MAX_CONTEXT_PACK_CATALOG = 256


def context_pack_summary_payload(record: ContextPackRecord) -> dict[str, Any]:
    """Return the minimum current-revision row needed by the Work catalogue."""

    return {
        "context_pack_id": record.context_pack_id,
        "revision": record.revision,
        "summary": record.draft.summary,
        "fact_count": len(record.draft.facts),
        "decision_count": len(record.draft.decision_refs),
        "open_question_count": len(record.draft.open_questions),
    }


def context_pack_catalog_payload(records: tuple[ContextPackRecord, ...]) -> dict[str, Any]:
    """Project current records only; historical revisions remain exact-address reads."""

    ordered = sorted(records, key=lambda item: item.context_pack_id)
    visible = ordered[:MAX_CONTEXT_PACK_CATALOG]
    return {
        "schema": "agent-commons.ui.context-packs.v1",
        "state": "empty" if not visible else "ready",
        "packs": [context_pack_summary_payload(item) for item in visible],
        "truncated": len(ordered) > len(visible),
    }


def context_pack_detail_payload(record: ContextPackRecord) -> dict[str, Any]:
    """Project one exact semantic revision without author/session or provider data."""

    return {
        "schema": "agent-commons.ui.context-pack.v1",
        "state": record.state,
        "context_pack_id": record.context_pack_id,
        "revision": record.revision,
        "recorded_at": record.recorded_at,
        **record.draft.to_payload(),
    }
