"""Typed thread and handoff payloads after schema and domain validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from .envelopes import EventEnvelope, FrozenJsonObject, JsonValue, TypedRef, TypedRefPayload


class ThreadPayload(TypedDict):
    thread_id: str
    thread_type: NotRequired[str]
    subject: NotRequired[str]
    desired_outcome: NotRequired[str]
    to: NotRequired[list[str]]
    related_refs: NotRequired[list[TypedRefPayload]]
    message_id: NotRequired[str]
    body: NotRequired[str]
    expected_revision: NotRequired[str]
    resolution: NotRequired[str]
    summary: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


class HandoffPayload(TypedDict):
    handoff_id: str
    to: NotRequired[list[str]]
    completed: NotRequired[list[str]]
    active: NotRequired[list[str]]
    next_actions: NotRequired[list[str]]
    blockers: NotRequired[list[str]]
    risks: NotRequired[list[str]]
    open_questions: NotRequired[list[str]]
    related_refs: NotRequired[list[TypedRefPayload]]
    expected_revision: NotRequired[str]
    note: NotRequired[str]
    extensions: NotRequired[dict[str, JsonValue]]


ThreadEventType: TypeAlias = Literal["thread.opened", "thread.replied", "thread.resolved"]
HandoffEventType: TypeAlias = Literal["handoff.created", "handoff.acknowledged"]

_THREAD_EVENT_TYPES = frozenset({"thread.opened", "thread.replied", "thread.resolved"})
_HANDOFF_EVENT_TYPES = frozenset({"handoff.created", "handoff.acknowledged"})


@dataclass(frozen=True)
class ThreadEnvelope(EventEnvelope):
    event_type: ThreadEventType
    thread_id: str
    thread_type: str | None
    subject: str | None
    desired_outcome: str | None
    recipients: tuple[str, ...] | None
    related_refs: tuple[TypedRef, ...] | None
    message_id: str | None
    body: str | None
    expected_revision: str | None
    resolution: str | None
    summary: str | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> ThreadPayload:
        from .envelopes import thaw_json_object

        payload: ThreadPayload = {"thread_id": self.thread_id}
        if self.thread_type is not None:
            payload["thread_type"] = self.thread_type
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.desired_outcome is not None:
            payload["desired_outcome"] = self.desired_outcome
        if self.recipients is not None:
            payload["to"] = list(self.recipients)
        if self.related_refs is not None:
            payload["related_refs"] = [item.to_payload() for item in self.related_refs]
        if self.message_id is not None:
            payload["message_id"] = self.message_id
        if self.body is not None:
            payload["body"] = self.body
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.resolution is not None:
            payload["resolution"] = self.resolution
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class HandoffEnvelope(EventEnvelope):
    event_type: HandoffEventType
    handoff_id: str
    recipients: tuple[str, ...] | None
    completed: tuple[str, ...] | None
    active: tuple[str, ...] | None
    next_actions: tuple[str, ...] | None
    blockers: tuple[str, ...] | None
    risks: tuple[str, ...] | None
    open_questions: tuple[str, ...] | None
    related_refs: tuple[TypedRef, ...] | None
    expected_revision: str | None
    note: str | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> HandoffPayload:
        from .envelopes import thaw_json_object

        payload: HandoffPayload = {"handoff_id": self.handoff_id}
        if self.recipients is not None:
            payload["to"] = list(self.recipients)
        if self.completed is not None:
            payload["completed"] = list(self.completed)
        if self.active is not None:
            payload["active"] = list(self.active)
        if self.next_actions is not None:
            payload["next_actions"] = list(self.next_actions)
        if self.blockers is not None:
            payload["blockers"] = list(self.blockers)
        if self.risks is not None:
            payload["risks"] = list(self.risks)
        if self.open_questions is not None:
            payload["open_questions"] = list(self.open_questions)
        if self.related_refs is not None:
            payload["related_refs"] = [item.to_payload() for item in self.related_refs]
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.note is not None:
            payload["note"] = self.note
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


ThreadHandoffEnvelope: TypeAlias = ThreadEnvelope | HandoffEnvelope


def parse_thread_handoff_envelope(
    event_type: str, payload: Mapping[str, object]
) -> ThreadHandoffEnvelope | None:
    """Parse one thread or handoff payload that has already passed validation."""

    if event_type in _THREAD_EVENT_TYPES:
        return ThreadEnvelope(
            event_type=cast(ThreadEventType, event_type),
            thread_id=_required_string(payload, "thread_id"),
            thread_type=_optional_string(payload, "thread_type"),
            subject=_optional_string(payload, "subject"),
            desired_outcome=_optional_string(payload, "desired_outcome"),
            recipients=_optional_string_tuple(payload, "to"),
            related_refs=_optional_ref_tuple(payload, "related_refs"),
            message_id=_optional_string(payload, "message_id"),
            body=_optional_string(payload, "body"),
            expected_revision=_optional_string(payload, "expected_revision"),
            resolution=_optional_string(payload, "resolution"),
            summary=_optional_string(payload, "summary"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type in _HANDOFF_EVENT_TYPES:
        return HandoffEnvelope(
            event_type=cast(HandoffEventType, event_type),
            handoff_id=_required_string(payload, "handoff_id"),
            recipients=_optional_string_tuple(payload, "to"),
            completed=_optional_string_tuple(payload, "completed"),
            active=_optional_string_tuple(payload, "active"),
            next_actions=_optional_string_tuple(payload, "next_actions"),
            blockers=_optional_string_tuple(payload, "blockers"),
            risks=_optional_string_tuple(payload, "risks"),
            open_questions=_optional_string_tuple(payload, "open_questions"),
            related_refs=_optional_ref_tuple(payload, "related_refs"),
            expected_revision=_optional_string(payload, "expected_revision"),
            note=_optional_string(payload, "note"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    return None


def _required_string(payload: Mapping[str, object], field: str) -> str:
    return cast(str, payload[field])


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    return cast(str | None, payload.get(field))


def _optional_string_tuple(payload: Mapping[str, object], field: str) -> tuple[str, ...] | None:
    if field not in payload:
        return None
    return tuple(cast(list[str], payload[field]))


def _optional_ref_tuple(payload: Mapping[str, object], field: str) -> tuple[TypedRef, ...] | None:
    if field not in payload:
        return None
    return tuple(TypedRef.from_payload(item) for item in _mapping_list(payload, field))


def _optional_frozen_object(payload: Mapping[str, object], field: str) -> FrozenJsonObject | None:
    if field not in payload:
        return None
    from .envelopes import freeze_json_object

    return freeze_json_object(_required_mapping(payload, field))


def _required_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload[field])


def _mapping_list(payload: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], payload[field])
