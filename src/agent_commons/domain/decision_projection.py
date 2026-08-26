"""Frozen decision projection records and their decision-only reducer.

Decision events remain canonical JSON mappings.  This module receives their
already-validated typed envelopes and freezes the projected decision read model
without changing the mapping-shaped contract used by truth and view consumers.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass

from .envelopes import FrozenJsonObject, JsonValue, freeze_json_object, thaw_json_object


@dataclass(frozen=True)
class DecisionRecord(Mapping[str, object]):
    """One immutable projected decision with mapping-compatible reads."""

    decision_id: str
    state: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> DecisionRecord:
        """Freeze one already-validated projected decision mapping."""

        return cls(
            decision_id=_required_string(data, "decision_id"),
            state=_required_string(data, "state"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def with_stale(self, stale: bool) -> DecisionRecord:
        """Return this decision with its derived evidence staleness refreshed."""

        data = self.to_dict()
        data["stale"] = stale
        return self.from_projected_data(data)

    def with_conflict(self) -> DecisionRecord:
        """Return this decision in the fail-closed conflicted state."""

        data = self.to_dict()
        data["state"] = "conflicted"
        data["conflict"] = True
        return self.from_projected_data(data)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return fresh legacy decision data without exposing frozen internals."""

        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_decision_record(
    collection: dict[str, DecisionRecord],
    identifier: str,
    event: Mapping[str, object],
    state: str,
) -> None:
    """Apply one decision event without retaining a mutable projected decision."""

    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    payload_value = event.get("payload")
    if not isinstance(payload_value, Mapping):
        raise TypeError("decision projection event payload must be an object")
    # Projection has already validated this effective event.  Its persisted
    # payload is the legacy mapping's ordering source, including any corrected
    # replacement payload, so freeze a private copy without rebuilding it from
    # the typed envelope's field order.
    payload = deepcopy(dict(payload_value))
    authors = {
        str(session_id)
        for session_id in current_data.get("author_session_ids", [])
        if str(session_id)
    }
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("decision projection event actor must be an object")
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    event_id = _required_string(event, "event_id")
    collection[identifier] = DecisionRecord.from_projected_data(
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
        raise TypeError(f"decision projection {field} must be a string")
    return value
