"""Frozen agent projection records and their agent-only reducer.

Agent events are already schema-validated and parsed into typed envelopes
before projection reaches this module.  The record therefore owns the mutable
JSON-shaped read model without widening the canonical event boundary or asking
current mapping consumers to change at the same time.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .envelopes import FrozenJsonObject, JsonValue, freeze_json_object, thaw_json_object


@dataclass(frozen=True)
class AgentRecord(Mapping[str, object]):
    """One immutable projected agent with mapping-compatible reads.

    ``data`` retains the pre-existing ordered JSON read shape.  Named fields
    give role creation and Context Pack consumers a stable projection boundary
    while ``to_dict`` keeps legacy callers and snapshot serialization intact.
    """

    agent_id: str
    state: str
    revision: str
    effective_revision: str
    data: FrozenJsonObject

    @classmethod
    def from_projected_data(cls, data: Mapping[str, object]) -> AgentRecord:
        """Freeze one already-validated projected agent mapping."""

        return cls(
            agent_id=_required_string(data, "agent_id"),
            state=_required_string(data, "state"),
            revision=_required_string(data, "revision"),
            effective_revision=_required_string(data, "effective_revision"),
            data=freeze_json_object(data),
        )

    def with_created_defaults(self, event_id: str) -> AgentRecord:
        """Return a created role with its historical projected defaults."""

        data = self.to_dict()
        data.setdefault("created_by_agent_id", None)
        data.setdefault("turnover_budget", None)
        data.setdefault("template", False)
        data["created_event_id"] = event_id
        return self.from_projected_data(data)

    def with_lifetime_retirement(self, task_id: str) -> AgentRecord:
        """Return this active task-scoped role in its derived terminal state."""

        data = self.to_dict()
        data["state"] = "retired"
        data["retired_by"] = "lifetime"
        data["retired_with_task_id"] = task_id
        return self.from_projected_data(data)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return fresh legacy mapping data without exposing frozen internals."""

        return thaw_json_object(self.data)

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.data.values)

    def __len__(self) -> int:
        return len(self.data.values)


def apply_agent_record(
    collection: dict[str, AgentRecord],
    identifier: str,
    event: Mapping[str, object],
    payload: Mapping[str, object],
    state: str,
) -> None:
    """Apply one agent event without retaining a mutable projected role."""

    current = collection.get(identifier)
    current_data = current.to_dict() if current is not None else {}
    authors = {
        str(session_id)
        for session_id in current_data.get("author_session_ids", [])
        if str(session_id)
    }
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        raise TypeError("agent projection event actor must be an object")
    actor_session_id = str(actor.get("session_id", ""))
    if actor_session_id:
        authors.add(actor_session_id)
    event_id = _required_string(event, "event_id")
    collection[identifier] = AgentRecord.from_projected_data(
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
        raise TypeError(f"agent projection {field} must be a string")
    return value
