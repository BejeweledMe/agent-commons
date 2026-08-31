"""Shared primitives for private operational state.

The policy constants preserve the stores' historical filesystem behaviour.
They make the remaining differences explicit so hardening can be reviewed as a
separate behavioural change instead of being hidden inside a mechanical move.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_commons.core.canonical import canonical_json_file_bytes, loads_json_strict
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.platform_support import lock_exclusive, unlock

_AUDIT_EVENT_FILE = re.compile(r"^[0-9]{20}-[a-f0-9]{32}\.json$")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_FILE_LOCKS: dict[tuple[str, str], threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class OperationalStoragePolicy:
    """Describe one store's existing directory and locking behaviour."""

    label: str
    reject_directory_symlinks: bool
    process_lock_namespace: str | None
    lock_identity: Literal["resolved", "conditional", "literal"]
    nofollow_lock: bool
    enforce_lock_mode: bool
    legacy_stream_lock: bool = False


SESSION_STORAGE = OperationalStoragePolicy(
    label="session",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
ATTEMPT_STORAGE = OperationalStoragePolicy(
    label="runtime",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
DELEGATION_STORAGE = OperationalStoragePolicy(
    label="runtime delegation",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
COMMUNICATION_STORAGE = OperationalStoragePolicy(
    label="communication",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
CONTEXT_BINDING_STORAGE = OperationalStoragePolicy(
    label="runtime context binding",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
PROVIDER_QUALIFICATION_STORAGE = OperationalStoragePolicy(
    label="provider qualification",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="resolved",
    nofollow_lock=True,
    enforce_lock_mode=True,
)


def iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def canonical_state_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def strict_state_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize newly written operational state as strict canonical JSON."""

    return canonical_json_file_bytes(value)


def ensure_private_directory(path: Path, *, policy: OperationalStoragePolicy) -> None:
    if policy.reject_directory_symlinks and path.is_symlink():
        raise IntegrityError(f"{policy.label} operational directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if policy.reject_directory_symlinks and (path.is_symlink() or not path.is_dir()):
        raise IntegrityError(f"{policy.label} operational path is not a real directory: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _lock_identity(path: Path, policy: OperationalStoragePolicy) -> str:
    if policy.lock_identity == "resolved":
        return str(path.expanduser().resolve())
    if policy.lock_identity == "conditional" and path.parent.exists():
        return str(path.expanduser().resolve())
    return str(path)


@contextmanager
def exclusive_lock(path: Path, *, policy: OperationalStoragePolicy) -> Iterator[None]:
    """Hold the store's historical process-local and filesystem locks."""

    process_lock: threading.Lock | None = None
    if policy.process_lock_namespace is not None:
        identity = _lock_identity(path, policy)
        key = (policy.process_lock_namespace, identity)
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_FILE_LOCKS.setdefault(key, threading.Lock())
    with process_lock if process_lock is not None else nullcontext():
        ensure_private_directory(path.parent, policy=policy)
        if policy.legacy_stream_lock:
            with open(
                path,
                "a+b",
                opener=lambda name, flags: os.open(name, flags, 0o600),
            ) as handle:
                lock_exclusive(handle.fileno())
                try:
                    yield
                finally:
                    unlock(handle.fileno())
            return

        if path.is_symlink():
            raise IntegrityError(f"{policy.label} operational lock must not be a symlink: {path}")
        flags = os.O_RDWR | os.O_CREAT
        if policy.nofollow_lock:
            flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise IntegrityError(
                    f"{policy.label} operational lock must not be a symlink: {path}"
                ) from exc
            raise
        try:
            if policy.enforce_lock_mode:
                os.fchmod(descriptor, 0o600)
            lock_exclusive(descriptor)
            yield
        finally:
            unlock(descriptor)
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_audit_event(
    path: Path,
    body: Mapping[str, Any],
    *,
    policy: OperationalStoragePolicy,
) -> None:
    """Publish one immutable audit event with no overwrite semantics."""

    ensure_private_directory(path.parent, policy=policy)
    data = strict_state_bytes(body)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise IntegrityError("operational audit event path collision") from exc
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def read_audit_event(path: Path, *, schema: str, label: str) -> dict[str, Any]:
    """Read one canonical regular-file audit event without following symlinks."""

    if _AUDIT_EVENT_FILE.fullmatch(path.name) is None or path.is_symlink():
        raise IntegrityError(f"{label} audit event has an unsafe path")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise IntegrityError(f"{label} audit event is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            raw = handle.read()
        descriptor = -1
        value = loads_json_strict(raw)
    except (OSError, ValidationError) as exc:
        raise IntegrityError(f"{label} audit event is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IntegrityError(f"{label} audit event has an invalid envelope")
    if raw != strict_state_bytes(value):
        raise IntegrityError(f"{label} audit event is not canonical JSON")
    return value


def next_audit_event_path(event_root: Path) -> Path:
    maximum = 0
    for path in event_root.glob("*.json"):
        if _AUDIT_EVENT_FILE.fullmatch(path.name) is None:
            raise IntegrityError("unexpected operational audit event filename")
        try:
            maximum = max(maximum, int(path.name.split("-", 1)[0]))
        except (ValueError, IndexError):
            raise IntegrityError("unexpected operational audit event filename") from None
    return event_root / f"{maximum + 1:020d}-{uuid.uuid4().hex}.json"
