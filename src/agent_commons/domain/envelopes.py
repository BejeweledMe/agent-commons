"""Typed in-memory envelopes for validated delegation and maintenance events.

Canonical events remain JSON-shaped mappings at the storage boundary.  This
module is deliberately one step inside that boundary: callers must first run
the event payload through its JSON Schema and ``validate_payload``.  The
parsers then give projection and recovery code immutable, named fields without
changing the persisted schema, event names, or serialized payload bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, TypedDict, cast


class TypedRefPayload(TypedDict):
    kind: str
    id: str


class DelegationBudgetPayload(TypedDict):
    unit: str
    limit: int


class DelegationLimitsPayload(TypedDict):
    max_depth: int
    wall_time_seconds: int
    max_attempts: int
    max_concurrency: int
    budget: DelegationBudgetPayload


class DelegationRequestedPayload(TypedDict):
    delegation_id: str
    target_ref: TypedRefPayload
    target_revision: str
    target_profile: str
    purpose: str
    parent_session_id: str
    root_delegation_id: str
    depth: int
    limits: DelegationLimitsPayload
    parent_delegation_id: NotRequired[str]


class DelegationStartedPayload(TypedDict):
    delegation_id: str
    expected_revision: str
    child_session_id: str
    attempt: int


class DelegationSummaryPayload(TypedDict):
    delegation_id: str
    expected_revision: str
    summary: str


class DelegationResumedPayload(TypedDict):
    delegation_id: str
    expected_revision: str
    resolution: str


class DelegationSucceededPayload(TypedDict):
    delegation_id: str
    expected_revision: str
    summary: str
    result_refs: list[TypedRefPayload]


class DelegationReasonPayload(TypedDict):
    delegation_id: str
    expected_revision: str
    reason: str


class DelegationFailurePayload(TypedDict):
    delegation_id: str
    expected_revision: str
    reason_code: str
    summary: str


JsonScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class FrozenJsonArray:
    values: tuple[FrozenJsonValue, ...]


@dataclass(frozen=True)
class FrozenJsonObject:
    values: tuple[tuple[str, FrozenJsonValue], ...]


FrozenJsonValue: TypeAlias = JsonScalar | FrozenJsonArray | FrozenJsonObject
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class EventCorrectedPayload(TypedDict):
    target_event_id: str
    expected_target_sha256: str
    replacement_payload: dict[str, JsonValue]
    superseded_correction_event_ids: NotRequired[list[str]]
    extensions: NotRequired[dict[str, JsonValue]]


class EventInvalidatedPayload(TypedDict):
    target_ref: TypedRefPayload
    reason: str
    extensions: NotRequired[dict[str, JsonValue]]


class EventInvalidationRevokedPayload(TypedDict):
    invalidation_event_id: str
    reason: str
    extensions: NotRequired[dict[str, JsonValue]]


DelegationPayload: TypeAlias = (
    DelegationRequestedPayload
    | DelegationStartedPayload
    | DelegationSummaryPayload
    | DelegationResumedPayload
    | DelegationSucceededPayload
    | DelegationReasonPayload
    | DelegationFailurePayload
)
MaintenancePayload: TypeAlias = (
    EventCorrectedPayload | EventInvalidatedPayload | EventInvalidationRevokedPayload
)


@dataclass(frozen=True)
class TypedRef:
    kind: str
    identifier: str

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> TypedRef:
        return cls(kind=_required_string(value, "kind"), identifier=_required_string(value, "id"))

    def to_payload(self) -> TypedRefPayload:
        return {"kind": self.kind, "id": self.identifier}


@dataclass(frozen=True)
class DelegationBudget:
    unit: str
    limit: int

    def to_payload(self) -> DelegationBudgetPayload:
        return {"unit": self.unit, "limit": self.limit}


@dataclass(frozen=True)
class DelegationLimits:
    max_depth: int
    wall_time_seconds: int
    max_attempts: int
    max_concurrency: int
    budget: DelegationBudget

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> DelegationLimits:
        budget = _required_mapping(value, "budget")
        return cls(
            max_depth=_required_int(value, "max_depth"),
            wall_time_seconds=_required_int(value, "wall_time_seconds"),
            max_attempts=_required_int(value, "max_attempts"),
            max_concurrency=_required_int(value, "max_concurrency"),
            budget=DelegationBudget(
                unit=_required_string(budget, "unit"),
                limit=_required_int(budget, "limit"),
            ),
        )

    def to_payload(self) -> DelegationLimitsPayload:
        return {
            "max_depth": self.max_depth,
            "wall_time_seconds": self.wall_time_seconds,
            "max_attempts": self.max_attempts,
            "max_concurrency": self.max_concurrency,
            "budget": self.budget.to_payload(),
        }


class EventEnvelope:
    """Base class for one parsed, immutable event-family envelope."""

    event_type: str

    def to_payload(self) -> Mapping[str, object]:
        raise NotImplementedError


class DelegationEnvelope(EventEnvelope):
    """Base class for the closed delegation event family."""

    event_type: str
    delegation_id: str

    def to_payload(self) -> DelegationPayload:
        raise NotImplementedError


@dataclass(frozen=True)
class DelegationRequestedEnvelope(DelegationEnvelope):
    delegation_id: str
    target_ref: TypedRef
    target_revision: str
    target_profile: str
    purpose: str
    parent_session_id: str
    root_delegation_id: str
    depth: int
    limits: DelegationLimits
    parent_delegation_id: str | None
    event_type: Literal["delegation.requested"] = "delegation.requested"

    def to_payload(self) -> DelegationRequestedPayload:
        payload: DelegationRequestedPayload = {
            "delegation_id": self.delegation_id,
            "target_ref": self.target_ref.to_payload(),
            "target_revision": self.target_revision,
            "target_profile": self.target_profile,
            "purpose": self.purpose,
            "parent_session_id": self.parent_session_id,
            "root_delegation_id": self.root_delegation_id,
            "depth": self.depth,
            "limits": self.limits.to_payload(),
        }
        if self.parent_delegation_id is not None:
            payload["parent_delegation_id"] = self.parent_delegation_id
        return payload


@dataclass(frozen=True)
class DelegationStartedEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    child_session_id: str
    attempt: int
    event_type: Literal["delegation.started"] = "delegation.started"

    def to_payload(self) -> DelegationStartedPayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "child_session_id": self.child_session_id,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class DelegationSummaryEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    summary: str
    event_type: Literal["delegation.input_needed", "delegation.timed_out"]

    def to_payload(self) -> DelegationSummaryPayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class DelegationResumedEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    resolution: str
    event_type: Literal["delegation.resumed"] = "delegation.resumed"

    def to_payload(self) -> DelegationResumedPayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "resolution": self.resolution,
        }


@dataclass(frozen=True)
class DelegationSucceededEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    summary: str
    result_refs: tuple[TypedRef, ...]
    event_type: Literal["delegation.succeeded"] = "delegation.succeeded"

    def to_payload(self) -> DelegationSucceededPayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "summary": self.summary,
            "result_refs": [item.to_payload() for item in self.result_refs],
        }


@dataclass(frozen=True)
class DelegationReasonEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    reason: str
    event_type: Literal["delegation.cancelled", "delegation.recovered"]

    def to_payload(self) -> DelegationReasonPayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DelegationFailureEnvelope(DelegationEnvelope):
    delegation_id: str
    expected_revision: str
    reason_code: str
    summary: str
    event_type: Literal["delegation.failed", "delegation.needs_operator"]

    def to_payload(self) -> DelegationFailurePayload:
        return {
            "delegation_id": self.delegation_id,
            "expected_revision": self.expected_revision,
            "reason_code": self.reason_code,
            "summary": self.summary,
        }


class MaintenanceEnvelope(EventEnvelope):
    """Base class for the closed maintenance event family."""

    event_type: str

    def to_payload(self) -> MaintenancePayload:
        raise NotImplementedError


@dataclass(frozen=True)
class EventCorrectedEnvelope(MaintenanceEnvelope):
    target_event_id: str
    expected_target_sha256: str
    replacement_payload: FrozenJsonObject
    superseded_correction_event_ids: tuple[str, ...] | None
    extensions: FrozenJsonObject | None
    event_type: Literal["event.corrected"] = "event.corrected"

    def to_payload(self) -> EventCorrectedPayload:
        payload: EventCorrectedPayload = {
            "target_event_id": self.target_event_id,
            "expected_target_sha256": self.expected_target_sha256,
            "replacement_payload": _thaw_object(self.replacement_payload),
        }
        if self.superseded_correction_event_ids is not None:
            payload["superseded_correction_event_ids"] = list(self.superseded_correction_event_ids)
        if self.extensions is not None:
            payload["extensions"] = _thaw_object(self.extensions)
        return payload


@dataclass(frozen=True)
class EventInvalidatedEnvelope(MaintenanceEnvelope):
    target_ref: TypedRef
    reason: str
    extensions: FrozenJsonObject | None
    event_type: Literal["event.invalidated"] = "event.invalidated"

    def to_payload(self) -> EventInvalidatedPayload:
        payload: EventInvalidatedPayload = {
            "target_ref": self.target_ref.to_payload(),
            "reason": self.reason,
        }
        if self.extensions is not None:
            payload["extensions"] = _thaw_object(self.extensions)
        return payload


@dataclass(frozen=True)
class EventInvalidationRevokedEnvelope(MaintenanceEnvelope):
    invalidation_event_id: str
    reason: str
    extensions: FrozenJsonObject | None
    event_type: Literal["event.invalidation_revoked"] = "event.invalidation_revoked"

    def to_payload(self) -> EventInvalidationRevokedPayload:
        payload: EventInvalidationRevokedPayload = {
            "invalidation_event_id": self.invalidation_event_id,
            "reason": self.reason,
        }
        if self.extensions is not None:
            payload["extensions"] = _thaw_object(self.extensions)
        return payload


# Every A5 vertical slice subclasses this immutable interface.  The closed
# family-specific unions live with the family so this dispatcher can stay a
# small, non-cyclic bridge between them.
TypedEventEnvelope: TypeAlias = EventEnvelope


def parse_event_envelope(
    event_type: str, payload: Mapping[str, object]
) -> TypedEventEnvelope | None:
    """Parse one already schema- and domain-validated event payload.

    Other event families intentionally stay at their existing mapping boundary
    until their own A5 slice.  This avoids a broad partial type that would
    conceal the next family's fields behind ``dict[str, Any]`` again.
    """

    if event_type == "delegation.requested":
        return DelegationRequestedEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            target_ref=TypedRef.from_payload(_required_mapping(payload, "target_ref")),
            target_revision=_required_string(payload, "target_revision"),
            target_profile=_required_string(payload, "target_profile"),
            purpose=_required_string(payload, "purpose"),
            parent_session_id=_required_string(payload, "parent_session_id"),
            root_delegation_id=_required_string(payload, "root_delegation_id"),
            depth=_required_int(payload, "depth"),
            limits=DelegationLimits.from_payload(_required_mapping(payload, "limits")),
            parent_delegation_id=_optional_string(payload, "parent_delegation_id"),
        )
    if event_type == "delegation.started":
        return DelegationStartedEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            child_session_id=_required_string(payload, "child_session_id"),
            attempt=_required_int(payload, "attempt"),
        )
    if event_type in {"delegation.input_needed", "delegation.timed_out"}:
        return DelegationSummaryEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            summary=_required_string(payload, "summary"),
            event_type=cast(Literal["delegation.input_needed", "delegation.timed_out"], event_type),
        )
    if event_type == "delegation.resumed":
        return DelegationResumedEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            resolution=_required_string(payload, "resolution"),
        )
    if event_type == "delegation.succeeded":
        return DelegationSucceededEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            summary=_required_string(payload, "summary"),
            result_refs=tuple(
                TypedRef.from_payload(item)
                for item in _required_mapping_list(payload, "result_refs")
            ),
        )
    if event_type in {"delegation.cancelled", "delegation.recovered"}:
        return DelegationReasonEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            reason=_required_string(payload, "reason"),
            event_type=cast(Literal["delegation.cancelled", "delegation.recovered"], event_type),
        )
    if event_type in {"delegation.failed", "delegation.needs_operator"}:
        return DelegationFailureEnvelope(
            delegation_id=_required_string(payload, "delegation_id"),
            expected_revision=_required_string(payload, "expected_revision"),
            reason_code=_required_string(payload, "reason_code"),
            summary=_required_string(payload, "summary"),
            event_type=cast(Literal["delegation.failed", "delegation.needs_operator"], event_type),
        )
    if event_type == "event.corrected":
        return EventCorrectedEnvelope(
            target_event_id=_required_string(payload, "target_event_id"),
            expected_target_sha256=_required_string(payload, "expected_target_sha256"),
            replacement_payload=_freeze_object(_required_mapping(payload, "replacement_payload")),
            superseded_correction_event_ids=_optional_string_tuple(
                payload, "superseded_correction_event_ids"
            ),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type == "event.invalidated":
        return EventInvalidatedEnvelope(
            target_ref=TypedRef.from_payload(_required_mapping(payload, "target_ref")),
            reason=_required_string(payload, "reason"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    if event_type == "event.invalidation_revoked":
        return EventInvalidationRevokedEnvelope(
            invalidation_event_id=_required_string(payload, "invalidation_event_id"),
            reason=_required_string(payload, "reason"),
            extensions=_optional_frozen_object(payload, "extensions"),
        )
    from .task_review_envelopes import parse_task_review_envelope

    task_or_review = parse_task_review_envelope(event_type, payload)
    if task_or_review is not None:
        return task_or_review
    from .thread_handoff_envelopes import parse_thread_handoff_envelope

    return parse_thread_handoff_envelope(event_type, payload)


def serialize_event_envelope(envelope: TypedEventEnvelope) -> Mapping[str, object]:
    """Return the exact JSON-shaped payload represented by a typed envelope."""

    return envelope.to_payload()


def _required_string(payload: Mapping[str, object], field: str) -> str:
    return cast(str, payload[field])


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    return cast(str | None, value)


def _required_int(payload: Mapping[str, object], field: str) -> int:
    return cast(int, payload[field])


def _required_mapping(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    return cast(Mapping[str, object], payload[field])


def _required_mapping_list(payload: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], payload[field])


def _optional_string_tuple(payload: Mapping[str, object], field: str) -> tuple[str, ...] | None:
    if field not in payload:
        return None
    return tuple(cast(list[str], payload[field]))


def _optional_frozen_object(payload: Mapping[str, object], field: str) -> FrozenJsonObject | None:
    if field not in payload:
        return None
    return _freeze_object(_required_mapping(payload, field))


def _freeze_object(value: Mapping[str, object]) -> FrozenJsonObject:
    return FrozenJsonObject(
        tuple((cast(str, key), _freeze_json(child)) for key, child in value.items())
    )


def _freeze_json(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _freeze_object(cast(Mapping[str, object], value))
    if isinstance(value, list):
        return FrozenJsonArray(tuple(_freeze_json(item) for item in value))
    raise TypeError(f"validated JSON payload contains {type(value).__name__}")


def _thaw_object(value: FrozenJsonObject) -> dict[str, JsonValue]:
    return {key: _thaw_json(child) for key, child in value.values}


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, FrozenJsonObject):
        return _thaw_object(value)
    if isinstance(value, FrozenJsonArray):
        return [_thaw_json(item) for item in value.values]
    return value


def freeze_json_object(value: Mapping[str, object]) -> FrozenJsonObject:
    """Freeze a validated arbitrary JSON object for a typed envelope."""

    return _freeze_object(value)


def thaw_json_object(value: FrozenJsonObject) -> dict[str, JsonValue]:
    """Restore the JSON object a typed envelope serializes to disk."""

    return _thaw_object(value)
