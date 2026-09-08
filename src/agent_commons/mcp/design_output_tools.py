"""Publish an actual worker-generated image without completing its task."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.states import LIVE_WORKER_DELEGATION_STATES
from agent_commons.errors import (
    CommonsError,
    IdempotencyConflictError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.services.artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal
from agent_commons.services.generated_outputs import OUTPUT_KIND, generated_series

_REFUSAL = "Generated image publication is unavailable for this worker and task."


def register_design_output_tools(
    register: Callable[..., Any],
    commons: Any,
    require_worker: Callable[[], Any],
    *,
    worker_binding: Mapping[str, Any] | None,
) -> None:
    bound = dict(worker_binding or {})
    bound["target_ref"] = dict(bound.get("target_ref") or {})

    @register(
        {
            "readOnlyHint": False,
            "idempotentHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
        worker_only=True,
        worker_purposes=("implementation", "verification"),
    )
    def commons_publish_design_image(
        source_path: str,
        title: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Publish your generated PNG/JPEG into this task's and agent's Results.

        source_path is a repository-relative generated image (no hidden folders,
        symlinks or external paths), at most 10 MiB and 16 million pixels. Write
        the image first, then call this tool. Only metadata enters the ledger.
        This neither completes the task nor creates an accepted Design Package.
        Reuse the key for an unchanged retry; use a new key for a new version of
        the same path. Source changes make earlier exact previews stale.
        """
        if (
            not isinstance(idempotency_key, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}", idempotency_key)
            or not isinstance(title, str)
            or not 1 <= len(title) <= 256
            or any(ord(char) < 32 or ord(char) == 127 for char in title)
        ):
            raise ValidationError("Generated image publication input is invalid.")
        try:
            if commons.read_only:
                raise LifecycleConflictError(_REFUSAL)
            with commons._canonical_write_lock():
                worker = require_worker()
                session = commons.sessions.require_active(commons.session_id)
                snapshot = commons.snapshot()
                if (
                    not isinstance(worker, dict)
                    or not isinstance(snapshot, ProjectSnapshot)
                    or snapshot.workspace_id != commons.workspace_id
                    or session.session_id != commons.session_id
                    or worker.get("purpose") not in {"implementation", "verification"}
                    or any(
                        worker.get(field) != bound.get(field)
                        for field in ("id", "agent_id", "target_ref", "target_revision", "purpose")
                    )
                ):
                    raise LifecycleConflictError(_REFUSAL)
                current = snapshot.delegations.get(worker.get("id"))
                if (
                    not current
                    or current.get("child_session_id") != commons.session_id
                    or current.get("state") not in LIVE_WORKER_DELEGATION_STATES
                    or any(
                        current.get(field) != worker.get(field)
                        for field in ("agent_id", "target_ref", "target_revision", "purpose")
                    )
                ):
                    raise LifecycleConflictError(_REFUSAL)
                target = current.get("target_ref") or {}
                task = (
                    snapshot.tasks.get(target.get("id")) if target.get("kind") == "task" else None
                )
                if task is None or current.get("agent_id") not in snapshot.agents:
                    raise LifecycleConflictError(_REFUSAL)
                image = ArtifactPreviewReader(commons).inspect_generated_source(source_path)
                key = (
                    "generated-image-"
                    + hashlib.sha256(f"{current['id']}:{idempotency_key}".encode()).hexdigest()
                )
                artifact_id = commons._new_entity_id("artifact", "artifact.registered", key)
                metadata = {
                    "output_kind": OUTPUT_KIND,
                    "output_task": {
                        "id": target["id"],
                        "revision": snapshot.entity_revision("task", target["id"]),
                    },
                    "output_delegation": {
                        "id": current["id"],
                        "revision": snapshot.entity_revision("delegation", current["id"]),
                    },
                    "series_id": generated_series(current["id"], source_path),
                    "title": title,
                }
                existing = snapshot.artifacts.get(artifact_id)
                if existing is not None:
                    bundle = commons.get_artifact_bundle(artifact_id)
                    manifest = bundle["manifest"]
                    old = manifest.get("metadata", {})
                    if (
                        manifest.get("revision") != image.revision
                        or manifest.get("source") != {"path": source_path}
                        or manifest.get("media_type") != image.media_type
                        or manifest.get("classification") != "internal"
                        or old.get("title") != title
                        or old.get("series_id") != metadata["series_id"]
                        or old.get("output_kind") != OUTPUT_KIND
                        or old.get("output_task", {}).get("id") != target["id"]
                        or old.get("output_delegation", {}).get("id") != current["id"]
                        or snapshot.entity_revision_actor(
                            "artifact",
                            artifact_id,
                            snapshot.entity_revision("artifact", artifact_id),
                        )
                        != commons.session_id
                    ):
                        raise IdempotencyConflictError(
                            "Generated image retry differs from its publication."
                        )
                    metadata = old
                    revision = snapshot.entity_revision("artifact", artifact_id)
                else:
                    result = commons.register_artifact(
                        source_path,
                        media_type=image.media_type,
                        classification="internal",
                        metadata=metadata,
                        expected_revision=image.revision,
                        expected_size=len(image.content),
                        expected_source_path=source_path,
                        idempotency_key=key,
                    )
                    revision = result["revision"]
                return {
                    "schema": "agent_commons.generated-image-result.v1",
                    "kind": "artifact_image",
                    "artifact_id": artifact_id,
                    "artifact_revision": revision,
                    "content_revision": image.revision,
                    "task_id": target["id"],
                    "task_revision": metadata["output_task"]["revision"],
                    "producer_agent_id": current["agent_id"],
                    "producer_delegation_id": current["id"],
                    "delegation_revision": metadata["output_delegation"]["revision"],
                    "series_id": metadata["series_id"],
                    "content_copied": False,
                }
        except IdempotencyConflictError:
            raise IdempotencyConflictError(
                "Generated image retry differs from its publication."
            ) from None
        except (CommonsError, ArtifactPreviewRefusal, OSError, ValueError, TypeError, KeyError):
            raise LifecycleConflictError(_REFUSAL) from None
