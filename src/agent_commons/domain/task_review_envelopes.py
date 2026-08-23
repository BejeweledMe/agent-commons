"""Typed task and review payloads after schema and domain validation.

This module owns the A5.2 vertical slice.  It deliberately has no knowledge of
storage or lifecycle rules: JSON Schema plus ``validate_payload`` have already
established the input contract, and projection decides whether a parsed event
may transition the current snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast

from .envelopes import EventEnvelope, FrozenJsonObject, JsonValue, TypedRef, TypedRefPayload


class RevisionBoundRefPayload(TypedDict):
    ref: TypedRefPayload
    revision: str


class TaskChangesPayload(TypedDict):
    title: NotRequired[str]
    description: NotRequired[str]
    acceptance_criteria: NotRequired[list[str]]


class TaskPayload(TypedDict):
    task_id: str
    title: NotRequired[str]
    description: NotRequired[str]
    acceptance_criteria: NotRequired[list[str]]
    changes: NotRequired[TaskChangesPayload]
    priority: NotRequired[str]
    dependencies: NotRequired[list[str]]
    expected_revision: NotRequired[str]
    owner_session_id: NotRequired[str]
    reason: NotRequired[str]
    resolution: NotRequired[str]
    summary: NotRequired[str]
    artifact_refs: NotRequired[list[TypedRefPayload]]
    artifact_bindings: NotRequired[list[RevisionBoundRefPayload]]
    acceptance_review: NotRequired[RevisionBoundRefPayload]
    extensions: NotRequired[dict[str, JsonValue]]


class ReviewPayload(TypedDict):
    review_id: str
    target_ref: NotRequired[TypedRefPayload]
    target_revision: NotRequired[str]
    criteria: NotRequired[list[str]]
    independent: NotRequired[bool]
    expected_revision: NotRequired[str]
    verdict: NotRequired[str]
    summary: NotRequired[str]
    evidence_refs: NotRequired[list[RevisionBoundRefPayload]]
    extensions: NotRequired[dict[str, JsonValue]]


TaskEventType: TypeAlias = Literal[
    "task.created",
    "task.revised",
    "task.taken",
    "task.started",
    "task.blocked",
    "task.unblocked",
    "task.completed",
    "task.submitted",
    "task.accepted",
    "task.cancelled",
    "task.reopened",
]
ReviewEventType: TypeAlias = Literal["review.requested", "review.completed"]

_TASK_EVENT_TYPES = frozenset(
    {
        "task.created",
        "task.revised",
        "task.taken",
        "task.started",
        "task.blocked",
        "task.unblocked",
        "task.completed",
        "task.submitted",
        "task.accepted",
        "task.cancelled",
        "task.reopened",
    }
)
_REVIEW_EVENT_TYPES = frozenset({"review.requested", "review.completed"})


@dataclass(frozen=True)
class RevisionBoundRef:
    reference: TypedRef
    revision: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> RevisionBoundRef:
        return cls(
            reference=TypedRef.from_payload(_required_mapping(value, "ref")),
            revision=_required_string(value, "revision"),
        )

    def to_payload(self) -> RevisionBoundRefPayload:
        return {"ref": self.reference.to_payload(), "revision": self.revision}


@dataclass(frozen=True)
class TaskChanges:
    title: str | None
    description: str | None
    acceptance_criteria: tuple[str, ...] | None

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> TaskChanges:
        return cls(
            title=_optional_string(value, "title"),
            description=_optional_string(value, "description"),
            acceptance_criteria=_optional_string_tuple(value, "acceptance_criteria"),
        )

    def to_payload(self) -> TaskChangesPayload:
        payload: TaskChangesPayload = {}
        if self.title is not None:
            payload["title"] = self.title
        if self.description is not None:
            payload["description"] = self.description
        if self.acceptance_criteria is not None:
            payload["acceptance_criteria"] = list(self.acceptance_criteria)
        return payload


@dataclass(frozen=True)
class TaskEnvelope(EventEnvelope):
    event_type: TaskEventType
    task_id: str
    title: str | None
    description: str | None
    acceptance_criteria: tuple[str, ...] | None
    changes: TaskChanges | None
    priority: str | None
    dependencies: tuple[str, ...] | None
    expected_revision: str | None
    owner_session_id: str | None
    reason: str | None
    resolution: str | None
    summary: str | None
    artifact_refs: tuple[TypedRef, ...] | None
    artifact_bindings: tuple[RevisionBoundRef, ...] | None
    acceptance_review: RevisionBoundRef | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> TaskPayload:
        from .envelopes import thaw_json_object

        payload: TaskPayload = {"task_id": self.task_id}
        if self.title is not None:
            payload["title"] = self.title
        if self.description is not None:
            payload["description"] = self.description
        if self.acceptance_criteria is not None:
            payload["acceptance_criteria"] = list(self.acceptance_criteria)
        if self.changes is not None:
            payload["changes"] = self.changes.to_payload()
        if self.priority is not None:
            payload["priority"] = self.priority
        if self.dependencies is not None:
            payload["dependencies"] = list(self.dependencies)
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.owner_session_id is not None:
            payload["owner_session_id"] = self.owner_session_id
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.resolution is not None:
            payload["resolution"] = self.resolution
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.artifact_refs is not None:
            payload["artifact_refs"] = [item.to_payload() for item in self.artifact_refs]
        if self.artifact_bindings is not None:
            payload["artifact_bindings"] = [item.to_payload() for item in self.artifact_bindings]
        if self.acceptance_review is not None:
            payload["acceptance_review"] = self.acceptance_review.to_payload()
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


@dataclass(frozen=True)
class ReviewEnvelope(EventEnvelope):
    event_type: ReviewEventType
    review_id: str
    target_ref: TypedRef | None
    target_revision: str | None
    criteria: tuple[str, ...] | None
    independent: bool | None
    expected_revision: str | None
    verdict: str | None
    summary: str | None
    evidence_refs: tuple[RevisionBoundRef, ...] | None
    extensions: FrozenJsonObject | None

    def to_payload(self) -> ReviewPayload:
        from .envelopes import thaw_json_object

        payload: ReviewPayload = {"review_id": self.review_id}
        if self.target_ref is not None:
            payload["target_ref"] = self.target_ref.to_payload()
        if self.target_revision is not None:
            payload["target_revision"] = self.target_revision
        if self.criteria is not None:
            payload["criteria"] = list(self.criteria)
        if self.independent is not None:
            payload["independent"] = self.independent
        if self.expected_revision is not None:
            payload["expected_revision"] = self.expected_revision
        if self.verdict is not None:
            payload["verdict"] = self.verdict
        if self.summary is not None:
            payload["summary"] = self.summary
        if self.evidence_refs is not None:
            payload["evidence_refs"] = [item.to_payload() for item in self.evidence_refs]
        if self.extensions is not None:
            payload["extensions"] = thaw_json_object(self.extensions)
        return payload


TaskReviewEnvelope: TypeAlias = TaskEnvelope | ReviewEnvelope


def parse_task_review_envelope(
    event_type: str, payload: Mapping[str, object]
) -> TaskReviewEnvelope | None:
    """Parse a task or review payload that has already passed validation."""

    if event_type in _TASK_EVENT_TYPES:
        return TaskEnvelope(
            event_type=cast(TaskEventType, event_type),
            task_id=_required_string(payload, "task_id"),
            title=_optional_string(payload, "title"),
            description=_optional_string(payload, "description"),
            acceptance_criteria=_optional_string_tuple(payload, "acceptance_criteria"),
            changes=_optional_task_changes(payload),
            priority=_optional_string(payload, "priority"),
            dependencies=_optional_string_tuple(payload, "dependencies"),
            expected_revision=_optional_string(payload, "expected_revision"),
            owner_session_id=_optional_string(payload, "owner_session_id"),
            reason=_optional_string(payload, "reason"),
            resolution=_optional_string(payload, "resolution"),
            summary=_optional_string(payload, "summary"),
            artifact_refs=_optional_ref_tuple(payload, "artifact_refs"),
            artifact_bindings=_optional_bound_ref_tuple(payload, "artifact_bindings"),
            acceptance_review=_optional_bound_ref(payload, "acceptance_review"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type in _REVIEW_EVENT_TYPES:
        return ReviewEnvelope(
            event_type=cast(ReviewEventType, event_type),
            review_id=_required_string(payload, "review_id"),
            target_ref=_optional_ref(payload, "target_ref"),
            target_revision=_optional_string(payload, "target_revision"),
            criteria=_optional_string_tuple(payload, "criteria"),
            independent=_optional_bool(payload, "independent"),
            expected_revision=_optional_string(payload, "expected_revision"),
            verdict=_optional_string(payload, "verdict"),
            summary=_optional_string(payload, "summary"),
            evidence_refs=_optional_bound_ref_tuple(payload, "evidence_refs"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    return None


def _required_string(payload: Mapping[str, object], field: str) -> str:
    return cast(str, payload[field])


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    return cast(str | None, payload.get(field))


def _optional_bool(payload: Mapping[str, object], field: str) -> bool | None:
    return cast(bool | None, payload.get(field))


def _required_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload[field])


def _optional_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object] | None:
    if field not in payload:
        return None
    return _required_mapping(payload, field)


def _optional_string_tuple(payload: Mapping[str, object], field: str) -> tuple[str, ...] | None:
    if field not in payload:
        return None
    return tuple(cast(list[str], payload[field]))


def _optional_ref(payload: Mapping[str, object], field: str) -> TypedRef | None:
    value = _optional_mapping(payload, field)
    return TypedRef.from_payload(value) if value is not None else None


def _optional_ref_tuple(payload: Mapping[str, object], field: str) -> tuple[TypedRef, ...] | None:
    if field not in payload:
        return None
    return tuple(TypedRef.from_payload(item) for item in _mapping_list(payload, field))


def _optional_bound_ref(payload: Mapping[str, object], field: str) -> RevisionBoundRef | None:
    value = _optional_mapping(payload, field)
    return RevisionBoundRef.from_payload(value) if value is not None else None


def _optional_bound_ref_tuple(
    payload: Mapping[str, object], field: str
) -> tuple[RevisionBoundRef, ...] | None:
    if field not in payload:
        return None
    return tuple(RevisionBoundRef.from_payload(item) for item in _mapping_list(payload, field))


def _optional_task_changes(payload: Mapping[str, object]) -> TaskChanges | None:
    value = _optional_mapping(payload, "changes")
    return TaskChanges.from_payload(value) if value is not None else None


def _optional_frozen_object(payload: Mapping[str, object], field: str) -> FrozenJsonObject | None:
    value = _optional_mapping(payload, field)
    if value is None:
        return None
    from .envelopes import freeze_json_object

    return freeze_json_object(value)


def _mapping_list(payload: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], payload[field])
