"""Explicit, append-only local session registry shared by Git worktrees."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.errors import IntegrityError, LifecycleConflictError, ValidationError
from agent_commons.security import SecurityPolicy

SESSION_SCHEMA = "agent_commons.session.v1"
SESSION_EVENT_SCHEMA = "agent_commons.session_event.v1"
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_AUDIT_EVENT_FILE = re.compile(r"^[0-9]{20}-[a-f0-9]{32}\.json$")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _validate_text(name: str, value: str | None, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{name} is required")
        return None
    normalized = value.strip()
    if required and not normalized:
        raise ValidationError(f"{name} is required")
    if len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ValidationError(f"{name} is not a safe identifier")
    return normalized or None


def discover_operational_state_root(
    repo_root: str | Path,
    *,
    configured: str | Path | None = None,
) -> Path:
    """Return one operational-state directory shared by every Git worktree."""

    configured_value = configured or os.environ.get("AGENT_COMMONS_STATE_ROOT")
    if configured_value:
        return Path(configured_value).expanduser().resolve()
    root = Path(repo_root).expanduser().resolve()
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValidationError("Agent Commons operational state requires a Git repository") from exc
    common = Path(raw)
    if not common.is_absolute():
        common = (root / common).resolve()
    return common / "agent-commons-state"


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    _ensure_private_directory(path.parent)
    with open(path, "a+b", opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _atomic_publish(path: Path, body: Mapping[str, Any]) -> None:
    """Publish one immutable audit event with no overwrite semantics."""

    _ensure_private_directory(path.parent)
    data = _canonical_bytes(body)
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


def _read_audit_event(path: Path, *, schema: str, label: str) -> dict[str, Any]:
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
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{label} audit event is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IntegrityError(f"{label} audit event has an invalid envelope")
    if raw != _canonical_bytes(value):
        raise IntegrityError(f"{label} audit event is not canonical JSON")
    return value


def _next_event_path(event_root: Path) -> Path:
    maximum = 0
    for path in event_root.glob("*.json"):
        if _AUDIT_EVENT_FILE.fullmatch(path.name) is None:
            raise IntegrityError("unexpected operational audit event filename")
        try:
            maximum = max(maximum, int(path.name.split("-", 1)[0]))
        except (ValueError, IndexError):
            raise IntegrityError("unexpected operational audit event filename") from None
    return event_root / f"{maximum + 1:020d}-{uuid.uuid4().hex}.json"


@dataclass(frozen=True, slots=True)
class SourceProducer:
    """Identity reported by imported material, separate from the active writer."""

    client: str
    software: str
    model_family: str | None = None
    model: str | None = None
    principal: str | None = None
    external_session_id: str | None = None

    @classmethod
    def from_value(cls, value: SourceProducer | Mapping[str, Any]) -> SourceProducer:
        if isinstance(value, cls):
            return value
        allowed = {
            "client",
            "software",
            "model_family",
            "model",
            "principal",
            "external_session_id",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValidationError("source_producer has unknown fields")
        return cls(
            client=str(value.get("client", "")),
            software=str(value.get("software", "")),
            model_family=(
                str(value["model_family"]) if value.get("model_family") is not None else None
            ),
            model=str(value["model"]) if value.get("model") is not None else None,
            principal=(str(value["principal"]) if value.get("principal") is not None else None),
            external_session_id=(
                str(value["external_session_id"])
                if value.get("external_session_id") is not None
                else None
            ),
        )

    def validated(self) -> SourceProducer:
        return SourceProducer(
            client=str(_validate_text("source_producer.client", self.client)),
            software=str(_validate_text("source_producer.software", self.software)),
            model_family=_validate_text(
                "source_producer.model_family", self.model_family, required=False
            ),
            model=_validate_text("source_producer.model", self.model, required=False),
            principal=_validate_text("source_producer.principal", self.principal, required=False),
            external_session_id=_validate_text(
                "source_producer.external_session_id",
                self.external_session_id,
                required=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class Session:
    schema: str
    session_id: str
    stable_instance_id: str
    principal: str
    client: str
    software: str
    model_family: str | None
    model: str | None
    role: str
    capabilities: tuple[str, ...]
    source_producer: SourceProducer | None
    nonce: str
    opened_at: str
    last_seen_at: str
    expires_at: str
    status: str = "active"
    closed_at: str | None = None

    @property
    def expired(self) -> bool:
        return _parse_iso(self.expires_at) <= time.time()

    def active_at(self, timestamp: float) -> bool:
        return self.status == "active" and _parse_iso(self.expires_at) > timestamp

    def actor_context(self) -> dict[str, Any]:
        """Return trusted writer context without the ownership nonce."""

        actor = {
            "session_id": self.session_id,
            "stable_instance_id": self.stable_instance_id,
            "principal_id": self.principal,
            "client": self.client,
            "software": self.software,
            "role_id": self.role,
            "capabilities": list(self.capabilities),
        }
        if self.model_family is not None:
            actor["model_family"] = self.model_family
        if self.model is not None:
            actor["model"] = self.model
        if self.source_producer is not None:
            actor["source_producer"] = asdict(self.source_producer)
        return actor


class SessionRegistry:
    """Append-only registry for active local coordination identities."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        state_root: str | Path | None = None,
        policy: SecurityPolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.state_root = discover_operational_state_root(self.repo_root, configured=state_root)
        self.root = self.state_root / "sessions"
        self.event_root = self.root / "events"
        self.lock_path = self.root / "registry.lock"
        self.policy = policy or SecurityPolicy()
        self.clock = clock
        _ensure_private_directory(self.state_root)
        _ensure_private_directory(self.root)
        _ensure_private_directory(self.event_root)

    def _events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in sorted(self.event_root.glob("*.json")):
            value = _read_audit_event(
                path,
                schema=SESSION_EVENT_SCHEMA,
                label="session",
            )
            self.policy.assert_safe(value, context="stored session audit event")
            events.append(value)
        return events

    @staticmethod
    def _session_from_mapping(value: Mapping[str, Any]) -> Session:
        producer_value = value.get("source_producer")
        producer = (
            SourceProducer.from_value(producer_value).validated()
            if isinstance(producer_value, Mapping)
            else None
        )
        try:
            return Session(
                schema=str(value["schema"]),
                session_id=str(value["session_id"]),
                stable_instance_id=str(value["stable_instance_id"]),
                principal=str(value["principal"]),
                client=str(value["client"]),
                software=str(value["software"]),
                model_family=(
                    str(value["model_family"]) if value.get("model_family") is not None else None
                ),
                model=str(value["model"]) if value.get("model") is not None else None,
                role=str(value["role"]),
                capabilities=tuple(str(item) for item in value.get("capabilities", ())),
                source_producer=producer,
                nonce=str(value["nonce"]),
                opened_at=str(value["opened_at"]),
                last_seen_at=str(value["last_seen_at"]),
                expires_at=str(value["expires_at"]),
                status=str(value.get("status", "active")),
                closed_at=(str(value["closed_at"]) if value.get("closed_at") is not None else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("session audit event has an invalid session body") from exc

    def _state(self) -> dict[str, Session]:
        state: dict[str, Session] = {}
        for event in self._events():
            action = event.get("action")
            if action == "opened":
                session_value = event.get("session")
                if not isinstance(session_value, Mapping):
                    raise IntegrityError("session opened event is missing its session")
                session = self._session_from_mapping(session_value)
                if session.schema != SESSION_SCHEMA or session.session_id in state:
                    raise IntegrityError("session opened event conflicts with existing history")
                state[session.session_id] = session
                continue
            session_id = str(event.get("session_id", ""))
            current = state.get(session_id)
            if current is None:
                raise IntegrityError("session event references an unknown session")
            if event.get("previous_nonce") != current.nonce:
                raise IntegrityError("session event ownership chain is invalid")
            if action == "heartbeat":
                if current.status != "active":
                    raise IntegrityError("closed session has a heartbeat event")
                state[session_id] = replace(
                    current,
                    last_seen_at=str(event["recorded_at"]),
                    expires_at=str(event["expires_at"]),
                    nonce=str(event["nonce"]),
                )
            elif action == "closed":
                if current.status != "active":
                    raise IntegrityError("session is closed more than once")
                state[session_id] = replace(
                    current,
                    status="closed",
                    closed_at=str(event["recorded_at"]),
                    last_seen_at=str(event["recorded_at"]),
                    nonce=str(event["nonce"]),
                )
            else:
                raise IntegrityError("session audit event has an unknown action")
        return state

    def _append(self, body: Mapping[str, Any]) -> None:
        self.policy.assert_safe(body, context="session audit event")
        _atomic_publish(_next_event_path(self.event_root), body)

    @staticmethod
    def _validate_capabilities(capabilities: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(str(item).strip() for item in capabilities)))
        if any(not _CAPABILITY.fullmatch(item) for item in normalized):
            raise ValidationError("capabilities must use stable lowercase identifiers")
        return normalized

    def open_session(
        self,
        *,
        stable_instance_id: str,
        principal: str,
        client: str,
        software: str,
        role: str,
        capabilities: Sequence[str] = (),
        model_family: str | None = None,
        model: str | None = None,
        source_producer: SourceProducer | Mapping[str, Any] | None = None,
        ttl_seconds: int = 8 * 3600,
    ) -> Session:
        stable_instance = str(_validate_text("stable_instance_id", stable_instance_id))
        if len(stable_instance) < 8:
            raise ValidationError("stable_instance_id must be an explicit durable identifier")
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be positive")
        producer = (
            SourceProducer.from_value(source_producer).validated()
            if source_producer is not None
            else None
        )
        request = {
            "stable_instance_id": stable_instance,
            "principal": _validate_text("principal", principal),
            "client": _validate_text("client", client),
            "software": _validate_text("software", software),
            "model_family": _validate_text("model_family", model_family, required=False),
            "model": _validate_text("model", model, required=False),
            "role": _validate_text("role", role),
            "capabilities": self._validate_capabilities(capabilities),
            "source_producer": producer,
        }
        # Reject sensitive identity metadata before allocating a session ID or event path.
        self.policy.assert_safe(
            {
                **request,
                "source_producer": asdict(producer) if producer is not None else None,
            },
            context="session registration",
        )
        with _exclusive_lock(self.lock_path):
            now = self.clock()
            active_for_instance = [
                item
                for item in self._state().values()
                if item.stable_instance_id == stable_instance and item.active_at(now)
            ]
            if len(active_for_instance) > 1:
                raise IntegrityError("stable instance has multiple active sessions")
            if active_for_instance:
                current = active_for_instance[0]
                comparable = {
                    "stable_instance_id": current.stable_instance_id,
                    "principal": current.principal,
                    "client": current.client,
                    "software": current.software,
                    "model_family": current.model_family,
                    "model": current.model,
                    "role": current.role,
                    "capabilities": current.capabilities,
                    "source_producer": current.source_producer,
                }
                if comparable == request:
                    return current
                raise LifecycleConflictError(
                    "stable instance already has an active session with different identity metadata"
                )
            timestamp = _iso(now)
            session = Session(
                schema=SESSION_SCHEMA,
                session_id=f"session.{uuid.uuid4().hex}",
                stable_instance_id=stable_instance,
                principal=str(request["principal"]),
                client=str(request["client"]),
                software=str(request["software"]),
                model_family=request["model_family"],
                model=request["model"],
                role=str(request["role"]),
                capabilities=request["capabilities"],
                source_producer=producer,
                nonce=uuid.uuid4().hex,
                opened_at=timestamp,
                last_seen_at=timestamp,
                expires_at=_iso(now + int(ttl_seconds)),
            )
            self._append(
                {
                    "schema": SESSION_EVENT_SCHEMA,
                    "event_id": f"session_event.{uuid.uuid4().hex}",
                    "action": "opened",
                    "recorded_at": timestamp,
                    "session": {
                        **asdict(session),
                        "capabilities": list(session.capabilities),
                    },
                }
            )
            return session

    def list_sessions(self, *, active_only: bool = False) -> tuple[Session, ...]:
        now = self.clock()
        sessions = sorted(self._state().values(), key=lambda item: item.session_id)
        if active_only:
            sessions = [item for item in sessions if item.active_at(now)]
        return tuple(sessions)

    def require_active(
        self,
        session_id: str | None,
        *,
        capability: str | None = None,
    ) -> Session:
        if not session_id:
            raise LifecycleConflictError("an explicit active session is required for writes")
        session = self._state().get(session_id)
        if session is None or not session.active_at(self.clock()):
            raise LifecycleConflictError("session is absent, expired, or closed")
        if not session.stable_instance_id:
            raise LifecycleConflictError("session has no stable instance identity")
        if capability is not None and capability not in session.capabilities:
            raise LifecycleConflictError("session lacks the required capability")
        return session

    def assert_can_write(
        self, session_id: str | None, *, capability: str | None = None
    ) -> dict[str, Any]:
        """Fail closed and return trusted actor context for another writer."""

        return self.require_active(session_id, capability=capability).actor_context()

    def heartbeat(
        self,
        session_id: str,
        *,
        nonce: str,
        ttl_seconds: int = 8 * 3600,
    ) -> Session:
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be positive")
        self.policy.assert_safe(
            {"session_id": session_id, "nonce": nonce}, context="session heartbeat"
        )
        with _exclusive_lock(self.lock_path):
            current = self.require_active(session_id)
            if current.nonce != nonce:
                raise LifecycleConflictError("session ownership nonce does not match")
            now = self.clock()
            new_nonce = uuid.uuid4().hex
            event = {
                "schema": SESSION_EVENT_SCHEMA,
                "event_id": f"session_event.{uuid.uuid4().hex}",
                "action": "heartbeat",
                "recorded_at": _iso(now),
                "session_id": session_id,
                "previous_nonce": current.nonce,
                "nonce": new_nonce,
                "expires_at": _iso(now + int(ttl_seconds)),
            }
            self._append(event)
            return replace(
                current,
                last_seen_at=str(event["recorded_at"]),
                expires_at=str(event["expires_at"]),
                nonce=new_nonce,
            )

    def close(self, session_id: str, *, nonce: str) -> Session:
        self.policy.assert_safe({"session_id": session_id, "nonce": nonce}, context="session close")
        with _exclusive_lock(self.lock_path):
            current = self.require_active(session_id)
            if current.nonce != nonce:
                raise LifecycleConflictError("session ownership nonce does not match")
            timestamp = _iso(self.clock())
            new_nonce = uuid.uuid4().hex
            self._append(
                {
                    "schema": SESSION_EVENT_SCHEMA,
                    "event_id": f"session_event.{uuid.uuid4().hex}",
                    "action": "closed",
                    "recorded_at": timestamp,
                    "session_id": session_id,
                    "previous_nonce": current.nonce,
                    "nonce": new_nonce,
                }
            )
            return replace(
                current,
                status="closed",
                closed_at=timestamp,
                last_seen_at=timestamp,
                nonce=new_nonce,
            )
