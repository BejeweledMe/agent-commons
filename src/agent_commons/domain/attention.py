"""Canonical selection of records that require a person's attention."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .agents import session_agent_map
from .snapshot import ProjectSnapshot

AttentionKind = Literal["run_blocked", "work_returned", "thread"]
_HUMAN_DECISION_THREADS = frozenset({"decision_request", "question", "help_request", "proposal"})
_HUMAN_RECIPIENTS = frozenset({"operator", "*"})
_BLOCKED_RUN_STATES = frozenset({"input_needed", "failed", "timed_out", "needs_operator"})


@dataclass(frozen=True, slots=True)
class AttentionItem:
    kind: AttentionKind
    identifier: str
    record: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AttentionSet:
    items: tuple[AttentionItem, ...]
    node_ids: frozenset[str]


def thread_awaits_human(record: Mapping[str, Any]) -> bool:
    """Return whether an open canonical thread is addressed to a person."""

    if record.get("state") != "open":
        return False
    if str(record.get("thread_type", "")) not in _HUMAN_DECISION_THREADS:
        return False
    recipients = {str(item) for item in record.get("to") or ()}
    return bool(recipients & _HUMAN_RECIPIENTS)


def _delegation_attention_kind(
    snapshot: ProjectSnapshot,
    record: Mapping[str, Any],
) -> AttentionKind | None:
    state = str(record.get("state", ""))
    if state in _BLOCKED_RUN_STATES:
        return "run_blocked"
    if state != "succeeded":
        return None
    target = record.get("target_ref") or {}
    if target.get("kind") != "task":
        return None
    task = snapshot.tasks.get(str(target.get("id") or ""))
    if task is None or task.get("state") in {"accepted", "cancelled"}:
        return None
    return "work_returned"


def awaits_human(snapshot: ProjectSnapshot) -> AttentionSet:
    """Return canonical attention items and every graph node they should ring."""

    items: list[AttentionItem] = []
    node_ids: set[str] = set()
    returned: dict[str, AttentionItem] = {}
    for identifier, record in sorted(snapshot.delegations.items()):
        kind = _delegation_attention_kind(snapshot, record)
        if kind is None:
            continue
        item = AttentionItem(kind=kind, identifier=identifier, record=record)
        target = record.get("target_ref") or {}
        target_id = str(target.get("id") or "")
        if kind == "work_returned":
            returned[target_id] = item
        else:
            items.append(item)
        node_ids.add(identifier)
        node_ids.update(
            str(value)
            for value in (
                record.get("agent_id"),
                record.get("child_session_id"),
                target_id,
            )
            if value
        )
    items.extend(returned.values())

    for identifier, record in sorted(snapshot.threads.items()):
        if not thread_awaits_human(record):
            continue
        items.append(AttentionItem(kind="thread", identifier=identifier, record=record))
        node_ids.add(identifier)
        session_id = str((record.get("actor") or {}).get("session_id", ""))
        if session_id:
            node_ids.add(session_id)

    bindings = session_agent_map(snapshot.delegations)
    for session_id in list(node_ids):
        node_ids.update(bindings.get(session_id, ()))
    return AttentionSet(items=tuple(items), node_ids=frozenset(node_ids))
