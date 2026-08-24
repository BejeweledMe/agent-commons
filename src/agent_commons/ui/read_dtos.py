"""Typed, wire-compatible DTOs for local UI read models.

The local panel still receives the established JSON objects.  These immutable
records make the in-memory UI boundary explicit without changing HTTP routes,
persisted events, or their serialized shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict, cast

from agent_commons.domain.envelopes import JsonValue
from agent_commons.views import bounded_copy


class RunBlockedAttentionPayload(TypedDict):
    kind: Literal["run_blocked"]
    id: str
    agent_id: JsonValue
    target_ref: JsonValue
    run_state: JsonValue
    reason_code: JsonValue
    summary: JsonValue
    operation_id: JsonValue
    metadata: JsonValue
    answerable_here: bool
    answer_from_session: list[str]
    deadline: JsonValue


class WorkReturnedAttentionPayload(TypedDict):
    kind: Literal["work_returned"]
    id: str
    task_id: str
    title: JsonValue
    task_state: JsonValue
    task_revision: str
    delegation_id: str
    agent_id: JsonValue
    agent_name: JsonValue


class ThreadAttentionPayload(TypedDict):
    kind: Literal["thread"]
    id: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue
    proposal: None


class ProposalAttentionPayload(TypedDict):
    kind: Literal["proposal"]
    id: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue
    proposal: dict[str, JsonValue]


class ConfigBrokenAttentionPayload(TypedDict):
    kind: Literal["config_broken"]
    id: str
    agent_id: str
    name: JsonValue
    missing_skills: list[str]


AttentionItemPayload: TypeAlias = (
    RunBlockedAttentionPayload
    | WorkReturnedAttentionPayload
    | ThreadAttentionPayload
    | ProposalAttentionPayload
    | ConfigBrokenAttentionPayload
)


class AttentionResponsePayload(TypedDict):
    items: list[AttentionItemPayload]
    count: int
    writes_enabled: bool


@dataclass(frozen=True, slots=True)
class RunBlockedAttention:
    identifier: str
    agent_id: JsonValue
    target_ref: JsonValue
    run_state: JsonValue
    reason_code: JsonValue
    summary: JsonValue
    operation_id: JsonValue
    metadata: JsonValue
    answerable_here: bool
    answer_from_session: tuple[str, ...]
    deadline: JsonValue

    def to_wire(self) -> RunBlockedAttentionPayload:
        return {
            "kind": "run_blocked",
            "id": self.identifier,
            "agent_id": self.agent_id,
            "target_ref": self.target_ref,
            "run_state": self.run_state,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "operation_id": self.operation_id,
            "metadata": self.metadata,
            "answerable_here": self.answerable_here,
            "answer_from_session": list(self.answer_from_session),
            "deadline": self.deadline,
        }


@dataclass(frozen=True, slots=True)
class WorkReturnedAttention:
    task_id: str
    title: JsonValue
    task_state: JsonValue
    task_revision: str
    delegation_id: str
    agent_id: JsonValue
    agent_name: JsonValue

    def to_wire(self) -> WorkReturnedAttentionPayload:
        return {
            "kind": "work_returned",
            "id": self.task_id,
            "task_id": self.task_id,
            "title": self.title,
            "task_state": self.task_state,
            "task_revision": self.task_revision,
            "delegation_id": self.delegation_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
        }


@dataclass(frozen=True, slots=True)
class ThreadAttention:
    identifier: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue

    def to_wire(self) -> ThreadAttentionPayload:
        return {
            "kind": "thread",
            "id": self.identifier,
            "thread_type": self.thread_type,
            "subject": self.subject,
            "revision": self.revision,
            "proposal": None,
        }


@dataclass(frozen=True, slots=True)
class ProposalAttention:
    identifier: str
    thread_type: str
    subject: JsonValue
    revision: JsonValue
    proposal: dict[str, JsonValue]

    def to_wire(self) -> ProposalAttentionPayload:
        return {
            "kind": "proposal",
            "id": self.identifier,
            "thread_type": self.thread_type,
            "subject": self.subject,
            "revision": self.revision,
            "proposal": self.proposal,
        }


@dataclass(frozen=True, slots=True)
class ConfigBrokenAttention:
    agent_id: str
    name: JsonValue
    missing_skills: tuple[str, ...]

    def to_wire(self) -> ConfigBrokenAttentionPayload:
        return {
            "kind": "config_broken",
            "id": self.agent_id,
            "agent_id": self.agent_id,
            "name": self.name,
            "missing_skills": list(self.missing_skills),
        }


AttentionItem: TypeAlias = (
    RunBlockedAttention
    | WorkReturnedAttention
    | ThreadAttention
    | ProposalAttention
    | ConfigBrokenAttention
)


@dataclass(frozen=True, slots=True)
class AttentionResponse:
    """The immutable panel response before its established display bounding."""

    items: tuple[AttentionItem, ...]
    writes_enabled: bool

    def to_wire(self) -> AttentionResponsePayload:
        """Serialize the pre-existing attention JSON shape, including item bounds."""

        serialized = [item.to_wire() for item in self.items]
        return {
            "items": [cast(AttentionItemPayload, bounded_copy(item)) for item in serialized],
            "count": len(serialized),
            "writes_enabled": self.writes_enabled,
        }
