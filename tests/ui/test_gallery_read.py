from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from agent_commons.errors import IntegrityError
from agent_commons.services import CommonsManager
from agent_commons.services.design_gallery import (
    DesignGalleryReads,
    GalleryReadRefusal,
    GalleryRefusalCode,
)
from agent_commons.ui.gallery_dtos import GalleryResponseDTO
from agent_commons.ui.gallery_routes import register_gallery_routes


def _png(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"
    )


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="gallery-read-tests")
    state = tmp_path / "state"
    bootstrap = CommonsManager(repo, state_root=state)
    session = bootstrap.start_session(
        stable_instance_id="gallery-read-builder-12345678",
        principal="designer",
        client="pytest",
        software="pytest",
        role="product-designer",
    )
    return CommonsManager(repo, state_root=state, session_id=str(session["session_id"]))


def _screen_work(
    manager: CommonsManager,
    *,
    suffix: str,
    ordinal: int,
) -> tuple[dict[str, object], dict[str, object], Path, dict[str, object]]:
    source = manager.repo_root / "screens" / f"{suffix}.png"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(_png(width=ordinal + 2, height=ordinal + 1))
    artifact = manager.register_artifact(
        source,
        media_type="image/png",
        classification="internal",
        idempotency_key=f"gallery-artifact-{suffix}",
    )
    task = manager.create_task(
        title=f"Design {suffix}",
        description="Produce one exact Gallery screen.",
        acceptance_criteria=("The PNG revision is bound.",),
        idempotency_key=f"gallery-task-{suffix}",
    )
    task_id = str(task["entity_ref"]["id"])
    taken = manager.take_task(
        task_id,
        str(task["revision"]),
        idempotency_key=f"gallery-task-take-{suffix}",
    )
    started = manager.start_task(
        task_id,
        str(taken["revision"]),
        idempotency_key=f"gallery-task-start-{suffix}",
    )
    completed = manager.complete_task(
        task_id,
        str(started["revision"]),
        summary="The exact Gallery screen is ready.",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key=f"gallery-task-complete-{suffix}",
    )
    artifact_id = str(artifact["entity_ref"]["id"])
    projected = manager.snapshot().artifacts[artifact_id]
    screen = {
        "screen_id": "screen." + "0" * 24 + f"{ordinal:02d}",
        "ordinal": ordinal,
        "title": suffix.title(),
        "artifact_binding": {
            "ref": dict(artifact["entity_ref"]),
            "revision": artifact["revision"],
        },
        "artifact_content_revision": projected["content_revision"],
        "producer_task_binding": {
            "ref": dict(completed["entity_ref"]),
            "revision": completed["revision"],
        },
        "classification": "internal",
        "media_type": "image/png",
        "safe_preview_eligible": True,
    }
    return artifact, completed, source, screen


def _published(
    tmp_path: Path,
) -> tuple[CommonsManager, object, list[tuple[dict[str, object], Path]]]:
    manager = _manager(tmp_path)
    first_artifact, _first_task, first_source, first_screen = _screen_work(
        manager, suffix="checkout", ordinal=1
    )
    second_artifact, _second_task, second_source, second_screen = _screen_work(
        manager, suffix="confirmation", ordinal=2
    )
    package = manager.design_packages.publish(
        {"title": "Checkout flow", "screens": [first_screen, second_screen]},
        idempotency_key="gallery-package",
    )
    return (
        manager,
        package,
        [
            (first_artifact, first_source),
            (second_artifact, second_source),
        ],
    )


def test_gallery_empty_and_loading_states_are_explicit(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    response = GalleryResponseDTO.from_snapshot(DesignGalleryReads(manager).list()).to_wire()
    loading = GalleryResponseDTO.loading().to_wire()

    assert response == {
        "schema": "agent_commons.gallery.v1",
        "state": "empty",
        "freshness": "fresh",
        "read_at": response["read_at"],
        "packages": [],
        "error": None,
    }
    assert loading == {
        "schema": "agent_commons.gallery.v1",
        "state": "loading",
        "freshness": None,
        "read_at": None,
        "packages": [],
        "error": None,
    }


def test_gallery_returns_real_ordered_screens_and_exact_provenance(tmp_path: Path) -> None:
    manager, package, _artifacts = _published(tmp_path)

    view = DesignGalleryReads(manager).get(package.design_package_id)
    wire = GalleryResponseDTO.from_snapshot(view).to_wire()

    assert wire["state"] == "ready"
    assert wire["freshness"] == "fresh"
    assert len(wire["packages"]) == 1
    rendered = wire["packages"][0]
    assert rendered["design_package_id"] == package.design_package_id
    assert rendered["revision"] == package.revision
    assert rendered["screen_count"] == 2
    assert [screen["ordinal"] for screen in rendered["screens"]] == [1, 2]
    assert [screen["title"] for screen in rendered["screens"]] == [
        "Checkout",
        "Confirmation",
    ]
    for screen in rendered["screens"]:
        assert screen["artifact_id"].startswith("artifact.")
        assert screen["artifact_revision"].startswith("evt.")
        assert screen["artifact_content_revision"].startswith("sha256:")
        assert screen["producer_task_id"].startswith("task.")
        assert screen["producer_task_revision"].startswith("evt.")
        assert screen["producer_session_id"] == manager.session_id
        assert screen["preview_eligible"] is True
        assert screen["preview_state"] == "ready"
        assert screen["width"] is not None and screen["height"] is not None
        assert "path" not in screen


@pytest.mark.parametrize("replacement", ["changed", "symlink"])
def test_gallery_fails_closed_when_preview_source_is_unsafe(
    tmp_path: Path,
    replacement: str,
) -> None:
    manager, _package, artifacts = _published(tmp_path)
    _artifact, source = artifacts[0]
    if replacement == "changed":
        source.write_bytes(_png() + b"changed")
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(_png())
        source.unlink()
        source.symlink_to(outside)

    wire = GalleryResponseDTO.from_snapshot(DesignGalleryReads(manager).list()).to_wire()
    screen = wire["packages"][0]["screens"][0]

    assert wire["state"] == "stale"
    assert wire["freshness"] == "stale"
    assert screen["preview_eligible"] is False
    assert screen["preview_state"] in {"stale", "unavailable"}
    assert screen["preview_reason"] in {
        "artifact_preview_stale_source",
        "artifact_preview_symlink_source",
    }
    assert str(source) not in str(wire)
    assert str(outside if replacement == "symlink" else source) not in str(wire)


def test_gallery_marks_missing_manifest_authorization_stale(tmp_path: Path) -> None:
    manager, _package, _artifacts = _published(tmp_path)
    snapshot = manager.snapshot()
    snapshot.known_manifest_ids.clear()

    class MissingManifestManager:
        def snapshot(self):  # type: ignore[no-untyped-def]
            return snapshot

        def __getattr__(self, name: str) -> Any:
            return getattr(manager, name)

    wire = GalleryResponseDTO.from_snapshot(
        DesignGalleryReads(MissingManifestManager()).list()
    ).to_wire()

    assert wire["state"] == "stale"
    assert wire["packages"][0]["screens"][0]["preview_reason"] == "artifact_binding_changed"


def test_gallery_route_returns_bounded_typed_success_and_refusals(tmp_path: Path) -> None:
    manager, package, _artifacts = _published(tmp_path)
    app = FastAPI()

    async def require_session(x_session: str | None = Header(default=None)) -> None:
        if x_session != "allowed":
            raise HTTPException(status_code=401, detail="session required")

    register_gallery_routes(
        app,
        dependencies=[Depends(require_session)],
        manager_factory=lambda: manager,
    )
    client = TestClient(app)

    unauthenticated = client.get("/api/gallery")
    response = client.get("/api/gallery", headers={"X-Session": "allowed"})
    detail = client.get(
        f"/api/gallery/{package.design_package_id}", headers={"X-Session": "allowed"}
    )
    missing = client.get(
        "/api/gallery/design_package.00000000000000000000000000",
        headers={"X-Session": "allowed"},
    )

    assert unauthenticated.status_code == 401
    assert response.status_code == detail.status_code == 200
    assert response.json()["schema"] == "agent_commons.gallery.v1"
    assert detail.json()["packages"][0]["revision"] == package.revision
    assert missing.status_code == 404
    assert missing.json()["state"] == "error"
    assert missing.json()["error"]["code"] == "gallery_package_not_found"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_gallery_route_collapses_missing_projection_without_leaking_details() -> None:
    class MissingProjection:
        def snapshot(self):  # type: ignore[no-untyped-def]
            raise IntegrityError("private /workspace/cache/index.sqlite3 detail")

    app = FastAPI()
    register_gallery_routes(app, dependencies=[], manager_factory=MissingProjection)
    response = TestClient(app).get("/api/gallery")

    assert response.status_code == 409
    assert response.json()["state"] == "error"
    assert response.json()["error"]["code"] == "gallery_projection_unavailable"
    assert "/workspace" not in response.text


def test_gallery_bounds_refuse_instead_of_silently_truncating(tmp_path: Path) -> None:
    manager, _package, _artifacts = _published(tmp_path)

    with pytest.raises(GalleryReadRefusal) as raised:
        DesignGalleryReads(manager, max_screens=1).list()

    assert raised.value.code is GalleryRefusalCode.BOUNDS_EXCEEDED
    assert "screen" in raised.value.message.lower()
