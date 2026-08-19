"""Bounded operational task-input exchange between a delegation parent and child.

See `docs/adr/0006-task-scoped-communication-and-runtime-control.md`.  Nothing
in this module is canonical: it is a private, atomic, rebuildable operational
store for bounded request/reply, progress, and blocker exchanges scoped to one
delegation attempt.  A durable finding, decision, or requirement still belongs
in the normal typed Commons workflow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_commons.core.ids import is_typed_id, stable_id
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy
from agent_commons.storage.atomic import atomic_write_replace
from agent_commons.storage.opstate import (
    COMMUNICATION_STORAGE,
    canonical_state_bytes,
    ensure_private_directory,
    exclusive_lock,
    iso_timestamp,
    parse_timestamp,
    strict_state_bytes,
)

OPERATION_SCHEMA = "agent_commons.runtime_operation.v1"
_UNAVAILABLE = "communication operation is unavailable"
_INTEGRITY_KEY_BYTES = 32


class CommunicationAuthorizationError(ValidationError):
    """A session outside an operation's fixed participant graph tried to act on it."""


def _semantic_material(value: Mapping[str, Any]) -> dict[str, Any]:
    semantic = dict(value)
    semantic.pop("semantic_sha256", None)
    semantic.pop("integrity_hmac_sha256", None)
    return semantic


def _semantic_sha256(value: Mapping[str, Any]) -> str:
    semantic = _semantic_material(value)
    return hashlib.sha256(canonical_state_bytes(semantic)).hexdigest()


def _integrity_hmac_sha256(value: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(
        key,
        canonical_state_bytes(_semantic_material(value)),
        hashlib.sha256,
    ).hexdigest()


def _require_typed(name: str, value: str, prefix: str) -> str:
    if not is_typed_id(value, prefix):
        raise ValidationError(f"{name} is not a valid {prefix} identifier")
    return value


def _require_sha256(name: str, value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{name} must be a SHA-256 digest")
    return value


class OperationKind(StrEnum):
    REQUEST = "request"
    PROGRESS = "progress"
    BLOCKER = "blocker"
    GUIDANCE = "guidance"
    CHECKPOINT = "checkpoint"

    @property
    def requires_reply(self) -> bool:
        return self is OperationKind.REQUEST


class OperationState(StrEnum):
    OPEN = "open"
    REPLIED = "replied"
    CANCEL_REQUESTED = "cancel_requested"
    ACKED = "acked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NEEDS_OPERATOR = "needs_operator"

    @property
    def terminal(self) -> bool:
        return self in {
            OperationState.ACKED,
            OperationState.CANCELLED,
            OperationState.EXPIRED,
            OperationState.NEEDS_OPERATOR,
        }


_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.OPEN: frozenset(
        {
            OperationState.REPLIED,
            OperationState.ACKED,
            OperationState.CANCEL_REQUESTED,
            OperationState.EXPIRED,
            OperationState.NEEDS_OPERATOR,
        }
    ),
    OperationState.REPLIED: frozenset(
        {
            OperationState.ACKED,
            OperationState.CANCEL_REQUESTED,
            OperationState.EXPIRED,
            OperationState.NEEDS_OPERATOR,
        }
    ),
    OperationState.CANCEL_REQUESTED: frozenset(
        {
            OperationState.CANCELLED,
            OperationState.EXPIRED,
            OperationState.NEEDS_OPERATOR,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class OperationLimits:
    """Operator-owned message, depth, size, and deadline budgets."""

    max_chain_depth: int = 4
    max_metadata_bytes: int = 4_096
    min_deadline_seconds: int = 1
    max_deadline_seconds: int = 3_600

    def __post_init__(self) -> None:
        for name in ("max_chain_depth", "max_metadata_bytes", "max_deadline_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValidationError(f"{name} must be a positive integer")
        if (
            isinstance(self.min_deadline_seconds, bool)
            or not isinstance(self.min_deadline_seconds, int)
            or self.min_deadline_seconds < 1
        ):
            raise ValidationError("min_deadline_seconds must be a positive integer")
        if self.min_deadline_seconds > self.max_deadline_seconds:
            raise ValidationError("min_deadline_seconds cannot exceed max_deadline_seconds")


@dataclass(frozen=True, slots=True)
class CommunicationScope:
    """Binds one operation to its exact workspace, delegation, and participants."""

    workspace_fingerprint: str
    delegation_id: str
    task_id: str
    target_revision: str
    attempt_id: str
    sender_session_id: str
    allowed_recipient_session_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sha256("workspace_fingerprint", self.workspace_fingerprint)
        _require_typed("delegation_id", self.delegation_id, "delegation")
        _require_typed("task_id", self.task_id, "task")
        _require_typed("target_revision", self.target_revision, "evt")
        _require_typed("attempt_id", self.attempt_id, "attempt")
        _require_typed("sender_session_id", self.sender_session_id, "session")
        if isinstance(self.allowed_recipient_session_ids, (str, bytes)):
            raise ValidationError("allowed_recipient_session_ids must be a sequence of sessions")
        recipients = tuple(dict.fromkeys(self.allowed_recipient_session_ids))
        if not recipients:
            raise ValidationError("communication scope requires at least one allowed recipient")
        for recipient in recipients:
            _require_typed("allowed recipient session id", recipient, "session")
        if self.sender_session_id in recipients:
            raise ValidationError("a session cannot be its own allowed recipient")
        object.__setattr__(self, "allowed_recipient_session_ids", recipients)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_fingerprint": self.workspace_fingerprint,
            "delegation_id": self.delegation_id,
            "task_id": self.task_id,
            "target_revision": self.target_revision,
            "attempt_id": self.attempt_id,
            "sender_session_id": self.sender_session_id,
            "allowed_recipient_session_ids": list(self.allowed_recipient_session_ids),
        }

    def participants(self) -> frozenset[str]:
        return frozenset({self.sender_session_id, *self.allowed_recipient_session_ids})

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CommunicationScope:
        expected = {
            "workspace_fingerprint",
            "delegation_id",
            "task_id",
            "target_revision",
            "attempt_id",
            "sender_session_id",
            "allowed_recipient_session_ids",
        }
        if set(value) != expected:
            raise IntegrityError("stored communication scope has an invalid shape")
        string_fields = expected - {"allowed_recipient_session_ids"}
        if any(not isinstance(value[field], str) for field in string_fields):
            raise IntegrityError("stored communication scope fields are invalid")
        recipients = value["allowed_recipient_session_ids"]
        if not isinstance(recipients, list) or not all(
            isinstance(item, str) for item in recipients
        ):
            raise IntegrityError("stored communication scope recipients are invalid")
        try:
            return cls(
                workspace_fingerprint=value["workspace_fingerprint"],
                delegation_id=value["delegation_id"],
                task_id=value["task_id"],
                target_revision=value["target_revision"],
                attempt_id=value["attempt_id"],
                sender_session_id=value["sender_session_id"],
                allowed_recipient_session_ids=tuple(recipients),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise IntegrityError("stored communication scope is invalid") from exc


@dataclass(frozen=True, slots=True)
class OperationRequestSpec:
    """Caller-supplied intent to open one bounded operational exchange."""

    idempotency_key: str
    kind: OperationKind
    scope: CommunicationScope
    metadata: Mapping[str, Any]
    deadline_seconds: int
    continuation_of: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not 0 < len(self.idempotency_key) <= 256:
            raise ValidationError("communication idempotency key is invalid")
        object.__setattr__(self, "kind", OperationKind(self.kind))
        if not isinstance(self.metadata, Mapping):
            raise ValidationError("communication metadata must be a mapping")
        if (
            isinstance(self.deadline_seconds, bool)
            or not isinstance(self.deadline_seconds, int)
            or self.deadline_seconds < 1
        ):
            raise ValidationError("communication deadline_seconds must be a positive integer")
        if self.continuation_of is not None:
            _require_typed("continuation_of", self.continuation_of, "operation")

    @property
    def operation_id(self) -> str:
        return stable_id("operation", self.idempotency_key)

    def semantic_body(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "scope": self.scope.as_dict(),
            "metadata": dict(self.metadata),
            "deadline_seconds": self.deadline_seconds,
            "continuation_of": self.continuation_of,
        }


@dataclass(frozen=True, slots=True)
class OperationRecord:
    schema: str
    operation_id: str
    idempotency_key: str
    kind: OperationKind
    scope: CommunicationScope
    state: OperationState
    reason: str
    depth: int
    metadata: Mapping[str, Any]
    reply: Mapping[str, Any] | None
    reply_idempotency_key: str | None
    ack_idempotency_key: str | None
    continuation_of: str | None
    deadline_seconds: int
    created_at: str
    updated_at: str
    deadline_at: str
    semantic_sha256: str
    integrity_hmac_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "idempotency_key": self.idempotency_key,
            "kind": self.kind.value,
            "scope": self.scope.as_dict(),
            "state": self.state.value,
            "reason": self.reason,
            "depth": self.depth,
            "metadata": dict(self.metadata),
            "reply": dict(self.reply) if self.reply is not None else None,
            "reply_idempotency_key": self.reply_idempotency_key,
            "ack_idempotency_key": self.ack_idempotency_key,
            "continuation_of": self.continuation_of,
            "deadline_seconds": self.deadline_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_at": self.deadline_at,
            "semantic_sha256": self.semantic_sha256,
            "integrity_hmac_sha256": self.integrity_hmac_sha256,
        }


def _seal_record(record: OperationRecord, integrity_key: bytes) -> OperationRecord:
    sealed = replace(record, semantic_sha256=_semantic_sha256(record.as_dict()))
    return replace(
        sealed,
        integrity_hmac_sha256=_integrity_hmac_sha256(sealed.as_dict(), integrity_key),
    )


def _validate_record_state(record: OperationRecord) -> None:
    has_reply = record.reply is not None
    has_reply_key = record.reply_idempotency_key is not None
    if has_reply != has_reply_key:
        raise IntegrityError("stored communication reply shape is invalid")
    if has_reply and record.kind is not OperationKind.REQUEST:
        raise IntegrityError("stored notification operation has an unexpected reply")
    if record.state is OperationState.OPEN and (
        has_reply or record.ack_idempotency_key is not None
    ):
        raise IntegrityError("stored open communication operation has terminal fields")
    if record.state is OperationState.REPLIED and (
        record.kind is not OperationKind.REQUEST
        or not has_reply
        or record.ack_idempotency_key is not None
    ):
        raise IntegrityError("stored replied communication operation has an invalid shape")
    if record.state is OperationState.ACKED:
        if record.ack_idempotency_key is None:
            raise IntegrityError("stored acknowledged operation has no acknowledgement key")
        if record.kind.requires_reply != has_reply:
            raise IntegrityError("stored acknowledged operation has an invalid reply shape")
    elif record.ack_idempotency_key is not None:
        raise IntegrityError("stored non-acknowledged operation has an acknowledgement key")


def _record_from_mapping(value: Mapping[str, Any], integrity_key: bytes) -> OperationRecord:
    expected = {
        "schema",
        "operation_id",
        "idempotency_key",
        "kind",
        "scope",
        "state",
        "reason",
        "depth",
        "metadata",
        "reply",
        "reply_idempotency_key",
        "ack_idempotency_key",
        "continuation_of",
        "deadline_seconds",
        "created_at",
        "updated_at",
        "deadline_at",
        "semantic_sha256",
        "integrity_hmac_sha256",
    }
    if set(value) != expected or value.get("schema") != OPERATION_SCHEMA:
        raise IntegrityError("stored communication operation has an invalid shape")
    required_string_fields = {
        "schema",
        "operation_id",
        "idempotency_key",
        "kind",
        "state",
        "reason",
        "created_at",
        "updated_at",
        "deadline_at",
        "semantic_sha256",
        "integrity_hmac_sha256",
    }
    if any(not isinstance(value[field], str) for field in required_string_fields):
        raise IntegrityError("stored communication operation fields are invalid")
    if not isinstance(value["scope"], Mapping):
        raise IntegrityError("stored communication scope is invalid")
    if isinstance(value["depth"], bool) or not isinstance(value["depth"], int):
        raise IntegrityError("stored communication operation depth is invalid")
    if isinstance(value["deadline_seconds"], bool) or not isinstance(
        value["deadline_seconds"], int
    ):
        raise IntegrityError("stored communication operation deadline is invalid")
    for field in ("reply_idempotency_key", "ack_idempotency_key", "continuation_of"):
        if value[field] is not None and not isinstance(value[field], str):
            raise IntegrityError("stored communication optional identifier is invalid")
    if not isinstance(value["metadata"], Mapping):
        raise IntegrityError("stored communication metadata is invalid")
    reply = value["reply"]
    if reply is not None and not isinstance(reply, Mapping):
        raise IntegrityError("stored communication reply is invalid")
    try:
        record = OperationRecord(
            schema=value["schema"],
            operation_id=value["operation_id"],
            idempotency_key=value["idempotency_key"],
            kind=OperationKind(value["kind"]),
            scope=CommunicationScope.from_mapping(value["scope"]),
            state=OperationState(value["state"]),
            reason=value["reason"],
            depth=value["depth"],
            metadata=dict(value["metadata"]),
            reply=dict(reply) if reply is not None else None,
            reply_idempotency_key=(
                value["reply_idempotency_key"]
                if value["reply_idempotency_key"] is not None
                else None
            ),
            ack_idempotency_key=(
                value["ack_idempotency_key"] if value["ack_idempotency_key"] is not None else None
            ),
            continuation_of=(
                value["continuation_of"] if value["continuation_of"] is not None else None
            ),
            deadline_seconds=value["deadline_seconds"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            deadline_at=value["deadline_at"],
            semantic_sha256=value["semantic_sha256"],
            integrity_hmac_sha256=value["integrity_hmac_sha256"],
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise IntegrityError("stored communication operation is invalid") from exc
    if record.operation_id != stable_id("operation", record.idempotency_key):
        raise IntegrityError("stored communication operation identity does not match its key")
    if isinstance(value["depth"], bool) or record.depth < 0:
        raise IntegrityError("stored communication operation depth is invalid")
    if (
        isinstance(value["deadline_seconds"], bool)
        or record.deadline_seconds < 1
        or not isinstance(record.idempotency_key, str)
        or not 0 < len(record.idempotency_key) <= 256
    ):
        raise IntegrityError("stored communication operation identity or deadline is invalid")
    if record.continuation_of is not None:
        try:
            _require_typed("stored continuation_of", record.continuation_of, "operation")
        except ValidationError as exc:
            raise IntegrityError("stored communication continuation is invalid") from exc
    try:
        created_at = parse_timestamp(record.created_at)
        updated_at = parse_timestamp(record.updated_at)
        deadline_at = parse_timestamp(record.deadline_at)
    except ValueError as exc:
        raise IntegrityError("stored communication operation timestamps are invalid") from exc
    if updated_at < created_at or abs(deadline_at - created_at - record.deadline_seconds) > 1e-6:
        raise IntegrityError("stored communication operation timeline is invalid")
    try:
        _require_sha256("stored semantic_sha256", record.semantic_sha256)
        _require_sha256("stored integrity_hmac_sha256", record.integrity_hmac_sha256)
    except ValidationError as exc:
        raise IntegrityError("stored communication operation digest is invalid") from exc
    if record.semantic_sha256 != _semantic_sha256(record.as_dict()):
        raise IntegrityError("stored communication operation semantic digest does not match")
    expected_hmac = _integrity_hmac_sha256(record.as_dict(), integrity_key)
    if not hmac.compare_digest(record.integrity_hmac_sha256, expected_hmac):
        raise IntegrityError("stored communication operation authentication tag does not match")
    _validate_record_state(record)
    return record


class CommunicationStore:
    """One atomic document per operational exchange, private to the runtime state root."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        wall_clock: Callable[[], float] = time.time,
        limits: OperationLimits | None = None,
        security_policy: SecurityPolicy | None = None,
        read_only: bool = False,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.root = self.state_root / "runtime" / "communication"
        self.operation_root = self.root / "operations"
        self.lock_path = self.root / "communication.lock"
        self.integrity_key_path = self.root / "integrity.key"
        self.clock = clock
        self.wall_clock = wall_clock
        self.limits = limits or OperationLimits()
        self.security_policy = security_policy or SecurityPolicy()
        self.read_only = read_only
        if not read_only:
            ensure_private_directory(self.state_root, policy=COMMUNICATION_STORAGE)
            ensure_private_directory(self.root, policy=COMMUNICATION_STORAGE)
            ensure_private_directory(self.operation_root, policy=COMMUNICATION_STORAGE)
            with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
                self._integrity_key = self._load_or_create_integrity_key()
        else:
            self._integrity_key = None

    def _load_integrity_key(self) -> bytes:
        if self.integrity_key_path.is_symlink():
            raise IntegrityError("communication integrity key must not be a symlink")
        descriptor = -1
        try:
            descriptor = os.open(
                self.integrity_key_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise IntegrityError("communication integrity key is not a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise IntegrityError("communication integrity key permissions must be 0600")
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise IntegrityError("communication integrity key has a foreign owner")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                key = handle.read(_INTEGRITY_KEY_BYTES + 1)
            descriptor = -1
        except OSError as exc:
            raise IntegrityError("communication integrity key is unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(key) != _INTEGRITY_KEY_BYTES:
            raise IntegrityError("communication integrity key has an invalid length")
        return key

    def _load_or_create_integrity_key(self) -> bytes:
        if self.integrity_key_path.is_symlink():
            raise IntegrityError("communication integrity key must not be a symlink")
        if not self.integrity_key_path.exists():
            atomic_write_replace(
                self.integrity_key_path,
                os.urandom(_INTEGRITY_KEY_BYTES),
                mode=0o600,
            )
        return self._load_integrity_key()

    def _integrity_key_bytes(self) -> bytes:
        if self._integrity_key is None:
            self._integrity_key = self._load_integrity_key()
        else:
            current = self._load_integrity_key()
            if not hmac.compare_digest(self._integrity_key, current):
                raise IntegrityError("communication integrity key changed during store lifetime")
        return self._integrity_key

    def _require_writable(self) -> None:
        if self.read_only:
            raise LifecycleConflictError("communication store was opened read-only")

    def _path(self, operation_id: str) -> Path:
        _require_typed("operation_id", operation_id, "operation")
        return self.operation_root / f"{operation_id}.json"

    def _read_document(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise IntegrityError("communication operation document must not be a symlink")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("communication operation document is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read()
            descriptor = -1
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("communication operation document is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict) or raw != canonical_state_bytes(value):
            raise IntegrityError("communication operation document is not canonical JSON")
        record = _record_from_mapping(value, self._integrity_key_bytes())
        self.security_policy.assert_safe(value, context="operational communication record")
        return value if _record_matches_path(record, path) else _reject_filename_mismatch()

    def _write_document(self, path: Path, record: OperationRecord) -> None:
        value = record.as_dict()
        self.security_policy.assert_safe(value, context="operational communication record")
        atomic_write_replace(path, strict_state_bytes(value), mode=0o600)

    def _documents(self) -> list[OperationRecord]:
        records: list[OperationRecord] = []
        for path in sorted(self.operation_root.glob("operation.*.json")):
            records.append(
                _record_from_mapping(self._read_document(path), self._integrity_key_bytes())
            )
        return records

    def _find(self, operation_id: str) -> OperationRecord | None:
        path = self._path(operation_id)
        if not path.exists():
            return None
        return _record_from_mapping(self._read_document(path), self._integrity_key_bytes())

    def _load(self, operation_id: str) -> OperationRecord:
        record = self._find(operation_id)
        if record is None:
            raise CommunicationAuthorizationError(_UNAVAILABLE)
        return record

    @staticmethod
    def _assert_participant(record: OperationRecord, session_id: str) -> None:
        if session_id not in record.scope.participants():
            raise CommunicationAuthorizationError(_UNAVAILABLE)

    @staticmethod
    def _assert_not_foreign(
        record: OperationRecord,
        *,
        workspace_fingerprint: str | None = None,
        delegation_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        if (
            (
                workspace_fingerprint is not None
                and workspace_fingerprint != record.scope.workspace_fingerprint
            )
            or (delegation_id is not None and delegation_id != record.scope.delegation_id)
            or (attempt_id is not None and attempt_id != record.scope.attempt_id)
        ):
            raise CommunicationAuthorizationError(_UNAVAILABLE)

    @staticmethod
    def _assert_current_revision(record: OperationRecord, target_revision: str | None) -> None:
        if target_revision is not None and target_revision != record.scope.target_revision:
            raise LifecycleConflictError(
                "communication operation is stale for the current target revision"
            )

    def _assert_metadata_within_budget(self, metadata: Mapping[str, Any]) -> None:
        if len(strict_state_bytes(dict(metadata))) > self.limits.max_metadata_bytes:
            raise ValidationError("communication metadata exceeds the configured size limit")

    def _chain_depth(self, parent: OperationRecord) -> int:
        """Depth is fixed by the immediate parent; the rest of the walk only guards
        against a corrupted or cyclic ancestor chain, it must not perturb the result."""

        depth = parent.depth + 1
        visited = {parent.operation_id}
        cursor = parent
        while cursor.continuation_of is not None:
            ancestor_id = cursor.continuation_of
            if ancestor_id in visited:
                raise ValidationError("communication continuation chain is cyclic")
            visited.add(ancestor_id)
            if len(visited) > self.limits.max_chain_depth + 1:
                raise IntegrityError("communication continuation chain is corrupt")
            ancestor = self._find(ancestor_id)
            if ancestor is None:
                raise IntegrityError(
                    "communication continuation chain references a missing ancestor"
                )
            cursor = ancestor
        return depth

    def request(self, spec: OperationRequestSpec) -> OperationRecord:
        self._require_writable()
        self.security_policy.assert_safe(
            dict(spec.metadata), context="operational communication metadata"
        )
        self._assert_metadata_within_budget(spec.metadata)
        if not (
            self.limits.min_deadline_seconds
            <= spec.deadline_seconds
            <= self.limits.max_deadline_seconds
        ):
            raise ValidationError("communication deadline is out of the configured bounds")
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            existing = self._find(spec.operation_id)
            if existing is not None:
                unchanged = (
                    existing.idempotency_key == spec.idempotency_key
                    and dict(existing.metadata) == dict(spec.metadata)
                    and existing.kind is spec.kind
                    and existing.scope == spec.scope
                    and existing.continuation_of == spec.continuation_of
                    and existing.deadline_seconds == spec.deadline_seconds
                )
                if not unchanged:
                    raise IdempotencyConflictError(
                        "communication idempotency key was reused for a different operation"
                    )
                return existing

            if spec.continuation_of is not None:
                parent = self._find(spec.continuation_of)
                if parent is None:
                    raise ValidationError("communication continuation_of operation does not exist")
                if parent.scope != spec.scope:
                    raise ValidationError(
                        "communication continuation_of belongs to a foreign delegation scope"
                    )
                depth = self._chain_depth(parent)
            else:
                depth = 0
            if depth > self.limits.max_chain_depth:
                raise ValidationError(
                    "communication continuation chain exceeds the configured depth limit"
                )

            timestamp = iso_timestamp(self.clock())
            record = OperationRecord(
                schema=OPERATION_SCHEMA,
                operation_id=spec.operation_id,
                idempotency_key=spec.idempotency_key,
                kind=spec.kind,
                scope=spec.scope,
                state=OperationState.OPEN,
                reason="opened",
                depth=depth,
                metadata=dict(spec.metadata),
                reply=None,
                reply_idempotency_key=None,
                ack_idempotency_key=None,
                continuation_of=spec.continuation_of,
                deadline_seconds=spec.deadline_seconds,
                created_at=timestamp,
                updated_at=timestamp,
                deadline_at=iso_timestamp(
                    self._deadline_after(spec.deadline_seconds, base=timestamp)
                ),
                semantic_sha256="0" * 64,
                integrity_hmac_sha256="0" * 64,
            )
            record = _seal_record(record, self._integrity_key_bytes())
            self._write_document(self._path(record.operation_id), record)
            return record

    def _deadline_after(self, seconds: int, *, base: str) -> float:
        return parse_timestamp(base) + seconds

    def _lazy_expire(self, record: OperationRecord) -> OperationRecord:
        if record.state.terminal:
            return record
        if self.wall_clock() < parse_timestamp(record.deadline_at):
            return record
        return self._transition(record, OperationState.EXPIRED, reason="deadline_exceeded")

    def _transition(
        self,
        record: OperationRecord,
        target: OperationState,
        *,
        reason: str,
        reply: Mapping[str, Any] | None = None,
        reply_idempotency_key: str | None = None,
        ack_idempotency_key: str | None = None,
    ) -> OperationRecord:
        self._require_writable()
        if target not in _TRANSITIONS.get(record.state, frozenset()):
            raise LifecycleConflictError(
                f"illegal communication transition {record.state.value} -> {target.value}"
            )
        updated = replace(
            record,
            state=target,
            reason=reason,
            reply=reply if reply is not None else record.reply,
            reply_idempotency_key=(
                reply_idempotency_key
                if reply_idempotency_key is not None
                else record.reply_idempotency_key
            ),
            ack_idempotency_key=(
                ack_idempotency_key
                if ack_idempotency_key is not None
                else record.ack_idempotency_key
            ),
            updated_at=iso_timestamp(self.clock()),
        )
        updated = _seal_record(updated, self._integrity_key_bytes())
        self._write_document(self._path(record.operation_id), updated)
        return updated

    def check(
        self,
        operation_id: str,
        *,
        requester_session_id: str,
        target_revision: str | None = None,
        workspace_fingerprint: str | None = None,
        delegation_id: str | None = None,
        attempt_id: str | None = None,
    ) -> OperationRecord:
        if self.read_only:
            record = self._load(operation_id)
            self._assert_not_foreign(
                record,
                workspace_fingerprint=workspace_fingerprint,
                delegation_id=delegation_id,
                attempt_id=attempt_id,
            )
            self._assert_participant(record, requester_session_id)
            self._assert_current_revision(record, target_revision)
            return record
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            record = self._load(operation_id)
            self._assert_not_foreign(
                record,
                workspace_fingerprint=workspace_fingerprint,
                delegation_id=delegation_id,
                attempt_id=attempt_id,
            )
            self._assert_participant(record, requester_session_id)
            self._assert_current_revision(record, target_revision)
            return self._lazy_expire(record)

    def reply(
        self,
        operation_id: str,
        *,
        responder_session_id: str,
        idempotency_key: str,
        answer: Mapping[str, Any],
    ) -> OperationRecord:
        self._require_writable()
        if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= 256:
            raise ValidationError("communication reply idempotency key is invalid")
        if not isinstance(answer, Mapping):
            raise ValidationError("communication reply answer must be a mapping")
        self.security_policy.assert_safe(dict(answer), context="operational communication reply")
        self._assert_metadata_within_budget(answer)
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            record = self._load(operation_id)
            self._assert_participant(record, responder_session_id)
            if responder_session_id not in record.scope.allowed_recipient_session_ids:
                raise CommunicationAuthorizationError(
                    "only an allowed recipient may reply to this communication operation"
                )
            if record.kind is not OperationKind.REQUEST:
                raise LifecycleConflictError("only a request operation accepts a reply")
            record = self._lazy_expire(record)
            if record.state is OperationState.REPLIED:
                if record.reply_idempotency_key == idempotency_key and dict(
                    record.reply or {}
                ) == dict(answer):
                    return record
                raise LifecycleConflictError(
                    "communication operation already has an exactly-once reply"
                )
            return self._transition(
                record,
                OperationState.REPLIED,
                reason="replied",
                reply=dict(answer),
                reply_idempotency_key=idempotency_key,
            )

    def ack(
        self,
        operation_id: str,
        *,
        acker_session_id: str,
        idempotency_key: str,
    ) -> OperationRecord:
        self._require_writable()
        if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= 256:
            raise ValidationError("communication ack idempotency key is invalid")
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            record = self._load(operation_id)
            self._assert_participant(record, acker_session_id)
            if record.kind.requires_reply:
                if acker_session_id != record.scope.sender_session_id:
                    raise CommunicationAuthorizationError(
                        "only the requesting session may acknowledge a reply"
                    )
                expected = OperationState.REPLIED
            else:
                if acker_session_id not in record.scope.allowed_recipient_session_ids:
                    raise CommunicationAuthorizationError(
                        "only an allowed recipient may acknowledge a notification"
                    )
                expected = OperationState.OPEN
            record = self._lazy_expire(record)
            if record.state is OperationState.ACKED:
                if record.ack_idempotency_key == idempotency_key:
                    return record
                raise LifecycleConflictError(
                    "communication operation already has an exactly-once ack"
                )
            if record.state is not expected:
                raise LifecycleConflictError(
                    "illegal communication transition "
                    f"{record.state.value} -> {OperationState.ACKED.value}"
                )
            return self._transition(
                record,
                OperationState.ACKED,
                reason="acked",
                ack_idempotency_key=idempotency_key,
            )

    def request_cancel(self, operation_id: str, *, by_session_id: str) -> OperationRecord:
        self._require_writable()
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            record = self._load(operation_id)
            self._assert_participant(record, by_session_id)
            if by_session_id != record.scope.sender_session_id:
                raise CommunicationAuthorizationError(
                    "only the requesting session may request cancellation"
                )
            record = self._lazy_expire(record)
            if record.state is OperationState.CANCEL_REQUESTED:
                return record
            if record.state is OperationState.CANCELLED:
                return record
            return self._transition(
                record,
                OperationState.CANCEL_REQUESTED,
                reason=f"cancel_requested_by:{by_session_id}",
            )

    def confirm_cancel(self, operation_id: str, *, by_session_id: str) -> OperationRecord:
        self._require_writable()
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            record = self._load(operation_id)
            self._assert_participant(record, by_session_id)
            if by_session_id not in record.scope.allowed_recipient_session_ids:
                raise CommunicationAuthorizationError(
                    "only an allowed recipient may confirm cancellation"
                )
            record = self._lazy_expire(record)
            if record.state is OperationState.CANCELLED:
                return record
            return self._transition(
                record,
                OperationState.CANCELLED,
                reason=f"cancel_confirmed_by:{by_session_id}",
            )

    def reconcile(self) -> tuple[OperationRecord, ...]:
        self._require_writable()
        reconciled: list[OperationRecord] = []
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            for record in self._documents():
                if record.state.terminal:
                    continue
                reconciled.append(self._lazy_expire(record))
        return tuple(reconciled)

    def list_operations(self, *, requester_session_id: str) -> tuple[OperationRecord, ...]:
        _require_typed("requester_session_id", requester_session_id, "session")

        def visible_records() -> tuple[OperationRecord, ...]:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._documents()
                        if requester_session_id in item.scope.participants()
                    ),
                    key=lambda item: (item.created_at, item.operation_id),
                )
            )

        if self.read_only:
            return visible_records()
        with exclusive_lock(self.lock_path, policy=COMMUNICATION_STORAGE):
            return visible_records()


def _record_matches_path(record: OperationRecord, path: Path) -> bool:
    return path.name == f"{record.operation_id}.json"


def _reject_filename_mismatch() -> Any:
    raise IntegrityError("communication operation filename does not match its identity")
