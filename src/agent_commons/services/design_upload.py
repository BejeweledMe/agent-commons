"""Explicit image import for Gallery, with canonical metadata-only provenance."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
import uuid
from typing import Any

from agent_commons.errors import ValidationError
from agent_commons.services.artifact_content import (
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_PIXELS,
    ArtifactPreviewRefusal,
    _image_dimensions,
)
from agent_commons.services.design_authoring import _candidate_id

UPLOAD_SCHEMA = "agent_commons.gallery-import.v1"


class GalleryImportInputError(ValidationError):
    """Invalid image input, rejected before this request can write anything."""


def _validated(body: dict[str, Any]) -> tuple[str, str, str, bytes]:
    if set(body) != {"title", "media_type", "content_base64", "idempotency_key"}:
        raise ValidationError("image import has unsupported or missing fields")
    title, key = body["title"], body["idempotency_key"]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 256:
        raise ValidationError("image title must contain 1 to 256 characters")
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValidationError("image import requires a bounded idempotency key")
    media_type = body["media_type"]
    if type(media_type) is not str or media_type not in {"image/png", "image/jpeg"}:
        raise ValidationError("image import supports PNG and JPEG only")
    encoded = body["content_base64"]
    if not isinstance(encoded, str) or len(encoded) > (MAX_PREVIEW_BYTES + 2) // 3 * 4:
        raise ValidationError("image import exceeds the 10 MiB limit")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("image import is not valid base64") from exc
    if not content or len(content) > MAX_PREVIEW_BYTES:
        raise ValidationError("image import exceeds the 10 MiB limit")
    try:
        width, height = _image_dimensions(content, media_type)
    except ArtifactPreviewRefusal as exc:
        raise ValidationError("image import does not contain a supported image") from exc
    if width * height > MAX_PREVIEW_PIXELS:
        raise ValidationError("image import exceeds the 16 megapixel limit")
    return title.strip(), key, media_type, content


def _store_source(manager: Any, content: bytes, media_type: str) -> Any:
    """Write a fixed content-addressed source path without following links."""

    digest = hashlib.sha256(content).hexdigest()
    filename = digest + (".png" if media_type == "image/png" else ".jpg")
    root_fd = os.open(manager.repo_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    folder_fd = -1
    file_fd = -1
    temporary = ".import-" + uuid.uuid4().hex
    try:
        try:
            os.mkdir("design-assets", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        folder_fd = os.open(
            "design-assets", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        try:
            file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder_fd)
        except FileNotFoundError:
            file_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=folder_fd,
            )
            with os.fdopen(file_fd, "wb") as stream:
                file_fd = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Publish only a complete file; a killed writer cannot leave a
            # partial file under the stable name or overwrite another source.
            os.link(
                temporary,
                filename,
                src_dir_fd=folder_fd,
                dst_dir_fd=folder_fd,
                follow_symlinks=False,
            )
            os.fsync(folder_fd)
        else:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != len(content):
                raise ValidationError("existing imported image does not match its content")
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = -1
                if stream.read(MAX_PREVIEW_BYTES + 1) != content:
                    raise ValidationError("existing imported image does not match its content")
    except OSError as exc:
        raise ValidationError("the project image storage is unavailable or unsafe") from exc
    finally:
        if folder_fd >= 0:
            try:
                os.unlink(temporary, dir_fd=folder_fd)
            except FileNotFoundError:
                pass
        for descriptor in (file_fd, folder_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)
    return manager.repo_root / "design-assets" / filename


def import_screen(manager: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Import one user-selected screen; no model execution or task acceptance.

    The visible import task records that image ingestion finished, not that a
    design was approved. Every canonical step has a deterministic retry key.
    Earlier successful steps remain honest and retrievable after interruption.
    """

    try:
        title, key, media_type, content = _validated(body)
    except ValidationError as exc:
        raise GalleryImportInputError("Image input is invalid") from exc
    if not manager.design_package_writes_enabled:
        raise ValidationError("Design Package publishing is disabled")
    manager.policy.assert_safe({"title": title}, context="image import metadata")
    digest = hashlib.sha256(content).hexdigest()
    prefix = "gallery-import-" + hashlib.sha256(key.encode()).hexdigest()
    with manager._canonical_write_lock():
        # The first event binds image digest and title before filesystem writes.
        # A changed retry fails here, leaving the original import intact.
        task = manager.create_task(
            title=f"Import screen: {title}"[:256],
            description=(
                f"Import an operator-selected image. Image SHA256: {digest}. Title: {title}"
            ),
            acceptance_criteria=("The exact PNG/JPEG is registered for Gallery preview.",),
            idempotency_key=prefix + ":task",
        )
        source = _store_source(manager, content, media_type)
        artifact = manager.register_artifact(
            source,
            media_type=media_type,
            classification="internal",
            metadata={"title": title, "origin": "operator_image_import"},
            expected_revision=f"sha256:{digest}",
            expected_size=len(content),
            idempotency_key=prefix + ":artifact",
        )
        task_id = str(task["entity_ref"]["id"])
        taken = manager.take_task(task_id, str(task["revision"]), idempotency_key=prefix + ":take")
        started = manager.start_task(
            task_id, str(taken["revision"]), idempotency_key=prefix + ":start"
        )
        completed = manager.complete_task(
            task_id,
            str(started["revision"]),
            summary="The operator-selected image was imported; design approval remains separate.",
            artifact_refs=(artifact["entity_ref"],),
            idempotency_key=prefix + ":complete",
        )
    artifact_id = str(artifact["entity_ref"]["id"])
    return {
        "schema": UPLOAD_SCHEMA,
        "state": "imported",
        "artifact_id": artifact_id,
        "task_id": task_id,
        "candidate_id": _candidate_id(
            artifact_id, str(artifact["revision"]), task_id, str(completed["revision"])
        ),
    }
