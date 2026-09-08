"""Worker-scoped publication of private live-preview metadata; never server launch."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal

from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import LIVE_WORKER_DELEGATION_STATES
from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.runtime.collaboration_storage import collaboration_state_root
from agent_commons.runtime.live_previews import LivePreviewRegistry
from agent_commons.storage.opstate import iso_timestamp

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")
_REFUSAL = "Live preview publication is unavailable for this worker and task."


def register_live_preview_tools(
    register: Callable[..., Any],
    commons: Any,
    require_worker: Callable[[], Any],
    *,
    worker_binding: Mapping[str, Any] | None,
    registry_factory: Callable[[], LivePreviewRegistry] | None = None,
) -> None:
    """Register through MCP's existing live-worker guard and purpose filter.

    Catalog-only servers may describe this tool; execution still requires a
    writable manager and a currently active, canonically bound child session.
    No task, delegation, agent, session or revision arguments are caller-supplied.
    """
    bound = dict(worker_binding or {})
    bound["target_ref"] = dict(bound.get("target_ref") or {})
    annotation = {
        "readOnlyHint": False,
        "idempotentHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    }

    @register(annotation, worker_only=True, worker_purposes=("implementation", "verification"))
    def commons_publish_live_preview(
        title: str,
        url: str,
        idempotency_key: str,
        reported_state: Literal["starting", "ready", "unavailable"],
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """Advertise an existing frontend preview for your exact delegated task.

        Accepts only a root HTTP origin at 127.0.0.1 or [::1] with an explicit
        port; no route, query, fragment or credentials. TTL is 60–3600 seconds.
        Readiness is your report; reachability is never checked. This tool starts
        no process and returns no navigable URL. Results authorizes navigation.
        Use a new idempotency key to renew or change the advertisement.
        """
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise ValidationError("Live preview idempotency key is invalid.")
        try:
            if commons.read_only:
                raise LifecycleConflictError(_REFUSAL)
            worker = require_worker()
            if (
                not isinstance(worker, dict)
                or any(
                    worker.get(field) != bound.get(field)
                    for field in ("id", "agent_id", "target_ref", "target_revision", "purpose")
                )
                or worker.get("purpose")
                not in {
                    "implementation",
                    "verification",
                }
            ):
                raise LifecycleConflictError(_REFUSAL)
            session_id = commons.session_id
            active = commons.sessions.require_active(session_id)
            if active.session_id != session_id:
                raise LifecycleConflictError(_REFUSAL)
            snapshot = commons.snapshot()
            if (
                not isinstance(snapshot, ProjectSnapshot)
                or snapshot.workspace_id != commons.workspace_id
            ):
                raise LifecycleConflictError(_REFUSAL)
            current = snapshot.delegations.get(worker.get("id"))
            if (
                not current
                or current.get("child_session_id") != session_id
                or current.get("state") not in LIVE_WORKER_DELEGATION_STATES
                or current.get("purpose") != worker.get("purpose")
                or current.get("agent_id") != worker.get("agent_id")
                or current.get("target_ref") != worker.get("target_ref")
                or current.get("target_revision") != worker.get("target_revision")
            ):
                raise LifecycleConflictError(_REFUSAL)
            target = current.get("target_ref")
            if not isinstance(target, dict) or target.get("kind") != "task":
                raise LifecycleConflictError(_REFUSAL)
            store = (
                registry_factory()
                if registry_factory
                else LivePreviewRegistry(
                    collaboration_state_root(commons),
                    project_root=commons.repo_root,
                    workspace_id=snapshot.workspace_id,
                    forbidden_ports=frozenset(),
                    publication_only=True,
                )
            )
            # Each worker owns its retry namespace, so unrelated agents may use
            # the same natural operation key without colliding in private state.
            key = (
                "worker-"
                + hashlib.sha256(f"{current['id']}:{idempotency_key}".encode()).hexdigest()
            )
            row = store.publish(
                snapshot,
                {
                    "task_id": target["id"],
                    "task_revision": current.get("target_revision"),
                    "delegation_id": current["id"],
                    "delegation_revision": current.get("effective_revision")
                    or current.get("revision"),
                    "title": title,
                    "url": url,
                    "ttl_seconds": ttl_seconds,
                    "reported_state": reported_state,
                    "idempotency_key": key,
                },
            )
            return {
                "schema": "agent_commons.worker-live-preview.v1",
                "preview_id": row.output_id,
                "task_id": row.task_id,
                "task_revision": row.task_revision,
                "delegation_id": row.producer_delegation_id,
                "producer_agent_id": row.producer_agent_id,
                "reported_state": row.reported_state,
                "state": row.state,
                "reachability": "unknown",
                "expires_at": iso_timestamp(row.expires_at),
            }
        except Exception:
            # Neither filesystem failures nor canonical/provider metadata may
            # escape through MCP's exception rendering.
            raise LifecycleConflictError(_REFUSAL) from None
