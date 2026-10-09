"""Activate UI implementation work before freezing a delegation's task revision."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agent_commons.core.canonical import canonical_sha256
from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError


def create_task_delegation(
    manager: Any,
    *,
    target_ref: Mapping[str, str],
    target_revision: str,
    target_profile: str,
    purpose: str,
    limits: Mapping[str, Any],
    on_behalf_of_agent_id: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """One locked, crash-resumable preparation; no task revision is silently rebound.

    The UI owns activation. A worker always receives the resulting active task
    revision. Replays keep the original binding even if that task later moves.
    """
    key = manager._idempotency_key("delegation.requested", idempotency_key)
    request = {
        "target_ref": dict(target_ref),
        "target_profile": target_profile,
        "purpose": purpose,
        "limits": dict(limits),
        "on_behalf_of_agent_id": on_behalf_of_agent_id,
    }
    with manager._canonical_write_lock():
        session = manager._active_session()
        namespace = manager._namespace(session)
        prior = manager._event_for_idempotency_identity(namespace, key)
        start_key = "ui-task-start-" + hashlib.sha256(key.encode()).hexdigest()
        started = (
            manager._event_for_idempotency_identity(namespace, start_key) if prior is None else None
        )
        if started is not None and purpose != "implementation":
            raise IdempotencyConflictError("Task launch retry differs from its activation.")
        if prior is not None:
            if prior.event["event_type"] != "delegation.requested" or prior.event["payload"][
                "target_ref"
            ] != dict(target_ref):
                raise IdempotencyConflictError(
                    "Task launch retry differs from its original request."
                )
            # The caller's UI snapshot may now show a completed or edited task.
            # Replay the already frozen launch, never make it target those edits.
            target_revision = prior.event["payload"]["target_revision"]
        elif purpose == "implementation":
            task_id = target_ref.get("id")
            snapshot = manager.snapshot()
            task = snapshot.tasks.get(task_id) if target_ref.get("kind") == "task" else None
            if task is None:
                raise LifecycleConflictError("Implementation launch requires an existing task.")
            current_revision = snapshot.entity_revision("task", task_id)
            request_hash = canonical_sha256(request)
            if started is not None:
                payload = started.event["payload"]
                if (
                    started.event["event_type"] != "task.started"
                    or payload.get("task_id") != task_id
                    or (payload.get("extensions") or {}).get("implementation_launch")
                    != {"request_sha256": request_hash}
                ):
                    raise IdempotencyConflictError("Task launch retry differs from its activation.")
                if current_revision != started.event["event_id"] or target_revision not in {
                    payload["expected_revision"],
                    started.event["event_id"],
                }:
                    raise LifecycleConflictError(
                        "The task changed after launch activation; refresh and create a new launch."
                    )
                # Repair a response/receipt interruption using the exact first body.
                activation = manager.start_task(
                    task_id,
                    payload["expected_revision"],
                    extensions=payload["extensions"],
                    idempotency_key=start_key,
                )
                target_revision = activation["revision"]
            else:
                if current_revision != target_revision:
                    raise LifecycleConflictError("The task changed before launch activation.")
                if task.get("state") in {"ready", "assigned"}:
                    if task.get("owner_session_id") not in {None, session.session_id}:
                        raise LifecycleConflictError(
                            "Only the task owner may activate assigned work."
                        )
                    activation = manager.start_task(
                        task_id,
                        target_revision,
                        extensions={
                            **dict(task.get("extensions") or {}),
                            "implementation_launch": {"request_sha256": request_hash},
                        },
                        idempotency_key=start_key,
                    )
                    target_revision = activation["revision"]
                elif task.get("state") != "active":
                    raise LifecycleConflictError(
                        "Implementation launch requires ready, assigned, or active work."
                    )
        return manager.create_delegation(
            **request,
            target_revision=target_revision,
            idempotency_key=key,
        )
