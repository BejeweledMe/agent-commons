"""Private message attachments, staged before a separately committed message.

Routes authenticate callers and supply authoritative workspace/thread/operator IDs.
This store accepts byte chunks, never client paths. Only descriptors belong in a
canonical message; content and upload/binding state remain outside the checkout.
A binding intent is durable before publication. Missing or uncertain canonical
receipts never make its content eligible for automatic expiry.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import time
import unicodedata
import uuid
import warnings
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import (
    ConfigurationError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.platform_support import lock_exclusive, require_supported_platform, unlock
from agent_commons.storage.opstate import (
    OperationalStoragePolicy,
    ensure_private_directory,
    strict_state_bytes,
)

MAX_ATTACHMENT_BYTES = 15_000_000
MAX_IMAGE_ATTACHMENTS = 10
MAX_FILE_ATTACHMENTS = 10
MAX_ATTACHMENTS = 20
MAX_UPLOAD_OPERATIONS = 128
MAX_ACTIVE_UPLOADS = 20
UNBOUND_TTL_SECONDS = 24 * 60 * 60
MAX_IMAGE_PIXELS = 16_000_000
_SCHEMA = "agent_commons.message-attachments.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_DRAFT_ID = re.compile(r"^draft\.[0-9a-f]{32}$")
_ATTACHMENT_ID = re.compile(r"^attachment\.[0-9a-f]{32}$")
_MIME = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_POLICY = OperationalStoragePolicy(
    label="message attachment",
    reject_directory_symlinks=True,
    process_lock_namespace="operational",
    lock_identity="literal",
    nofollow_lock=True,
    enforce_lock_mode=True,
)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identifier(value: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValidationError("attachment scope or identifier is invalid")
    return value


def _filename(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or len(value.encode("utf-8")) > 180
        or any(unicodedata.category(c).startswith("C") or c in '/\\:<>"|?*' for c in value)
    ):
        raise ValidationError("attachment filename must be a bounded plain filename")
    return value


def _dimensions(width: int, height: int) -> tuple[int, int]:
    if not 1 <= width <= 32768 or not 1 <= height <= 32768 or width * height > MAX_IMAGE_PIXELS:
        raise ValidationError("attachment image dimensions exceed the supported limit")
    return width, height


def _image_type(data: bytes) -> tuple[str, int, int] | None:
    """Verify image signatures, structural boundaries and declared dimensions.

    This is bounded structural validation, not a general image decoder. Consumers
    must still use their normal safe image decoding boundary for previews.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        position, dimensions, saw_data = 8, None, False
        while position + 12 <= len(data):
            size = int.from_bytes(data[position : position + 4], "big")
            kind = data[position + 4 : position + 8]
            end = position + 12 + size
            if end > len(data):
                break
            body = data[position + 8 : end - 4]
            if zlib.crc32(kind + body) != int.from_bytes(data[end - 4 : end], "big"):
                break
            if position == 8:
                if kind != b"IHDR" or size != 13:
                    break
                width, height = int.from_bytes(body[:4], "big"), int.from_bytes(body[4:8], "big")
                depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
                if (
                    body[8] not in depths.get(body[9], set())
                    or body[10:12] != b"\0\0"
                    or body[12] not in {0, 1}
                ):
                    break
                dimensions = _dimensions(width, height)
            elif kind == b"IHDR":
                break
            if kind == b"IDAT":
                saw_data = saw_data or bool(body)
            if kind == b"IEND":
                if size == 0 and end == len(data) and dimensions and saw_data:
                    return "image/png", *dimensions
                break
            position = end
        raise ValidationError("attachment PNG structure is invalid")
    if data.startswith(b"\xff\xd8"):
        position, dimensions, saw_scan = 2, None, False
        while position < len(data):
            if data[position] != 0xFF:
                if saw_scan:
                    position += 1
                    continue
                break
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position == len(data):
                break
            marker = data[position]
            position += 1
            if marker == 0xD9:
                if dimensions and saw_scan and position == len(data):
                    return "image/jpeg", *dimensions
                break
            if marker == 0 or 0xD0 <= marker <= 0xD7:
                if saw_scan:
                    continue
                break
            if position + 2 > len(data):
                break
            size = int.from_bytes(data[position : position + 2], "big")
            if size < 2 or position + size > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2}:
                if size < 8 or dimensions is not None:
                    break
                height = int.from_bytes(data[position + 3 : position + 5], "big")
                width = int.from_bytes(data[position + 5 : position + 7], "big")
                components = data[position + 7]
                if (
                    data[position + 2] != 8
                    or components not in {1, 3, 4}
                    or size != 8 + components * 3
                ):
                    break
                dimensions = _dimensions(width, height)
            if marker == 0xDA:
                if dimensions is None or size < 6 or size != 6 + 2 * data[position + 2]:
                    break
                saw_scan = True
            position += size
        raise ValidationError("attachment JPEG structure is invalid")
    return None


def _verify_image_decode(data: bytes, detected: tuple[str, int, int]) -> None:
    """Decode every pixel within the existing dimensions/byte bounds, fail closed."""
    try:
        from PIL import Image, ImageFile, UnidentifiedImageError
    except ImportError as exc:
        raise ConfigurationError("attachment image decoding requires Pillow") from exc
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ConfigurationError("attachment image decoding requires strict truncation checks")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as image:
                dimensions = _dimensions(*image.size)
                if (
                    image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[detected[0]]
                    or dimensions != detected[1:]
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValidationError("attachment must contain one supported image")
                image.verify()
            # verify() invalidates the decoder. Reopen the same bounded bytes,
            # check dimensions before allocation, then force full pixel decoding.
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as image:
                _dimensions(*image.size)
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("attachment image cannot be fully decoded") from exc


class MessageAttachmentUnavailable(ValidationError):
    """A bounded, non-disclosing lookup could not prove this message binding."""

    code = "message_attachment_unavailable"

    def __init__(self) -> None:
        super().__init__("attachment is unavailable for this message")


@dataclass(frozen=True, slots=True)
class AttachmentDescriptor:
    attachment_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    kind: Literal["image", "file"]
    width: int | None = None
    height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Metadata-only descriptor; contains no storage or client path."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AttachmentDraft:
    draft_id: str
    workspace_id: str
    thread_id: str
    operator_id: str
    state: Literal["ready", "binding", "bound"]
    expires_at: float
    attachments: tuple[AttachmentDescriptor, ...]
    operation_id: str | None = None
    message_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "attachments": [item.as_dict() for item in self.attachments]}


@dataclass(frozen=True, slots=True)
class BindingReceipt:
    """Evidence supplied by trusted canonical-message code, never an HTTP body."""

    workspace_id: str
    thread_id: str
    operator_id: str
    operation_id: str
    message_id: str
    attachment_ids: tuple[str, ...]


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk every component without following symlinks; operate through pinned fds."""
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        try:
            for part in path.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            if create:
                # This is a kernel-owned alias for our already validated descriptor,
                # not a client path. The shared helper applies the private mode.
                ensure_private_directory(
                    Path(f"/dev/fd/{descriptor}"),
                    policy=replace(_POLICY, reject_directory_symlinks=False),
                )
                if os.fstat(descriptor).st_mode & 0o077:
                    raise IntegrityError("attachment storage directory is not private")
        except OSError as exc:
            raise IntegrityError("attachment storage directory is unavailable or unsafe") from exc
        yield descriptor
    finally:
        os.close(descriptor)


class MessageAttachmentStore:
    """Private per-workspace store; all access also requires thread/operator scope.

    A server must authorize these IDs before calling this class. BindingReceipt
    and reconciliation callbacks are trusted integration seams, not user input.
    """

    def __init__(
        self,
        state_root: str | Path,
        *,
        workspace_root: str | Path,
        workspace_id: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        require_supported_platform()
        self.workspace_id = _identifier(workspace_id)
        state = Path(state_root).expanduser().absolute()
        workspace = Path(workspace_root).expanduser().resolve()
        if state.resolve().is_relative_to(workspace):
            raise ValidationError("attachment storage must be outside the workspace")
        self.root = (
            state
            / "runtime"
            / "message-attachments"
            / hashlib.sha256(workspace_id.encode()).hexdigest()
        )
        self.clock = clock
        with _directory(self.root, create=True):
            pass

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with _directory(self.root) as root:
            descriptor = os.open(
                "store.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=root
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise IntegrityError("attachment lock is not a private regular file")
                os.fchmod(descriptor, 0o600)
                lock_exclusive(descriptor)
                try:
                    yield root
                finally:
                    unlock(descriptor)
            finally:
                os.close(descriptor)

    @staticmethod
    def _read_bytes(folder: int, name: str, limit: int) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise IntegrityError(
                    "attachment storage file is not a bounded private regular file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise IntegrityError("attachment storage file exceeds its bound")
            return content
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_state(folder: int, value: Mapping[str, Any]) -> None:
        temporary = ".state-" + uuid.uuid4().hex
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(strict_state_bytes(value))
                stream.flush()
                os.fsync(descriptor)
            os.replace(temporary, "draft.json", src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=folder)
            except FileNotFoundError:
                pass

    @contextmanager
    def _draft(
        self, root: int, draft_id: str, thread_id: str, operator_id: str
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        _identifier(draft_id, _DRAFT_ID)
        _identifier(thread_id)
        _identifier(operator_id)
        try:
            folder = os.open(draft_id, _DIRECTORY_FLAGS, dir_fd=root)
        except OSError as exc:
            raise ValidationError("attachment draft is unavailable in this scope") from exc
        try:
            value = self._load(folder, draft_id)
            if (value["workspace_id"], value["thread_id"], value["operator_id"]) != (
                self.workspace_id,
                thread_id,
                operator_id,
            ):
                raise ValidationError("attachment draft is unavailable in this scope")
            yield folder, value
        finally:
            os.close(folder)

    def _load(self, folder: int, draft_id: str) -> dict[str, Any]:
        raw = self._read_bytes(folder, "draft.json", 64 * 1024)
        value = loads_json_strict(raw)
        expected = {
            "schema",
            "draft_id",
            "workspace_id",
            "thread_id",
            "operator_id",
            "state",
            "expires_at",
            "attachments",
            "operation_id",
            "message_id",
        }
        try:
            if (
                not isinstance(value, dict)
                or set(value) != expected
                or value["schema"] != _SCHEMA
                or value["draft_id"] != draft_id
                or raw != strict_state_bytes(value)
            ):
                raise ValueError
            for key in ("workspace_id", "thread_id", "operator_id"):
                _identifier(value[key])
            if (
                value["state"] not in {"ready", "binding", "bound"}
                or not isinstance(value["expires_at"], (float, int))
                or isinstance(value["expires_at"], bool)
            ):
                raise ValueError
            items = value["attachments"]
            if not isinstance(items, list) or len(items) > MAX_ATTACHMENTS:
                raise ValueError
            descriptors = [AttachmentDescriptor(**item) for item in items]
            for item in descriptors:
                _identifier(item.attachment_id, _ATTACHMENT_ID)
                _filename(item.filename)
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", item.sha256)
                    or type(item.size_bytes) is not int
                    or not 1 <= item.size_bytes <= MAX_ATTACHMENT_BYTES
                    or item.kind not in {"image", "file"}
                    or not _MIME.fullmatch(item.media_type)
                ):
                    raise ValueError
                if item.kind == "image":
                    if (
                        item.media_type not in {"image/png", "image/jpeg"}
                        or type(item.width) is not int
                        or type(item.height) is not int
                    ):
                        raise ValueError
                    _dimensions(item.width, item.height)
                elif item.width is not None or item.height is not None:
                    raise ValueError
            if (
                len({item.attachment_id for item in descriptors}) != len(descriptors)
                or sum(item.kind == "image" for item in descriptors) > MAX_IMAGE_ATTACHMENTS
                or sum(item.kind == "file" for item in descriptors) > MAX_FILE_ATTACHMENTS
            ):
                raise ValueError
            if value["state"] == "ready":
                if value["operation_id"] is not None or value["message_id"] is not None:
                    raise ValueError
            else:
                _identifier(value["operation_id"])
                if value["state"] == "bound":
                    _identifier(value["message_id"])
                elif value["message_id"] is not None:
                    raise ValueError
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise IntegrityError("attachment draft metadata is invalid") from exc
        return value

    @staticmethod
    def _view(value: Mapping[str, Any]) -> AttachmentDraft:
        return AttachmentDraft(
            **{key: val for key, val in value.items() if key not in {"schema", "attachments"}},
            attachments=tuple(AttachmentDescriptor(**item) for item in value["attachments"]),
        )

    def reserve_draft(self, *, thread_id: str, operator_id: str) -> AttachmentDraft:
        _identifier(thread_id)
        _identifier(operator_id)
        draft_id = "draft." + uuid.uuid4().hex
        value = {
            "schema": _SCHEMA,
            "draft_id": draft_id,
            "workspace_id": self.workspace_id,
            "thread_id": thread_id,
            "operator_id": operator_id,
            "state": "ready",
            "expires_at": self.clock() + UNBOUND_TTL_SECONDS,
            "attachments": [],
            "operation_id": None,
            "message_id": None,
        }
        with self._locked() as root:
            os.mkdir(draft_id, 0o700, dir_fd=root)
            folder = os.open(draft_id, _DIRECTORY_FLAGS, dir_fd=root)
            try:
                self._write_state(folder, value)
                os.fsync(root)
            finally:
                os.close(folder)
        return self._view(value)

    def show_draft(self, draft_id: str, *, thread_id: str, operator_id: str) -> AttachmentDraft:
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (_, value),
        ):
            return self._view(value)

    @staticmethod
    def _write_private_record(folder: int, name: str, value: Mapping[str, Any]) -> None:
        temporary = ".record-" + uuid.uuid4().hex
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=folder,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(strict_state_bytes(value))
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

    @staticmethod
    def _upload_name(operation_id: str) -> str:
        return "upload." + hashlib.sha256(operation_id.encode()).hexdigest() + ".json"

    @staticmethod
    def _receipt_scope(value: Mapping[str, Any]) -> dict[str, str]:
        return {key: value[key] for key in ("workspace_id", "draft_id", "thread_id", "operator_id")}

    def _upload_receipt(
        self, folder: int, name: str, value: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        try:
            raw = self._read_bytes(folder, name, 8192)
        except FileNotFoundError:
            return None
        receipt = loads_json_strict(raw)
        scope = self._receipt_scope(value)
        try:
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {
                    "schema",
                    *scope,
                    "operation_hash",
                    "attachment_id",
                    "filename",
                    "declared_media_type",
                    "descriptor",
                }
                or receipt["schema"] != "agent_commons.attachment-upload.v1"
                or any(receipt[key] != item for key, item in scope.items())
                or name != "upload." + receipt["operation_hash"] + ".json"
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["operation_hash"])
                or raw != strict_state_bytes(receipt)
            ):
                raise ValueError
            _identifier(receipt["attachment_id"], _ATTACHMENT_ID)
            _filename(receipt["filename"])
            if not isinstance(receipt["declared_media_type"], str) or not _MIME.fullmatch(
                receipt["declared_media_type"]
            ):
                raise ValueError
            if receipt["descriptor"] is not None:
                item = AttachmentDescriptor(**receipt["descriptor"])
                if (
                    item.attachment_id != receipt["attachment_id"]
                    or item.filename != receipt["filename"]
                    or type(item.size_bytes) is not int
                    or not 1 <= item.size_bytes <= MAX_ATTACHMENT_BYTES
                    or not re.fullmatch(r"[0-9a-f]{64}", item.sha256)
                    or item.kind not in {"image", "file"}
                    or not isinstance(item.media_type, str)
                    or not _MIME.fullmatch(item.media_type)
                ):
                    raise ValueError
                if item.kind == "image":
                    if (
                        item.media_type not in {"image/png", "image/jpeg"}
                        or type(item.width) is not int
                        or type(item.height) is not int
                    ):
                        raise ValueError
                    _dimensions(item.width, item.height)
                elif item.width is not None or item.height is not None:
                    raise ValueError
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise IntegrityError("attachment upload receipt is invalid") from exc
        return receipt

    def _discard_record(
        self, folder: int, value: Mapping[str, Any], attachment_id: str
    ) -> dict[str, Any] | None:
        name = "discard." + attachment_id + ".json"
        try:
            raw = self._read_bytes(folder, name, 4096)
        except FileNotFoundError:
            return None
        record = loads_json_strict(raw)
        expected = {
            "schema": "agent_commons.attachment-discard.v1",
            **self._receipt_scope(value),
            "attachment_id": attachment_id,
        }
        if record != expected or raw != strict_state_bytes(expected):
            raise IntegrityError("attachment discard receipt is invalid")
        return record

    def _reserve_upload(
        self,
        folder: int,
        value: dict[str, Any],
        name: str,
        filename: str,
        declared_media_type: str,
    ) -> dict[str, Any]:
        receipt = self._upload_receipt(folder, name, value)
        if receipt is not None:
            if (
                receipt["filename"] != filename
                or receipt["declared_media_type"] != declared_media_type
            ):
                raise IdempotencyConflictError("upload operation already has a different intent")
            if self._discard_record(folder, value, receipt["attachment_id"]) is not None:
                raise LifecycleConflictError("discarded upload operations cannot be restored")
            if value["state"] != "ready" and not any(
                item["attachment_id"] == receipt["attachment_id"] for item in value["attachments"]
            ):
                raise LifecycleConflictError("attachment draft is not open for uploads")
            return receipt
        if value["state"] != "ready" or value["expires_at"] <= self.clock():
            raise LifecycleConflictError("attachment draft is not open for uploads")
        if len(value["attachments"]) >= MAX_ATTACHMENTS:
            raise ValidationError("attachment draft has reached its file limit")
        if sum(name.startswith("upload.") for name in os.listdir(folder)) >= MAX_UPLOAD_OPERATIONS:
            raise ValidationError("attachment draft has reached its upload operation limit")
        receipt = {
            "schema": "agent_commons.attachment-upload.v1",
            **self._receipt_scope(value),
            "operation_hash": name.removeprefix("upload.").removesuffix(".json"),
            "attachment_id": "attachment." + uuid.uuid4().hex,
            "filename": filename,
            "declared_media_type": declared_media_type,
            "descriptor": None,
        }
        try:
            os.stat(receipt["attachment_id"] + ".bin", dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise IntegrityError("new attachment identity already has unknown content")
        self._write_private_record(folder, name, receipt)
        return receipt

    @staticmethod
    def _received_descriptor(
        attachment_id: str, filename: str, declared_media_type: str, data: bytes
    ) -> AttachmentDescriptor:
        detected = _image_type(data)
        if detected:
            _verify_image_decode(data, detected)
            media_type, width, height = detected
            if declared_media_type not in {media_type, "application/octet-stream"}:
                raise ValidationError("attachment media type does not match its bytes")
            kind = "image"
        else:
            if declared_media_type.startswith("image/") or filename.lower().endswith(
                (".png", ".jpg", ".jpeg")
            ):
                raise ValidationError("attachment is not a supported PNG or JPEG image")
            media_type, width, height, kind = declared_media_type, None, None, "file"
        return AttachmentDescriptor(
            attachment_id,
            filename,
            media_type,
            len(data),
            hashlib.sha256(data).hexdigest(),
            kind,
            width,
            height,
        )

    def upload(
        self,
        draft_id: str,
        *,
        thread_id: str,
        operator_id: str,
        filename: str,
        declared_media_type: str,
        chunks: Iterable[bytes],
        operation_id: str | None = None,
    ) -> AttachmentDescriptor:
        """Receive/decode outside the workspace lock; publish an exact retry identity.

        Callers retain a stable operation_id across retries. Filename/media intent
        is fixed at reservation; bytes become fixed by the first fully validated
        receipt. An interrupted, incomplete body is never adopted as content.
        With no operation_id each call remains an independent legacy upload.
        """
        filename = _filename(filename)
        if (
            not isinstance(declared_media_type, str)
            or len(declared_media_type) > 127
            or not _MIME.fullmatch(declared_media_type)
        ):
            raise ValidationError("attachment media type is invalid")
        operation_id = _identifier(operation_id) if operation_id is not None else uuid.uuid4().hex
        name = self._upload_name(operation_id)
        partial = ".upload-" + uuid.uuid4().hex
        retained_folder, descriptor = -1, -1
        try:
            with (
                self._locked() as root,
                self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
            ):
                if (
                    sum(name.startswith(".upload-") for name in os.listdir(folder))
                    >= MAX_ACTIVE_UPLOADS
                ):
                    raise ValidationError("attachment draft has too many incomplete uploads")
                receipt = self._reserve_upload(folder, value, name, filename, declared_media_type)
                retained_folder = os.dup(folder)
                descriptor = os.open(
                    partial,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=retained_folder,
                )
            # A blocked client or expensive image decode holds no workspace lock.
            size, digest = 0, hashlib.sha256()
            with os.fdopen(descriptor, "w+b", closefd=False) as stream:
                for count, chunk in enumerate(chunks, 1):
                    if not isinstance(chunk, bytes) or count > 65536:
                        raise ValidationError("attachment stream is invalid")
                    size += len(chunk)
                    if size > MAX_ATTACHMENT_BYTES:
                        raise ValidationError("attachment exceeds the 15000000 byte limit")
                    stream.write(chunk)
                    digest.update(chunk)
                if not size:
                    raise ValidationError("attachment must contain at least one byte")
                stream.flush()
                os.fsync(descriptor)
                stream.seek(0)
                data = stream.read(MAX_ATTACHMENT_BYTES + 1)
            if len(data) != size or hashlib.sha256(data).digest() != digest.digest():
                raise IntegrityError("attachment staging content changed during receipt")
            item = self._received_descriptor(
                receipt["attachment_id"], filename, declared_media_type, data
            )
            with (
                self._locked() as root,
                self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
            ):
                original, current = os.fstat(retained_folder), os.fstat(folder)
                if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                    raise IntegrityError("attachment draft changed during receipt")
                current_receipt = self._upload_receipt(folder, name, value)
                if (
                    current_receipt is None
                    or current_receipt["attachment_id"] != item.attachment_id
                    or current_receipt["filename"] != filename
                    or current_receipt["declared_media_type"] != declared_media_type
                ):
                    raise IntegrityError("attachment upload reservation is unavailable")
                if self._discard_record(folder, value, item.attachment_id) is not None:
                    raise LifecycleConflictError("discarded upload operations cannot be restored")
                if (
                    current_receipt["descriptor"] is not None
                    and current_receipt["descriptor"] != item.as_dict()
                ):
                    raise IdempotencyConflictError("upload operation already has different content")
                existing = next(
                    (
                        entry
                        for entry in value["attachments"]
                        if entry["attachment_id"] == item.attachment_id
                    ),
                    None,
                )
                if existing is not None:
                    if (
                        current_receipt["descriptor"] != item.as_dict()
                        or existing != item.as_dict()
                    ):
                        raise IntegrityError(
                            "attachment upload receipt does not match ready metadata"
                        )
                    self._verified_attachment(folder, value, item.attachment_id)
                    return item
                if value["state"] != "ready" or value["expires_at"] <= self.clock():
                    raise LifecycleConflictError("attachment draft is not open for uploads")
                if len(value["attachments"]) >= MAX_ATTACHMENTS:
                    raise ValidationError("attachment draft has reached its file limit")
                if sum(entry["kind"] == item.kind for entry in value["attachments"]) >= 10:
                    raise ValidationError("attachment draft has reached its image or file limit")
                final = item.attachment_id + ".bin"
                try:
                    content = self._read_bytes(folder, final, MAX_ATTACHMENT_BYTES)
                except FileNotFoundError:
                    content = None
                if current_receipt["descriptor"] is None:
                    # A receiving receipt reserves this opaque name, but proves no
                    # payload yet. Never adopt nonempty bytes without an earlier
                    # exact publication receipt, even if a retry happens to match.
                    if content not in {None, b""}:
                        raise IntegrityError("attachment object has no prior publication receipt")
                    current_receipt["descriptor"] = item.as_dict()
                    self._write_private_record(folder, name, current_receipt)
                if content in {None, b""}:
                    # Reserve an empty, private destination before atomic replace.
                    # No nonempty object is overwritten; the durable receipt above
                    # proves the only payload allowed after a crash/retry. Atomic
                    # rename avoids a hardlink/unlink crash window entirely.
                    if content is None:
                        empty = os.open(
                            final,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=folder,
                        )
                        try:
                            os.fsync(empty)
                        finally:
                            os.close(empty)
                        os.fsync(folder)
                    os.replace(partial, final, src_dir_fd=retained_folder, dst_dir_fd=folder)
                    linked = os.stat(final, dir_fd=folder, follow_symlinks=False)
                    staged = os.fstat(descriptor)
                    if not stat.S_ISREG(linked.st_mode) or (linked.st_dev, linked.st_ino) != (
                        staged.st_dev,
                        staged.st_ino,
                    ):
                        raise IntegrityError("attachment staging object changed before publication")
                    os.fsync(folder)
                    content = self._read_bytes(folder, final, MAX_ATTACHMENT_BYTES)
                if (
                    len(content) != item.size_bytes
                    or hashlib.sha256(content).hexdigest() != item.sha256
                ):
                    raise IntegrityError("attachment object does not match its publication receipt")
                value["attachments"].append(item.as_dict())
                value["expires_at"] = self.clock() + UNBOUND_TTL_SECONDS
                self._write_state(folder, value)
                return item
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if retained_folder >= 0:
                try:
                    os.unlink(partial, dir_fd=retained_folder)
                except FileNotFoundError:
                    pass
                finally:
                    os.close(retained_folder)

    def _verified_attachment(
        self, folder: int, value: Mapping[str, Any], attachment_id: str
    ) -> tuple[AttachmentDescriptor, bytes]:
        item = next(
            (
                AttachmentDescriptor(**item)
                for item in value["attachments"]
                if item["attachment_id"] == attachment_id
            ),
            None,
        )
        if item is None:
            raise ValidationError("attachment is unavailable in this scope")
        content = self._read_bytes(folder, attachment_id + ".bin", MAX_ATTACHMENT_BYTES)
        if len(content) != item.size_bytes or hashlib.sha256(content).hexdigest() != item.sha256:
            raise IntegrityError("attachment content does not match its immutable descriptor")
        return item, content

    def read(
        self, draft_id: str, attachment_id: str, *, thread_id: str, operator_id: str
    ) -> tuple[AttachmentDescriptor, bytes]:
        _identifier(attachment_id, _ATTACHMENT_ID)
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
        ):
            return self._verified_attachment(folder, value, attachment_id)

    def remove_attachment(
        self, draft_id: str, attachment_id: str, *, thread_id: str, operator_id: str
    ) -> AttachmentDraft:
        """Discard one ready selection, committing metadata before deleting bytes.

        Retry is idempotent after descriptor removal. A failed/uncertain state
        commit never deletes content; a crash or unlink failure after the commit
        can leave a private orphan, retained by the conservative expiry policy.
        """
        _identifier(attachment_id, _ATTACHMENT_ID)
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
        ):
            if value["state"] != "ready":
                raise LifecycleConflictError("only an unbound draft can discard attachments")
            if not any(item["attachment_id"] == attachment_id for item in value["attachments"]):
                return self._view(value)
            self._verified_attachment(folder, value, attachment_id)
            if self._discard_record(folder, value, attachment_id) is None:
                self._write_private_record(
                    folder,
                    "discard." + attachment_id + ".json",
                    {
                        "schema": "agent_commons.attachment-discard.v1",
                        **self._receipt_scope(value),
                        "attachment_id": attachment_id,
                    },
                )
            value["attachments"] = [
                item for item in value["attachments"] if item["attachment_id"] != attachment_id
            ]
            # If this raises after rename, a caller must refresh: bytes stay put.
            self._write_state(folder, value)
            try:
                os.unlink(attachment_id + ".bin", dir_fd=folder)
                os.fsync(folder)
            except OSError:
                # Logical removal is already durable. Do not guess whether an
                # uncertain deletion committed, or touch any other upload.
                pass
            return self._view(value)

    def _bound_scope(self, thread_id: str, message_id: str, attachment_id: str) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "attachment_id": attachment_id,
        }

    @staticmethod
    def _bound_name(scope: Mapping[str, str]) -> str:
        return "bound." + hashlib.sha256(strict_state_bytes(scope)).hexdigest() + ".json"

    def _publish_bound_indexes(self, root: int, value: Mapping[str, Any]) -> None:
        """Publish bounded lookups only after the canonical receipt is confirmed.

        Draft metadata remains authoritative. A crash before this derived index
        is published refuses reads until trusted reconciliation republishes it.
        """
        for item in value["attachments"]:
            scope = self._bound_scope(
                value["thread_id"], value["message_id"], item["attachment_id"]
            )
            name = self._bound_name(scope)
            payload = strict_state_bytes(
                {
                    "schema": "agent_commons.message-attachment-binding.v1",
                    **scope,
                    "draft_id": value["draft_id"],
                }
            )
            try:
                existing = self._read_bytes(root, name, 4096)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if existing != payload:
                    raise IntegrityError("attachment binding index conflicts with the receipt")
                continue
            temporary = ".binding-" + uuid.uuid4().hex
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(descriptor)
                os.replace(temporary, name, src_dir_fd=root, dst_dir_fd=root)
                os.fsync(root)
            finally:
                os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=root)
                except FileNotFoundError:
                    pass

    def read_bound(
        self, *, thread_id: str, message_id: str, attachment_id: str
    ) -> tuple[AttachmentDescriptor, bytes]:
        """Read for an externally authorized canonical recipient, without creator ID.

        One fixed-size index and one bounded draft are read; no directory scan or
        draft/operator information is returned. Binding intents are never guessed.
        """
        _identifier(thread_id)
        _identifier(message_id)
        _identifier(attachment_id, _ATTACHMENT_ID)
        scope = self._bound_scope(thread_id, message_id, attachment_id)
        try:
            with self._locked() as root:
                raw = self._read_bytes(root, self._bound_name(scope), 4096)
                index = loads_json_strict(raw)
                if (
                    not isinstance(index, dict)
                    or set(index) != {"schema", "draft_id", *scope}
                    or index["schema"] != "agent_commons.message-attachment-binding.v1"
                    or any(index[key] != item for key, item in scope.items())
                    or raw != strict_state_bytes(index)
                ):
                    raise MessageAttachmentUnavailable()
                draft_id = _identifier(index["draft_id"], _DRAFT_ID)
                folder = os.open(draft_id, _DIRECTORY_FLAGS, dir_fd=root)
                try:
                    value = self._load(folder, draft_id)
                    if (
                        value["workspace_id"] != self.workspace_id
                        or value["thread_id"] != thread_id
                        or value["message_id"] != message_id
                        or value["state"] != "bound"
                    ):
                        raise MessageAttachmentUnavailable()
                    return self._verified_attachment(folder, value, attachment_id)
                finally:
                    os.close(folder)
        except (OSError, IntegrityError, ValidationError):
            raise MessageAttachmentUnavailable() from None

    def begin_binding(
        self, draft_id: str, *, thread_id: str, operator_id: str, operation_id: str
    ) -> AttachmentDraft:
        """Freeze an exact attachment set before the canonical message write."""
        _identifier(operation_id)
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
        ):
            if value["state"] != "ready":
                if value["operation_id"] != operation_id:
                    raise LifecycleConflictError(
                        "attachment draft already has another binding intent"
                    )
                return self._view(value)
            if value["expires_at"] <= self.clock() or not value["attachments"]:
                raise LifecycleConflictError("attachment draft is expired or empty")
            for item in value["attachments"]:
                if self._discard_record(folder, value, item["attachment_id"]) is not None:
                    raise LifecycleConflictError("an attachment discard must be reconciled first")
            value.update(state="binding", operation_id=operation_id)
            self._write_state(folder, value)
            return self._view(value)

    def _finalize(
        self, root: int, folder: int, value: dict[str, Any], receipt: BindingReceipt
    ) -> AttachmentDraft:
        if not isinstance(receipt, BindingReceipt):
            raise ValidationError("attachment finalization requires a trusted binding receipt")
        expected = (
            self.workspace_id,
            value["thread_id"],
            value["operator_id"],
            value["operation_id"],
        )
        actual = (
            receipt.workspace_id,
            receipt.thread_id,
            receipt.operator_id,
            receipt.operation_id,
        )
        if (
            value["state"] not in {"binding", "bound"}
            or actual != expected
            or set(receipt.attachment_ids)
            != {item["attachment_id"] for item in value["attachments"]}
            or len(receipt.attachment_ids) != len(value["attachments"])
        ):
            raise IntegrityError("canonical message receipt does not match the attachment intent")
        _identifier(receipt.message_id)
        if value["message_id"] not in {None, receipt.message_id}:
            raise IntegrityError("attachment draft is already bound to another message")
        value.update(state="bound", message_id=receipt.message_id)
        self._write_state(folder, value)
        self._publish_bound_indexes(root, value)
        return self._view(value)

    def finalize_binding(
        self, draft_id: str, *, thread_id: str, operator_id: str, receipt: BindingReceipt
    ) -> AttachmentDraft:
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
        ):
            return self._finalize(root, folder, value, receipt)

    def reconcile_binding(
        self,
        draft_id: str,
        *,
        thread_id: str,
        operator_id: str,
        lookup: Callable[[str], BindingReceipt | None],
    ) -> AttachmentDraft:
        """Confirm a committed message; absent/failed lookups leave intent intact."""
        with (
            self._locked() as root,
            self._draft(root, draft_id, thread_id, operator_id) as (folder, value),
        ):
            if value["state"] == "ready":
                return self._view(value)
            receipt = lookup(value["operation_id"])
            return (
                self._view(value)
                if receipt is None
                else self._finalize(root, folder, value, receipt)
            )

    def _expiry_names(self, folder: int, value: Mapping[str, Any]) -> set[str] | None:
        """Account for finished receipts; preserve pending/orphan content."""
        names = {"draft.json", *(item["attachment_id"] + ".bin" for item in value["attachments"])}
        files = set(os.listdir(folder))
        ready = {item["attachment_id"]: item for item in value["attachments"]}
        for name in files:
            if re.fullmatch(r"upload\.[0-9a-f]{64}\.json", name):
                receipt = self._upload_receipt(folder, name, value)
                if receipt is None:
                    return None
                item = receipt["descriptor"]
                if item is not None and ready.get(receipt["attachment_id"]) != item:
                    if self._discard_record(folder, value, receipt["attachment_id"]) is None:
                        return None
                names.add(name)
            elif re.fullmatch(r"discard\.attachment\.[0-9a-f]{32}\.json", name):
                attachment_id = name.removeprefix("discard.").removesuffix(".json")
                self._discard_record(folder, value, attachment_id)
                if attachment_id in ready:
                    return None
                names.add(name)
        return names if files == names else None

    def expire_unbound(self) -> tuple[str, ...]:
        """Delete only expired ready drafts with completely accounted-for files.

        Binding intents, bound drafts, corrupt metadata, partial uploads and
        unreferenced objects are retained for explicit operator reconciliation.
        """
        expired = []
        with self._locked() as root:
            for draft_id in sorted(os.listdir(root)):
                if not _DRAFT_ID.fullmatch(draft_id):
                    continue
                try:
                    folder = os.open(draft_id, _DIRECTORY_FLAGS, dir_fd=root)
                except OSError:
                    continue
                try:
                    try:
                        value = self._load(folder, draft_id)
                    except (IntegrityError, ValidationError, OSError):
                        continue
                    if (
                        value["workspace_id"] != self.workspace_id
                        or value["state"] != "ready"
                        or value["expires_at"] > self.clock()
                    ):
                        continue
                    try:
                        names = self._expiry_names(folder, value)
                    except (IntegrityError, ValidationError, OSError):
                        continue
                    if names is None:
                        continue
                    # Symlinks/hardlinks are never followed or garbage-collected.
                    infos = [os.stat(name, dir_fd=folder, follow_symlinks=False) for name in names]
                    if any(not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 for info in infos):
                        continue
                    for name in names - {"draft.json"}:
                        os.unlink(name, dir_fd=folder)
                    os.unlink("draft.json", dir_fd=folder)
                    os.fsync(folder)
                    os.rmdir(draft_id, dir_fd=root)
                    os.fsync(root)
                    expired.append(draft_id)
                finally:
                    os.close(folder)
        return tuple(expired)
