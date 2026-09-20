"""Local, opt-in, content-free UI instrumentation.

This is operational state only.  It deliberately accepts a very small, closed
vocabulary of coarse counters and has no network, export, or canonical-event
dependency.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict

INSTRUMENTATION_SCHEMA = "agent_commons.instrumentation.v1"
INSTRUMENTATION_UI_VERSION = "1"
MAX_INSTRUMENTATION_BYTES = 65_536
MAX_EVENTS_PER_REQUEST = 64
RETENTION_WINDOW_DAYS = 30
EVENT_KINDS = frozenset(
    {
        "task_create",
        "task_action",
        "run_prepare",
        "run_start",
        "library_edit",
        "board_edit",
        "navigation",
    }
)
OUTCOMES = frozenset({"confirmed", "refused", "uncertain", "cancelled"})
DURATION_BUCKETS = frozenset({"lt_1s", "lt_2s", "lt_8s", "ge_8s"})


class InstrumentationError(Exception):
    """A typed refusal safe to return to the local panel."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _day() -> str:
    return date.today().isoformat()


def empty_instrumentation(*, today: str | None = None) -> dict[str, Any]:
    return {
        "schema": INSTRUMENTATION_SCHEMA,
        "enabled": False,
        "ui_version": INSTRUMENTATION_UI_VERSION,
        "counters": {},
        "retention": {"window_days": RETENTION_WINDOW_DAYS, "updated_at_day": today or _day()},
    }


def _revision(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_day(value: object) -> str:
    if not isinstance(value, str):
        raise InstrumentationError("instrumentation_unavailable", 409, "retention day is invalid")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise InstrumentationError(
            "instrumentation_unavailable", 409, "retention day is invalid"
        ) from exc


def _validate_event(value: object) -> tuple[str, str, str]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "outcome", "duration_bucket"}:
        raise InstrumentationError("invalid_instrumentation", 422, "event has unsupported fields")
    kind, outcome, bucket = value["kind"], value["outcome"], value["duration_bucket"]
    if type(kind) is not str or kind not in EVENT_KINDS:
        raise InstrumentationError("invalid_instrumentation", 422, "event kind is not allowed")
    if type(outcome) is not str or outcome not in OUTCOMES:
        raise InstrumentationError("invalid_instrumentation", 422, "event outcome is not allowed")
    if type(bucket) is not str or bucket not in DURATION_BUCKETS:
        raise InstrumentationError(
            "invalid_instrumentation", 422, "event duration bucket is not allowed"
        )
    return kind, outcome, bucket


def validate_instrumentation(value: object) -> dict[str, Any]:
    """Validate persisted bytes without accepting arbitrary text or identifiers."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "enabled",
        "ui_version",
        "counters",
        "retention",
    }:
        raise InstrumentationError(
            "instrumentation_unavailable", 409, "instrumentation schema is invalid"
        )
    if value.get("schema") != INSTRUMENTATION_SCHEMA or type(value.get("enabled")) is not bool:
        raise InstrumentationError(
            "instrumentation_unavailable", 409, "instrumentation schema is invalid"
        )
    if value.get("ui_version") != INSTRUMENTATION_UI_VERSION:
        raise InstrumentationError(
            "instrumentation_unavailable", 409, "instrumentation UI version is invalid"
        )
    retention = value.get("retention")
    if not isinstance(retention, Mapping) or set(retention) != {"window_days", "updated_at_day"}:
        raise InstrumentationError("instrumentation_unavailable", 409, "retention is invalid")
    if retention.get("window_days") != RETENTION_WINDOW_DAYS:
        raise InstrumentationError("instrumentation_unavailable", 409, "retention is invalid")
    updated_at_day = _require_day(retention.get("updated_at_day"))
    raw_counters = value.get("counters")
    if not isinstance(raw_counters, Mapping) or set(raw_counters) - EVENT_KINDS:
        raise InstrumentationError("instrumentation_unavailable", 409, "counters are invalid")
    counters: dict[str, dict[str, dict[str, int]]] = {}
    for kind, outcomes in raw_counters.items():
        if not isinstance(outcomes, Mapping) or set(outcomes) - OUTCOMES:
            raise InstrumentationError("instrumentation_unavailable", 409, "counters are invalid")
        counters[kind] = {}
        for outcome, buckets in outcomes.items():
            if not isinstance(buckets, Mapping) or set(buckets) - DURATION_BUCKETS:
                raise InstrumentationError(
                    "instrumentation_unavailable", 409, "counters are invalid"
                )
            counters[kind][outcome] = {}
            for bucket, count in buckets.items():
                if type(count) is not int or count < 0:
                    raise InstrumentationError(
                        "instrumentation_unavailable", 409, "counters are invalid"
                    )
                counters[kind][outcome][bucket] = count
    return {
        "schema": INSTRUMENTATION_SCHEMA,
        "enabled": value["enabled"],
        "ui_version": INSTRUMENTATION_UI_VERSION,
        "counters": counters,
        "retention": {"window_days": RETENTION_WINDOW_DAYS, "updated_at_day": updated_at_day},
    }


class InstrumentationStore:
    """One bounded local counter document per project and workspace."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        project_root: str | Path,
        workspace_id: str,
        today: Callable[[], str] = _day,
    ) -> None:
        state = Path(os.path.abspath(Path(state_root).expanduser()))
        project = Path(project_root).expanduser().resolve()
        if not workspace_id or state.resolve().is_relative_to(project):
            raise InstrumentationError(
                "instrumentation_unavailable", 409, "instrumentation state root is unsafe"
            )
        partition = hashlib.sha256(
            f"{hashlib.sha256(str(project).encode()).hexdigest()}:{workspace_id}".encode()
        ).hexdigest()
        self.root = state / "runtime" / "instrumentation" / partition
        self.path = self.root / "instrumentation.json"
        self._today = today

    def _current(self) -> dict[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return empty_instrumentation(today=self._today())
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_INSTRUMENTATION_BYTES
            ):
                raise InstrumentationError(
                    "instrumentation_unavailable", 409, "instrumentation file is unsafe"
                )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_INSTRUMENTATION_BYTES + 1)
        finally:
            os.close(fd)
        try:
            return validate_instrumentation(loads_json_strict(raw))
        except InstrumentationError:
            raise
        except Exception as exc:
            raise InstrumentationError(
                "instrumentation_unavailable", 409, "instrumentation is unreadable"
            ) from exc

    def _retained(self, value: dict[str, Any]) -> dict[str, Any]:
        updated = date.fromisoformat(value["retention"]["updated_at_day"])
        current = date.fromisoformat(self._today())
        if current - updated > timedelta(days=RETENTION_WINDOW_DAYS):
            value = empty_instrumentation(today=self._today()) | {"enabled": value["enabled"]}
        return value

    def load(self) -> dict[str, Any]:
        value = self._retained(self._current())
        return {**value, "revision": _revision(value)}

    def _write(self, value: Mapping[str, Any]) -> None:
        encoded = canonical_json_bytes(value)
        if len(encoded) > MAX_INSTRUMENTATION_BYTES:
            raise InstrumentationError(
                "invalid_instrumentation", 422, "instrumentation is too large"
            )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".instrumentation-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    @staticmethod
    def _expected(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise InstrumentationError(
                "invalid_instrumentation", 422, "expected_revision must be a SHA-256 hex string"
            )
        return value

    def _check_revision(self, expected: object) -> dict[str, Any]:
        current = self.load()
        if current["revision"] != self._expected(expected):
            raise InstrumentationError(
                "instrumentation_revision_conflict", 409, "instrumentation revision is stale"
            )
        return {key: value for key, value in current.items() if key != "revision"}

    def settings(self, enabled: object, expected_revision: object) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise InstrumentationError("invalid_instrumentation", 422, "enabled must be a boolean")
        value = self._check_revision(expected_revision)
        value["enabled"] = enabled
        value["retention"]["updated_at_day"] = self._today()
        self._write(value)
        return self.load()

    def record(self, events: object, expected_revision: object) -> tuple[bool, dict[str, Any]]:
        if (
            not isinstance(events, Sequence)
            or isinstance(events, (str, bytes))
            or len(events) > MAX_EVENTS_PER_REQUEST
        ):
            raise InstrumentationError(
                "invalid_instrumentation", 422, "events must list at most 64 entries"
            )
        normalized = [_validate_event(event) for event in events]
        value = self._check_revision(expected_revision)
        if not value["enabled"]:
            return False, {**value, "revision": _revision(value)}
        counters = value["counters"]
        for kind, outcome, bucket in normalized:
            outcomes = counters.setdefault(kind, {})
            buckets = outcomes.setdefault(outcome, {})
            buckets[bucket] = buckets.get(bucket, 0) + 1
        value["retention"]["updated_at_day"] = self._today()
        self._write(value)
        return True, self.load()

    def clear(self, expected_revision: object) -> None:
        self._check_revision(expected_revision)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def register_instrumentation_routes(
    routes: Any,
    *,
    dependencies: list[object],
    store_factory: Callable[[], InstrumentationStore],
    register_writes: bool,
) -> None:
    """Mount local instrumentation reads and, for operator panels, writes."""

    def refusal(exc: InstrumentationError) -> Response:
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code
        )

    async def body(request: Request) -> object:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_INSTRUMENTATION_BYTES:
                raise InstrumentationError("invalid_instrumentation", 413, "request is too large")
        try:
            return loads_json_strict(bytes(content))
        except Exception as exc:
            raise InstrumentationError(
                "invalid_instrumentation", 422, "request is not JSON"
            ) from exc

    @routes.get("/api/instrumentation", dependencies=dependencies)
    async def read_instrumentation() -> Response:
        try:
            return JSONResponse(store_factory().load(), headers={"Cache-Control": "no-store"})
        except InstrumentationError as exc:
            return refusal(exc)

    if not register_writes:
        return

    @routes.post("/api/instrumentation/settings", dependencies=dependencies)
    async def update_settings(request: Request) -> Response:
        try:
            payload = await body(request)
            if not isinstance(payload, Mapping) or set(payload) != {"enabled", "expected_revision"}:
                raise InstrumentationError(
                    "invalid_instrumentation", 422, "settings has unsupported fields"
                )
            return JSONResponse(
                store_factory().settings(payload["enabled"], payload["expected_revision"]),
                headers={"Cache-Control": "no-store"},
            )
        except InstrumentationError as exc:
            return refusal(exc)

    @routes.post("/api/instrumentation/events", dependencies=dependencies)
    async def record_events(request: Request) -> Response:
        try:
            payload = await body(request)
            if not isinstance(payload, Mapping) or set(payload) != {"events", "expected_revision"}:
                raise InstrumentationError(
                    "invalid_instrumentation", 422, "events has unsupported fields"
                )
            written, value = store_factory().record(payload["events"], payload["expected_revision"])
            if not written:
                return Response(status_code=204)
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except InstrumentationError as exc:
            return refusal(exc)

    @routes.post("/api/instrumentation/clear", dependencies=dependencies)
    async def clear_instrumentation(request: Request) -> Response:
        try:
            payload = await body(request)
            if not isinstance(payload, Mapping) or set(payload) != {"expected_revision"}:
                raise InstrumentationError(
                    "invalid_instrumentation", 422, "clear has unsupported fields"
                )
            store_factory().clear(payload["expected_revision"])
            return Response(status_code=204)
        except InstrumentationError as exc:
            return refusal(exc)
