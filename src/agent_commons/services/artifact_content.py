"""Verified, current-image reads for workspace artifact previews.

Artifact manifests deliberately retain metadata and a source reference rather
than copying content into the ledger.  This module is the narrow read boundary
for the first safe visual preview: it resolves that reference below the
workspace root without following symlinks, then proves the still-current bytes
match the projected manifest before returning them.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

from agent_commons.errors import CommonsError, IntegrityError, LifecycleConflictError
from agent_commons.services.manager import CommonsManager

MAX_PREVIEW_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_PIXELS = 16_000_000

PreviewMediaType = Literal["image/jpeg", "image/png"]
PreviewRefusalCode = Literal[
    "artifact_preview_invalid_id",
    "artifact_preview_not_found",
    "artifact_preview_manifest_invalid",
    "artifact_preview_classification_blocked",
    "artifact_preview_unsupported_media_type",
    "artifact_preview_missing_source",
    "artifact_preview_symlink_source",
    "artifact_preview_non_regular_source",
    "artifact_preview_stale_source",
    "artifact_preview_oversize",
    "artifact_preview_invalid_image",
    "artifact_preview_pixel_limit",
]


class ArtifactRecord(TypedDict):
    """The projection fields the preview reader requires from an artifact."""

    id: str
    content_revision: str
    classification: str


class ArtifactSource(TypedDict):
    """A repository-relative path preserved in an artifact manifest."""

    path: str


class ArtifactManifest(TypedDict):
    """The manifest fields that bind visible pixels to an artifact revision."""

    artifact_id: str
    revision: str
    source: ArtifactSource
    media_type: str
    size_bytes: int
    classification: str


class ArtifactBundle(TypedDict):
    """Typed in-memory view over the pre-existing manager artifact bundle."""

    artifact: ArtifactRecord
    manifest: ArtifactManifest


@dataclass(frozen=True)
class ArtifactPreview:
    """Verified image bytes and metadata safe for an authenticated response."""

    artifact_id: str
    revision: str
    media_type: PreviewMediaType
    content: bytes
    width: int
    height: int


@dataclass(frozen=True)
class _FileIdentity:
    """Stable identity facts used to reject replacement while a file is read."""

    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _FileIdentity:
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
        )


class ArtifactPreviewRefusal(CommonsError):
    """A closed, filesystem-safe explanation for refusing a preview request."""

    def __init__(self, code: PreviewRefusalCode, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ArtifactPreviewReader:
    """Read the current manifest-bound PNG or JPEG bytes for one artifact."""

    def __init__(
        self,
        manager: CommonsManager,
        *,
        max_bytes: int = MAX_PREVIEW_BYTES,
        max_pixels: int = MAX_PREVIEW_PIXELS,
    ) -> None:
        if max_bytes < 1 or max_pixels < 1:
            raise ValueError("preview limits must be positive")
        self._manager = manager
        self._root = manager.repo_root.resolve()
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels

    def read(self, artifact_id: str) -> ArtifactPreview:
        """Return verified pixels only when the current source still matches its manifest."""

        _validate_artifact_id(artifact_id)
        bundle = self._load_bundle(artifact_id)
        artifact = bundle["artifact"]
        manifest = bundle["manifest"]
        self._validate_binding(artifact_id, artifact, manifest)
        self._validate_classification(artifact, manifest)
        media_type = _preview_media_type(manifest["media_type"])
        expected_digest = _revision_digest(manifest["revision"])
        expected_size = manifest["size_bytes"]
        if expected_size > self._max_bytes:
            _refuse(
                "artifact_preview_oversize",
                413,
                "artifact preview exceeds the configured byte limit",
            )
        relative = _relative_source(manifest["source"]["path"])
        content = self._read_verified_source(relative, expected_size, expected_digest)
        width, height = _image_dimensions(content, media_type)
        if width * height > self._max_pixels:
            _refuse(
                "artifact_preview_pixel_limit",
                413,
                "artifact preview exceeds the configured pixel limit",
            )
        return ArtifactPreview(
            artifact_id=artifact_id,
            revision=manifest["revision"],
            media_type=media_type,
            content=content,
            width=width,
            height=height,
        )

    def _load_bundle(self, artifact_id: str) -> ArtifactBundle:
        try:
            raw_bundle = self._manager.get_artifact_bundle(artifact_id)
        except LifecycleConflictError:
            _refuse("artifact_preview_not_found", 404, "artifact does not exist")
        except IntegrityError:
            _refuse(
                "artifact_preview_manifest_invalid",
                409,
                "artifact manifest cannot be verified",
            )
        return _parse_bundle(raw_bundle)

    def _validate_binding(
        self,
        artifact_id: str,
        artifact: ArtifactRecord,
        manifest: ArtifactManifest,
    ) -> None:
        if artifact["id"] != artifact_id or manifest["artifact_id"] != artifact_id:
            _refuse(
                "artifact_preview_manifest_invalid",
                409,
                "artifact manifest identity does not match the current projection",
            )
        if artifact["content_revision"] != manifest["revision"]:
            _refuse(
                "artifact_preview_manifest_invalid",
                409,
                "artifact manifest revision does not match the current projection",
            )

    def _validate_classification(
        self,
        artifact: ArtifactRecord,
        manifest: ArtifactManifest,
    ) -> None:
        if artifact["classification"] != manifest["classification"]:
            _refuse(
                "artifact_preview_manifest_invalid",
                409,
                "artifact manifest classification does not match the current projection",
            )
        if manifest["classification"] not in {"public", "internal"}:
            _refuse(
                "artifact_preview_classification_blocked",
                403,
                "artifact classification is not eligible for preview",
            )

    def _read_verified_source(
        self,
        relative: Path,
        expected_size: int,
        expected_digest: str,
    ) -> bytes:
        descriptor = self._open_regular(relative)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                _refuse(
                    "artifact_preview_non_regular_source",
                    409,
                    "artifact preview source is not a regular file",
                )
            identity = _FileIdentity.from_stat(before)
            if identity.size > self._max_bytes:
                _refuse(
                    "artifact_preview_oversize",
                    413,
                    "artifact preview exceeds the configured byte limit",
                )
            if identity.size != expected_size:
                _refuse(
                    "artifact_preview_stale_source",
                    409,
                    "artifact preview source no longer matches its manifest",
                )
            chunks: list[bytes] = []
            remaining = expected_size + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if _FileIdentity.from_stat(os.fstat(descriptor)) != identity:
                _refuse(
                    "artifact_preview_stale_source",
                    409,
                    "artifact preview source changed while it was read",
                )
        finally:
            os.close(descriptor)
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_digest:
            _refuse(
                "artifact_preview_stale_source",
                409,
                "artifact preview source no longer matches its manifest",
            )
        self._assert_path_identity(relative, identity)
        return content

    def _assert_path_identity(self, relative: Path, expected: _FileIdentity) -> None:
        descriptor = self._open_regular(relative)
        try:
            current = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if _FileIdentity.from_stat(current) != expected:
            _refuse(
                "artifact_preview_stale_source",
                409,
                "artifact preview source changed while it was read",
            )

    def _open_regular(self, relative: Path) -> int:
        if (
            not relative.parts
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd
        ):
            _refuse(
                "artifact_preview_non_regular_source",
                409,
                "artifact preview source cannot be opened safely",
            )
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        directories: list[int] = []
        try:
            current = os.open(self._root, directory_flags)
            directories.append(current)
            for component in relative.parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                directories.append(current)
            return os.open(relative.parts[-1], file_flags, dir_fd=current)
        except FileNotFoundError:
            _refuse(
                "artifact_preview_missing_source",
                409,
                "artifact preview source is no longer available",
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                _refuse(
                    "artifact_preview_symlink_source",
                    409,
                    "artifact preview source must not traverse symlinks",
                )
            _refuse(
                "artifact_preview_non_regular_source",
                409,
                "artifact preview source cannot be opened safely",
            )
        finally:
            for descriptor in reversed(directories):
                os.close(descriptor)


def _validate_artifact_id(artifact_id: str) -> None:
    if not artifact_id.startswith("artifact.") or len(artifact_id) < 10:
        _refuse("artifact_preview_invalid_id", 400, "artifact id is malformed")


def _parse_bundle(value: object) -> ArtifactBundle:
    if not isinstance(value, Mapping):
        _invalid_manifest()
    artifact = _parse_artifact_record(value.get("artifact"))
    manifest = _parse_manifest(value.get("manifest"))
    return {"artifact": artifact, "manifest": manifest}


def _parse_artifact_record(value: object) -> ArtifactRecord:
    mapping = _mapping(value)
    return {
        "id": _string(mapping, "id"),
        "content_revision": _string(mapping, "content_revision"),
        "classification": _string(mapping, "classification"),
    }


def _parse_manifest(value: object) -> ArtifactManifest:
    mapping = _mapping(value)
    source = _mapping(mapping.get("source"))
    size = mapping.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        _invalid_manifest()
    return {
        "artifact_id": _string(mapping, "artifact_id"),
        "revision": _string(mapping, "revision"),
        "source": {"path": _string(source, "path")},
        "media_type": _string(mapping, "media_type"),
        "size_bytes": size,
        "classification": _string(mapping, "classification"),
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _invalid_manifest()
    return cast(Mapping[str, object], value)


def _string(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        _invalid_manifest()
    return value


def _invalid_manifest() -> None:
    _refuse(
        "artifact_preview_manifest_invalid",
        409,
        "artifact manifest cannot be verified",
    )


def _revision_digest(revision: str) -> str:
    if not revision.startswith("sha256:"):
        _invalid_manifest()
    digest = revision.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        _invalid_manifest()
    return digest


def _relative_source(value: str) -> Path:
    candidate = Path(value)
    if not candidate.parts or candidate.is_absolute() or ".." in candidate.parts:
        _invalid_manifest()
    return candidate


def _preview_media_type(value: str) -> PreviewMediaType:
    if value not in {"image/jpeg", "image/png"}:
        _refuse(
            "artifact_preview_unsupported_media_type",
            415,
            "only PNG and JPEG artifacts can be previewed",
        )
    return cast(PreviewMediaType, value)


def _image_dimensions(content: bytes, media_type: PreviewMediaType) -> tuple[int, int]:
    if media_type == "image/png":
        return _png_dimensions(content)
    return _jpeg_dimensions(content)


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
        _invalid_image()
    width = int.from_bytes(content[16:20], "big")
    height = int.from_bytes(content[20:24], "big")
    if width < 1 or height < 1:
        _invalid_image()
    return width, height


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 4 or content[:3] != b"\xff\xd8\xff":
        _invalid_image()
    position = 2
    while position < len(content):
        if content[position] != 0xFF:
            _invalid_image()
        while position < len(content) and content[position] == 0xFF:
            position += 1
        if position >= len(content):
            _invalid_image()
        marker = content[position]
        position += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(content):
            _invalid_image()
        segment_size = int.from_bytes(content[position : position + 2], "big")
        if segment_size < 2 or position + segment_size > len(content):
            _invalid_image()
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_size < 8:
                _invalid_image()
            height = int.from_bytes(content[position + 3 : position + 5], "big")
            width = int.from_bytes(content[position + 5 : position + 7], "big")
            if width < 1 or height < 1:
                _invalid_image()
            return width, height
        position += segment_size
    _invalid_image()


def _invalid_image() -> None:
    _refuse(
        "artifact_preview_invalid_image",
        422,
        "artifact bytes do not match the declared PNG or JPEG image type",
    )


def _refuse(code: PreviewRefusalCode, status_code: int, message: str) -> None:
    raise ArtifactPreviewRefusal(code, status_code, message)
