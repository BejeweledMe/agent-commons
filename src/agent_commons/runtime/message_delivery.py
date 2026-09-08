"""Private metadata receipts: fetched/acknowledged never means reasoning completed."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.platform_support import lock_exclusive, require_supported_platform, unlock
from agent_commons.storage.opstate import ATTEMPT_STORAGE, strict_state_bytes

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_ATTACHMENT = re.compile(r"attachment\.[A-Za-z0-9_-]{16,64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")
_SCHEMA = "agent_commons.message-delivery.v1"
_MAX_BYTES = 16_384
_PROCESS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[tuple[str | None, int, int], threading.Lock] = {}


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValidationError("Message delivery identity is invalid.")
    return value


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise IntegrityError("Message delivery timestamp is invalid.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrityError("Message delivery timestamp is invalid.") from exc


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Pin every component before advancing; never resolve through a symlink."""
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise IntegrityError("Message delivery storage is unsafe.")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid():
            raise IntegrityError("Message delivery storage is unsafe.")
        if create:
            os.fchmod(descriptor, 0o700)
        elif info.st_mode & 0o077:
            raise IntegrityError("Message delivery storage is not private.")
        yield descriptor
    except OSError as exc:
        raise IntegrityError("Message delivery storage is unavailable or unsafe.") from exc
    finally:
        os.close(descriptor)


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()


class MessageDeliveryStore:
    def __init__(self, state_root: Path, workspace_id: str) -> None:
        require_supported_platform()
        self.root = Path(state_root).expanduser().absolute() / "runtime" / "message-delivery"
        self.workspace_id = _identifier(workspace_id)
        with _directory(self.root, create=True) as folder:
            info = os.fstat(folder)
            self._root_identity = (info.st_dev, info.st_ino)

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with _directory(self.root) as folder:
            info = os.fstat(folder)
            identity = (info.st_dev, info.st_ino)
            if identity != self._root_identity:
                raise IntegrityError("Message delivery storage changed.")
            key = (ATTEMPT_STORAGE.process_lock_namespace, *identity)
            with _PROCESS_GUARD:
                process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())
            with process_lock:
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                try:
                    descriptor = os.open(
                        "receipts.lock", flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=folder
                    )
                except FileExistsError:
                    # Concurrent first writers must not use the shared
                    # create-or-open path. Never recreate a vanished lock or
                    # retry other errors against a potentially changed root.
                    descriptor = os.open("receipts.lock", flags, dir_fd=folder)
                try:
                    info = os.fstat(descriptor)
                    if not _regular(info):
                        raise IntegrityError("Message delivery lock is unsafe.")
                    os.fchmod(descriptor, 0o600)
                    lock_exclusive(descriptor)
                    try:
                        current = os.stat("receipts.lock", dir_fd=folder, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                            raise IntegrityError("Message delivery lock changed.")
                        yield folder
                    finally:
                        unlock(descriptor)
                finally:
                    os.close(descriptor)

    def _identity(self, thread_id: str, message_id: str, delegation_id: str) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "thread_id": _identifier(thread_id),
            "message_id": _identifier(message_id),
            "delegation_id": _identifier(delegation_id),
        }

    @staticmethod
    def _name(identity: dict[str, str]) -> str:
        return hashlib.sha256(strict_state_bytes(identity)).hexdigest() + ".json"

    @staticmethod
    def _validate(value: Any, identity: dict[str, str]) -> dict[str, Any]:
        expected = {*identity, "schema", "fetched_at", "acknowledged_at", "attachments"}
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or any(value.get(k) != v for k, v in identity.items())
            or value.get("schema") != _SCHEMA
            or not isinstance(value.get("attachments"), dict)
            or len(value["attachments"]) > 20
        ):
            raise IntegrityError("Message delivery receipt is invalid.")
        fetched, acknowledged = (
            _timestamp(value["fetched_at"]),
            _timestamp(value["acknowledged_at"]),
        )
        if acknowledged is not None and (fetched is None or acknowledged < fetched):
            raise IntegrityError("Message delivery acknowledgment has no preceding fetch.")
        for attachment_id, timestamp in value["attachments"].items():
            if (
                not isinstance(attachment_id, str)
                or not _ATTACHMENT.fullmatch(attachment_id)
                or _timestamp(timestamp) is None
            ):
                raise IntegrityError("Message delivery attachment receipt is invalid.")
        return value

    def _read(self, folder: int, identity: dict[str, str]) -> dict[str, Any]:
        try:
            descriptor = os.open(
                self._name(identity), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder
            )
        except FileNotFoundError:
            return {
                **identity,
                "schema": _SCHEMA,
                "fetched_at": None,
                "acknowledged_at": None,
                "attachments": {},
            }
        try:
            info = os.fstat(descriptor)
            if not _regular(info) or info.st_mode & 0o077 or not 1 <= info.st_size <= _MAX_BYTES:
                raise IntegrityError("Message delivery receipt is invalid.")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise IntegrityError("Message delivery receipt is too large.")
            try:
                value = loads_json_strict(data)
            except (ValidationError, RecursionError) as exc:
                raise IntegrityError("Message delivery receipt is invalid.") from exc
            return self._validate(value, identity)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write(folder: int, name: str, value: dict[str, Any]) -> None:
        data = strict_state_bytes(value)
        if len(data) > _MAX_BYTES:
            raise IntegrityError("Message delivery receipt is too large.")
        temporary = ".receipt-" + uuid.uuid4().hex
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(descriptor)
            os.replace(temporary, name, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=folder)
            except FileNotFoundError:
                pass

    def show(self, thread_id: str, message_id: str, delegation_id: str) -> dict[str, Any]:
        identity = self._identity(thread_id, message_id, delegation_id)
        with self._locked() as folder:
            return self._read(folder, identity)

    def record(
        self,
        thread_id: str,
        message_id: str,
        delegation_id: str,
        *,
        attachment_id: str | None = None,
        acknowledged: bool = False,
    ) -> dict[str, Any]:
        identity = self._identity(thread_id, message_id, delegation_id)
        if (
            type(acknowledged) is not bool
            or (
                attachment_id is not None
                and (not isinstance(attachment_id, str) or not _ATTACHMENT.fullmatch(attachment_id))
            )
            or (acknowledged and attachment_id is not None)
        ):
            raise ValidationError("Message delivery operation is invalid.")
        with self._locked() as folder:
            value = self._read(folder, identity)
            now = datetime.now(UTC)
            if acknowledged and value["fetched_at"] is None:
                raise ValidationError("Read the message before acknowledging it.")
            if attachment_id is not None:
                if len(value["attachments"]) >= 20 and attachment_id not in value["attachments"]:
                    raise ValidationError("Attachment delivery receipt limit exceeded.")
                value["attachments"].setdefault(attachment_id, now.isoformat())
            elif acknowledged:
                fetched = _timestamp(value["fetched_at"])
                assert fetched is not None
                value["acknowledged_at"] = value["acknowledged_at"] or max(now, fetched).isoformat()
            else:
                value["fetched_at"] = value["fetched_at"] or now.isoformat()
            self._validate(value, identity)
            self._write(folder, self._name(identity), value)
            return value
