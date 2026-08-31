"""Typed immutable envelopes for canonical Context Pack events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from .context_pack import ContextPackDraft
from .envelopes import EventEnvelope


class ContextPackEnvelope(EventEnvelope):
    """Closed base for the canonical Context Pack event family."""

    context_pack_id: str


@dataclass(frozen=True)
class ContextPackCreatedEnvelope(ContextPackEnvelope):
    context_pack_id: str
    draft: ContextPackDraft
    event_type: Literal["context_pack.created"] = "context_pack.created"

    def to_payload(self) -> Mapping[str, object]:
        return {"context_pack_id": self.context_pack_id, **self.draft.to_payload()}


@dataclass(frozen=True)
class ContextPackRevisedEnvelope(ContextPackEnvelope):
    context_pack_id: str
    expected_revision: str
    draft: ContextPackDraft
    event_type: Literal["context_pack.revised"] = "context_pack.revised"

    def to_payload(self) -> Mapping[str, object]:
        return {
            "context_pack_id": self.context_pack_id,
            "expected_revision": self.expected_revision,
            **self.draft.to_payload(),
        }


def parse_context_pack_envelope(
    event_type: str, payload: Mapping[str, object]
) -> ContextPackEnvelope | None:
    if event_type not in {"context_pack.created", "context_pack.revised"}:
        return None
    draft = ContextPackDraft.from_payload(
        {
            "summary": payload["summary"],
            "facts": payload["facts"],
            "decision_refs": payload["decision_refs"],
            "open_questions": payload["open_questions"],
        }
    )
    if event_type == "context_pack.created":
        return ContextPackCreatedEnvelope(
            context_pack_id=cast(str, payload["context_pack_id"]),
            draft=draft,
        )
    return ContextPackRevisedEnvelope(
        context_pack_id=cast(str, payload["context_pack_id"]),
        expected_revision=cast(str, payload["expected_revision"]),
        draft=draft,
    )
