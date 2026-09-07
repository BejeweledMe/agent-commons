from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from agent_commons.services.design_upload import import_screen
from tests.services.test_design_packages import _png
from tests.ui.conftest import authorized


def _body(key: str = "upload-screen") -> dict[str, str]:
    return {
        "title": "Checkout",
        "media_type": "image/png",
        "content_base64": base64.b64encode(_png()).decode(),
        "idempotency_key": key,
    }


def test_import_retry_and_publish_are_real_canonical_work(writable, writable_client):
    body = _body()
    assert writable_client.post("/api/gallery/import", json=body).status_code == 401
    response = writable_client.post("/api/gallery/import", json=body, headers=authorized())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "imported"
    retry = writable_client.post("/api/gallery/import", json=body, headers=authorized())
    assert retry.json() == result
    snapshot = writable.writer().snapshot()
    assert len(snapshot.artifacts) == len(snapshot.tasks) == 1
    assert snapshot.tasks[result["task_id"]]["state"] == "completed"
    assert "accepted_subject_revision" not in snapshot.tasks[result["task_id"]]
    candidates = writable_client.get("/api/gallery/authoring", headers=authorized()).json()
    assert candidates["candidates"][0]["candidate_id"] == result["candidate_id"]
    published = writable_client.post(
        "/api/gallery/packages",
        json={
            "title": "Checkout design",
            "screens": [{"candidate_id": result["candidate_id"], "title": "Checkout"}],
            "idempotency_key": "publish-import",
        },
        headers=authorized(),
    )
    assert published.status_code == 200, published.text
    # Exercise the actual producer UUID session emitted by Commons, rather
    # than a hand-authored frontend fixture using a task-style ULID session.
    gallery = writable_client.get("/api/gallery", headers=authorized())
    parsed = subprocess.run(
        ["node", "--input-type=module", "-", json.dumps(gallery.json())],
        input=(
            'import { parseGalleryResponse } from "./frontend/gallery/src/contracts.ts";'
            "const parsed = parseGalleryResponse(JSON.parse(process.argv[2]));"
            "if (parsed.packages.length !== 1) process.exit(1);"
        ),
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    image = writable_client.get(
        f"/api/artifacts/{result['artifact_id']}/preview", headers=authorized()
    )
    assert image.status_code == 200 and image.content == _png()
    for record in writable.writer().events.iter_events():
        serialized = json.dumps(record.event)
        assert "content_base64" not in serialized
        assert body["content_base64"] not in serialized


@pytest.mark.parametrize(
    "change",
    [
        {"media_type": "image/svg+xml"},
        {"content_base64": "not-base64"},
        {"path": "../../outside"},
        {"content_base64": base64.b64encode(_png(100_000, 100_000)).decode()},
    ],
)
def test_invalid_import_refuses_before_canonical_changes(writable, writable_client, change):
    response = writable_client.post(
        "/api/gallery/import", json={**_body(), **change}, headers=authorized()
    )
    assert response.status_code == 422
    snapshot = writable.writer().snapshot()
    assert not snapshot.tasks and not snapshot.artifacts
    assert "../../outside" not in response.text


def test_changed_retry_keeps_first_image_and_task(writable, writable_client):
    first = writable_client.post("/api/gallery/import", json=_body(), headers=authorized())
    assert first.status_code == 200
    changed = {**_body(), "content_base64": base64.b64encode(_png(4, 3)).decode()}
    refused = writable_client.post("/api/gallery/import", json=changed, headers=authorized())
    assert refused.status_code == 409
    assert len(list((writable.repo / "design-assets").iterdir())) == 1
    assert len(writable.writer().snapshot().tasks) == 1


def test_partial_import_resumes_same_steps(writable, monkeypatch):
    manager = writable.writer()
    original = manager.start_task

    def interrupted(*args, **kwargs):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(manager, "start_task", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        import_screen(manager, _body())
    assert len(manager.snapshot().tasks) == len(manager.snapshot().artifacts) == 1
    monkeypatch.setattr(manager, "start_task", original)
    result = import_screen(manager, _body())
    assert manager.snapshot().tasks[result["task_id"]]["state"] == "completed"
    assert len(manager.snapshot().tasks) == len(manager.snapshot().artifacts) == 1


def test_import_never_follows_storage_symlink(writable, writable_client, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (writable.repo / "design-assets").symlink_to(outside, target_is_directory=True)
    response = writable_client.post("/api/gallery/import", json=_body(), headers=authorized())
    assert response.status_code == 409
    assert not list(outside.iterdir())
    assert not writable.writer().snapshot().artifacts

    # The conflict follows task creation. Recover with the same identity, so
    # the pending task is completed instead of leaving a duplicate behind.
    original_task_ids = set(writable.writer().snapshot().tasks)
    assert len(original_task_ids) == 1
    (writable.repo / "design-assets").unlink()
    retried = writable_client.post("/api/gallery/import", json=_body(), headers=authorized())
    assert retried.status_code == 200, retried.text
    snapshot = writable.writer().snapshot()
    assert set(snapshot.tasks) == original_task_ids
    assert len(snapshot.artifacts) == 1
    assert snapshot.tasks[retried.json()["task_id"]]["state"] == "completed"


def test_replaced_source_never_registers_different_bytes_as_selected_image(writable, monkeypatch):
    from agent_commons.errors import IntegrityError
    from agent_commons.services import design_upload

    original = design_upload._store_source

    def replace_after_storage(manager, content, media_type):
        source = original(manager, content, media_type)
        source.write_bytes(_png(5, 4))
        return source

    monkeypatch.setattr(design_upload, "_store_source", replace_after_storage)
    with pytest.raises(IntegrityError):
        import_screen(writable.writer(), _body())
    snapshot = writable.writer().snapshot()
    assert not snapshot.artifacts
    assert all(task["state"] == "ready" for task in snapshot.tasks.values())
