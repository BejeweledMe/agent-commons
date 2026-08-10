"""The operator catalogue, edited from the panel and read by the broker.

Two properties matter and neither is provable from the context object alone:
the routes exist only behind their own gate, and what the panel writes is a
file the next launch can actually load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.catalog import load_role_catalog, write_role_catalog
from agent_commons.errors import ConfigurationError
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import CATALOG_ROUTES, MUTATING_ROUTES, create_app
from tests.ui.conftest import PORT, authorized


def _client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    return TestClient(create_app(context, token="test-token", port=PORT), base_url=f"http://127.0.0.1:{PORT}")


@pytest.fixture
def editable(workspace: dict[str, Any], tmp_path: Path) -> UIContext:
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="catalog-writer-window-12",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    return UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        catalog_path=tmp_path / "catalog.yaml",
        catalog_editing=True,
    )


def test_catalogue_routes_exist_only_behind_their_own_gate(
    workspace: dict[str, Any], tmp_path: Path
) -> None:
    """Editing presets and changing what a run is told to do are not one switch."""

    writable_only = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=None,
        catalog_path=tmp_path / "catalog.yaml",
    )
    with _client(writable_only) as client:
        found = {
            (method, route.path)
            for route in client.app.routes
            for method in (getattr(route, "methods", set()) or set())
            if method not in {"GET", "HEAD"}
        }
    assert found == set()

    editing = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        catalog_path=tmp_path / "catalog.yaml",
        catalog_editing=True,
    )
    with _client(editing) as client:
        found = {
            (method, route.path)
            for route in client.app.routes
            for method in (getattr(route, "methods", set()) or set())
            if method not in {"GET", "HEAD"}
        }
    # Catalogue editing brings its own routes and none of the role-write ones.
    assert found == set(CATALOG_ROUTES)
    assert not found & set(MUTATING_ROUTES)


def test_a_saved_skill_is_a_file_the_next_launch_can_load(
    editable: UIContext, tmp_path: Path
) -> None:
    with _client(editable) as client:
        response = client.post(
            "/api/catalog/entries",
            json={
                "section": "skills",
                "id": "conventional-commits",
                "title": "Conventional commits",
                "description": "how this project words its commit messages",
                "instruction": "Write commit subjects in the imperative mood.",
            },
            headers=authorized(),
        )
        assert response.status_code == 200, response.text

    path = tmp_path / "catalog.yaml"
    assert path.stat().st_mode & 0o077 == 0
    reloaded = load_role_catalog(path)
    assert [entry["id"] for entry in reloaded["skills"]] == ["conventional-commits"]
    assert reloaded["skills"][0]["instruction"].startswith("Write commit subjects")


def test_a_skill_without_instructions_is_refused_rather_than_stored_empty(
    editable: UIContext,
) -> None:
    with _client(editable) as client:
        response = client.post(
            "/api/catalog/entries",
            json={"section": "skills", "id": "empty", "title": "Empty", "instruction": "  "},
            headers=authorized(),
        )
    assert response.status_code == 409
    assert "instruction text" in response.json()["error"]["message"]


def test_removing_an_entry_a_role_requires_is_refused_and_names_the_role(
    editable: UIContext, workspace: dict[str, Any], tmp_path: Path
) -> None:
    write_role_catalog(
        tmp_path / "catalog.yaml",
        {
            "skills": [
                {"id": "house-style", "title": "House style", "instruction": "Follow the style."}
            ],
            "tools": [],
        },
    )
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="catalog-role-window-1234",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    manager.session_id = session["session_id"]
    role = manager.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="requires the house style",
        skills=("house-style",),
        idempotency_key="catalog-role",
    )

    with _client(editable) as client:
        response = client.post(
            "/api/catalog/entries/remove",
            json={"section": "skills", "id": "house-style"},
            headers=authorized(),
        )
    assert response.status_code == 409
    assert role["entity_ref"]["id"] in response.json()["error"]["message"]
    assert load_role_catalog(tmp_path / "catalog.yaml")["skills"] != []


def test_a_rejected_edit_leaves_the_previous_catalogue_intact(tmp_path: Path) -> None:
    path = tmp_path / "catalog.yaml"
    write_role_catalog(
        path, {"skills": [], "tools": [{"id": "commons-repo-read", "title": "Read"}]}
    )
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        write_role_catalog(path, {"skills": [{"id": "broken"}], "tools": []})
    assert path.read_bytes() == before


def test_a_catalogue_inside_the_workspace_is_refused(workspace: dict[str, Any]) -> None:
    """A writable builder can rewrite anything in the workspace, including this."""

    inside = workspace["repo"] / "catalog.yaml"
    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        write_role_catalog(inside, {"skills": [], "tools": []}, workspace_root=workspace["repo"])
