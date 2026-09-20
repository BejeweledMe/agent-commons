"""Local instrumentation remains bounded, private, and opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from agent_commons.ui.instrumentation import (
    INSTRUMENTATION_SCHEMA,
    InstrumentationError,
    InstrumentationStore,
    register_instrumentation_routes,
)


def _store(tmp_path: Path, *, today: str = "2026-09-19") -> InstrumentationStore:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return InstrumentationStore(
        tmp_path / "state", project_root=project, workspace_id="workspace.1", today=lambda: today
    )


def _client(tmp_path: Path, *, writes: bool) -> tuple[TestClient, InstrumentationStore]:
    app = FastAPI()
    store = _store(tmp_path)

    async def require_session(x_session: str | None = Header(default=None)) -> None:
        if x_session != "allowed":
            raise HTTPException(status_code=401, detail="session required")

    register_instrumentation_routes(
        app,
        dependencies=[Depends(require_session)],
        store_factory=lambda: store,
        register_writes=writes,
    )
    return TestClient(app), store


def test_store_is_off_by_default_and_uses_a_private_atomic_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    value = store.load()
    assert value["schema"] == INSTRUMENTATION_SCHEMA
    assert value["enabled"] is False
    assert value["counters"] == {}
    saved = store.settings(True, value["revision"])
    assert saved["enabled"] is True
    assert store.path.name == "instrumentation.json"
    assert store.path.stat().st_mode & 0o077 == 0
    assert not [name for name in os.listdir(store.root) if name.startswith(".instrumentation-")]
    assert sorted((tmp_path / "project").iterdir()) == []


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "free text", "outcome": "confirmed", "duration_bucket": "lt_1s"},
        {"kind": "task_create", "outcome": "confirmed", "duration_bucket": "/tmp/a"},
        {"kind": "task_create", "outcome": "agent.012345", "duration_bucket": "lt_1s"},
        {"kind": "task_create", "outcome": "confirmed", "duration_bucket": "2026-09-19T12:00:00Z"},
        {"kind": "task_create", "outcome": "confirmed", "duration_bucket": "lt_1s", "task_id": "x"},
    ],
)
def test_events_reject_content_paths_identifiers_and_timestamps(
    tmp_path: Path, event: object
) -> None:
    store = _store(tmp_path)
    with pytest.raises(InstrumentationError) as refused:
        store.record([event], store.load()["revision"])
    assert refused.value.code == "invalid_instrumentation"
    assert refused.value.status_code == 422


def test_events_are_ignored_off_then_counted_in_closed_buckets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    revision = store.load()["revision"]
    written, ignored = store.record(
        [{"kind": "navigation", "outcome": "confirmed", "duration_bucket": "lt_1s"}], revision
    )
    assert written is False
    assert ignored["counters"] == {}
    enabled = store.settings(True, revision)
    written, counted = store.record(
        [
            {"kind": "navigation", "outcome": "confirmed", "duration_bucket": "lt_1s"},
            {"kind": "navigation", "outcome": "confirmed", "duration_bucket": "lt_1s"},
        ],
        enabled["revision"],
    )
    assert written is True
    assert counted["counters"] == {"navigation": {"confirmed": {"lt_1s": 2}}}
    with pytest.raises(InstrumentationError) as stale:
        store.record([], enabled["revision"])
    assert stale.value.code == "instrumentation_revision_conflict"


def test_retention_resets_old_counters_and_clear_removes_only_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path, today="2026-09-19")
    value = store.load()
    enabled = store.settings(True, value["revision"])
    store.record(
        [{"kind": "navigation", "outcome": "confirmed", "duration_bucket": "lt_1s"}],
        enabled["revision"],
    )
    later = InstrumentationStore(
        tmp_path / "state",
        project_root=tmp_path / "project",
        workspace_id="workspace.1",
        today=lambda: "2026-10-20",
    )
    reset = later.load()
    assert reset["enabled"] is True
    assert reset["counters"] == {}
    sentinel = later.root / "other.json"
    sentinel.write_text("keep", encoding="utf-8")
    later.clear(reset["revision"])
    assert not later.path.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_routes_enforce_session_revision_and_readonly_registration(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, writes=True)
    headers = {"X-Session": "allowed"}
    assert client.get("/api/instrumentation").status_code == 401
    initial = client.get("/api/instrumentation", headers=headers).json()
    ignored = client.post(
        "/api/instrumentation/events",
        json={"events": [], "expected_revision": initial["revision"]},
        headers=headers,
    )
    assert ignored.status_code == 204
    enabled = client.post(
        "/api/instrumentation/settings",
        json={"enabled": True, "expected_revision": initial["revision"]},
        headers=headers,
    )
    assert enabled.status_code == 200
    bad = client.post(
        "/api/instrumentation/events",
        json={
            "events": [{"kind": "path:/tmp", "outcome": "confirmed", "duration_bucket": "lt_1s"}],
            "expected_revision": enabled.json()["revision"],
        },
        headers=headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_instrumentation"
    stale = client.post(
        "/api/instrumentation/settings",
        json={"enabled": False, "expected_revision": initial["revision"]},
        headers=headers,
    )
    assert stale.status_code == 409
    readonly, _ = _client(tmp_path / "readonly", writes=False)
    assert readonly.get("/api/instrumentation", headers=headers).status_code == 200
    assert readonly.post("/api/instrumentation/settings", json={}, headers=headers).status_code in (
        404,
        405,
    )
