"""Atomic, audited multi-resource claims for local agent coordination."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from agent_commons.errors import (
    ClaimConflictError,
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy

from .sessions import (
    SessionRegistry,
    _atomic_publish,
    _canonical_bytes,
    _ensure_private_directory,
    _exclusive_lock,
    _iso,
    _next_event_path,
    _parse_iso,
    _read_audit_event,
    discover_operational_state_root,
)

CLAIM_SCHEMA = "agent_commons.claim.v1"
CLAIM_EVENT_SCHEMA = "agent_commons.claim_event.v1"
_RESOURCE_KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def normalize_resource(resource: str) -> str:
    """Normalize one typed resource without resolving aliases or symlinks."""

    if not isinstance(resource, str) or ":" not in resource:
        raise ValidationError("resource must use '<kind>:<value>' syntax")
    raw_kind, raw_value = resource.split(":", 1)
    kind = raw_kind.strip().lower()
    value = raw_value.strip()
    if not _RESOURCE_KIND.fullmatch(kind) or not value:
        raise ValidationError("resource kind or value is invalid")
    if any(ord(character) < 32 for character in value) or len(value) > 1024:
        raise ValidationError("resource value is not safe")
    if kind == "path":
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValidationError("path resources must be normalized repository-relative paths")
        normalized_parts = tuple(part for part in path.parts if part not in {"", "."})
        if not normalized_parts:
            raise ValidationError("path resource cannot claim the repository root")
        value = PurePosixPath(*normalized_parts).as_posix()
    return f"{kind}:{value}"


def _path_parts(resource: str) -> tuple[str, ...] | None:
    kind, value = resource.split(":", 1)
    return tuple(PurePosixPath(value).parts) if kind == "path" else None


def resources_overlap(left: str, right: str) -> bool:
    """Return whether resources conflict, including path ancestry."""

    left = normalize_resource(left)
    right = normalize_resource(right)
    left_kind = left.split(":", 1)[0]
    right_kind = right.split(":", 1)[0]
    if left_kind != right_kind:
        return False
    if left_kind != "path":
        return left == right
    left_parts = _path_parts(left)
    right_parts = _path_parts(right)
    assert left_parts is not None and right_parts is not None
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def normalize_resources(resources: Sequence[str]) -> tuple[str, ...]:
    """Sort, deduplicate, and remove path descendants covered by an ancestor."""

    normalized = sorted(set(normalize_resource(item) for item in resources))
    if not normalized:
        raise ValidationError("at least one resource is required")
    result: list[str] = []
    for candidate in sorted(
        normalized,
        key=lambda item: (
            item.split(":", 1)[0],
            len(_path_parts(item) or (item,)),
            item,
        ),
    ):
        if any(resources_overlap(existing, candidate) for existing in result):
            # Only exact non-path resources or an already retained path ancestor can overlap.
            continue
        result.append(candidate)
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class Claim:
    schema: str
    claim_id: str
    resources: tuple[str, ...]
    owner_session_id: str
    mode: str
    nonce: str
    acquired_at: str
    renewed_at: str
    expires_at: str
    description: str = ""
    status: str = "active"
    ended_at: str | None = None
    ended_by_session_id: str | None = None
    end_reason: str | None = None

    def active_at(self, timestamp: float) -> bool:
        return self.status == "active" and _parse_iso(self.expires_at) > timestamp


class ClaimService:
    """Serialize all-or-none claim bundles into one append-only audit stream."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        sessions: SessionRegistry,
        state_root: str | Path | None = None,
        policy: SecurityPolicy | None = None,
        clock: Callable[[], float] = time.time,
        read_only: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.state_root = discover_operational_state_root(
            self.repo_root, configured=state_root or sessions.state_root
        )
        if self.state_root != sessions.state_root:
            raise ValidationError("claims and sessions must use the same operational state root")
        self.root = self.state_root / "claims"
        self.event_root = self.root / "events"
        self.lock_path = self.root / "registry.lock"
        self.sessions = sessions
        self.policy = policy or sessions.policy
        self.clock = clock
        self.read_only = read_only
        if not read_only:
            _ensure_private_directory(self.state_root)
            _ensure_private_directory(self.root)
            _ensure_private_directory(self.event_root)

    def _require_writable(self) -> None:
        if self.read_only:
            raise LifecycleConflictError("claim service was opened read-only")

    def _events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in sorted(self.event_root.glob("*.json")):
            value = _read_audit_event(
                path,
                schema=CLAIM_EVENT_SCHEMA,
                label="claim",
            )
            self.policy.assert_safe(value, context="stored claim audit event")
            events.append(value)
        return events

    @staticmethod
    def _claim_from_mapping(value: Mapping[str, Any]) -> Claim:
        try:
            claim = Claim(
                schema=str(value["schema"]),
                claim_id=str(value["claim_id"]),
                resources=tuple(str(item) for item in value["resources"]),
                owner_session_id=str(value["owner_session_id"]),
                mode=str(value["mode"]),
                nonce=str(value["nonce"]),
                acquired_at=str(value["acquired_at"]),
                renewed_at=str(value["renewed_at"]),
                expires_at=str(value["expires_at"]),
                description=str(value.get("description", "")),
                status=str(value.get("status", "active")),
                ended_at=str(value["ended_at"]) if value.get("ended_at") else None,
                ended_by_session_id=(
                    str(value["ended_by_session_id"]) if value.get("ended_by_session_id") else None
                ),
                end_reason=str(value["end_reason"]) if value.get("end_reason") else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("claim audit event has an invalid claim body") from exc
        if claim.schema != CLAIM_SCHEMA or claim.mode not in {"advisory", "exclusive"}:
            raise IntegrityError("claim body has an invalid schema or mode")
        if claim.resources != normalize_resources(claim.resources):
            raise IntegrityError("claim resources are not canonical")
        return claim

    def _state(self) -> dict[str, Claim]:
        state: dict[str, Claim] = {}
        for event in self._events():
            action = event.get("action")
            if action == "acquired":
                raw_claim = event.get("claim")
                if not isinstance(raw_claim, Mapping):
                    raise IntegrityError("claim acquisition event has no claim body")
                claim = self._claim_from_mapping(raw_claim)
                if claim.claim_id in state:
                    raise IntegrityError("claim is acquired more than once")
                state[claim.claim_id] = claim
                continue
            claim_id = str(event.get("claim_id", ""))
            current = state.get(claim_id)
            if current is None or current.status != "active":
                raise IntegrityError("claim event references an absent or inactive claim")
            if event.get("previous_nonce") != current.nonce:
                raise IntegrityError("claim ownership chain is invalid")
            if action == "renewed":
                state[claim_id] = replace(
                    current,
                    nonce=str(event["nonce"]),
                    renewed_at=str(event["recorded_at"]),
                    expires_at=str(event["expires_at"]),
                )
            elif action in {"released", "broken"}:
                state[claim_id] = replace(
                    current,
                    nonce=str(event["nonce"]),
                    status="released" if action == "released" else "broken",
                    ended_at=str(event["recorded_at"]),
                    ended_by_session_id=str(event["actor_session_id"]),
                    end_reason=(str(event["reason"]) if event.get("reason") is not None else None),
                )
            else:
                raise IntegrityError("claim audit event has an unknown action")
        return state

    def _append(self, body: Mapping[str, Any]) -> None:
        self.policy.assert_safe(body, context="claim audit event")
        _atomic_publish(_next_event_path(self.event_root), body)

    def audit_events(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._events())

    def list_claims(self, *, active_only: bool = False) -> tuple[Claim, ...]:
        now = self.clock()
        claims = sorted(self._state().values(), key=lambda item: item.claim_id)
        if active_only:
            claims = [item for item in claims if item.active_at(now)]
        return tuple(claims)

    def get(self, claim_id: str) -> Claim:
        try:
            return self._state()[claim_id]
        except KeyError as exc:
            raise ClaimConflictError("claim does not exist") from exc

    @staticmethod
    def _semantic_digest(request: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical_bytes(request)).hexdigest()

    def acquire(
        self,
        resources: Sequence[str],
        *,
        owner_session_id: str | None,
        mode: str = "exclusive",
        ttl_seconds: int = 2 * 3600,
        description: str = "",
        idempotency_key: str | None = None,
    ) -> Claim:
        self._require_writable()
        if mode not in {"advisory", "exclusive"}:
            raise ValidationError("claim mode must be advisory or exclusive")
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be positive")
        normalized = normalize_resources(resources)
        self.policy.assert_safe(
            {
                "resources": list(normalized),
                "owner_session_id": owner_session_id,
                "mode": mode,
                "ttl_seconds": int(ttl_seconds),
                "description": description,
                "idempotency_key": idempotency_key,
            },
            context="claim acquisition",
        )
        if idempotency_key is not None and not idempotency_key:
            raise ValidationError("idempotency_key cannot be empty")
        # Session lock is always acquired before the claims lock. This prevents
        # close/expiry transitions from racing the final active-session check.
        with _exclusive_lock(self.sessions.lock_path):
            owner = self.sessions.require_active(owner_session_id)
            request = {
                "resources": list(normalized),
                "owner_session_id": owner.session_id,
                "mode": mode,
                "ttl_seconds": int(ttl_seconds),
                "description": description,
            }
            semantic_digest = self._semantic_digest(request)
            with _exclusive_lock(self.lock_path):
                events = self._events()
                if idempotency_key is not None:
                    matches = [
                        event
                        for event in events
                        if event.get("action") == "acquired"
                        and event.get("idempotency_key") == idempotency_key
                    ]
                    if len(matches) > 1:
                        raise IntegrityError(
                            "claim idempotency key has multiple acquisition events"
                        )
                    if matches:
                        if matches[0].get("semantic_sha256") != semantic_digest:
                            raise IdempotencyConflictError(
                                "claim idempotency key is bound to different semantic content"
                            )
                        return self.get(str(matches[0]["claim"]["claim_id"]))

                now = self.clock()
                active = [item for item in self._state().values() if item.active_at(now)]
                conflicts: list[tuple[str, str, str]] = []
                for existing in active:
                    for requested in normalized:
                        for held in existing.resources:
                            if resources_overlap(requested, held):
                                conflicts.append((requested, held, existing.claim_id))
                if conflicts:
                    requested, held, claim_id = sorted(conflicts)[0]
                    raise ClaimConflictError(
                        f"resource {requested} overlaps active claim {claim_id} on {held}"
                    )
                timestamp = _iso(now)
                claim = Claim(
                    schema=CLAIM_SCHEMA,
                    claim_id=f"claim.{uuid.uuid4().hex}",
                    resources=normalized,
                    owner_session_id=owner.session_id,
                    mode=mode,
                    nonce=uuid.uuid4().hex,
                    acquired_at=timestamp,
                    renewed_at=timestamp,
                    expires_at=_iso(now + int(ttl_seconds)),
                    description=description,
                )
                self._append(
                    {
                        "schema": CLAIM_EVENT_SCHEMA,
                        "event_id": f"claim_event.{uuid.uuid4().hex}",
                        "action": "acquired",
                        "recorded_at": timestamp,
                        "actor_session_id": owner.session_id,
                        "idempotency_key": idempotency_key,
                        "semantic_sha256": semantic_digest,
                        "claim": {**asdict(claim), "resources": list(claim.resources)},
                    }
                )
                return claim

    def renew(
        self,
        claim_id: str,
        *,
        owner_session_id: str | None,
        nonce: str,
        ttl_seconds: int = 2 * 3600,
    ) -> Claim:
        self._require_writable()
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be positive")
        self.policy.assert_safe({"claim_id": claim_id, "nonce": nonce}, context="claim renewal")
        with _exclusive_lock(self.sessions.lock_path):
            owner = self.sessions.require_active(owner_session_id)
            with _exclusive_lock(self.lock_path):
                current = self.get(claim_id)
                if not current.active_at(self.clock()):
                    raise ClaimConflictError("claim is inactive or expired")
                if current.owner_session_id != owner.session_id or current.nonce != nonce:
                    raise ClaimConflictError("claim ownership does not match")
                now = self.clock()
                new_nonce = uuid.uuid4().hex
                event = {
                    "schema": CLAIM_EVENT_SCHEMA,
                    "event_id": f"claim_event.{uuid.uuid4().hex}",
                    "action": "renewed",
                    "recorded_at": _iso(now),
                    "actor_session_id": owner.session_id,
                    "claim_id": claim_id,
                    "previous_nonce": current.nonce,
                    "nonce": new_nonce,
                    "expires_at": _iso(now + int(ttl_seconds)),
                }
                self._append(event)
                return replace(
                    current,
                    nonce=new_nonce,
                    renewed_at=str(event["recorded_at"]),
                    expires_at=str(event["expires_at"]),
                )

    def release(
        self,
        claim_id: str,
        *,
        owner_session_id: str | None,
        nonce: str,
    ) -> Claim:
        self._require_writable()
        self.policy.assert_safe({"claim_id": claim_id, "nonce": nonce}, context="claim release")
        with _exclusive_lock(self.sessions.lock_path):
            owner = self.sessions.require_active(owner_session_id)
            with _exclusive_lock(self.lock_path):
                current = self.get(claim_id)
                if not current.active_at(self.clock()):
                    raise ClaimConflictError("claim is inactive or expired")
                if current.owner_session_id != owner.session_id or current.nonce != nonce:
                    raise ClaimConflictError("claim ownership does not match")
                timestamp = _iso(self.clock())
                new_nonce = uuid.uuid4().hex
                self._append(
                    {
                        "schema": CLAIM_EVENT_SCHEMA,
                        "event_id": f"claim_event.{uuid.uuid4().hex}",
                        "action": "released",
                        "recorded_at": timestamp,
                        "actor_session_id": owner.session_id,
                        "claim_id": claim_id,
                        "previous_nonce": current.nonce,
                        "nonce": new_nonce,
                    }
                )
                return replace(
                    current,
                    nonce=new_nonce,
                    status="released",
                    ended_at=timestamp,
                    ended_by_session_id=owner.session_id,
                )

    def break_claim(
        self,
        claim_id: str,
        *,
        actor_session_id: str | None,
        reason: str,
    ) -> Claim:
        self._require_writable()
        if not reason.strip():
            raise ValidationError("breaking a claim requires a reason")
        self.policy.assert_safe({"claim_id": claim_id, "reason": reason}, context="claim break")
        with _exclusive_lock(self.sessions.lock_path):
            actor = self.sessions.require_active(actor_session_id, capability="claim:break")
            with _exclusive_lock(self.lock_path):
                current = self.get(claim_id)
                if not current.active_at(self.clock()):
                    raise ClaimConflictError("claim is inactive or expired")
                timestamp = _iso(self.clock())
                new_nonce = uuid.uuid4().hex
                self._append(
                    {
                        "schema": CLAIM_EVENT_SCHEMA,
                        "event_id": f"claim_event.{uuid.uuid4().hex}",
                        "action": "broken",
                        "recorded_at": timestamp,
                        "actor_session_id": actor.session_id,
                        "claim_id": claim_id,
                        "previous_nonce": current.nonce,
                        "nonce": new_nonce,
                        "reason": reason,
                    }
                )
                return replace(
                    current,
                    nonce=new_nonce,
                    status="broken",
                    ended_at=timestamp,
                    ended_by_session_id=actor.session_id,
                    end_reason=reason,
                )
