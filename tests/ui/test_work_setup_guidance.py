"""The Work setup read must expose only its closed, redacted DTO."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent_commons.ui.setup as setup
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from tests.ui.conftest import PORT, authorized

_SENTINELS = (
    "parser-detail-DO-NOT-RENDER",
    "provider-detail-DO-NOT-RENDER",
    "/private/operator/secret-workspace",
    "/Users/example/.config/agent-commons/runtime.yaml",
    "config-body-DO-NOT-RENDER",
    "fake-secret-DO-NOT-RENDER",
    "stderr-detail-DO-NOT-RENDER",
)


def _discovery(
    *,
    claude: bool,
    codex: bool,
    grok: bool = False,
    mcp: bool,
    git: bool,
) -> setup.ProviderDiscovery:
    """Build a discovery full of hostile details that Work must not echo."""

    def probe(name: str, found: bool) -> setup.ExecutableProbe:
        return setup.ExecutableProbe(
            name=name,
            role="provider",
            path="/private/operator/secret-workspace/fake-secret-DO-NOT-RENDER" if found else None,
            refusals=(
                setup.ProbeRefusal(
                    candidate="/Users/example/.config/agent-commons/runtime.yaml",
                    reason="provider-detail-DO-NOT-RENDER stderr-detail-DO-NOT-RENDER",
                ),
            ),
        )

    return setup.ProviderDiscovery(
        claude=probe("claude", claude),
        codex=probe("codex", codex),
        grok=probe("grok", grok),
        mcp=probe("agent-commons-mcp", mcp),
        git=probe("git", git),
    )


def _workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=())
    return repo


def _guidance_client(tmp_path: Path) -> TestClient:
    repo = _workspace(tmp_path)
    return TestClient(
        create_app(UIContext(repo), token="test-token", port=PORT),
        base_url=f"http://127.0.0.1:{PORT}",
    )


@pytest.mark.parametrize(
    ("state", "discovery", "expected"),
    [
        (
            setup.SETUP_UNCONFIGURED,
            _discovery(claude=False, codex=False, mcp=False, git=False),
            {
                "blocker_code": setup.SETUP_NO_PROVIDER_FOUND,
                "tools": ["Claude", "Codex", "Grok"],
                "next_action_key": "install_provider_and_check_again",
                "location_label": None,
            },
        ),
        (
            setup.SETUP_UNCONFIGURED,
            _discovery(claude=True, codex=False, mcp=False, git=True),
            {
                "blocker_code": "setup_support_binary_unresolved",
                "tools": ["agent-commons-mcp"],
                "next_action_key": "install_support_tool_and_check_again",
                "location_label": None,
            },
        ),
        (
            setup.SETUP_UNCONFIGURED,
            _discovery(claude=False, codex=True, mcp=True, git=False),
            {
                "blocker_code": "setup_support_binary_unresolved",
                "tools": ["git"],
                "next_action_key": "install_support_tool_and_check_again",
                "location_label": None,
            },
        ),
        (
            setup.SETUP_UNCONFIGURED,
            _discovery(claude=True, codex=False, mcp=True, git=True),
            {
                "blocker_code": setup.SETUP_UNCONFIGURED,
                "tools": [],
                "next_action_key": "configure_runtime",
                "location_label": None,
            },
        ),
        (
            setup.CONFIG_REJECTED_BY_LOADER,
            _discovery(claude=True, codex=True, mcp=True, git=True),
            {
                "blocker_code": setup.CONFIG_REJECTED_BY_LOADER,
                "tools": [],
                "next_action_key": "repair_workspace_configuration",
                "location_label": None,
            },
        ),
    ],
)
def test_work_setup_guidance_is_closed_and_redacts_runtime_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    discovery: setup.ProviderDiscovery,
    expected: dict[str, object],
) -> None:
    """Raw setup detail never crosses the dedicated Work endpoint."""

    monkeypatch.setattr(
        setup,
        "setup_state_report",
        lambda *_args, **_kwargs: {
            "state": state,
            "rejected_reason": (
                "parser-detail-DO-NOT-RENDER config-body-DO-NOT-RENDER fake-secret-DO-NOT-RENDER"
            ),
            "rejected_path": "/Users/example/.config/agent-commons/runtime.yaml",
        },
    )
    monkeypatch.setattr(setup, "discover_providers", lambda *_args, **_kwargs: discovery)

    with _guidance_client(tmp_path) as client:
        response = client.get("/api/work/setup-guidance", headers=authorized())

    assert response.status_code == 200
    assert response.json() == expected
    serialized = json.dumps(response.json())
    assert all(sentinel not in serialized for sentinel in _SENTINELS)


def test_work_setup_guidance_reveals_only_a_closed_generic_location_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup,
        "setup_state_report",
        lambda *_args, **_kwargs: {
            "state": setup.CONFIG_REJECTED_BY_LOADER,
            "rejected_reason": "parser-detail-DO-NOT-RENDER",
            "rejected_path": "/private/operator/secret-workspace/runtime.yaml",
        },
    )

    with _guidance_client(tmp_path) as client:
        response = client.get("/api/work/setup-guidance?reveal_location=true", headers=authorized())

    assert response.status_code == 200
    assert response.json() == {
        "blocker_code": setup.CONFIG_REJECTED_BY_LOADER,
        "tools": [],
        "next_action_key": "repair_workspace_configuration",
        "location_label": "workspace_configuration",
    }
    serialized = json.dumps(response.json())
    assert all(sentinel not in serialized for sentinel in _SENTINELS)


def test_work_setup_guidance_requires_an_authenticated_browser_session(tmp_path: Path) -> None:
    with _guidance_client(tmp_path) as client:
        response = client.get("/api/work/setup-guidance")

    assert response.status_code == 401
