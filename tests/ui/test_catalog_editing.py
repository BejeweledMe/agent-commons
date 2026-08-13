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


def test_an_invalid_catalogue_read_is_a_named_4xx_not_an_opaque_500(
    workspace: dict[str, Any], tmp_path: Path
) -> None:
    """Round 2 (round-1 L7): a catalogue that fails to load is a
    misconfiguration.  The read route names it with a 4xx rather than a bare
    500, and the ui command validates it at startup (tested separately)."""

    bad = tmp_path / "bad-catalog.yaml"
    # `name:` instead of `title:` — an unsupported field the loader rejects.
    bad.write_text("skills:\n  - id: tdd\n    name: Test-driven\n", encoding="utf-8")
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        catalog_path=bad,
    )
    with _client(context) as client:
        response = client.get("/api/catalog", headers=authorized())
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ConfigurationError"
    assert "unsupported fields" in response.json()["error"]["message"]


def test_profile_tools_in_the_catalog_match_a_launch_bit_for_bit(
    workspace: dict[str, Any],
) -> None:
    """Wave 1 item 3: the Tools reference the panel renders is the same
    composition `_worker_tools` hands a launch — short names, outcome tools
    fixed, everything else narrowable — so the view cannot drift from
    reality."""

    from agent_commons.runtime.model import (
        _MCP_TOOL_PREFIX,
        BuiltinProfileId,
        _worker_tools,
    )

    context = UIContext(workspace["repo"], state_root=workspace["state_root"])
    payload = context.catalog()
    summary = payload["profile_tools"]
    assert set(summary) == {profile.value for profile in BuiltinProfileId}
    for profile in BuiltinProfileId:
        entry = summary[profile.value]
        purpose = entry["purpose"]
        launched = [
            tool.removeprefix(_MCP_TOOL_PREFIX)
            for tool in _worker_tools(profile, purpose)
        ]
        assert sorted(entry["fixed"] + entry["narrowable"]) == sorted(launched)
        # Outcome tools are how a role hands work back: they must all be fixed.
        assert entry["fixed"], profile.value
        assert not set(entry["fixed"]) & set(entry["narrowable"])


def test_a_granted_selection_the_next_launch_would_refuse_is_a_422_now(
    workspace: dict[str, Any], tmp_path: Path
) -> None:
    """Wave 1 item 5: reconfigure was a passthrough, so the panel could grant
    a skill the catalogue lost — or a foreign tool — and only the NEXT launch
    would fail. Both refusals now happen at click time (the panel's uniform
    409-refusal shape), name the ids, and leave no event behind."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="mirror-check-window-01",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    catalog_file = tmp_path / "catalog.yaml"
    write_role_catalog(
        catalog_file,
        {
            "skills": [
                {"id": "pytest-runner", "title": "Pytest", "instruction": "run the tests"}
            ],
            "tools": [],
        },
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        catalog_path=catalog_file,
    )
    with _client(context) as client:
        created = client.post(
            "/api/agents",
            json={
                "name": "Backend owner",
                "profile_id": "claude-builder",
                "rationale": "owns the surface",
                "skills": ["pytest-runner"],
            },
            headers=authorized(),
        )
        assert created.status_code == 200, created.text
        record = created.json()
        agent_id = record["entity_ref"]["id"]
        revision = record["revision"]

        ghost = client.post(
            "/api/agents/" + agent_id + "/reconfigure",
            json={
                "expected_revision": revision,
                "changes": {"skills": ["pytest-runner", "ghost-skill"]},
                "reason": "grant a skill the catalogue does not define",
            },
            headers=authorized(),
        )
        assert ghost.status_code == 409, ghost.text
        assert "ghost-skill" in ghost.json()["error"]["message"]

        foreign = client.post(
            "/api/agents/" + agent_id + "/reconfigure",
            json={
                "expected_revision": revision,
                "changes": {"tool_allowlist": ["commons_orient", "not-a-profile-tool"]},
                "reason": "narrow to a tool the profile never had",
            },
            headers=authorized(),
        )
        assert foreign.status_code == 409, foreign.text
        assert "not-a-profile-tool" in foreign.json()["error"]["message"]

        # Refusals recorded nothing: the role still shows its hire revision.
        assert manager.get_agent(agent_id)["revision"] == revision

        # A hire naming a foreign tool is refused by the same mirror.
        hire = client.post(
            "/api/agents",
            json={
                "name": "Docs reviewer",
                "profile_id": "claude-independent-reviewer",
                "rationale": "reviews docs",
                "tool_allowlist": ["definitely-not-a-tool"],
            },
            headers=authorized(),
        )
        assert hire.status_code == 409, hire.text
        assert "definitely-not-a-tool" in hire.json()["error"]["message"]


def test_hiring_from_a_template_inherits_what_the_form_omits(
    workspace: dict[str, Any],
) -> None:
    """The agent catalogue: a hire that names only from_preset_id (the panel's
    'from the catalogue' mode omits profile/grants/budget entirely) inherits
    every one of them from the template, because an explicit value would
    override the template instead."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="preset-window-000001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
    )
    with _client(context) as client:
        template = client.post(
            "/api/agents",
            json={
                "name": "Reviewer template",
                "profile_id": "claude-independent-reviewer",
                "rationale": "standard reviewer setup",
                "template": True,
                "grants": {"create_roles": "deny", "retire_roles": "deny", "open_links": "ask"},
            },
            headers=authorized(),
        )
        assert template.status_code == 200, template.text
        preset_id = template.json()["entity_ref"]["id"]

        # The template appears in the catalogue payload the panel renders.
        presets = client.get("/api/catalog", headers=authorized()).json()["presets"]
        assert preset_id in {preset["id"] for preset in presets}

        hired = client.post(
            "/api/agents",
            json={
                "name": "Docs reviewer",
                "rationale": "reviews the docs",
                "from_preset_id": preset_id,
            },
            headers=authorized(),
        )
        assert hired.status_code == 200, hired.text
        record = manager.get_agent(hired.json()["entity_ref"]["id"])
        assert record["profile_id"] == "claude-independent-reviewer"
        assert record["grants"]["open_links"] == "ask"
        assert not record.get("template")
