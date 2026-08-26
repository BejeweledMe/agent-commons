"""The isolated Work shell shares the local UI's strict public/private boundary."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_commons.ui import work_static_directory
from agent_commons.ui.security import (
    SESSION_COOKIE_NAME,
    is_public_path,
    work_content_security_policy,
)
from agent_commons.ui.server import create_app


def _work_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "work"
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    (bundle / "index.html").write_text(
        '<!doctype html><script type="module" src="/work/assets/work-test.js"></script>',
        encoding="utf-8",
    )
    (assets / "work-test.js").write_text("export {};\n", encoding="utf-8")
    return bundle


def test_work_reader_points_to_a_dedicated_packaged_subtree() -> None:
    assert work_static_directory().as_posix().endswith("static/work")


def test_work_shell_and_assets_are_public_but_existing_api_data_is_not(
    context, monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """The temporary bundle lets this adapter commit be tested before bundle integration."""

    from agent_commons.ui import work_routes

    bundle = _work_bundle(tmp_path)
    monkeypatch.setattr(work_routes, "work_static_directory", lambda: bundle)
    monkeypatch.setattr(
        work_routes, "read_work_shell", lambda: (bundle / "index.html").read_text("utf-8")
    )

    app = create_app(context, token="test-token", port=51234)
    with TestClient(app, base_url="http://127.0.0.1:51234") as work_client:
        shell = work_client.get("/work")
        assert shell.status_code == 200
        assert "Content-Security-Policy" in shell.headers
        assert "script-src 'self'" in shell.headers["Content-Security-Policy"]
        assert work_client.get("/work/assets/work-test.js").status_code == 200
        assert work_client.get("/api/meta").status_code == 401
        assert (
            work_client.get(
                "/api/meta", headers={"Cookie": f"{SESSION_COOKIE_NAME}=test-token"}
            ).status_code
            == 200
        )


def test_work_public_path_rule_never_exposes_an_api() -> None:
    assert is_public_path("/work") is True
    assert is_public_path("/work/assets/work-test.js") is True
    assert is_public_path("/api/work") is False


def test_work_csp_allows_only_packaged_same_origin_assets() -> None:
    policy = work_content_security_policy()
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
