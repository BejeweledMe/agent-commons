"""Frozen projection records for immutable blueprint applications."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass

from .envelopes import (
    FrozenJsonObject,
    JsonValue,
    freeze_json_object,
    thaw_json_field,
    thaw_json_object,
)


@dataclass(frozen=True)
class ApplicationRecord(Mapping[str, object]):
    application_id: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> ApplicationRecord:
        return cls(
            application_id=_required_string(data, "application_id"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return thaw_json_field(self.data, key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_application_record(
    collection: dict[str, ApplicationRecord], identifier: str, event: Mapping[str, object]
) -> None:
    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("application projection event payload must be an object")
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("application projection event actor must be an object")
    authors = {str(value) for value in current_data.get("author_session_ids", []) if str(value)}
    if session_id := str(actor.get("session_id", "")):
        authors.add(session_id)
    collection[identifier] = ApplicationRecord.from_projected_data(
        {
            **current_data,
            **deepcopy(dict(payload)),
            "id": identifier,
            "state": "active",
            "revision": str(event["event_id"]),
            "effective_revision": str(event.get("_effective_correction_id") or event["event_id"]),
            "recorded_at": event.get("recorded_at"),
            "actor": actor,
            "author_session_ids": sorted(authors),
        }
    )


def _required_string(data: Mapping[str, object], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise TypeError(f"application projection {field} must be a string")
    return value
