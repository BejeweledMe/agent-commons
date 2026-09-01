"""Composable authenticated routes for tracker snapshots and bounded SSE."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from agent_commons.errors import CommonsError
from agent_commons.ui.tracker_dtos import TrackerSnapshotDTO
from agent_commons.ui.tracker_reads import unavailable_tracker_snapshot

MAX_TRACKER_FRAME_BYTES = 1_048_576
TRACKER_POLL_SECONDS = 2.0
TRACKER_HEARTBEAT_SECONDS = 15.0


class TrackerRouteRegistrar(Protocol):
    def get(
        self, path: str, **kwargs: object
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...


class TrackerSnapshotSource(Protocol):
    def __call__(self, *, resume_after: int | None = None) -> TrackerSnapshotDTO: ...


def register_tracker_routes(
    routes: TrackerRouteRegistrar,
    *,
    dependencies: list[object],
    source: TrackerSnapshotSource,
) -> None:
    """Register read-only endpoints without extending the UI composition facade."""

    @routes.get("/api/work/tracker", dependencies=dependencies)
    async def tracker_snapshot() -> Response:
        snapshot = await _safe_snapshot(source, resume_after=None)
        return JSONResponse(snapshot.to_wire())

    @routes.get("/api/work/tracker/stream", dependencies=dependencies)
    async def tracker_stream(request: Request) -> Response:
        resume_after = _parse_last_event_id(request.headers.get("last-event-id"))
        return StreamingResponse(
            tracker_events(source, resume_after=resume_after),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )


async def tracker_events(
    source: TrackerSnapshotSource,
    *,
    resume_after: int | None,
    poll_seconds: float = TRACKER_POLL_SECONDS,
    heartbeat_seconds: float = TRACKER_HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    """Emit changed bounded snapshots; absence of history is always explicit."""

    snapshot = await _safe_snapshot(source, resume_after=resume_after)
    if resume_after is not None and snapshot.sequence < resume_after:
        snapshot = unavailable_tracker_snapshot(
            generated_at=snapshot.freshness.generated_at,
            sequence=resume_after,
            source_revision=snapshot.source_revision,
            resume_gap=True,
            gap="tracker_sequence_regressed",
        )
        yield _sse("error", snapshot, event_id=None)
        last_sent: int | None = resume_after
    else:
        event = "error" if snapshot.state == "error" else "snapshot"
        event_id = None if event == "error" else snapshot.sequence
        frame, advances_cursor = _sse_frame(event, snapshot, event_id=event_id)
        yield frame
        last_sent = snapshot.sequence if advances_cursor else resume_after
    last_signature = _snapshot_signature(snapshot)
    since_heartbeat = 0.0
    while True:
        await asyncio.sleep(poll_seconds)
        since_heartbeat += poll_seconds
        current = await _safe_snapshot(source, resume_after=last_sent)
        current_signature = _snapshot_signature(current)
        emitted = False
        if last_sent is None or current.sequence > last_sent:
            event = "error" if current.state == "error" else "snapshot"
            event_id = None if event == "error" else current.sequence
            frame, advances_cursor = _sse_frame(event, current, event_id=event_id)
            if current_signature != last_signature:
                yield frame
                emitted = True
                last_signature = current_signature
                if advances_cursor:
                    last_sent = current.sequence
        elif current.sequence < last_sent:
            regression = unavailable_tracker_snapshot(
                generated_at=current.freshness.generated_at,
                sequence=last_sent,
                source_revision=current.source_revision,
                resume_gap=True,
                gap="tracker_sequence_regressed",
            )
            signature = _snapshot_signature(regression)
            if signature != last_signature:
                yield _sse("error", regression, event_id=None)
                emitted = True
                last_signature = signature
        elif current_signature != last_signature:
            if current.state == "error":
                yield _sse("error", current, event_id=None)
                emitted = True
                last_signature = current_signature
            else:
                reused = unavailable_tracker_snapshot(
                    generated_at=current.freshness.generated_at,
                    sequence=last_sent,
                    source_revision=current.source_revision,
                    gap="tracker_sequence_reused",
                )
                signature = _snapshot_signature(reused)
                if signature != last_signature:
                    yield _sse("error", reused, event_id=None)
                    emitted = True
                    last_signature = signature
        if emitted:
            since_heartbeat = 0.0
        elif since_heartbeat >= heartbeat_seconds:
            yield b": keepalive\n\n"
            since_heartbeat = 0.0


def _sse(event: str, snapshot: TrackerSnapshotDTO, *, event_id: int | None) -> bytes:
    return _sse_frame(event, snapshot, event_id=event_id)[0]


def _sse_frame(
    event: str, snapshot: TrackerSnapshotDTO, *, event_id: int | None
) -> tuple[bytes, bool]:
    """Serialize one frame and report whether it may advance Last-Event-ID."""

    body = json.dumps(snapshot.to_wire(), separators=(",", ":"), ensure_ascii=False).encode()
    prefix = (b"" if event_id is None else b"id: " + str(event_id).encode() + b"\n") + (
        b"event: " + event.encode() + b"\ndata: "
    )
    if len(prefix) + len(body) + 2 > MAX_TRACKER_FRAME_BYTES:
        body = json.dumps(
            {
                "schema": "agent-commons.tracker.v1",
                "sequence": snapshot.sequence,
                "source_revision": snapshot.source_revision,
                "truncated": True,
                "state": "error",
                "tasks": [],
                "edges": [],
                "runs": [],
                "attention": [],
                "capacity": {
                    "state": "unknown",
                    "active": None,
                    "limit": None,
                    "queued": None,
                    "queue_capacity": None,
                },
                "freshness": {
                    "generated_at": snapshot.freshness.generated_at,
                    "source_updated_at": None,
                    "state": "unknown",
                    "resume_gap": snapshot.freshness.resume_gap,
                },
                "focus_task_ids": [],
                "critical_path_task_ids": [],
                "critical_path_basis": "dependency_depth_only",
                "critical_path_predictive": False,
                "gaps": ["tracker_snapshot_too_large"],
            },
            separators=(",", ":"),
        ).encode()
        event = "error"
        event_id = None
        prefix = b"event: error\ndata: "
    frame = prefix + body + b"\n\n"
    return frame, event == "snapshot" and event_id is not None


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


async def _safe_snapshot(
    source: TrackerSnapshotSource, *, resume_after: int | None
) -> TrackerSnapshotDTO:
    try:
        return await asyncio.to_thread(source, resume_after=resume_after)
    except (CommonsError, OSError, RuntimeError, TypeError, ValueError):
        return unavailable_tracker_snapshot(
            generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            sequence=resume_after or 0,
        )


def _snapshot_signature(snapshot: TrackerSnapshotDTO) -> bytes:
    """Compare state transitions while ignoring observation-clock churn."""

    wire = snapshot.to_wire()
    wire["freshness"]["generated_at"] = ""
    wire["freshness"]["source_updated_at"] = None
    return json.dumps(wire, separators=(",", ":"), sort_keys=True).encode()
