"""Deterministic current and historical Context Pack projection."""

from __future__ import annotations

from collections.abc import Mapping

from agent_commons.errors import LifecycleConflictError, ValidationError

from .context_pack import ContextPackRecord
from .context_pack_envelopes import ContextPackEnvelope


def apply_context_pack_record(
    current: dict[str, ContextPackRecord],
    revisions: dict[tuple[str, str], ContextPackRecord],
    envelope: ContextPackEnvelope,
    event: Mapping[str, object],
) -> None:
    """Apply one validated pack event and retain every exact effective revision."""

    pack_id = envelope.context_pack_id
    previous = current.get(pack_id)
    if envelope.event_type == "context_pack.created" and previous is not None:
        raise LifecycleConflictError(f"context_pack already exists: {pack_id}")
    event_id = event.get("event_id")
    if not isinstance(event_id, str):
        raise ValidationError("context pack event_id must be a string")
    effective_revision = str(event.get("_effective_correction_id") or event_id)
    actor = event.get("actor")
    actor_session = str(actor.get("session_id", "")) if isinstance(actor, Mapping) else ""
    authors = set(previous.author_session_ids if previous is not None else ())
    if actor_session:
        authors.add(actor_session)
    record = ContextPackRecord.create(
        context_pack_id=pack_id,
        revision=effective_revision,
        source_event_id=event_id,
        draft=envelope.draft,
        recorded_at=(str(event["recorded_at"]) if event.get("recorded_at") is not None else None),
        author_session_ids=tuple(authors),
    )
    current[pack_id] = record
    revisions[(pack_id, effective_revision)] = record
