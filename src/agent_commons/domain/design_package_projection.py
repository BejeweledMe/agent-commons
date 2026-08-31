"""Deterministic current and historical Design Package projection."""

from __future__ import annotations

from collections.abc import Mapping

from agent_commons.errors import LifecycleConflictError, ValidationError

from .design_package_envelopes import DesignPackageEnvelope
from .design_packages import DesignPackageRecord


def apply_design_package_record(
    current: dict[str, DesignPackageRecord],
    revisions: dict[tuple[str, str], DesignPackageRecord],
    envelope: DesignPackageEnvelope,
    event: Mapping[str, object],
) -> None:
    """Apply one validated package event and retain each exact revision."""

    package_id = envelope.design_package_id
    previous = current.get(package_id)
    if envelope.event_type == "design_package.created" and previous is not None:
        raise LifecycleConflictError(f"design_package already exists: {package_id}")
    event_id = event.get("event_id")
    if not isinstance(event_id, str):
        raise ValidationError("design package event_id must be a string")
    effective_revision = str(event.get("_effective_correction_id") or event_id)
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise ValidationError("design package event actor must be an object")
    producer_session_id = actor.get("session_id")
    if not isinstance(producer_session_id, str) or not producer_session_id:
        raise ValidationError("design package event actor session must be a string")
    authors = set(previous.author_session_ids if previous is not None else ())
    authors.add(producer_session_id)
    record = DesignPackageRecord.create(
        design_package_id=package_id,
        revision=effective_revision,
        source_event_id=event_id,
        draft=envelope.draft,
        producer_session_id=producer_session_id,
        recorded_at=(str(event["recorded_at"]) if event.get("recorded_at") is not None else None),
        author_session_ids=tuple(authors),
    )
    current[package_id] = record
    revisions[(package_id, effective_revision)] = record
