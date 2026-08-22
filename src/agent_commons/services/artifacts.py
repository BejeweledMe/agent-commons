"""Artifact commands mixed into the universal workspace manager."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.lifecycle import entity
from agent_commons.errors import IntegrityError, LifecycleConflictError, ValidationError


class ArtifactCommands:
    """Commands for immutable, metadata-only workspace artifacts."""

    def list_artifacts(self) -> list[dict[str, Any]]:
        return self._list("artifact")

    def get_artifact_bundle(self, artifact_id: str) -> dict[str, Any]:
        """Return one projected artifact with its integrity-checked manifest metadata."""

        current = entity(self.snapshot(), "artifact", artifact_id)
        if current is None:
            raise LifecycleConflictError(f"artifact does not exist: {artifact_id}")
        manifest_ref = str(current.get("manifest_ref", ""))
        try:
            manifest = self.manifests.get(manifest_ref)
        except FileNotFoundError as exc:
            raise IntegrityError(f"artifact {artifact_id} references a missing manifest") from exc
        if manifest.manifest.get("artifact_id") != artifact_id:
            raise IntegrityError("artifact manifest identity does not match its projection")
        return {"artifact": dict(current), "manifest": dict(manifest.manifest)}

    def _hash_artifact(self, source: str | Path) -> tuple[str, int, str]:
        raw = Path(source).expanduser()
        candidate = raw if raw.is_absolute() else self.repo_root / raw
        if candidate.is_symlink():
            raise ValidationError("artifact source must not be a symlink")
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.repo_root).as_posix()
        except ValueError as exc:
            raise ValidationError("artifact source must be inside the project") from exc
        if not resolved.is_file():
            raise ValidationError("artifact source must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        digest = hashlib.sha256()
        try:
            before = os.fstat(descriptor)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if before_identity != after_identity:
                raise IntegrityError("artifact source changed while it was being hashed")
            try:
                path_after = os.stat(resolved, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise IntegrityError(
                    "artifact source path changed while it was being hashed"
                ) from exc
            path_identity = (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_size,
                path_after.st_mtime_ns,
                path_after.st_ctime_ns,
            )
            if path_identity != after_identity:
                raise IntegrityError("artifact source path changed while it was being hashed")
            return digest.hexdigest(), int(after.st_size), relative
        finally:
            os.close(descriptor)

    def _artifact_manifest(
        self,
        artifact_id: str,
        source: str | Path,
        *,
        media_type: str,
        classification: str,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], str]:
        digest, size_bytes, relative = self._hash_artifact(source)
        revision = f"sha256:{digest}"
        manifest = {
            "schema": "commons.manifest.artifact.v1",
            "kind": "artifact",
            "artifact_id": artifact_id,
            "revision": revision,
            "source": {"path": relative},
            "media_type": media_type,
            "size_bytes": size_bytes,
            "classification": classification,
            "captured": False,
            "metadata": dict(metadata or {}),
        }
        self.policy.assert_safe(manifest, context="artifact metadata")
        self.schemas.validate_manifest(manifest)
        return manifest, revision

    def register_artifact(
        self,
        source: str | Path,
        *,
        media_type: str = "application/octet-stream",
        classification: str = "internal",
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("artifact.registered", idempotency_key)
        artifact_id = self._new_entity_id("artifact", "artifact.registered", key)
        manifest, revision = self._artifact_manifest(
            artifact_id,
            source,
            media_type=media_type,
            classification=classification,
            metadata=metadata,
        )
        manifest_id = f"mft.artifact.sha256.{canonical_sha256(manifest)}"
        subject = {"kind": "artifact", "id": artifact_id}
        result = self.record_event(
            "artifact.registered",
            {
                "artifact_id": artifact_id,
                "manifest_ref": manifest_id,
                "revision": revision,
                "classification": classification,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    subject,
                    "uses",
                    {"kind": "manifest", "id": manifest_id},
                ),
            ),
            tags=("artifact",),
            _manifest=manifest,
        )
        return {**result, "manifest_id": manifest_id, "content_copied": False}

    def revise_artifact(
        self,
        artifact_id: str,
        expected_revision: str,
        source: str | Path,
        *,
        media_type: str = "application/octet-stream",
        classification: str = "internal",
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = self._idempotency_key("artifact.revised", idempotency_key)
        self._active_session()
        manifest, revision = self._artifact_manifest(
            artifact_id,
            source,
            media_type=media_type,
            classification=classification,
            metadata=metadata,
        )
        manifest_id = f"mft.artifact.sha256.{canonical_sha256(manifest)}"
        subject = {"kind": "artifact", "id": artifact_id}
        result = self.record_event(
            "artifact.revised",
            {
                "artifact_id": artifact_id,
                "expected_revision": expected_revision,
                "manifest_ref": manifest_id,
                "revision": revision,
                "classification": classification,
            },
            idempotency_key=key,
            relations=(
                self._relation(
                    subject,
                    "uses",
                    {"kind": "manifest", "id": manifest_id},
                ),
            ),
            tags=("artifact",),
            _manifest=manifest,
        )
        return {**result, "manifest_id": manifest_id, "content_copied": False}
