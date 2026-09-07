from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_commons.library import LibraryError, LibraryStore
from agent_commons.ui.library_routes import register_library_routes


def _client(tmp_path: Path, *, editing: bool = True) -> TestClient:
    app = FastAPI()
    store = LibraryStore(tmp_path / "library")

    def authorize() -> None:
        if not editing:
            raise LibraryError("library_editor_required", "An operator editor is required.", 403)

    register_library_routes(
        app,
        dependencies=[],
        write_dependencies=[],
        store_factory=lambda: store,
        authorize_edit=authorize,
        editing_enabled=lambda: editing,
        register_writes=editing,
    )
    return TestClient(app)


def test_read_only_library_is_useful_without_content_or_write_routes(tmp_path: Path) -> None:
    client = _client(tmp_path, editing=False)
    response = client.get("/api/library")
    assert response.status_code == 200
    body = response.json()
    assert len(body["skills"]) == 45
    assert len(body["roles"]) == 30
    assert not body["editing_enabled"]
    assert "system_prompt" not in response.text
    ref = body["roles"][0]["ref"]
    path = f"/api/library/role/{ref['source']}/{ref['id']}/{ref['version']}"
    assert client.get(path).status_code == 403
    assert client.post("/api/library/skill", json={}).status_code == 404


def test_editor_gets_exact_text_and_creates_retryable_custom_versions(tmp_path: Path) -> None:
    client = _client(tmp_path)
    catalog = client.get("/api/library").json()
    ref = catalog["skills"][0]["ref"]
    detail = client.get(f"/api/library/skill/{ref['source']}/{ref['id']}/{ref['version']}").json()
    assert detail["content"]["instruction"]
    body = {
        "id": "our-method",
        "expected_version": None,
        "idempotency_key": "save-once",
        "fork_ref": ref,
        "content": {**detail["content"], "instruction": "Our edited instruction"},
    }
    first = client.post("/api/library/skill", json=body)
    assert first.status_code == 200
    assert client.post("/api/library/skill", json=body).json() == first.json()
    saved = first.json()["ref"]
    assert saved["source"] == "custom"
    response = client.get(
        f"/api/library/skill/custom/{saved['id']}/{saved['version']}/files/SKILL.md"
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Our edited instruction"
    changed = {**body, "idempotency_key": "stale-edit", "fork_ref": None}
    assert client.post("/api/library/skill", json=changed).status_code == 409


def test_editor_rejects_paths_extra_authority_and_oversized_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "id": "simple",
        "expected_version": None,
        "idempotency_key": "bad",
        "content": {"name": "Method", "description": "", "instruction": "Do work"},
        "root": "/operator/path",
    }
    response = client.post("/api/library/skill", json=body)
    assert response.status_code == 422
    assert "/operator/path" not in response.text
    assert client.post("/api/library/skill", content=b"x" * 524_289).status_code == 413
