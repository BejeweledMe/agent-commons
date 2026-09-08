"""Verified, context-owned cache for UI canonical reads, never mutation guards."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.errors import IntegrityError
from agent_commons.services.manager import CommonsManager

_MAX_FILES = 10_000
_MAX_BYTES = 64 * 1024 * 1024
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class _BudgetExceeded(Exception):
    """Large sources retain the ordinary uncached read path."""


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    descriptor = os.open(path.anchor, _DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in {".", ".."}:
                raise IntegrityError("Canonical cache source is unsafe.")
            child = os.open(component, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _source_fingerprint(manager: CommonsManager) -> bytes:
    """Hash all bytes the canonical projector reads, with bounded no-follow IO.

    Size/mtime alone cannot prove unchanged content. ctime/inode checks also
    detect ordinary replace/restore races around a replay, even at equal bytes.
    Private sessions, attempts and delivery receipts are deliberately excluded.
    """
    digest = hashlib.sha256()
    files, size = 0, 0

    def stamp(label: str, info: os.stat_result) -> None:
        digest.update(label.encode())
        digest.update(repr(_identity(info)).encode())
        digest.update(b"\0")

    def read_file(folder: int, name: str, label: str) -> None:
        nonlocal files, size
        files += 1
        if files > _MAX_FILES:
            raise _BudgetExceeded
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise IntegrityError("Canonical cache source is not a regular file.")
            if size + before.st_size > _MAX_BYTES:
                raise _BudgetExceeded
            stamp(label, before)
            while chunk := os.read(descriptor, 65_536):
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise _BudgetExceeded
                digest.update(chunk)
            if _identity(os.fstat(descriptor)) != _identity(before):
                raise IntegrityError("Canonical cache source changed while being read.")
        finally:
            os.close(descriptor)

    def walk(folder: int, label: str, depth: int, pattern: str) -> None:
        before = os.fstat(folder)
        stamp(label, before)
        for name in sorted(os.listdir(folder)):
            if depth == 0:
                if fnmatch.fnmatchcase(name, pattern):
                    read_file(folder, name, label + "/" + name)
                continue
            info = os.stat(name, dir_fd=folder, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise IntegrityError("Canonical cache source contains a directory symlink.")
            if not stat.S_ISDIR(info.st_mode):
                continue
            child = os.open(name, _DIRECTORY, dir_fd=folder)
            try:
                walk(child, label + "/" + name, depth - 1, pattern)
            finally:
                os.close(child)
        if _identity(os.fstat(folder)) != _identity(before):
            raise IntegrityError("Canonical cache source changed while being scanned.")

    try:
        with _directory(manager.paths.commons_root) as root:
            before = os.fstat(root)
            # Exclude root mtime: unrelated onboarding/private files are not
            # projection inputs. Keep its identity and mode to bind this root.
            digest.update(repr((before.st_dev, before.st_ino, before.st_mode)).encode())
            read_file(root, "workspace.yaml", "workspace.yaml")
            for name, depth, pattern in (("events", 3, "evt.*.json"), ("manifests", 2, "*.json")):
                try:
                    folder = os.open(name, _DIRECTORY, dir_fd=root)
                except FileNotFoundError:
                    digest.update((name + ":missing").encode())
                    continue
                try:
                    walk(folder, name, depth, pattern)
                finally:
                    os.close(folder)
            if _identity(os.fstat(root)) != _identity(before):
                raise IntegrityError("Canonical cache root changed while being scanned.")
    except OSError as exc:
        raise IntegrityError("Canonical cache source is unavailable or unsafe.") from exc
    return digest.digest()


class UIReadSnapshotCache:
    """One verified snapshot, one load at a time, owned by one UIContext."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scope: tuple[object, ...] | None = None
        self._fingerprint: bytes | None = None
        self._snapshot: ProjectSnapshot | None = None

    def read(self, manager: CommonsManager) -> ProjectSnapshot:
        if not manager.read_only:
            raise IntegrityError("A UI read cache cannot serve a mutation manager.")
        scope = (
            str(manager.repo_root),
            str(manager.paths.commons_root),
            str(manager.paths.state_root),
            manager.workspace_id,
            hashlib.sha256(canonical_json_bytes(manager.workspace_config)).digest(),
        )
        with self._lock:
            try:
                before = _source_fingerprint(manager)
                if (
                    self._scope == scope
                    and self._fingerprint == before
                    and self._snapshot is not None
                ):
                    return deepcopy(self._snapshot)
                self._snapshot = None
                self._fingerprint = None
                snapshot = CommonsManager.snapshot(manager)
                if before != _source_fingerprint(manager):
                    raise IntegrityError("Canonical sources changed during the UI read; retry.")
                self._scope, self._fingerprint = scope, before
                self._snapshot = deepcopy(snapshot)
                return snapshot
            except _BudgetExceeded:
                self._snapshot = None
                self._fingerprint = None
                return CommonsManager.snapshot(manager)
            except Exception:
                self._snapshot = None
                self._fingerprint = None
                raise


class CachedUIReadManager(CommonsManager):
    """UI facade: ordinary mutation managers never receive this cache."""

    def __init__(self, *args: Any, snapshot_cache: UIReadSnapshotCache, **kwargs: Any) -> None:
        kwargs["read_only"] = True
        super().__init__(*args, **kwargs)
        self._ui_snapshot_cache = snapshot_cache

    def snapshot(self) -> ProjectSnapshot:
        return self._ui_snapshot_cache.read(self)

    def _read_snapshot(self, *, fresh: bool = False) -> tuple[ProjectSnapshot, dict[str, Any]]:
        if not fresh:
            return super()._read_snapshot(fresh=False)
        # A caller explicitly asking for fresh bypasses both cache reuse and
        # cache publication. Mutation guards also retain their original path.
        snapshot = CommonsManager.snapshot(self)
        return snapshot, {
            "source": "canonical",
            "reason": "fresh",
            "canonical_content_files_read": len(snapshot.known_event_ids)
            + len(snapshot.known_manifest_ids),
            "index_sync": None,
            "projection": dict(snapshot.replay_metrics),
        }
