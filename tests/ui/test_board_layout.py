"""The project board arrangement is bounded, atomic, revision-guarded and never canonical."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from agent_commons.ui.board_layout import (
    BOARD_LAYOUT_SCHEMA,
    BoardLayoutError,
    BoardLayoutStore,
    register_board_routes,
    validate_layout_input,
)

AGENT_A = "agent.0" + "1" * 25
AGENT_B = "agent.0" + "2" * 25
FRAME = "frame.0123abcd"


def _store(tmp_path: Path) -> BoardLayoutStore:
    project = tmp_path / "project"
    project.mkdir()
    return BoardLayoutStore(tmp_path / "state", project_root=project, workspace_id="workspace.1")


def _layout(revision: int = 0) -> dict[str, object]:
    return {
        "expected_revision": revision,
        "positions": {AGENT_A: {"x": 10, "y": 20}, AGENT_B: {"x": -5, "y": 0}},
        "frames": [
            {
                "id": FRAME,
                "title": "Frontend department",
                "x": 0,
                "y": 0,
                "width": 800,
                "height": 400,
                "members": [AGENT_A, AGENT_A],
                "blueprint_id": "team-frontend",
            }
        ],
    }


def test_store_starts_empty_saves_atomically_and_bumps_the_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.load() == {
        "schema": BOARD_LAYOUT_SCHEMA,
        "revision": 0,
        "positions": {},
        "frames": [],
    }
    saved = store.save(_layout())
    assert saved["revision"] == 1
    assert saved["frames"][0]["members"] == [AGENT_A]  # duplicates collapse
    assert saved["positions"][AGENT_B] == {"x": -5, "y": 0}
    assert store.load() == saved
    assert store.path.is_file()
    assert store.path.stat().st_mode & 0o077 == 0
    assert not [name for name in os.listdir(store.root) if name.startswith(".layout-")]
    # The layout is not a ledger: nothing under the project root was written.
    assert sorted(os.listdir(tmp_path / "project")) == []


def test_save_requires_the_current_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(_layout())
    with pytest.raises(BoardLayoutError) as stale:
        store.save(_layout(revision=0))
    assert stale.value.code == "board_revision_conflict"
    assert stale.value.status_code == 409
    assert store.save(_layout(revision=1))["revision"] == 2


@pytest.mark.parametrize(
    "patch",
    [
        {"positions": {"agent.not-an-id": {"x": 0, "y": 0}}},
        {"positions": {AGENT_A: {"x": 0.5, "y": 0}}},
        {"positions": {AGENT_A: {"x": True, "y": 0}}},
        {"positions": {AGENT_A: {"x": 2_000_000, "y": 0}}},
        {"positions": {AGENT_A: {"x": 0}}},
        {"frames": [{"id": "frame.x", "title": "t", "x": 0, "y": 0, "width": 1, "height": 1}]},
        {"frames": [{"id": FRAME, "title": "", "x": 0, "y": 0, "width": 1, "height": 1}]},
        {"frames": [{"id": FRAME, "title": "t", "x": 0, "y": 0, "width": 0, "height": 1}]},
        {
            "frames": [
                {
                    "id": FRAME,
                    "title": "t",
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 1,
                    "members": ["task.x"],
                }
            ]
        },
        {
            "frames": [
                {"id": FRAME, "title": "t", "x": 0, "y": 0, "width": 1, "height": 1, "path": "/tmp"}
            ]
        },
        {
            "frames": [
                {
                    "id": FRAME,
                    "title": "t",
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 1,
                    "blueprint_id": "a b",
                }
            ]
        },
        {"schema": BOARD_LAYOUT_SCHEMA, "extra": 1},
    ],
)
def test_malformed_layouts_are_refused_with_a_typed_422(patch: dict[str, object]) -> None:
    body = {"positions": {}, "frames": [], **patch}
    with pytest.raises(BoardLayoutError) as refused:
        validate_layout_input(body)
    assert refused.value.code == "invalid_board_layout"
    assert refused.value.status_code == 422


def test_frame_ids_must_not_repeat_and_titles_are_cleaned() -> None:
    frame = {"id": FRAME, "title": "  Dept\x00 A  ", "x": 0, "y": 0, "width": 1, "height": 1}
    with pytest.raises(BoardLayoutError):
        validate_layout_input({"frames": [frame, dict(frame)]})
    assert validate_layout_input({"frames": [frame]})["frames"][0]["title"] == "Dept A"


def test_unsafe_or_foreign_files_are_refused_not_trusted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.root.mkdir(parents=True, mode=0o700)
    store.path.write_text(json.dumps({"schema": "other", "revision": 0}), encoding="utf-8")
    os.chmod(store.path, 0o600)
    with pytest.raises(BoardLayoutError) as foreign:
        store.load()
    assert foreign.value.code == "board_layout_unavailable"
    os.chmod(store.path, 0o644)
    with pytest.raises(BoardLayoutError) as loose:
        store.load()
    assert loose.value.code == "board_layout_unavailable"


def test_store_refuses_a_state_root_inside_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(BoardLayoutError):
        BoardLayoutStore(project / "state", project_root=project, workspace_id="workspace.1")


def _client(tmp_path: Path, *, writes: bool) -> tuple[TestClient, BoardLayoutStore]:
    app = FastAPI()
    store = _store(tmp_path)

    async def require_session(x_session: str | None = Header(default=None)) -> None:
        if x_session != "allowed":
            raise HTTPException(status_code=401, detail="session required")

    register_board_routes(
        app,
        dependencies=[Depends(require_session)],
        store_factory=lambda: store,
        register_writes=writes,
    )
    return TestClient(app), store


def test_routes_read_and_replace_the_layout_behind_the_session_gate(tmp_path: Path) -> None:
    client, store = _client(tmp_path, writes=True)
    headers = {"X-Session": "allowed"}
    assert client.get("/api/board").status_code == 401
    assert client.post("/api/board", json=_layout()).status_code == 401
    empty = client.get("/api/board", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["revision"] == 0
    assert empty.headers["Cache-Control"] == "no-store"
    saved = client.post("/api/board", json=_layout(), headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert client.get("/api/board", headers=headers).json() == saved.json()
    stale = client.post("/api/board", json=_layout(0), headers=headers)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "board_revision_conflict"
    bad = client.post(
        "/api/board", json={"expected_revision": 1, "positions": {"x": 1}}, headers=headers
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_board_layout"
    assert store.load()["revision"] == 1


def test_read_only_panels_register_no_board_write(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, writes=False)
    headers = {"X-Session": "allowed"}
    assert client.get("/api/board", headers=headers).status_code == 200
    assert client.post("/api/board", json=_layout(), headers=headers).status_code == 405
