"""Tracker sampling proves one observation and advances only meaningful cursors."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_commons.ui import tracker_reads
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from agent_commons.ui.tracker_reads import ObservedTrackerSource
from agent_commons.ui.tracker_routes import tracker_events
from tests.services.test_work_metrics import _attempt
from tests.services.test_work_observation import NOW, OLD, old_running_snapshot, quiet_snapshot
from tests.ui.conftest import PORT, authorized, tree_digest

FINGERPRINT = "sha256:" + "a" * 64


class QuietContext:
    def __init__(self, state_root: Path):
        self.canonical = quiet_snapshot()
        self.graph = {
            "generated_at": OLD,
            "ledger_fingerprint": FINGERPRINT,
            "limits": {"truncated": False},
        }
        self.manager_value = SimpleNamespace(
            paths=SimpleNamespace(state_root=state_root), snapshot=lambda: self.canonical
        )
        self.fingerprints: Iterator[str] | None = None
        self.canonical_reads = 0

    def refresh_if_changed(self) -> bool:
        return False

    def snapshot_frame(self) -> tuple[int, dict[str, Any]]:
        return 7, self.graph

    def fingerprint(self) -> str:
        return next(self.fingerprints) if self.fingerprints is not None else FINGERPRINT

    def manager(self) -> Any:
        self.canonical_reads += 1
        return self.manager_value


def test_quiet_snapshot_is_reobserved_without_mutating_graph_or_flooding_sequences(
    tmp_path: Path,
) -> None:
    context = QuietContext(tmp_path)
    original = deepcopy(context.graph)
    clock = iter(["2026-08-30T09:00:59Z", "2026-08-30T09:01:01Z", NOW])
    source = ObservedTrackerSource(cast(UIContext, context), clock=lambda: next(clock))
    first, second, third = source(), source(), source()
    assert first.freshness.state == second.freshness.state == third.freshness.state == "fresh"
    assert first.sequence == second.sequence == third.sequence
    assert third.freshness.generated_at == NOW
    assert third.tasks[0].readiness == "ready"
    assert context.graph == original
    assert context.canonical_reads == 3
    assert "canonical_observed_at" not in json.dumps(third.to_wire())


@pytest.mark.parametrize("moving", [False, True])
def test_source_rejects_prior_graph_mismatch_and_changes_during_sampling(
    tmp_path: Path, moving: bool
) -> None:
    context = QuietContext(tmp_path)
    other = "sha256:" + "b" * 64
    context.fingerprints = iter([FINGERPRINT, other] if moving else [other])
    result = ObservedTrackerSource(cast(UIContext, context), clock=lambda: NOW)()
    assert result.state == "error"
    assert result.freshness.state == "unknown"
    assert result.gaps == ("source_revision_unavailable",)
    assert result.tasks == ()
    assert context.canonical_reads == int(moving)


@pytest.mark.parametrize("value", [None, "bad-clock", "2027-01-01T00:00:00Z"])
def test_source_observation_cannot_replace_invalid_or_future_graph_clocks(
    tmp_path: Path, value: str | None
) -> None:
    context = QuietContext(tmp_path)
    context.graph["generated_at"] = value
    result = ObservedTrackerSource(cast(UIContext, context), clock=lambda: NOW)()
    assert result.state == "error"
    assert result.gaps == ("graph_malformed",)


def test_new_source_meaning_advances_cursor_without_observation_only_sse_frames(
    tmp_path: Path,
) -> None:
    context = QuietContext(tmp_path)
    source = ObservedTrackerSource(cast(UIContext, context), clock=lambda: NOW)
    first = source()
    context.canonical.tasks["task.1"]["recorded_at"] = "2027-01-01T00:00:00Z"  # type: ignore[index]
    second = source()
    assert second.sequence > first.sequence
    assert second.freshness.state == "unknown"
    context.canonical = quiet_snapshot()

    async def observe() -> list[bytes]:
        events = tracker_events(
            source, resume_after=None, poll_seconds=0.001, heartbeat_seconds=0.002
        )
        try:
            return [await anext(events), await anext(events)]
        finally:
            await events.aclose()

    frames = asyncio.run(observe())
    assert b"event: snapshot" in frames[0]
    assert frames[1] == b": keepalive\n\n"


def test_real_tracker_http_refresh_keeps_old_ready_task_current_and_canonical_tree_unchanged(
    populated: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    context = UIContext(populated["repo"], state_root=populated["state_root"])
    task = context.manager().snapshot().tasks[populated["task_id"]]
    recorded_at = str(task.get("recorded_at"))
    later = datetime.fromisoformat(recorded_at.replace("Z", "+00:00")) + timedelta(hours=2)
    assert later > datetime.now(UTC)
    monkeypatch.setattr(tracker_reads, "_observation_clock", lambda: later.isoformat())
    app = create_app(context, token="test-token", port=PORT, api_base="/api")
    before = tree_digest(populated["commons_root"])
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        first = client.get("/api/work/tracker", headers=authorized()).json()
        second = client.get("/api/work/tracker", headers=authorized()).json()
    assert first["freshness"]["state"] == second["freshness"]["state"] == "fresh"
    assert "projection_stale" not in second["gaps"]
    assert second["tasks"][0]["readiness"] == "ready"
    assert second["sequence"] == first["sequence"]
    assert tree_digest(populated["commons_root"]) == before
    assert (
        str(context.manager().snapshot().tasks[populated["task_id"]].get("recorded_at"))
        == recorded_at
    )


def test_active_heartbeat_aging_advances_tracker_sequence_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = QuietContext(tmp_path)
    context.canonical = old_running_snapshot()
    attempt = _attempt(created_at=OLD, updated_at=OLD)
    monkeypatch.setattr(
        tracker_reads,
        "AttemptStore",
        lambda *_args, **_kwargs: SimpleNamespace(list_attempts=lambda: [attempt]),
    )
    clock = iter(
        [
            "2026-08-30T09:00:30Z",
            "2026-08-30T09:01:01Z",
            "2026-08-30T09:01:01Z",
            "2026-08-30T09:01:02Z",
        ]
    )
    source = ObservedTrackerSource(cast(UIContext, context), clock=lambda: next(clock))
    fresh, stale, unchanged, elapsed = source(), source(), source(), source()
    assert fresh.freshness.state == "fresh"
    assert stale.freshness.state == "stale"
    assert "projection_stale" in stale.gaps
    assert stale.sequence > fresh.sequence
    assert unchanged.sequence == stale.sequence
    assert stale.runs[0].freshness == "stale"
    assert elapsed.runs[0].duration_seconds == stale.runs[0].duration_seconds + 1
    assert elapsed.sequence > unchanged.sequence
