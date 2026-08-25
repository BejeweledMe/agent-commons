"""Stable public representations for local coordination records.

Session and claim storage owns their lifecycle.  This module owns only the
wire-shaped copies returned by the application service, so command modules do
not need to duplicate nonce redaction or collection conversion.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from agent_commons.coordination import Claim, Session, SourceProducer


class SourceProducerView(TypedDict):
    """The public, nested source-producer representation in a session view."""

    client: str
    software: str
    model_family: str | None
    model: str | None
    principal: str | None
    external_session_id: str | None


class SessionView(TypedDict):
    """The existing public session shape, with optional ownership nonce."""

    schema: str
    session_id: str
    stable_instance_id: str
    principal: str
    client: str
    software: str
    model_family: str | None
    model: str | None
    role: str
    capabilities: list[str]
    source_producer: SourceProducerView | None
    nonce: NotRequired[str]
    opened_at: str
    last_seen_at: str
    expires_at: str
    status: str
    closed_at: str | None
    effective_status: str


class ClaimView(TypedDict):
    """The existing public claim shape, with optional ownership nonce."""

    schema: str
    claim_id: str
    resources: list[str]
    owner_session_id: str
    mode: str
    nonce: NotRequired[str]
    acquired_at: str
    renewed_at: str
    expires_at: str
    description: str
    status: str
    ended_at: str | None
    ended_by_session_id: str | None
    end_reason: str | None


def _public_source_producer(value: SourceProducer | None) -> SourceProducerView | None:
    if value is None:
        return None
    return {
        "client": value.client,
        "software": value.software,
        "model_family": value.model_family,
        "model": value.model,
        "principal": value.principal,
        "external_session_id": value.external_session_id,
    }


def _public_session(
    session: Session,
    *,
    include_nonce: bool = False,
    effective_at: float | None = None,
) -> SessionView:
    """Copy a session into its established public wire shape."""

    result: SessionView = {
        "schema": session.schema,
        "session_id": session.session_id,
        "stable_instance_id": session.stable_instance_id,
        "principal": session.principal,
        "client": session.client,
        "software": session.software,
        "model_family": session.model_family,
        "model": session.model,
        "role": session.role,
        "capabilities": list(session.capabilities),
        "source_producer": _public_source_producer(session.source_producer),
        "nonce": session.nonce,
        "opened_at": session.opened_at,
        "last_seen_at": session.last_seen_at,
        "expires_at": session.expires_at,
        "status": session.status,
        "closed_at": session.closed_at,
        "effective_status": session.status,
    }
    effectively_active = (
        not session.expired if effective_at is None else session.active_at(effective_at)
    )
    result["effective_status"] = (
        "expired" if session.status == "active" and not effectively_active else session.status
    )
    if not include_nonce:
        result.pop("nonce", None)
    return result


def _public_claim(value: Claim, *, include_nonce: bool = False) -> ClaimView:
    """Copy a claim into its established public wire shape."""

    result: ClaimView = {
        "schema": value.schema,
        "claim_id": value.claim_id,
        "resources": list(value.resources),
        "owner_session_id": value.owner_session_id,
        "mode": value.mode,
        "nonce": value.nonce,
        "acquired_at": value.acquired_at,
        "renewed_at": value.renewed_at,
        "expires_at": value.expires_at,
        "description": value.description,
        "status": value.status,
        "ended_at": value.ended_at,
        "ended_by_session_id": value.ended_by_session_id,
        "end_reason": value.end_reason,
    }
    if not include_nonce:
        result.pop("nonce", None)
    return result
