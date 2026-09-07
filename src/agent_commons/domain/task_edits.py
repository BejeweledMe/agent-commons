"""Bounded task dependency edits and operational editing refusals.

Dependency validation is replayable. The live-work guard is checked by the
editing service under the canonical lock, without rewriting historical rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import NON_TERMINAL_DELEGATION_STATES
from agent_commons.errors import LifecycleConflictError, ValidationError

MAX_TASK_DEPENDENCIES = 128
MAX_EDIT_GRAPH_NODES = 4096
EDITABLE_TASK_STATES = frozenset({"ready", "assigned", "active", "blocked", "completed", "review"})
CANCELLABLE_TASK_STATES = frozenset({"ready", "assigned", "active", "blocked"})


class TaskEditRefusal(LifecycleConflictError):
    """A bounded refusal safe for the operator editor."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_task_dependencies(value: object) -> None:
    """Validate the new dependency-edit field before materializing a graph."""

    if not isinstance(value, (list, tuple)) or len(value) > MAX_TASK_DEPENDENCIES:
        raise ValidationError("task dependencies must be a list of at most 128 task IDs")
    if any(type(item) is not str or not is_typed_id(item, "task") for item in value):
        raise ValidationError("task dependencies must contain only task IDs")
    if len(set(value)) != len(value):
        raise ValidationError("task dependencies must not repeat")


def validate_task_dependency_change(snapshot: ProjectSnapshot, payload: Mapping[str, Any]) -> None:
    """Reject dangling edges and cycles in the exact post-edit dependency graph."""

    changes = payload.get("changes")
    if not isinstance(changes, Mapping) or "dependencies" not in changes:
        return
    dependencies = changes["dependencies"]
    validate_task_dependencies(dependencies)
    task_id = str(payload["task_id"])
    for dependency in dependencies:
        if dependency not in snapshot.tasks:
            raise TaskEditRefusal(
                "task_dependency_missing", "A selected dependency no longer exists."
            )
        if dependency == task_id:
            raise TaskEditRefusal("task_dependency_cycle", "A task cannot depend on itself.")
    # Only nodes reachable through the changed edges can introduce a cycle.
    # Walk iteratively so deeply nested operator data cannot exhaust the stack.
    visited: set[str] = set()
    pending = list(dependencies)
    while pending:
        current = pending.pop()
        if current == task_id:
            raise TaskEditRefusal("task_dependency_cycle", "This dependency would create a cycle.")
        if current in visited:
            continue
        visited.add(current)
        if len(visited) > MAX_EDIT_GRAPH_NODES:
            raise TaskEditRefusal(
                "task_graph_too_large", "The dependency graph exceeds the editing limit."
            )
        record = snapshot.tasks.get(current)
        if record is None:
            raise TaskEditRefusal(
                "task_dependency_missing", "The dependency graph contains a missing task."
            )
        parents = record.get("dependencies") or ()
        validate_task_dependencies(parents)
        pending.extend(parents)


def task_has_live_work(snapshot: ProjectSnapshot, task_id: str) -> bool:
    """A requested or running delegation must be resolved before editing work."""

    for delegation in snapshot.delegations.values():
        if delegation.get("state") not in NON_TERMINAL_DELEGATION_STATES:
            continue
        target = delegation.get("target_ref") or {}
        if isinstance(target, Mapping) and target.get("kind") in {"review", "verification"}:
            collection = snapshot.reviews if target["kind"] == "review" else snapshot.verifications
            subject = collection.get(str(target.get("id", "")))
            target = subject.get("target_ref") if subject is not None else {}
        if (
            isinstance(target, Mapping)
            and target.get("kind") == "task"
            and target.get("id") == task_id
        ):
            return True
    return False


def validate_task_suggestion(snapshot: ProjectSnapshot, payload: Mapping[str, Any]) -> None:
    """A blueprint hint names an existing role and carries no assignment authority."""

    extensions = payload.get("extensions")
    if not isinstance(extensions, Mapping) or "suggested_agent_id" not in extensions:
        return
    identifier = extensions["suggested_agent_id"]
    if type(identifier) is not str or not is_typed_id(identifier, "agent"):
        raise ValidationError("suggested_agent_id must name an agent")
    role = snapshot.agents.get(identifier)
    if role is None or role.get("state") != "active" or role.get("template"):
        raise TaskEditRefusal(
            "task_suggested_role_unavailable", "The suggested role must be an active hired role."
        )


def require_task_editable(snapshot: ProjectSnapshot, task_id: str, *, cancel: bool = False) -> None:
    record = snapshot.tasks.get(task_id)
    if record is None:
        raise TaskEditRefusal("task_edit_missing", "The selected task no longer exists.")
    allowed = CANCELLABLE_TASK_STATES if cancel else EDITABLE_TASK_STATES
    if record.get("state") not in allowed:
        raise TaskEditRefusal("task_edit_state", "This task must be reopened before this action.")
    if task_has_live_work(snapshot, task_id):
        raise TaskEditRefusal(
            "task_live_work",
            "Resolve the task's requested or running work before editing or cancelling it.",
        )
