from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from agent_commons.services import CommonsManager
from tests.ui.conftest import authorized


class PreviewWorkspace(TypedDict):
    """Fixture fields the preview route setup needs to register an artifact."""

    repo: Path
    state_root: Path


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (3).to_bytes(4, "big")
        + (2).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    )


def _artifact(
    workspace: PreviewWorkspace,
    *,
    classification: str = "internal",
) -> tuple[str, Path, bytes]:
    repo = workspace["repo"]
    state_root = workspace["state_root"]
    bootstrap = CommonsManager(repo, state_root=state_root)
    session = bootstrap.start_session(
        stable_instance_id="artifact-preview-route-test",
        principal="test",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager = CommonsManager(repo, state_root=state_root, session_id=session["session_id"])
    source = repo / "screen.png"
    content = _png()
    source.write_bytes(content)
    registered = manager.register_artifact(
        source,
        media_type="image/png",
        classification=classification,
        idempotency_key="artifact-preview-route",
    )
    return registered["entity_ref"]["id"], source, content


def test_preview_route_requires_bearer_and_returns_verified_image(client, workspace) -> None:  # type: ignore[no-untyped-def]
    artifact_id, _source, content = _artifact(workspace)
    path = f"/api/artifacts/{artifact_id}/preview"

    missing = client.get(path)
    response = client.get(path, headers=authorized())

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"].startswith("Bearer")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.content == content


def test_preview_route_returns_typed_refusals_without_a_source_path(client, workspace) -> None:  # type: ignore[no-untyped-def]
    restricted_id, _source, _content = _artifact(workspace, classification="restricted")
    response = client.get(f"/api/artifacts/{restricted_id}/preview", headers=authorized())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "artifact_preview_classification_blocked"
    assert "screen.png" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_preview_route_refuses_changed_current_source(client, workspace) -> None:  # type: ignore[no-untyped-def]
    artifact_id, source, _content = _artifact(workspace)
    source.write_bytes(_png() + b"replaced")

    response = client.get(f"/api/artifacts/{artifact_id}/preview", headers=authorized())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "artifact_preview_stale_source"
