"""The Work Starter Pack catalogue is authenticated, descriptive, and read-only."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_commons.domain.roles import DENY_ALL
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from agent_commons.ui.starter_packs import StarterPackCatalogUnavailable
from tests.ui.conftest import PORT, authorized, tree_digest


def _state_root(repo: Path) -> Path:
    return repo.parent / "state"


def _client(repo: Path) -> TestClient:
    """Build a Work-capable local app for an initialized test workspace."""

    return TestClient(
        create_app(UIContext(repo, state_root=_state_root(repo)), token="test-token", port=PORT),
        base_url=f"http://127.0.0.1:{PORT}",
    )


def _operator_client(repo: Path) -> tuple[TestClient, CommonsManager]:
    """Build a writing Work-capable app for one initialized test workspace."""

    manager = CommonsManager(repo, state_root=_state_root(repo))
    session = manager.start_session(
        stable_instance_id="starter-pack-operator-window",
        principal="operator",
        client="codex",
        software="codex",
        role="operator",
    )
    context = UIContext(
        repo,
        state_root=_state_root(repo),
        writer_session_id=str(session["session_id"]),
    )
    return (
        TestClient(
            create_app(context, token="test-token", port=PORT),
            base_url=f"http://127.0.0.1:{PORT}",
        ),
        manager,
    )


def _workspace(tmp_path: Path) -> Path:
    """Create only the ordinary initialized project prerequisite for Work reads."""

    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=())
    return repo


def test_work_starter_pack_catalog_exposes_only_the_two_bundled_examples_without_writing(
    tmp_path: Path,
) -> None:
    repo = _workspace(tmp_path)
    before = tree_digest(repo)

    with _client(repo) as client:
        response = client.get("/api/work/starter-packs", headers=authorized())

    assert response.status_code == 200
    assert response.json() == {
        "packs": [
            {
                "id": "starter.feature-delivery.mock",
                "version": "0.1.0",
                "title": "Feature delivery (example)",
                "summary": (
                    "An implementer and an independent reviewer for a small technical change."
                ),
                "source_kind": "bundled",
                "example": True,
                "blueprints": [
                    {
                        "id": "feature-delivery",
                        "title": "Feature delivery",
                        "summary": "Plan, implement, then independently review a bounded change.",
                        "roles": [
                            {
                                "id": "implementer",
                                "name": "Implementer",
                                "purpose": (
                                    "Build the scoped change and report verifiable evidence."
                                ),
                                "profile_id": "claude-builder",
                                "context_mode": "fresh",
                                "skills": [
                                    "commons-start",
                                    "commons-coordinate",
                                    "commons-record",
                                ],
                            },
                            {
                                "id": "independent-reviewer",
                                "name": "Independent reviewer",
                                "purpose": (
                                    "Assess the submitted work without inheriting the "
                                    "implementer's context."
                                ),
                                "profile_id": "claude-independent-reviewer",
                                "context_mode": "fresh",
                                "skills": ["commons-start", "commons-review", "commons-record"],
                            },
                        ],
                    }
                ],
            },
            {
                "id": "starter.product-discovery.mock",
                "version": "0.1.0",
                "title": "Product discovery (example)",
                "summary": (
                    "A researcher and product reviewer for an evidence-backed product question."
                ),
                "source_kind": "bundled",
                "example": True,
                "blueprints": [
                    {
                        "id": "product-discovery",
                        "title": "Product discovery",
                        "summary": (
                            "Gather evidence, then review the recommendation "
                            "before an owner decides."
                        ),
                        "roles": [
                            {
                                "id": "researcher",
                                "name": "Researcher",
                                "purpose": (
                                    "Collect bounded evidence and state assumptions "
                                    "and open questions."
                                ),
                                "profile_id": "claude-builder",
                                "context_mode": "fresh",
                                "skills": ["commons-start", "commons-share", "commons-record"],
                            },
                            {
                                "id": "product-reviewer",
                                "name": "Product reviewer",
                                "purpose": (
                                    "Check whether evidence supports the recommendation "
                                    "without making the owner's decision."
                                ),
                                "profile_id": "claude-independent-reviewer",
                                "context_mode": "fresh",
                                "skills": ["commons-start", "commons-review", "commons-record"],
                            },
                        ],
                    }
                ],
            },
        ]
    }
    assert tree_digest(repo) == before
    serialized = json.dumps(response.json())
    assert "runtime_instruction" not in serialized
    assert "grant" not in serialized
    assert "payload" not in serialized


def test_work_starter_pack_apply_requires_confirmation_and_creates_role_templates(
    tmp_path: Path,
) -> None:
    repo = _workspace(tmp_path)
    client, manager = _operator_client(repo)

    with client:
        refused = client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"idempotency_key": "starter-pack-apply-test"},
            headers=authorized(),
        )
        response = client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"confirmed": True, "idempotency_key": "starter-pack-apply-test"},
            headers=authorized(),
        )
        repeated = client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"confirmed": True, "idempotency_key": "starter-pack-apply-test"},
            headers=authorized(),
        )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "starter_pack_apply_confirmation_required"
    assert response.status_code == 200, response.text
    assert repeated.status_code == 200, repeated.text
    payload = response.json()
    assert repeated.json() == payload
    assert payload["pack_id"] == "starter.feature-delivery.mock"
    assert payload["blueprint_id"] == "feature-delivery"
    assert [role["source_role_id"] for role in payload["roles"]] == [
        "implementer",
        "independent-reviewer",
    ]
    assert [role["profile_id"] for role in payload["roles"]] == [
        "claude-builder",
        "claude-independent-reviewer",
    ]
    assert all(role["context_mode"] == "fresh" for role in payload["roles"])
    assert all(role["template"] is True for role in payload["roles"])
    assert all(role["grants"] == DENY_ALL for role in payload["roles"])
    assert all(
        all(skill.startswith("commons-") for skill in role["skills"]) for role in payload["roles"]
    )
    serialized = json.dumps(payload)
    assert "runtime_instruction" not in serialized
    assert "payload" not in serialized

    for item in payload["roles"]:
        role = manager.get_agent(item["agent_id"])
        assert role["template"] is True
        assert role["context_mode"] == "fresh"
        assert role["profile_id"] == item["profile_id"]
        assert role["grants"] == DENY_ALL
        assert tuple(role.get("skills") or ()) == tuple(item["skills"])


def test_work_starter_pack_catalog_requires_an_authenticated_browser_session(
    tmp_path: Path,
) -> None:
    repo = _workspace(tmp_path)

    with _client(repo) as client:
        response = client.get("/api/work/starter-packs")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_work_starter_pack_catalog_fails_closed_when_bundled_resources_do_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _workspace(tmp_path)

    def unavailable() -> None:
        raise StarterPackCatalogUnavailable

    monkeypatch.setattr(
        "agent_commons.ui.starter_pack_routes.read_starter_pack_catalog", unavailable
    )
    with _client(repo) as client:
        response = client.get("/api/work/starter-packs", headers=authorized())

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "starter_pack_catalog_unavailable",
            "message": "bundled Starter Pack examples could not be verified",
        }
    }


def test_work_starter_pack_catalog_refuses_before_workspace_initialization(tmp_path: Path) -> None:
    repo = tmp_path / "new-project"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)

    with _client(repo) as client:
        response = client.get("/api/work/starter-packs", headers=authorized())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "setup_uninitialized"


def test_work_starter_pack_apply_is_an_authenticated_operator_write(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)

    with _client(repo) as read_client:
        read_only = read_client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"confirmed": True, "idempotency_key": "starter-pack-apply-read-only"},
            headers=authorized(),
        )
        unauthenticated = read_client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"confirmed": True, "idempotency_key": "starter-pack-apply-read-only"},
        )

    assert read_only.status_code == 404
    assert unauthenticated.status_code == 401
