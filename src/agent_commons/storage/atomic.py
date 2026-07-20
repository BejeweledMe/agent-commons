"""Atomic no-overwrite publication on a shared filesystem."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent_commons.core.canonical import sha256_bytes
from agent_commons.errors import ImmutableCollisionError


@dataclass(frozen=True)
class AtomicWriteResult:
    path: Path
    created: bool
    sha256: str
    size: int


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_immutable(
    path: str | Path, data: bytes, *, mode: int = 0o644
) -> AtomicWriteResult:
    """Durably publish bytes without replacing an existing final file."""

    if not isinstance(data, bytes):
        raise TypeError("immutable writer requires bytes")
    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(data)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.", suffix=".tmp", dir=final_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        try:
            os.link(temporary_path, final_path, follow_symlinks=False)
        except FileExistsError:
            if final_path.is_symlink() or not final_path.is_file():
                raise ImmutableCollisionError(
                    f"immutable path is not a regular file: {final_path}"
                ) from None
            if final_path.read_bytes() != data:
                raise ImmutableCollisionError(
                    f"immutable path already contains different bytes: {final_path}"
                ) from None
            return AtomicWriteResult(final_path, False, digest, len(data))
        _fsync_directory(final_path.parent)
        return AtomicWriteResult(final_path, True, digest, len(data))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def atomic_write_replace(path: str | Path, data: bytes, *, mode: int = 0o600) -> AtomicWriteResult:
    """Durably replace one mutable operational projection file."""

    if not isinstance(data, bytes):
        raise TypeError("atomic replacement requires bytes")
    final_path = Path(path)
    if final_path.is_symlink():
        raise ImmutableCollisionError(f"mutable path must not be a symlink: {final_path}")
    final_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(data)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.", suffix=".tmp", dir=final_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        if final_path.is_symlink():
            raise ImmutableCollisionError(f"mutable path became a symlink: {final_path}")
        os.replace(temporary_path, final_path)
        _fsync_directory(final_path.parent)
        return AtomicWriteResult(final_path, True, digest, len(data))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def list_stale_temporary_files(root: str | Path) -> list[Path]:
    base = Path(root)
    if not base.exists():
        return []
    return sorted(path for path in base.rglob(".*.tmp") if path.is_file())
