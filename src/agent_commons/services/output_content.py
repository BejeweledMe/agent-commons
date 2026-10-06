"""Bounded local, immutable output bytes; canonical manifests remain metadata.

No automatic eviction: quota refusal preserves every previously published version.
Unreferenced content from an interrupted publication remains charged to the quota
and an identical retry reuses it. Back up this directory with the workspace state.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import stat
import threading
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from agent_commons.errors import IntegrityError, ValidationError

MAX_STORE_BYTES = 256 * 1024 * 1024
MAX_STORE_OBJECTS = 512
MAX_BUILD_BYTES = 20 * 1024 * 1024
MAX_BUILD_FILES = 128
BUILD_KIND = "task_static_build"
BUILD_MEDIA = "application/zip"
_SUFFIXES = {
    ".html",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".txt",
    ".webp",
}


_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class OutputContentStore:
    def __init__(self, manager):
        self.root = manager.paths.state_root / "output-content"

    def _directory(self, *, create: bool = False) -> int:
        if create:
            self.root.mkdir(mode=0o700, exist_ok=True)
        return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    @contextmanager
    def _write_lock(self):
        with _STORE_LOCKS_GUARD:
            lock = _STORE_LOCKS.setdefault(str(self.root), threading.RLock())
        with lock:
            directory = self._directory(create=True)
            descriptor = None
            try:
                descriptor = os.open(
                    "store.lock",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                    dir_fd=directory,
                )
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise IntegrityError("Unsafe retained content lock")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(directory)

    def put(self, content: bytes) -> dict[str, object]:
        with self._write_lock():
            return self._put_locked(content)

    def _put_locked(self, content: bytes) -> dict[str, object]:
        if not 1 <= len(content) <= MAX_BUILD_BYTES:
            raise ValidationError("Output exceeds retained content bounds")
        digest = hashlib.sha256(content).hexdigest()
        directory = self._directory(create=True)
        temporary = "partial-" + digest + "-" + uuid.uuid4().hex
        try:
            try:
                self._recover_linked_partial(directory, digest, len(content))
                if self._read(directory, digest, len(content)) == content:
                    return {"revision": "sha256:" + digest, "size_bytes": len(content)}
            except FileNotFoundError:
                pass
            names = [name for name in os.listdir(directory) if name != "store.lock"]
            size = 0
            for name in names:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise IntegrityError("Unsafe retained content store")
                size += info.st_size
            if len(names) >= MAX_STORE_OBJECTS or size + len(content) > MAX_STORE_BYTES:
                raise ValidationError("Retained output storage quota exceeded")
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(
                    temporary,
                    digest,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            return {"revision": "sha256:" + digest, "size_bytes": len(content)}
        finally:
            os.close(directory)

    def _recover_linked_partial(self, directory: int, digest: str, size: int) -> None:
        """Identical retry repairs only its interrupted atomic-link publication."""
        current = os.stat(digest, dir_fd=directory, follow_symlinks=False)
        if current.st_nlink == 1:
            return
        # Verify first; never delete another content identity to make a retry pass.
        self._read(directory, digest, size, allow_links=True)
        for name in os.listdir(directory):
            if re.fullmatch(r"partial-" + digest + r"-[0-9a-f]{32}", name) is None:
                continue
            partial = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (partial.st_dev, partial.st_ino) == (current.st_dev, current.st_ino):
                os.unlink(name, dir_fd=directory)
        os.fsync(directory)

    def read(self, revision: str, size: int) -> bytes:
        if (
            not isinstance(revision, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", revision)
            or type(size) is not int
            or not 1 <= size <= MAX_BUILD_BYTES
        ):
            raise IntegrityError("Invalid retained content binding")
        directory = self._directory()
        try:
            return self._read(directory, revision[7:], size)
        finally:
            os.close(directory)

    @staticmethod
    def _read(directory: int, digest: str, size: int, *, allow_links: bool = False) -> bytes:
        fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or (before.st_nlink != 1 and not allow_links)
                or before.st_size != size
            ):
                raise IntegrityError("Retained content is unavailable")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read(size + 1)
            after = os.fstat(fd)
            if (
                (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(content) != size
                or hashlib.sha256(content).hexdigest() != digest
            ):
                raise IntegrityError("Retained content failed verification")
            return content
        finally:
            os.close(fd)


def inspect_static_build(manager, source_path: str) -> tuple[bytes, list[dict[str, object]]]:
    """Capture an allowlisted dist directory into a deterministic download-only ZIP.

    Directory and file descriptors never follow symlinks. HTML is never served on
    the Commons origin. Hidden files, special files, hardlinks and escapes fail.
    """
    from agent_commons.services.artifact_content import ArtifactPreviewReader

    relative = Path(source_path)
    if (
        not isinstance(source_path, str)
        or not relative.parts
        or relative.is_absolute()
        or relative.as_posix() != source_path
        or any(ord(c) < 32 or ord(c) == 127 for c in source_path)
        or "\\" in source_path
        or len(source_path) > 1024
        or any(part.startswith(".") or part == "node_modules" for part in relative.parts)
    ):
        raise ValidationError("Invalid static build source")
    reader = ArtifactPreviewReader(manager, max_bytes=MAX_BUILD_BYTES)
    files: list[tuple[str, bytes]] = []
    total = 0
    directories = 0

    def walk(directory: int, path: Path):
        nonlocal total, directories
        directories += 1
        before = os.fstat(directory)
        if directories > 128 or len(path.parts) > 16:
            raise ValidationError("Static build directory bounds exceeded")
        for name in sorted(os.listdir(directory)):
            if (
                name == "commons-build-manifest.json"
                or name.startswith(".")
                or name == "node_modules"
                or "\\" in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
            ):
                raise ValidationError("Unsafe static build entry")
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            child = path / name
            if len(child.as_posix()) > 1024:
                raise ValidationError("Static build path exceeds bounds")
            if stat.S_ISDIR(info.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    walk(fd, child)
                finally:
                    os.close(fd)
            elif (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and child.suffix.lower() in _SUFFIXES
            ):
                total += info.st_size
                if (
                    len(files) >= MAX_BUILD_FILES
                    or total > MAX_BUILD_BYTES
                    or info.st_size > 10 * 1024 * 1024
                ):
                    raise ValidationError("Static build exceeds bounds")
                content = reader._read_verified_source(
                    relative / child, info.st_size, _file_digest(directory, name), single_link=True
                )
                files.append((child.as_posix(), content))
            else:
                raise ValidationError("Unsafe static build entry")
        after = os.fstat(directory)
        if (before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise IntegrityError("Static build directory changed while capturing")

    root_fd = reader._open_regular(relative)
    try:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise ValidationError("Static build source is not a directory")
        walk(root_fd, Path())
    finally:
        os.close(root_fd)
    for name, captured in files:
        reader._read_verified_source(
            relative / name, len(captured), hashlib.sha256(captured).hexdigest(), single_link=True
        )
    if not any(name == "index.html" for name, _ in files):
        raise ValidationError("Static build requires index.html")
    manifest = [
        {
            "path": name,
            "revision": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
        for name, content in files
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            zipfile.ZipInfo("commons-build-manifest.json"),
            json.dumps(
                {"schema": "agent_commons.static-build.v1", "files": manifest}, sort_keys=True
            ),
        )
        for name, content in files:
            archive.writestr(zipfile.ZipInfo(name), content)
    content = buffer.getvalue()
    if len(content) > MAX_BUILD_BYTES:
        raise ValidationError("Static build bundle exceeds bounds")
    return content, manifest


def _file_digest(directory: int, name: str) -> str:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        digest = hashlib.sha256()
        remaining = 10 * 1024 * 1024 + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                return digest.hexdigest()
            remaining -= len(chunk)
            digest.update(chunk)
        raise ValidationError("Static build entry exceeds bounds")
    finally:
        os.close(fd)
