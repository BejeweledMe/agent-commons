"""The first React Flow screen coexists with the legacy static panel."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from agent_commons.ui import gallery_static_directory, read_gallery_shell
from agent_commons.ui.context import UIContext
from agent_commons.ui.security import gallery_content_security_policy, is_public_path
from agent_commons.ui.server import create_app
from agent_commons.ui.setup import SETUP_NOT_A_REPOSITORY, SETUP_UNINITIALIZED
from tests.ui.conftest import authorized

_REPOSITORY_ROOT = Path(__file__).parents[2]
_GALLERY_SOURCE = _REPOSITORY_ROOT / "frontend" / "gallery"


def test_gallery_bundle_is_packaged_beside_the_legacy_panel() -> None:
    shell = read_gallery_shell()
    assets = gallery_static_directory() / "assets"

    assert '<script type="module" crossorigin src="/gallery/assets/gallery-' in shell
    assert assets.is_dir()
    assert any(path.suffix == ".js" for path in assets.iterdir())
    assert any(path.suffix == ".css" for path in assets.iterdir())


def test_gallery_shell_and_static_assets_are_public_but_data_is_not(client) -> None:  # type: ignore[no-untyped-def]
    shell = client.get("/gallery")
    assert shell.status_code == 200
    assert "Content-Security-Policy" in shell.headers
    assert "script-src 'self'" in shell.headers["Content-Security-Policy"]

    source = re.search(r'src="(/gallery/assets/[^"]+\.js)"', shell.text)
    assert source is not None
    assert client.get(source.group(1)).status_code == 200

    refused = client.get("/api/gallery")
    assert refused.status_code == 401
    available = client.get("/api/gallery", headers=authorized())
    assert available.status_code == 409
    assert available.json()["error"]["code"] == "gallery_data_unavailable"


def test_gallery_bootstrap_preserves_the_shared_first_run_refusal(tmp_path: Path) -> None:
    """Gallery data is unavailable only after a workspace can be read."""

    repository = tmp_path / "repository"
    repository.mkdir()
    context = UIContext(repository, state_root=tmp_path / "state")
    app = create_app(context, token="test-token", port=51234)

    with TestClient(app, base_url="http://127.0.0.1:51234") as gallery_client:
        response = gallery_client.get("/api/gallery", headers=authorized())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == SETUP_NOT_A_REPOSITORY


def test_gallery_bootstrap_preserves_the_uninitialized_refusal(tmp_path: Path) -> None:
    """A bare repository still receives the canonical setup state."""

    import subprocess

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True, capture_output=True)
    context = UIContext(repository, state_root=tmp_path / "state")
    app = create_app(context, token="test-token", port=51234)

    with TestClient(app, base_url="http://127.0.0.1:51234") as gallery_client:
        response = gallery_client.get("/api/gallery", headers=authorized())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == SETUP_UNINITIALIZED


def test_gallery_public_path_rule_never_exposes_an_api() -> None:
    assert is_public_path("/gallery") is True
    assert is_public_path("/gallery/assets/gallery-abc.js") is True
    assert is_public_path("/api/gallery") is False


def test_gallery_csp_allows_only_packaged_same_origin_assets() -> None:
    policy = gallery_content_security_policy()
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_gallery_locale_source_is_paired_and_has_no_demo_copy() -> None:
    messages = json.loads((_GALLERY_SOURCE / "src" / "i18n.json").read_text("utf-8"))
    assert set(messages) == {"en", "ru"}
    assert set(messages["en"]) == set(messages["ru"])
    assert "demo" not in " ".join(messages["en"].values()).lower()
    assert "демо" not in " ".join(messages["ru"].values()).lower()


def test_gallery_source_uses_react_flow_and_a_cookie_session_typed_refusal() -> None:
    source = (_GALLERY_SOURCE / "src" / "main.tsx").read_text("utf-8")
    package = json.loads((_GALLERY_SOURCE / "package.json").read_text("utf-8"))

    assert 'from "@xyflow/react"' in source
    assert "fetch(`${apiBase}/gallery`" in source
    assert 'fetch("/api/auth/exchange"' in source
    assert 'credentials: "same-origin"' in source
    assert "window.history.replaceState" in source
    assert "api_base" in source
    assert "Authorization" not in source
    assert "tokenFromFragment" not in source
    assert "gallery_data_unavailable" in source
    assert "@xyflow/react" in package["dependencies"]
    assert package["scripts"]["build"] == "tsc -b && vite build"
