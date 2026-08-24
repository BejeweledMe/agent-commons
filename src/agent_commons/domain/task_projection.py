"""Frozen task projection records and their task-only reducer.

Canonical task events remain JSON mappings.  This module sits after their
validation and freezes the projected task read model without changing its
legacy mapping-shaped wire contract.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass

from .envelopes import FrozenJsonObject, JsonValue, freeze_json_object, thaw_json_object


@dataclass(frozen=True)
class TaskRecord(Mapping[str, object]):
    """One immutable projected task with mapping-compatible reads.

    ``data`` owns every nested JSON value in the existing task read shape.
    Keeping that shape ordered lets this structural move preserve current
    snapshot and UI serialization byte-for-byte while the named fields give
    task projection its stable typed identity boundary.
    """

    task_id: str
    state: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> TaskRecord:
        """Freeze one already-validated projected task mapping."""

        task_id = _required_string(data, "task_id")
        return cls(
            task_id=task_id,
            state=_required_string(data, "state"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def with_artifact_stale(self, artifact_stale: bool) -> TaskRecord:
        """Return this task with its derived artifact staleness refreshed."""

        data = self.to_dict()
        data["artifact_stale"] = artifact_stale
        return self.from_projected_data(data)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a fresh legacy task mapping without exposing frozen internals."""

        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_task_record(
    collection: dict[str, TaskRecord],
    identifier: str,
    event: Mapping[str, object],
    state: str,
) -> None:
    """Apply one task event without retaining a mutable projected task."""

    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    payload_value = event.get("payload")
    if not isinstance(payload_value, Mapping):
        raise TypeError("task projection event payload must be an object")
    payload = deepcopy(dict(payload_value))
    authors = {
        str(session_id)
        for session_id in current_data.get("author_session_ids", [])
        if str(session_id)
    }
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("task projection event actor must be an object")
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    collection[identifier] = TaskRecord.from_projected_data(
        {
            **current_data,
            **payload,
            "id": identifier,
            "state": state,
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
        raise TypeError(f"task projection {field} must be a string")
    return value
