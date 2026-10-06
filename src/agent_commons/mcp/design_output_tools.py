"""Publish an actual worker-generated image without completing its task."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from agent_commons.core.canonical import canonical_sha256
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
from agent_commons.services.output_content import (
    BUILD_KIND,
    BUILD_MEDIA,
    OutputContentStore,
    inspect_static_build,
)

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

    def decorate(fn):
        return register(
            {
                "readOnlyHint": False,
                "idempotentHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
            worker_only=True,
            worker_purposes=("implementation", "verification"),
        )(fn)

    @decorate
    def commons_publish_static_build(
        source_path: str, title: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Retain a bounded static dist build as a verified ZIP download.

        Repository-relative directory; index.html required. At most 128 files,
        20 MiB total, allowlisted frontend extensions, no links/hidden files.
        Results offer Download build / Open locally, not a hosted preview.
        An unchanged retry uses the same key; changed bytes require a new key.
        """
        return publish(source_path, title, idempotency_key, build=True)

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
        """Retain your generated PNG/JPEG in this task's and agent's Results.

        Repository-relative image, no hidden folders or links, max 10 MiB and
        16 million pixels. Bytes survive source overwrite and server restart.
        An unchanged retry uses the same key; new versions use a new key.
        Publication does not complete, review or accept the task.
        """
        return publish(source_path, title, idempotency_key, build=False)

    def publish(
        source_path: str, title: str, idempotency_key: str, *, build: bool
    ) -> dict[str, Any]:
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
                build_files = None
                if build:
                    content, build_files = inspect_static_build(commons, source_path)
                    content_revision = "sha256:" + hashlib.sha256(content).hexdigest()
                    media_type = BUILD_MEDIA
                else:
                    image = ArtifactPreviewReader(commons).inspect_generated_source(source_path)
                    content, content_revision, media_type = (
                        image.content,
                        image.revision,
                        image.media_type,
                    )
                output_kind = BUILD_KIND if build else OUTPUT_KIND
                key = ("generated-build-" if build else "generated-image-") + hashlib.sha256(
                    f"{current['id']}:{idempotency_key}".encode()
                ).hexdigest()
                artifact_id = commons._new_entity_id("artifact", "artifact.registered", key)
                metadata = {
                    "output_kind": output_kind,
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
                retained = {"revision": content_revision, "size_bytes": len(content)}
                metadata["retained_content"] = retained
                if build:
                    metadata["build_files"] = build_files
                content_copied = True
                existing = snapshot.artifacts.get(artifact_id)
                if existing is not None:
                    bundle = commons.get_artifact_bundle(artifact_id)
                    manifest = bundle["manifest"]
                    old = manifest.get("metadata", {})
                    legacy_image = (
                        not build
                        and isinstance(old, dict)
                        and set(old)
                        == {"output_kind", "output_task", "output_delegation", "series_id", "title"}
                        and all(
                            isinstance(old.get(field), dict)
                            and set(old[field]) == {"id", "revision"}
                            and snapshot.entity_revision_actor(
                                kind, old[field]["id"], old[field]["revision"]
                            )
                            is not None
                            for field, kind in (
                                ("output_task", "task"),
                                ("output_delegation", "delegation"),
                            )
                        )
                    )
                    if (
                        manifest.get("revision") != content_revision
                        or manifest.get("size_bytes") != len(content)
                        or manifest.get("source") != {"path": source_path}
                        or manifest.get("media_type") != media_type
                        or manifest.get("classification") != "internal"
                        or old.get("title") != title
                        or old.get("series_id") != metadata["series_id"]
                        or old.get("output_kind") != output_kind
                        or (not legacy_image and old.get("retained_content") != retained)
                        or (build and old.get("build_files") != build_files)
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
                    # An identical pre-retention retry preserves its historical contract.
                    # Copying bytes here would silently upgrade immutable metadata.
                    content_copied = not legacy_image
                    if content_copied:
                        OutputContentStore(commons).put(content)
                    metadata = old
                    revision = snapshot.entity_revision("artifact", artifact_id)
                else:
                    OutputContentStore(commons).put(content)
                    if build:
                        manifest = {
                            "schema": "commons.manifest.artifact.v1",
                            "kind": "artifact",
                            "artifact_id": artifact_id,
                            "revision": content_revision,
                            "source": {"path": source_path},
                            "media_type": media_type,
                            "size_bytes": len(content),
                            "classification": "internal",
                            "captured": False,
                            "metadata": metadata,
                        }
                        commons.policy.assert_safe(manifest, context="artifact metadata")
                        commons.schemas.validate_manifest(manifest)
                        manifest_id = "mft.artifact.sha256." + canonical_sha256(manifest)
                        result = commons.record_event(
                            "artifact.registered",
                            {
                                "artifact_id": artifact_id,
                                "manifest_ref": manifest_id,
                                "revision": content_revision,
                                "classification": "internal",
                            },
                            idempotency_key=key,
                            relations=(
                                commons._relation(
                                    {"kind": "artifact", "id": artifact_id},
                                    "uses",
                                    {"kind": "manifest", "id": manifest_id},
                                ),
                            ),
                            tags=("artifact",),
                            _manifest=manifest,
                        )
                    else:
                        result = commons.register_artifact(
                            source_path,
                            media_type=media_type,
                            classification="internal",
                            metadata=metadata,
                            expected_revision=content_revision,
                            expected_size=len(content),
                            expected_source_path=source_path,
                            idempotency_key=key,
                        )
                    revision = result["revision"]
                return {
                    "schema": "agent_commons.generated-build-result.v1"
                    if build
                    else "agent_commons.generated-image-result.v1",
                    "kind": "static_build" if build else "artifact_image",
                    "artifact_id": artifact_id,
                    "artifact_revision": revision,
                    "content_revision": content_revision,
                    "task_id": target["id"],
                    "task_revision": metadata["output_task"]["revision"],
                    "producer_agent_id": current["agent_id"],
                    "producer_delegation_id": current["id"],
                    "delegation_revision": metadata["output_delegation"]["revision"],
                    "series_id": metadata["series_id"],
                    "content_copied": content_copied,
                }
        except IdempotencyConflictError:
            raise IdempotencyConflictError(
                "Generated image retry differs from its publication."
            ) from None
        except (CommonsError, ArtifactPreviewRefusal, OSError, ValueError, TypeError, KeyError):
            raise LifecycleConflictError(_REFUSAL) from None
