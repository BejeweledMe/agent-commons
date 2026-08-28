"""The Work Starter Pack catalogue is authenticated, descriptive, and read-only."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from agent_commons.ui.starter_packs import StarterPackCatalogUnavailable
from tests.ui.conftest import PORT, authorized, tree_digest


def _client(repo: Path) -> TestClient:
    """Build a Work-capable local app for an initialized test workspace."""

    return TestClient(
        create_app(UIContext(repo), token="test-token", port=PORT),
        base_url=f"http://127.0.0.1:{PORT}",
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
                                "context_mode": "fresh",
                                "skills": ["software-engineering", "qa-testing"],
                            },
                            {
                                "id": "independent-reviewer",
                                "name": "Independent reviewer",
                                "purpose": (
                                    "Assess the submitted work without inheriting the "
                                    "implementer's context."
                                ),
                                "context_mode": "fresh",
                                "skills": ["qa-testing"],
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
                                "context_mode": "fresh",
                                "skills": ["business-product-consulting"],
                            },
                            {
                                "id": "product-reviewer",
                                "name": "Product reviewer",
                                "purpose": (
                                    "Check whether evidence supports the recommendation "
                                    "without making the owner's decision."
                                ),
                                "context_mode": "fresh",
                                "skills": ["business-product-consulting"],
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
    assert "profile" not in serialized
    assert "grant" not in serialized
    assert "payload" not in serialized


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
