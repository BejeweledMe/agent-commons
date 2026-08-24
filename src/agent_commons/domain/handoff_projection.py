"""Frozen handoff projection records and their handoff-only reducer.

Handoff events remain canonical JSON mappings.  This module receives their
already-validated typed envelopes and freezes the projected handoff read model
without changing the mapping-shaped contract used by inbox and UI consumers.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .envelopes import FrozenJsonObject, JsonValue, freeze_json_object, thaw_json_object


@dataclass(frozen=True)
class HandoffRecord(Mapping[str, object]):
    """One immutable projected handoff with mapping-compatible reads."""

    handoff_id: str
    state: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> HandoffRecord:
        """Freeze one already-validated projected handoff mapping."""

        return cls(
            handoff_id=_required_string(data, "handoff_id"),
            state=_required_string(data, "state"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return fresh legacy handoff data without exposing frozen internals."""

        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_handoff_record(
    collection: dict[str, HandoffRecord],
    identifier: str,
    event: Mapping[str, object],
    payload: Mapping[str, object],
    state: str,
) -> None:
    """Apply one handoff event without retaining a mutable projected handoff."""

    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    authors = {
        str(session_id)
        for session_id in current_data.get("author_session_ids", [])
        if str(session_id)
    }
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("handoff projection event actor must be an object")
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    event_id = _required_string(event, "event_id")
    collection[identifier] = HandoffRecord.from_projected_data(
        {
            **current_data,
            **payload,
            "id": identifier,
            "state": state,
            "revision": event_id,
            "effective_revision": str(event.get("_effective_correction_id") or event_id),
            "recorded_at": event.get("recorded_at"),
            "actor": actor,
            "author_session_ids": sorted(authors),
        }
    )


def _required_string(data: Mapping[str, object], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise TypeError(f"handoff projection {field} must be a string")
    return value
