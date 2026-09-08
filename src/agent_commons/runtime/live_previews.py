"""Private, expiring preview advertisements; never a process manager or URL client.

Publication requires exact canonical task/delegation bindings. An advertisement
may survive its producer's direct, uncorrected success until its original expiry,
with the exact task binding unchanged. Server lifetime remains externally owned.
The operator API reports readiness; this module never proves reachability. Only literal loopback
HTTP origins are accepted, with no route, token, credentials, query or fragment.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from agent_commons.core.canonical import loads_json_strict
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.platform_support import lock_exclusive, unlock
from agent_commons.storage.opstate import strict_state_bytes

MAX_PREVIEWS = 256
MAX_REGISTRY_BYTES = 512 * 1024
EXPIRED_RETENTION_SECONDS = 24 * 60 * 60
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 3600
_SCHEMA = "agent_commons.live-previews.v1"
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_LOCK = threading.RLock()
_SESSION = re.compile(r"^session\.[a-f0-9]{32}$")
_ID = re.compile(r"^preview\.[a-f0-9]{32}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")
_URL = re.compile(r"^http://(127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})/?$")


class LivePreviewRefusal(Exception):
    def __init__(self, code: str = "live_preview_unavailable", status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        self.message = "The live preview registration is unavailable or no longer current."
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class LivePreview:
    output_id: str
    title: str
    task_id: str
    task_revision: str
    producer_agent_id: str
    producer_session_id: str
    producer_delegation_id: str
    delegation_revision: str
    url: str | None
    origin: str
    published_at: float
    expires_at: float
    reported_state: Literal["starting", "ready", "unavailable"]
    state: Literal["starting", "reported_ready", "unavailable", "expired", "stale"]
    latest: bool = True
    version_count: int = 1
    reachability: Literal["unknown"] = "unknown"


def preview_origin(value: object, *, forbidden_ports: frozenset[int]) -> str:
    if not isinstance(value, str) or not (match := _URL.fullmatch(value)):
        raise LivePreviewRefusal("live_preview_invalid_url", 400)
    port = int(match[2])
    if not 1024 <= port <= 65535 or port in forbidden_ports:
        raise LivePreviewRefusal("live_preview_invalid_url", 400)
    # Exact grammar eliminates browser URL canonicalization and DNS differences.
    return f"http://{match[1]}:{port}"


def _binding(
    snapshot: ProjectSnapshot,
    task_id: str,
    task_revision: str,
    delegation_id: str,
    delegation_revision: str,
) -> tuple[str, str]:
    for value, kind in (
        (task_id, "task"),
        (task_revision, "evt"),
        (delegation_id, "delegation"),
        (delegation_revision, "evt"),
    ):
        if not is_typed_id(value, kind):
            raise LivePreviewRefusal("live_preview_invalid_binding", 400)
    task = snapshot.tasks.get(task_id)
    delegation = snapshot.delegations.get(delegation_id)
    if not task or not delegation or any(issue.severity == "error" for issue in snapshot.issues):
        raise LivePreviewRefusal("live_preview_invalid_binding")
    agent = delegation.get("agent_id")
    child = delegation.get("child_session_id")
    if (
        delegation.get("state") in {"failed", "cancelled", "timed_out", "needs_operator"}
        or (task.get("effective_revision") or task.get("revision")) != task_revision
        or (delegation.get("effective_revision") or delegation.get("revision"))
        != delegation_revision
        or delegation.get("target_ref") != {"kind": "task", "id": task_id}
        or delegation.get("target_revision") != task_revision
        or not isinstance(agent, str)
        or not is_typed_id(agent, "agent")
        or agent not in snapshot.agents
        or not isinstance(child, str)
        or not (_SESSION.fullmatch(child) or is_typed_id(child, "session"))
        or snapshot.entity_revision_actor("delegation", delegation_id, delegation_revision) is None
        or snapshot.entity_revision_actor("task", task_id, task_revision) is None
    ):
        raise LivePreviewRefusal("live_preview_invalid_binding")
    return agent, child


def _read_binding(snapshot: ProjectSnapshot, row: LivePreview) -> None:
    """Keep only the exact advertisement or its direct successful successor.

    This is canonical lineage, not a process lease or a reachability check.
    A different active revision, correction, failure, or changed task cannot
    inherit the advertisement. The original publication clock is never renewed.
    """
    try:
        agent, child = _binding(
            snapshot,
            row.task_id,
            row.task_revision,
            row.producer_delegation_id,
            row.delegation_revision,
        )
        if agent != row.producer_agent_id or child != row.producer_session_id:
            raise LivePreviewRefusal("live_preview_invalid_binding")
        return
    except LivePreviewRefusal:
        pass
    delegation = snapshot.delegations.get(row.producer_delegation_id)
    if not delegation:
        raise LivePreviewRefusal("live_preview_invalid_binding")
    revision = delegation.get("revision")
    if (
        delegation.get("state") != "succeeded"
        or not isinstance(revision, str)
        or (delegation.get("effective_revision") or revision) != revision
        or delegation.get("expected_revision") != row.delegation_revision
        or snapshot.entity_revision_actor(
            "delegation", row.producer_delegation_id, row.delegation_revision
        )
        is None
        or snapshot.entity_revision_actor("delegation", row.producer_delegation_id, revision)
        != row.producer_session_id
    ):
        raise LivePreviewRefusal("live_preview_invalid_binding")
    agent, child = _binding(
        snapshot,
        row.task_id,
        row.task_revision,
        row.producer_delegation_id,
        revision,
    )
    if agent != row.producer_agent_id or child != row.producer_session_id:
        raise LivePreviewRefusal("live_preview_invalid_binding")


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    descriptor = os.open(path.anchor, _FLAGS)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if create:
            os.fchmod(descriptor, 0o700)
        if os.fstat(descriptor).st_mode & 0o077:
            raise LivePreviewRefusal()
        yield descriptor
    finally:
        os.close(descriptor)


class LivePreviewRegistry:
    """Bounded atomic metadata outside the checkout, partitioned by path AND ledger.

    `publish` is the future worker-tool seam: its caller must authenticate and bind
    a worker to its actual delegation before supplying these exact references.
    HTTP callers must pass the operator gate. No author or agent input is accepted.
    Reads are atomic and do not create directories, lock files, or expiry writes.
    Expired advertisements and retry keys are retained for 24 hours after expiry;
    subsequent publications reclaim older entries under the same atomic lock.
    """

    def __init__(
        self,
        state_root: str | Path,
        *,
        project_root: str | Path,
        workspace_id: str,
        forbidden_ports: frozenset[int],
        clock: Callable[[], float] = time.time,
        publication_only: bool = False,
    ) -> None:
        state = Path(os.path.abspath(Path(state_root).expanduser()))
        project = Path(project_root).expanduser().resolve()
        if (
            state.resolve().is_relative_to(project)
            or not workspace_id
            or (not forbidden_ports and not publication_only)
        ):
            raise LivePreviewRefusal()
        if any(type(port) is not int or not 1 <= port <= 65535 for port in forbidden_ports):
            raise LivePreviewRefusal()
        self.workspace_id = workspace_id
        self.project_key = hashlib.sha256(str(project).encode()).hexdigest()
        partition = hashlib.sha256(f"{self.project_key}:{workspace_id}".encode()).hexdigest()
        self.root = state / "runtime" / "live-previews" / partition
        self.forbidden_ports = forbidden_ports
        self.clock = clock
        self.publication_only = publication_only

    def _check_snapshot(self, snapshot: ProjectSnapshot) -> None:
        if snapshot.workspace_id != self.workspace_id or any(
            i.severity == "error" for i in snapshot.issues
        ):
            raise LivePreviewRefusal()

    def _load(self, folder: int) -> list[dict[str, Any]]:
        try:
            fd = os.open("registry.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        except FileNotFoundError:
            return []
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_REGISTRY_BYTES
            ):
                raise LivePreviewRefusal()
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_REGISTRY_BYTES + 1)
            value = loads_json_strict(raw)
        finally:
            os.close(fd)
        if (
            len(raw) > MAX_REGISTRY_BYTES
            or not isinstance(value, dict)
            or set(value) != {"schema", "workspace_id", "project_key", "items"}
            or value["schema"] != _SCHEMA
            or value["workspace_id"] != self.workspace_id
            or value["project_key"] != self.project_key
            or not isinstance(value["items"], list)
            or len(value["items"]) > MAX_PREVIEWS
        ):
            raise LivePreviewRefusal()
        ids: set[str] = set()
        for item in value["items"]:
            if not isinstance(item, dict) or set(item) != {"idempotency_key", "request", "preview"}:
                raise LivePreviewRefusal()
            if (
                not isinstance(item["idempotency_key"], str)
                or not _KEY.fullmatch(item["idempotency_key"])
                or item["idempotency_key"] in ids
            ):
                raise LivePreviewRefusal()
            ids.add(item["idempotency_key"])
            self._decode(item["preview"])
            if not isinstance(item["request"], dict):
                raise LivePreviewRefusal()
        return value["items"]

    def _decode(self, value: object) -> LivePreview:
        if not isinstance(value, dict):
            raise LivePreviewRefusal()
        try:
            row = LivePreview(**value)
        except (TypeError, ValueError):
            raise LivePreviewRefusal() from None
        if (
            not _ID.fullmatch(row.output_id)
            or not isinstance(row.title, str)
            or not 1 <= len(row.title) <= 160
            or any(ord(c) < 32 or ord(c) == 127 for c in row.title)
        ):
            raise LivePreviewRefusal()
        for identifier, kind in (
            (row.task_id, "task"),
            (row.task_revision, "evt"),
            (row.producer_agent_id, "agent"),
            (row.producer_session_id, "session"),
            (row.producer_delegation_id, "delegation"),
            (row.delegation_revision, "evt"),
        ):
            if not (
                is_typed_id(identifier, kind)
                or (
                    kind == "session"
                    and isinstance(identifier, str)
                    and _SESSION.fullmatch(identifier)
                )
            ):
                raise LivePreviewRefusal()
        # Stored origins may become forbidden after a UI restart: retain metadata,
        # but suppress their navigation URL in list() rather than trusting old rules.
        if (
            preview_origin(row.origin, forbidden_ports=frozenset()) != row.origin
            or row.url != row.origin + "/"
            or row.reported_state not in {"starting", "ready", "unavailable"}
            or row.state != {"ready": "reported_ready"}.get(row.reported_state, row.reported_state)
            or row.reachability != "unknown"
            or row.latest is not True
            or row.version_count != 1
        ):
            raise LivePreviewRefusal()
        if (
            any(
                type(number) not in {int, float} or not math.isfinite(number)
                for number in (row.published_at, row.expires_at)
            )
            or not MIN_TTL_SECONDS <= row.expires_at - row.published_at <= MAX_TTL_SECONDS
        ):
            raise LivePreviewRefusal()
        return row

    def publish(self, snapshot: ProjectSnapshot, payload: Mapping[str, Any]) -> LivePreview:
        self._check_snapshot(snapshot)
        fields = {
            "idempotency_key",
            "task_id",
            "task_revision",
            "delegation_id",
            "delegation_revision",
            "title",
            "url",
            "ttl_seconds",
            "reported_state",
        }
        if not isinstance(payload, Mapping) or set(payload) != fields:
            raise LivePreviewRefusal("live_preview_invalid_request", 400)
        owned = dict(payload)
        key, ttl = owned["idempotency_key"], owned["ttl_seconds"]
        if (
            not isinstance(key, str)
            or not _KEY.fullmatch(key)
            or type(ttl) is not int
            or not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS
        ):
            raise LivePreviewRefusal("live_preview_invalid_request", 400)
        origin = preview_origin(owned["url"], forbidden_ports=self.forbidden_ports)
        agent, child = _binding(
            snapshot,
            owned["task_id"],
            owned["task_revision"],
            owned["delegation_id"],
            owned["delegation_revision"],
        )
        now = self.clock()
        row = LivePreview(
            output_id="preview." + uuid.uuid4().hex,
            title=owned["title"],
            task_id=owned["task_id"],
            task_revision=owned["task_revision"],
            producer_agent_id=agent,
            producer_session_id=child,
            producer_delegation_id=owned["delegation_id"],
            delegation_revision=owned["delegation_revision"],
            url=origin + "/",
            origin=origin,
            published_at=now,
            expires_at=now + ttl,
            reported_state=owned["reported_state"],
            state={"ready": "reported_ready"}.get(owned["reported_state"], owned["reported_state"]),
        )
        self._decode(asdict(row))
        with _LOCK, _directory(self.root, create=True) as folder:
            lock = os.open(
                "registry.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=folder
            )
            try:
                if not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_nlink != 1:
                    raise LivePreviewRefusal()
                os.fchmod(lock, 0o600)
                lock_exclusive(lock)
                items = self._load(folder)
                for existing in items:
                    if existing["idempotency_key"] == key:
                        if existing["request"] != owned:
                            raise LivePreviewRefusal("live_preview_idempotency_conflict")
                        # A retry never extends the original lifetime or restores readiness.
                        replayed = self._decode(existing["preview"])
                        return (
                            replace(replayed, state="expired", url=None)
                            if now >= replayed.expires_at
                            else replayed
                        )
                items = [
                    item
                    for item in items
                    if self._decode(item["preview"]).expires_at >= now - EXPIRED_RETENTION_SECONDS
                ]
                if len(items) >= MAX_PREVIEWS:
                    raise LivePreviewRefusal("live_preview_capacity_exceeded")
                items.append({"idempotency_key": key, "request": owned, "preview": asdict(row)})
                self._write(folder, items)
                return row
            finally:
                unlock(lock)
                os.close(lock)

    def _write(self, folder: int, items: list[dict[str, Any]]) -> None:
        data = strict_state_bytes(
            {
                "schema": _SCHEMA,
                "workspace_id": self.workspace_id,
                "project_key": self.project_key,
                "items": items,
            }
        )
        if len(data) > MAX_REGISTRY_BYTES:
            raise LivePreviewRefusal("live_preview_capacity_exceeded")
        name = ".registry-" + uuid.uuid4().hex
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            os.replace(name, "registry.json", src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=folder)
            except FileNotFoundError:
                pass

    def list(
        self, snapshot: ProjectSnapshot, scope_kind: str, scope_id: str, *, versions: str = "latest"
    ) -> tuple[LivePreview, ...]:
        if self.publication_only:
            raise LivePreviewRefusal("live_preview_publication_only", 403)
        self._check_snapshot(snapshot)
        if (
            scope_kind not in {"task", "agent"}
            or not is_typed_id(scope_id, scope_kind)
            or versions not in {"latest", "all"}
        ):
            raise LivePreviewRefusal("live_preview_invalid_scope", 400)
        try:
            with _directory(self.root) as folder:
                items = [self._decode(item["preview"]) for item in self._load(folder)]
        except FileNotFoundError:
            return ()
        result: list[LivePreview] = []
        now = self.clock()
        for row in items:
            if row.expires_at < now - EXPIRED_RETENTION_SECONDS:
                continue
            if (row.task_id if scope_kind == "task" else row.producer_agent_id) != scope_id:
                continue
            delegation = snapshot.delegations.get(row.producer_delegation_id)
            if (
                not delegation
                or delegation.get("agent_id") != row.producer_agent_id
                or delegation.get("child_session_id") != row.producer_session_id
                or delegation.get("target_ref") != {"kind": "task", "id": row.task_id}
            ):
                continue
            state = row.state
            try:
                _read_binding(snapshot, row)
            except LivePreviewRefusal:
                state = "stale"
            if state != "stale" and now >= row.expires_at:
                state = "expired"
            if urlsplit(row.origin).port in self.forbidden_ports:
                state = "unavailable"
            result.append(
                replace(row, state=state, url=row.url if state == "reported_ready" else None)
            )
        result.sort(key=lambda row: (row.published_at, row.output_id), reverse=True)
        counts: dict[tuple[str, str], int] = {}
        for row in result:
            series = row.task_id, row.producer_agent_id
            counts[series] = counts.get(series, 0) + 1
        seen: set[tuple[str, str]] = set()
        selected = []
        for row in result:
            series = row.task_id, row.producer_agent_id
            latest = series not in seen
            seen.add(series)
            if latest or versions == "all":
                selected.append(
                    replace(
                        row,
                        latest=latest,
                        version_count=counts[series],
                        state=row.state if latest else "stale",
                        url=row.url if latest else None,
                    )
                )
        return tuple(selected)
