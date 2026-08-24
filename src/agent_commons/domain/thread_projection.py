"""Frozen thread projection records and their thread-only reducer.

Thread events remain canonical JSON mappings.  This module receives their
already-validated typed envelopes and freezes the projected thread read model
without changing the mapping-shaped contract used by attention, graph, and
engagement consumers.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .envelopes import FrozenJsonObject, JsonValue, freeze_json_object, thaw_json_object


@dataclass(frozen=True)
class ThreadRecord(Mapping[str, object]):
    """One immutable projected thread with mapping-compatible reads."""

    thread_id: str
    state: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> ThreadRecord:
        """Freeze one already-validated projected thread mapping."""

        return cls(
            thread_id=_required_string(data, "thread_id"),
            state=_required_string(data, "state"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def with_message(
        self,
        *,
        message_id: str | None,
        body: str | None,
        actor: object,
        recorded_at: object,
    ) -> ThreadRecord:
        """Return this thread with the historical reply projection appended."""

        data = self.to_dict()
        messages = data.setdefault("messages", [])
        if not isinstance(messages, list):
            raise TypeError("thread projection messages must be an array")
        messages.append(
            {
                "message_id": message_id,
                "body": body,
                "actor": actor,
                "recorded_at": recorded_at,
            }
        )
        return self.from_projected_data(data)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return fresh legacy thread data without exposing frozen internals."""

        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_thread_record(
    collection: dict[str, ThreadRecord],
    identifier: str,
    event: Mapping[str, object],
    payload: Mapping[str, object],
    state: str,
) -> None:
    """Apply one thread event without retaining a mutable projected thread."""

    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    authors = {
        str(session_id)
        for session_id in current_data.get("author_session_ids", [])
        if str(session_id)
    }
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("thread projection event actor must be an object")
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    event_id = _required_string(event, "event_id")
    collection[identifier] = ThreadRecord.from_projected_data(
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
        raise TypeError(f"thread projection {field} must be a string")
    return value
