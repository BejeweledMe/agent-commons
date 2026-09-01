from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_commons.ui.tracker_dtos import TrackerTaskDTO
from agent_commons.ui.tracker_reads import loading_tracker_snapshot, unavailable_tracker_snapshot
from agent_commons.ui.tracker_routes import (
    MAX_TRACKER_FRAME_BYTES,
    _parse_last_event_id,
    _sse,
    register_tracker_routes,
    tracker_events,
)

NOW = "2026-08-30T10:01:00Z"
SOURCE_REVISION = "sha256:" + "b" * 64


class Source:
    def __init__(self, sequences: list[int]) -> None:
        self._sequences: Iterator[int] = iter(sequences)
        self.last = sequences[-1]
        self.resume_values: list[int | None] = []

    def __call__(self, *, resume_after: int | None = None):  # type: ignore[no-untyped-def]
        self.resume_values.append(resume_after)
        sequence = next(self._sequences, self.last)
        return loading_tracker_snapshot(
            generated_at=NOW,
            sequence=sequence,
            source_revision=SOURCE_REVISION,
        )


class BrokenSource:
    def __call__(self, *, resume_after: int | None = None):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider stderr and secret must not cross")


class FlakySource:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, resume_after: int | None = None):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return loading_tracker_snapshot(
                generated_at=NOW,
                sequence=5,
                source_revision=SOURCE_REVISION,
            )
        raise RuntimeError("raw provider failure")


class NewerErrorSource:
    def __init__(self) -> None:
        self.calls = 0
        self.resume_values: list[int | None] = []

    def __call__(self, *, resume_after: int | None = None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.resume_values.append(resume_after)
        if self.calls == 1:
            return loading_tracker_snapshot(
                generated_at=NOW,
                sequence=1,
                source_revision=SOURCE_REVISION,
            )
        return unavailable_tracker_snapshot(
            generated_at=NOW,
            sequence=2,
            source_revision=SOURCE_REVISION,
            gap="source_revision_unavailable",
        )


def _oversized_snapshot():  # type: ignore[no-untyped-def]
    task = TrackerTaskDTO(
        task_id="task.oversized",
        title="x" * MAX_TRACKER_FRAME_BYTES,
        task_state="active",
        readiness="ready",
        dependency_task_ids=(),
        blocking_dependency_ids=(),
        owner_session_id=None,
        role_name=None,
        provider=None,
        profile_id=None,
        phase=None,
        awaits_human=False,
        next_action="none",
        freshness="fresh",
        evidence_state="complete",
        gaps=(),
    )
    return replace(
        loading_tracker_snapshot(
            generated_at=NOW,
            sequence=9,
            source_revision=SOURCE_REVISION,
        ),
        tasks=(task,),
    )


class OversizedThenNormalSource:
    def __init__(self) -> None:
        self.calls = 0
        self.resume_values: list[int | None] = []

    def __call__(self, *, resume_after: int | None = None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.resume_values.append(resume_after)
        if self.calls == 1:
            return _oversized_snapshot()
        return loading_tracker_snapshot(
            generated_at=NOW,
            sequence=9,
            source_revision=SOURCE_REVISION,
        )


def _drive(source: Any, count: int, *, resume_after: int | None = None) -> list[bytes]:
    async def run() -> list[bytes]:
        generator = tracker_events(
            source,
            resume_after=resume_after,
            poll_seconds=0,
            heartbeat_seconds=0,
        )
        frames = []
        try:
            for _ in range(count):
                frames.append(await anext(generator))
        finally:
            await generator.aclose()
        return frames

    return asyncio.run(run())


def test_snapshot_route_is_composable() -> None:
    app = FastAPI()
    source = Source([4, 4])
    register_tracker_routes(app, dependencies=[], source=source)

    with TestClient(app) as client:
        response = client.get("/api/work/tracker")
        assert response.status_code == 200
        assert response.json()["schema"] == "agent-commons.tracker.v1"
        assert response.json()["source_revision"] == SOURCE_REVISION
        assert response.json()["truncated"] is False
    assert {route.path for route in app.routes} >= {
        "/api/work/tracker",
        "/api/work/tracker/stream",
    }


def test_stream_emits_only_changes_and_bounded_keepalives() -> None:
    source = Source([1, 1, 2])
    frames = _drive(source, 3, resume_after=0)

    assert frames[0].startswith(b"id: 1\nevent: snapshot")
    assert frames[1] == b": keepalive\n\n"
    assert frames[2].startswith(b"id: 2\nevent: snapshot")
    payload = json.loads(frames[2].split(b"data: ", 1)[1])
    assert payload["sequence"] == 2
    assert all(len(frame) <= MAX_TRACKER_FRAME_BYTES for frame in frames)
    assert source.resume_values[:2] == [0, 1]


def test_stream_emits_first_failure_even_when_sequence_cannot_advance() -> None:
    frames = _drive(FlakySource(), 2)  # type: ignore[arg-type]

    assert frames[0].startswith(b"id: 5\nevent: snapshot")
    assert frames[1].startswith(b"event: error")
    assert b"id:" not in frames[1]
    assert b'"state":"error"' in frames[1]
    assert b"raw provider failure" not in frames[1]


def test_newer_error_does_not_advance_the_reconnect_cursor() -> None:
    source = NewerErrorSource()
    frames = _drive(source, 3)

    assert frames[0].startswith(b"id: 1\nevent: snapshot")
    assert frames[1].startswith(b"event: error")
    assert b"id:" not in frames[1]
    assert frames[2] == b": keepalive\n\n"
    assert source.resume_values == [None, 1, 1]


def test_stream_refuses_sequence_regression_without_replacing_last_event_id() -> None:
    frames = _drive(Source([5, 4]), 2)

    assert frames[0].startswith(b"id: 5\nevent: snapshot")
    assert frames[1].startswith(b"event: error")
    assert b"id:" not in frames[1]
    assert b"tracker_sequence_regressed" in frames[1]
    assert b'"resume_gap":true' in frames[1]


def test_reconnect_refuses_initial_sequence_older_than_last_event_id() -> None:
    frames = _drive(Source([5]), 1, resume_after=7)

    assert frames[0].startswith(b"event: error")
    assert b"id:" not in frames[0]
    assert b'"sequence":7' in frames[0]
    assert SOURCE_REVISION.encode() in frames[0]
    assert b"tracker_sequence_regressed" in frames[0]
    assert b'"resume_gap":true' in frames[0]


def test_invalid_last_event_id_does_not_guess_a_cursor() -> None:
    assert _parse_last_event_id(None) is None
    assert _parse_last_event_id("") is None
    assert _parse_last_event_id("-1") is None
    assert _parse_last_event_id("not-a-sequence") is None
    assert _parse_last_event_id("7") == 7


def test_source_exception_becomes_a_typed_redacted_error() -> None:
    app = FastAPI()
    register_tracker_routes(app, dependencies=[], source=BrokenSource())

    with TestClient(app) as client:
        response = client.get("/api/work/tracker")

    assert response.status_code == 200
    assert response.json()["state"] == "error"
    assert response.json()["gaps"] == ["projection_unavailable"]
    assert response.json()["source_revision"] is None
    assert response.json()["truncated"] is False
    assert "provider stderr" not in response.text


def test_oversized_snapshot_falls_back_to_bounded_explicit_truncation() -> None:
    frame = _sse("snapshot", _oversized_snapshot(), event_id=9)
    payload = json.loads(frame.split(b"data: ", 1)[1])

    assert len(frame) <= MAX_TRACKER_FRAME_BYTES
    assert frame.startswith(b"event: error")
    assert b"id:" not in frame
    assert payload["sequence"] == 9
    assert payload["truncated"] is True
    assert payload["source_revision"] == SOURCE_REVISION
    assert payload["gaps"] == ["tracker_snapshot_too_large"]


def test_oversized_normal_snapshot_does_not_advance_the_stream_cursor() -> None:
    source = OversizedThenNormalSource()
    frames = _drive(source, 2)

    assert frames[0].startswith(b"event: error")
    assert b"id:" not in frames[0]
    assert b'"truncated":true' in frames[0]
    assert frames[1].startswith(b"id: 9\nevent: snapshot")
    assert source.resume_values == [None, None]
