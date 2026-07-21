"""Crash-safe operational attempt state for the local delegation broker."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_commons.core.ids import stable_id
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy
from agent_commons.storage.atomic import atomic_write_replace

from .diagnostics import DiagnosticCode, classify_process_result
from .model import BuiltinProfileId, CorrelationIds, Provider, _safe_identifier
from .policy import PolicyViolationError, RuntimePolicy, RuntimeUsage
from .subprocess_runner import ProcessResult, RunOutcome

REQUEST_SCHEMA = "agent_commons.runtime_request.v3"
ATTEMPT_SCHEMA = "agent_commons.runtime_attempt.v3"
_LEGACY_REQUEST_SCHEMA = "agent_commons.runtime_request.v2"
_LEGACY_ATTEMPT_SCHEMA = "agent_commons.runtime_attempt.v2"
_REQUEST_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_FILE_LOCKS: dict[str, threading.Lock] = {}


class AttemptState(StrEnum):
    RESERVED = "reserved"
    LAUNCHING = "launching"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEEDS_OPERATOR = "needs_operator"

    @property
    def terminal(self) -> bool:
        return self in {
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.CANCELLED,
            AttemptState.TIMED_OUT,
            AttemptState.NEEDS_OPERATOR,
        }


class AttemptReason(StrEnum):
    RESERVED = "reserved"
    BROKER_RESTART_AMBIGUOUS = "broker_restart_ambiguous"


_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.RESERVED: frozenset(
        {
            AttemptState.LAUNCHING,
            AttemptState.CANCELLED,
            AttemptState.FAILED,
            AttemptState.NEEDS_OPERATOR,
        }
    ),
    AttemptState.LAUNCHING: frozenset(
        {
            AttemptState.RUNNING,
            AttemptState.CANCELLED,
            AttemptState.FAILED,
            AttemptState.TIMED_OUT,
            AttemptState.NEEDS_OPERATOR,
        }
    ),
    AttemptState.RUNNING: frozenset(
        {
            AttemptState.CANCEL_REQUESTED,
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.CANCELLED,
            AttemptState.TIMED_OUT,
            AttemptState.NEEDS_OPERATOR,
        }
    ),
    AttemptState.CANCEL_REQUESTED: frozenset(
        {
            AttemptState.CANCELLED,
            AttemptState.FAILED,
            AttemptState.TIMED_OUT,
            AttemptState.NEEDS_OPERATOR,
        }
    ),
}


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise IntegrityError(f"runtime operational directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise IntegrityError(f"runtime operational path is not a real directory: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        pass


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    identity = str(path.expanduser().resolve())
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_FILE_LOCKS.setdefault(identity, threading.Lock())
    with process_lock:
        _ensure_private_directory(path.parent)
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def checkout_fingerprint(cwd: str | Path) -> str:
    return hashlib.sha256(str(Path(cwd).expanduser().resolve()).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AttemptSpec:
    idempotency_key: str
    profile_id: BuiltinProfileId
    provider: Provider
    correlation: CorrelationIds
    parent_policy: RuntimePolicy
    child_policy: RuntimePolicy
    checkout_fingerprint: str
    launch_plan_sha256: str = "0" * 64
    launch_key_sha256: str = "0" * 64

    def __post_init__(self) -> None:
        if (
            not isinstance(self.idempotency_key, str)
            or _REQUEST_KEY.fullmatch(self.idempotency_key) is None
        ):
            raise ValidationError("runtime idempotency key is invalid")
        object.__setattr__(self, "profile_id", BuiltinProfileId(self.profile_id))
        object.__setattr__(self, "provider", Provider(self.provider))
        if self.profile_id.provider is not self.provider:
            raise ValidationError("runtime profile and provider do not match")
        if _SHA256.fullmatch(self.checkout_fingerprint) is None:
            raise ValidationError("checkout fingerprint must be a SHA-256 digest")
        if _SHA256.fullmatch(self.launch_plan_sha256) is None:
            raise ValidationError("launch plan must be bound to a SHA-256 digest")
        if _SHA256.fullmatch(self.launch_key_sha256) is None:
            raise ValidationError("launch key must be bound to a SHA-256 digest")

    @property
    def request_id(self) -> str:
        return stable_id("request", self.idempotency_key)

    def semantic_body(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id.value,
            "provider": self.provider.value,
            "correlation": self.correlation.as_dict(),
            "parent_policy": self.parent_policy.as_dict(),
            "child_policy": self.child_policy.as_dict(),
            "checkout_fingerprint": self.checkout_fingerprint,
            "launch_plan_sha256": self.launch_plan_sha256,
            "launch_key_sha256": self.launch_key_sha256,
        }

    @property
    def semantic_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.semantic_body())).hexdigest()


@dataclass(frozen=True, slots=True)
class Attempt:
    schema: str
    attempt_id: str
    request_id: str
    number: int
    profile_id: BuiltinProfileId
    provider: Provider
    correlation: CorrelationIds
    child_policy: RuntimePolicy
    checkout_fingerprint: str
    launch_plan_sha256: str
    launch_key_sha256: str
    state: AttemptState
    reason: str
    pid: int | None
    exit_code: int | None
    stdout_bytes_seen: int
    stderr_bytes_seen: int
    output_truncated: bool
    diagnostic_code: DiagnosticCode
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "attempt_id": self.attempt_id,
            "request_id": self.request_id,
            "number": self.number,
            "profile_id": self.profile_id.value,
            "provider": self.provider.value,
            "correlation": self.correlation.as_dict(),
            "child_policy": self.child_policy.as_dict(),
            "checkout_fingerprint": self.checkout_fingerprint,
            "launch_plan_sha256": self.launch_plan_sha256,
            "launch_key_sha256": self.launch_key_sha256,
            "state": self.state.value,
            "reason": self.reason,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "stdout_bytes_seen": self.stdout_bytes_seen,
            "stderr_bytes_seen": self.stderr_bytes_seen,
            "output_truncated": self.output_truncated,
            "diagnostic_code": self.diagnostic_code.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    attempt: Attempt
    created: bool


def _correlation_from_mapping(value: Mapping[str, Any]) -> CorrelationIds:
    allowed = {
        "delegation_id",
        "target_kind",
        "target_id",
        "target_revision",
        "parent_session_id",
        "child_session_id",
        "trace_id",
    }
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise IntegrityError("stored runtime correlation has unknown fields")
    try:
        return CorrelationIds(**value)
    except (TypeError, ValueError, ValidationError) as exc:
        raise IntegrityError("stored runtime correlation is invalid") from exc


def _attempt_from_mapping(value: Mapping[str, Any]) -> Attempt:
    expected = {
        "schema",
        "attempt_id",
        "request_id",
        "number",
        "profile_id",
        "provider",
        "correlation",
        "child_policy",
        "checkout_fingerprint",
        "launch_plan_sha256",
        "launch_key_sha256",
        "state",
        "reason",
        "pid",
        "exit_code",
        "stdout_bytes_seen",
        "stderr_bytes_seen",
        "output_truncated",
        "diagnostic_code",
        "created_at",
        "updated_at",
    }
    legacy = value.get("schema") == _LEGACY_ATTEMPT_SCHEMA
    if set(value) != (expected - {"diagnostic_code"} if legacy else expected):
        raise IntegrityError("stored runtime attempt has an invalid shape")
    try:
        attempt = Attempt(
            schema=str(value["schema"]),
            attempt_id=str(value["attempt_id"]),
            request_id=str(value["request_id"]),
            number=int(value["number"]),
            profile_id=BuiltinProfileId(str(value["profile_id"])),
            provider=Provider(str(value["provider"])),
            correlation=_correlation_from_mapping(value["correlation"]),
            child_policy=RuntimePolicy.from_mapping(dict(value["child_policy"])),
            checkout_fingerprint=str(value["checkout_fingerprint"]),
            launch_plan_sha256=str(value["launch_plan_sha256"]),
            launch_key_sha256=str(value["launch_key_sha256"]),
            state=AttemptState(str(value["state"])),
            reason=str(value["reason"]),
            pid=int(value["pid"]) if value["pid"] is not None else None,
            exit_code=(int(value["exit_code"]) if value["exit_code"] is not None else None),
            stdout_bytes_seen=int(value["stdout_bytes_seen"]),
            stderr_bytes_seen=int(value["stderr_bytes_seen"]),
            output_truncated=bool(value["output_truncated"]),
            diagnostic_code=(
                DiagnosticCode.LEGACY_UNCLASSIFIED
                if legacy
                else DiagnosticCode(str(value["diagnostic_code"]))
            ),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise IntegrityError("stored runtime attempt is invalid") from exc
    if attempt.schema not in {ATTEMPT_SCHEMA, _LEGACY_ATTEMPT_SCHEMA} or (
        attempt.profile_id.provider is not attempt.provider
    ):
        raise IntegrityError("stored runtime attempt schema/provider is invalid")
    try:
        _safe_identifier("stored runtime attempt_id", attempt.attempt_id)
        _safe_identifier("stored runtime request_id", attempt.request_id)
        _safe_identifier("stored runtime attempt reason", attempt.reason)
    except ValidationError as exc:
        raise IntegrityError("stored runtime attempt reason is invalid") from exc
    if (
        isinstance(value["number"], bool)
        or isinstance(value["stdout_bytes_seen"], bool)
        or isinstance(value["stderr_bytes_seen"], bool)
        or not isinstance(value["output_truncated"], bool)
        or attempt.number < 1
        or attempt.stdout_bytes_seen < 0
        or attempt.stderr_bytes_seen < 0
    ):
        raise IntegrityError("stored runtime attempt counters are invalid")
    if (value["pid"] is not None and (isinstance(value["pid"], bool) or attempt.pid < 1)) or (
        value["exit_code"] is not None and isinstance(value["exit_code"], bool)
    ):
        raise IntegrityError("stored runtime process metadata is invalid")
    if _SHA256.fullmatch(attempt.checkout_fingerprint) is None:
        raise IntegrityError("stored runtime checkout fingerprint is invalid")
    if _SHA256.fullmatch(attempt.launch_plan_sha256) is None:
        raise IntegrityError("stored runtime launch plan digest is invalid")
    if _SHA256.fullmatch(attempt.launch_key_sha256) is None:
        raise IntegrityError("stored runtime launch key digest is invalid")
    return attempt


class AttemptStore:
    """One atomic request document per idempotent broker request."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        security_policy: SecurityPolicy | None = None,
        read_only: bool = False,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.root = self.state_root / "runtime"
        self.request_root = self.root / "requests"
        self.lock_path = self.root / "attempts.lock"
        self.clock = clock
        self.security_policy = security_policy or SecurityPolicy()
        self.read_only = read_only
        if not read_only:
            _ensure_private_directory(self.state_root)
            _ensure_private_directory(self.root)
            _ensure_private_directory(self.request_root)

    def _require_writable(self) -> None:
        if self.read_only:
            raise LifecycleConflictError("runtime attempt store was opened read-only")

    def _path(self, request_id: str) -> Path:
        return self.request_root / f"{request_id}.json"

    def _read_document(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise IntegrityError("runtime request document must not be a symlink")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("runtime request document is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read()
            descriptor = -1
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("runtime request document is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict) or raw != _canonical_bytes(value):
            raise IntegrityError("runtime request document is not canonical JSON")
        self._validate_document(value)
        if value["schema"] == _LEGACY_REQUEST_SCHEMA:
            return {
                **value,
                "schema": REQUEST_SCHEMA,
                "attempts": [
                    {
                        **attempt,
                        "schema": ATTEMPT_SCHEMA,
                        "diagnostic_code": DiagnosticCode.LEGACY_UNCLASSIFIED.value,
                    }
                    for attempt in value["attempts"]
                ],
            }
        return value

    def _validate_document(self, value: Mapping[str, Any]) -> None:
        expected = {"schema", "request_id", "semantic_sha256", "spec", "attempts"}
        if set(value) != expected or value.get("schema") not in {
            REQUEST_SCHEMA,
            _LEGACY_REQUEST_SCHEMA,
        }:
            raise IntegrityError("runtime request document has an invalid envelope")
        if _SHA256.fullmatch(str(value.get("semantic_sha256", ""))) is None:
            raise IntegrityError("runtime request semantic digest is invalid")
        spec = value.get("spec")
        attempts = value.get("attempts")
        if not isinstance(spec, Mapping) or not isinstance(attempts, list) or not attempts:
            raise IntegrityError("runtime request document has an invalid body")
        if set(spec) != {
            "profile_id",
            "provider",
            "correlation",
            "parent_policy",
            "child_policy",
            "checkout_fingerprint",
            "launch_plan_sha256",
            "launch_key_sha256",
        }:
            raise IntegrityError("runtime request spec has an invalid shape")
        try:
            parent_policy = RuntimePolicy.from_mapping(dict(spec["parent_policy"]))
        except (TypeError, ValueError, ValidationError) as exc:
            raise IntegrityError("runtime request parent policy is invalid") from exc
        if hashlib.sha256(_canonical_bytes(spec)).hexdigest() != value["semantic_sha256"]:
            raise IntegrityError("runtime request semantic digest does not match its body")
        previous = 0
        parsed_attempts: list[Attempt] = []
        for raw_attempt in attempts:
            if not isinstance(raw_attempt, Mapping):
                raise IntegrityError("runtime attempt body is invalid")
            attempt = _attempt_from_mapping(raw_attempt)
            if attempt.request_id != value["request_id"] or attempt.number != previous + 1:
                raise IntegrityError("runtime attempt sequence is invalid")
            previous = attempt.number
            parsed_attempts.append(attempt)
        first = parsed_attempts[0]
        attempt_semantics = {
            "profile_id": first.profile_id.value,
            "provider": first.provider.value,
            "correlation": first.correlation.as_dict(),
            "parent_policy": parent_policy.as_dict(),
            "child_policy": first.child_policy.as_dict(),
            "checkout_fingerprint": first.checkout_fingerprint,
            "launch_plan_sha256": first.launch_plan_sha256,
            "launch_key_sha256": first.launch_key_sha256,
        }
        if dict(spec) != attempt_semantics:
            raise IntegrityError("runtime request spec does not match its attempts")
        if any(
            (
                attempt.profile_id,
                attempt.provider,
                attempt.correlation,
                attempt.child_policy,
                attempt.checkout_fingerprint,
                attempt.launch_plan_sha256,
                attempt.launch_key_sha256,
            )
            != (
                first.profile_id,
                first.provider,
                first.correlation,
                first.child_policy,
                first.checkout_fingerprint,
                first.launch_plan_sha256,
                first.launch_key_sha256,
            )
            for attempt in parsed_attempts[1:]
        ):
            raise IntegrityError("runtime retry attempts have inconsistent semantics")
        try:
            first.child_policy.assert_reduction_of(parent_policy)
        except ValidationError as exc:
            raise IntegrityError("runtime child policy exceeds its stored parent") from exc
        self.security_policy.assert_safe(value, context="runtime operational request")

    def _write_document(self, path: Path, value: Mapping[str, Any]) -> None:
        self._validate_document(value)
        atomic_write_replace(path, _canonical_bytes(value), mode=0o600)

    def _documents(self) -> list[tuple[Path, dict[str, Any]]]:
        documents: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.request_root.glob("request.*.json")):
            document = self._read_document(path)
            if path.name != f"{document['request_id']}.json":
                raise IntegrityError("runtime request filename does not match its identity")
            documents.append((path, document))
        return documents

    @staticmethod
    def _latest(document: Mapping[str, Any]) -> Attempt:
        return _attempt_from_mapping(document["attempts"][-1])

    @staticmethod
    def _usage(
        documents: list[tuple[Path, dict[str, Any]]],
        *,
        parent_session_id: str,
        attempts_started: int,
    ) -> RuntimeUsage:
        active = [
            AttemptStore._latest(document)
            for _, document in documents
            if not AttemptStore._latest(document).state.terminal
        ]
        return RuntimeUsage(
            active_fanout=sum(
                attempt.correlation.parent_session_id == parent_session_id for attempt in active
            ),
            attempts_started=attempts_started,
            active_concurrency=len(active),
        )

    @staticmethod
    def _assert_checkout_writer_available(
        documents: list[tuple[Path, dict[str, Any]]], spec: AttemptSpec
    ) -> None:
        if spec.profile_id.independent_reviewer:
            return
        for _, document in documents:
            latest = AttemptStore._latest(document)
            if (
                not latest.state.terminal
                and not latest.profile_id.independent_reviewer
                and latest.checkout_fingerprint == spec.checkout_fingerprint
            ):
                raise PolicyViolationError(
                    "another writable worker is already active for this checkout"
                )

    @staticmethod
    def _new_attempt(spec: AttemptSpec, *, number: int, timestamp: str) -> Attempt:
        return Attempt(
            schema=ATTEMPT_SCHEMA,
            attempt_id=stable_id("attempt", f"{spec.request_id}:{number}"),
            request_id=spec.request_id,
            number=number,
            profile_id=spec.profile_id,
            provider=spec.provider,
            correlation=spec.correlation,
            child_policy=spec.child_policy,
            checkout_fingerprint=spec.checkout_fingerprint,
            launch_plan_sha256=spec.launch_plan_sha256,
            launch_key_sha256=spec.launch_key_sha256,
            state=AttemptState.RESERVED,
            reason=AttemptReason.RESERVED.value,
            pid=None,
            exit_code=None,
            stdout_bytes_seen=0,
            stderr_bytes_seen=0,
            output_truncated=False,
            diagnostic_code=DiagnosticCode.NONE,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def reserve(
        self,
        spec: AttemptSpec,
        *,
        parent_policy: RuntimePolicy,
        retry: bool = False,
    ) -> AttemptReservation:
        self._require_writable()
        if spec.parent_policy != parent_policy:
            raise IdempotencyConflictError("runtime request parent policy does not match")
        spec.child_policy.assert_reduction_of(parent_policy)
        with _exclusive_lock(self.lock_path):
            documents = self._documents()
            path = self._path(spec.request_id)
            matching = next((value for candidate, value in documents if candidate == path), None)
            if matching is not None:
                if matching["semantic_sha256"] != spec.semantic_sha256:
                    raise IdempotencyConflictError(
                        "runtime idempotency key was reused for a different request"
                    )
                latest = self._latest(matching)
                if not retry:
                    return AttemptReservation(latest, False)
                if not latest.state.terminal or latest.state is AttemptState.SUCCEEDED:
                    raise LifecycleConflictError("only an unsuccessful terminal request can retry")
                usage = self._usage(
                    documents,
                    parent_session_id=spec.correlation.parent_session_id,
                    attempts_started=len(matching["attempts"]),
                )
                parent_policy.assert_launch_allowed(usage)
                self._assert_checkout_writer_available(documents, spec)
                attempt = self._new_attempt(
                    spec,
                    number=len(matching["attempts"]) + 1,
                    timestamp=_iso(self.clock()),
                )
                updated = {**matching, "attempts": [*matching["attempts"], attempt.as_dict()]}
                self._write_document(path, updated)
                return AttemptReservation(attempt, True)

            usage = self._usage(
                documents,
                parent_session_id=spec.correlation.parent_session_id,
                attempts_started=0,
            )
            parent_policy.assert_launch_allowed(usage)
            self._assert_checkout_writer_available(documents, spec)
            attempt = self._new_attempt(spec, number=1, timestamp=_iso(self.clock()))
            document = {
                "schema": REQUEST_SCHEMA,
                "request_id": spec.request_id,
                "semantic_sha256": spec.semantic_sha256,
                "spec": spec.semantic_body(),
                "attempts": [attempt.as_dict()],
            }
            self._write_document(path, document)
            return AttemptReservation(attempt, True)

    def list_attempts(self) -> tuple[Attempt, ...]:
        if self.read_only:
            attempts = [
                _attempt_from_mapping(raw_attempt)
                for _, document in self._documents()
                for raw_attempt in document["attempts"]
            ]
            return tuple(sorted(attempts, key=lambda item: (item.created_at, item.attempt_id)))
        with _exclusive_lock(self.lock_path):
            attempts = [
                _attempt_from_mapping(raw_attempt)
                for _, document in self._documents()
                for raw_attempt in document["attempts"]
            ]
        return tuple(sorted(attempts, key=lambda item: (item.created_at, item.attempt_id)))

    def get(self, attempt_id: str) -> Attempt:
        return (
            next(
                (attempt for attempt in self.list_attempts() if attempt.attempt_id == attempt_id),
                None,
            )
            or self._missing_attempt()
        )

    @staticmethod
    def _missing_attempt() -> Attempt:
        raise LifecycleConflictError("runtime attempt does not exist")

    def transition(
        self,
        attempt_id: str,
        target: AttemptState,
        *,
        reason: str,
        pid: int | None = None,
        exit_code: int | None = None,
        stdout_bytes_seen: int = 0,
        stderr_bytes_seen: int = 0,
        output_truncated: bool = False,
        diagnostic_code: DiagnosticCode | str | None = None,
    ) -> Attempt:
        self._require_writable()
        target = AttemptState(target)
        _safe_identifier("runtime transition reason", reason)
        if pid is not None and (isinstance(pid, bool) or pid < 1):
            raise ValidationError("runtime process id is invalid")
        if stdout_bytes_seen < 0 or stderr_bytes_seen < 0:
            raise ValidationError("runtime output counters cannot be negative")
        normalized_diagnostic = (
            DiagnosticCode(diagnostic_code) if diagnostic_code is not None else None
        )
        with _exclusive_lock(self.lock_path):
            for path, document in self._documents():
                for index, raw_attempt in enumerate(document["attempts"]):
                    current = _attempt_from_mapping(raw_attempt)
                    if current.attempt_id != attempt_id:
                        continue
                    if current.state is target:
                        if (
                            current.reason != reason
                            or (pid is not None and current.pid != pid)
                            or (exit_code is not None and current.exit_code != exit_code)
                            or current.stdout_bytes_seen != stdout_bytes_seen
                            or current.stderr_bytes_seen != stderr_bytes_seen
                            or current.output_truncated != bool(output_truncated)
                            or (
                                normalized_diagnostic is not None
                                and current.diagnostic_code is not normalized_diagnostic
                            )
                        ):
                            raise LifecycleConflictError(
                                "idempotent runtime transition has different semantics"
                            )
                        return current
                    if target not in _TRANSITIONS.get(current.state, frozenset()):
                        raise LifecycleConflictError(
                            f"illegal runtime transition {current.state.value} -> {target.value}"
                        )
                    if target is AttemptState.RUNNING and pid is None:
                        raise ValidationError("running attempt requires a process id")
                    updated = replace(
                        current,
                        state=target,
                        reason=reason,
                        pid=pid if pid is not None else current.pid,
                        exit_code=exit_code,
                        stdout_bytes_seen=stdout_bytes_seen,
                        stderr_bytes_seen=stderr_bytes_seen,
                        output_truncated=bool(output_truncated),
                        diagnostic_code=(
                            normalized_diagnostic
                            if normalized_diagnostic is not None
                            else current.diagnostic_code
                        ),
                        updated_at=_iso(self.clock()),
                    )
                    attempts = list(document["attempts"])
                    attempts[index] = updated.as_dict()
                    self._write_document(path, {**document, "attempts": attempts})
                    return updated
        raise LifecycleConflictError("runtime attempt does not exist")

    def finish(self, attempt_id: str, result: ProcessResult) -> Attempt:
        target = {
            RunOutcome.SUCCEEDED: AttemptState.SUCCEEDED,
            RunOutcome.FAILED: AttemptState.FAILED,
            RunOutcome.CANCELLED: AttemptState.CANCELLED,
            RunOutcome.TIMED_OUT: AttemptState.TIMED_OUT,
        }[result.outcome]
        diagnostic = classify_process_result(result)
        return self.transition(
            attempt_id,
            target,
            reason=result.reason.value,
            pid=result.pid,
            exit_code=result.exit_code,
            stdout_bytes_seen=result.stdout_bytes_seen,
            stderr_bytes_seen=result.stderr_bytes_seen,
            output_truncated=result.output_truncated,
            diagnostic_code=diagnostic.code,
        )

    def reconcile(self) -> tuple[Attempt, ...]:
        """Fail closed after a broker restart; live pipes cannot be reattached safely."""

        self._require_writable()
        reconciled: list[Attempt] = []
        with _exclusive_lock(self.lock_path):
            for path, document in self._documents():
                latest = self._latest(document)
                if latest.state.terminal:
                    continue
                updated = replace(
                    latest,
                    state=AttemptState.NEEDS_OPERATOR,
                    reason=AttemptReason.BROKER_RESTART_AMBIGUOUS.value,
                    updated_at=_iso(self.clock()),
                )
                attempts = list(document["attempts"])
                attempts[-1] = updated.as_dict()
                self._write_document(path, {**document, "attempts": attempts})
                reconciled.append(updated)
        return tuple(reconciled)
