"""Read and verify the two packaged Starter Pack mock examples."""

from __future__ import annotations

import hashlib
import os
import stat
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Protocol

from .manifest import (
    MAX_MANIFEST_BYTES,
    MAX_PACK_BYTES,
    StarterPackManifest,
    StarterPackValidationError,
    parse_manifest_bytes,
    parse_registry_bytes,
)

_REGISTRY_NAME = "registry.json"


class _ResourceRoot(Protocol):
    def joinpath(self, *descendants: str) -> _ResourceRoot: ...

    def is_file(self) -> bool: ...

    def read_bytes(self) -> bytes: ...


def list_bundled_packs() -> tuple[StarterPackManifest, ...]:
    """Return both packaged mock packs after validating all declared bytes.

    This is a read-only operation: it opens only packaged resources and does
    not access the network, project workspace, operator catalogue, or ledger.
    """

    root = resources.files("agent_commons.resources").joinpath("starter_packs")
    if isinstance(root, Path):
        return load_bundled_packs_from_directory(root)
    return _load_from_resource_root(root)


def get_bundled_pack(pack_id: str) -> StarterPackManifest:
    """Return one verified mock pack or fail closed for an unknown ID."""

    for pack in list_bundled_packs():
        if pack.id == pack_id:
            return pack
    raise StarterPackValidationError("starter_pack_not_found")


def load_bundled_packs_from_directory(root: Path) -> tuple[StarterPackManifest, ...]:
    """Load a package-resource directory with symlink-safe regular-file reads.

    The public helper keeps the byte-verification seam testable for source
    checkouts and future package extraction.  It neither writes nor follows a
    symlink, including a symlinked intermediate directory component.
    """

    registry = _read_regular_from_directory(root, _REGISTRY_NAME, MAX_MANIFEST_BYTES)
    names = parse_registry_bytes(registry)
    manifests = tuple(
        parse_manifest_bytes(_read_regular_from_directory(root, name, MAX_MANIFEST_BYTES))
        for name in names
    )
    _validate_pack_set(manifests, names)
    for manifest in manifests:
        for payload in manifest.files:
            raw = _read_regular_from_directory(root, payload.path, MAX_PACK_BYTES)
            _validate_payload(raw, payload.sha256, payload.size)
    return manifests


def _load_from_resource_root(root: _ResourceRoot) -> tuple[StarterPackManifest, ...]:
    names = parse_registry_bytes(_read_resource(root, _REGISTRY_NAME, MAX_MANIFEST_BYTES))
    manifests = tuple(
        parse_manifest_bytes(_read_resource(root, name, MAX_MANIFEST_BYTES)) for name in names
    )
    _validate_pack_set(manifests, names)
    for manifest in manifests:
        for payload in manifest.files:
            _validate_payload(
                _read_resource(root, payload.path, MAX_PACK_BYTES), payload.sha256, payload.size
            )
    return manifests


def _validate_pack_set(manifests: tuple[StarterPackManifest, ...], names: tuple[str, ...]) -> None:
    if len(manifests) != 2 or len({manifest.id for manifest in manifests}) != 2:
        raise StarterPackValidationError("starter_pack_duplicate_id")
    for manifest, name in zip(manifests, names, strict=True):
        if manifest.source_resource != name:
            raise StarterPackValidationError("starter_pack_manifest_invalid")


def _read_resource(root: _ResourceRoot, relative_path: str, limit: int) -> bytes:
    resource = root
    for part in PurePosixPath(relative_path).parts:
        resource = resource.joinpath(part)
    if not resource.is_file():
        raise StarterPackValidationError("starter_pack_resource_missing")
    try:
        raw = resource.read_bytes()
    except (OSError, ModuleNotFoundError) as exc:
        raise StarterPackValidationError("starter_pack_resource_missing") from exc
    if len(raw) > limit:
        raise StarterPackValidationError("starter_pack_payload_too_large")
    return raw


def _read_regular_from_directory(root: Path, relative_path: str, limit: int) -> bytes:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise StarterPackValidationError("starter_pack_payload_path_invalid")
    if root.is_symlink() or not root.is_dir():
        raise StarterPackValidationError("starter_pack_resource_missing")
    descriptor = -1
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        for part in relative.parts[:-1]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StarterPackValidationError("starter_pack_payload_symlink")
            with os.fdopen(file_descriptor, "rb", closefd=True) as handle:
                file_descriptor = -1
                raw = handle.read(limit + 1)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
    except StarterPackValidationError:
        raise
    except OSError as exc:
        raise StarterPackValidationError("starter_pack_payload_symlink") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > limit:
        raise StarterPackValidationError("starter_pack_payload_too_large")
    return raw


def _validate_payload(raw: bytes, expected_sha256: str, expected_size: int) -> None:
    if len(raw) != expected_size:
        raise StarterPackValidationError("starter_pack_payload_hash_mismatch")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise StarterPackValidationError("starter_pack_payload_hash_mismatch")
