from __future__ import annotations

import hashlib
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_commons.services import CommonsManager
from agent_commons.services.artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal
from agent_commons.services.generated_outputs import generated_series
from agent_commons.services.output_content import OutputContentStore
from agent_commons.services.outputs import OutputReads
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from tests.mcp import test_design_output_tools as generated
from tests.ui.conftest import PORT, authorized
from tests.ui.test_output_routes import _client


@pytest.fixture
def published_context(tmp_path, monkeypatch):
    return generated.published_context.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture(params=("legacy", "retained"))
def storage(request):
    return request.param


def publish_image(ctx, storage, *, idempotency_key="homepage-001"):
    if storage == "retained":
        return generated.publish(ctx, idempotency_key=idempotency_key)
    # Model a real pre-upgrade publication through the supported artifact API,
    # rather than stripping retention from a newly published immutable manifest.
    snapshot = ctx.worker.snapshot()
    metadata = {
        "output_kind": "task_design_image",
        "output_task": {"id": ctx.task, "revision": snapshot.entity_revision("task", ctx.task)},
        "output_delegation": {
            "id": ctx.run,
            "revision": snapshot.entity_revision("delegation", ctx.run),
        },
        "series_id": generated_series(ctx.run, "outputs/screen.png"),
        "title": "Homepage",
    }
    key = "generated-image-" + hashlib.sha256(f"{ctx.run}:{idempotency_key}".encode()).hexdigest()
    result = ctx.worker.register_artifact(
        "outputs/screen.png",
        media_type="image/png",
        classification="internal",
        metadata=metadata,
        idempotency_key=key,
    )
    artifact_id = result["entity_ref"]["id"]
    bundle = ctx.worker.get_artifact_bundle(artifact_id)
    assert bundle["manifest"]["metadata"] == metadata
    assert not (ctx.worker.paths.state_root / "output-content").exists()
    return {
        "artifact_id": artifact_id,
        "artifact_revision": result["revision"],
        "content_revision": bundle["manifest"]["revision"],
    }


def finish(ctx, storage, transition="complete_task"):
    receipt = publish_image(ctx, storage)
    before = ctx.worker.snapshot().tasks[ctx.task]
    if transition == "submit_task":
        completed = ctx.parent.complete_task(
            ctx.task, before["effective_revision"], summary="Work complete."
        )
        before = {"effective_revision": completed["revision"]}
    getattr(ctx.parent, transition)(
        ctx.task,
        before["effective_revision"],
        summary="Image finished.",
        artifact_refs=({"kind": "artifact", "id": receipt["artifact_id"]},),
        idempotency_key="image-complete",
    )
    return receipt


@pytest.mark.parametrize("transition", ["complete_task", "submit_task"])
@pytest.mark.parametrize("source_change", ["overwrite", "delete"])
def test_completed_generated_image_remains_viewable_as_verified_history(
    published_context, storage, transition, source_change
):
    ctx = published_context
    original_bytes = ctx.source.read_bytes()
    receipt = finish(ctx, storage, transition)
    reads = OutputReads(ctx.worker)
    for kind, scope in (("task", ctx.task), ("agent", ctx.agent)):
        item = reads.list(kind, scope).items[0]
        assert item.state == "stale" and item.reason == "producer_task_revision_changed"
        assert item.historical_preview_verified is True
        assert item.width is None and item.height is None
        assert reads.summary(kind, scope).stale == 1
        client = _client(lambda: ctx.worker)
        response = client.get(
            "/api/outputs",
            params={"scope_kind": kind, "scope_id": scope},
            headers={"X-Session": "allowed"},
        )
        assert response.status_code == 200
        wire = response.json()["items"][0]
        assert wire["historical_preview_verified"] is True
        assert wire["retained"] is (storage == "retained")
        assert wire["state"] == "stale" and wire["task_revision"] == item.task_revision
    assert not ctx.worker.snapshot().design_packages
    subprocess.run(
        ["git", "init", "--quiet", str(ctx.worker.repo_root)], check=True, capture_output=True
    )
    app = create_app(
        UIContext(ctx.worker.repo_root, state_root=ctx.worker.paths.state_root),
        token="test-token",
        exchange_code="test-exchange-code",
        port=PORT,
        api_base="/api",
        read_only=True,
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        url = f"/api/artifacts/{receipt['artifact_id']}/preview"
        assert client.get(url).status_code == 401
        response = client.get(url, headers=authorized())
        assert response.status_code == 200, response.json()
        assert response.content == original_bytes
        if source_change == "overwrite":
            Image.new("RGB", (4, 3), (220, 20, 20)).save(ctx.source)
        else:
            ctx.source.unlink()
        after = client.get(url, headers=authorized())
        if storage == "retained":
            assert after.status_code == 200
            assert after.content == original_bytes
        else:
            assert after.status_code == 409
    # Reopening the durable state must obey the same contract after mutation.
    reopened = CommonsManager(
        ctx.worker.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=ctx.worker.session_id,
    )
    item = OutputReads(reopened).list("task", ctx.task).items[0]
    assert item.historical_preview_verified is (storage == "retained")
    assert item.reason == (
        "producer_task_revision_changed"
        if storage == "retained"
        else "artifact_preview_stale_source"
        if source_change == "overwrite"
        else "artifact_preview_missing_source"
    )


def test_historical_summary_never_reads_bytes_and_source_deletion_obeys_retention(
    published_context, storage
):
    ctx = published_context
    finish(ctx, storage)
    calls = []

    def forbidden(manager):
        calls.append(True)
        raise AssertionError("Summary must not construct a byte reader")

    assert (
        OutputReads(ctx.worker, preview_reader_factory=forbidden).summary("task", ctx.task).stale
        == 1
    )
    assert not calls
    ctx.source.unlink()
    item = OutputReads(ctx.worker).list("task", ctx.task).items[0]
    assert item.state == "stale"
    assert item.reason == (
        "producer_task_revision_changed"
        if storage == "retained"
        else "artifact_preview_missing_source"
    )
    assert item.historical_preview_verified is (storage == "retained")


def test_historical_preview_respects_current_classification_and_exact_producer(
    published_context, storage, monkeypatch
):
    ctx = published_context
    receipt = finish(ctx, storage)
    snapshot = ctx.worker.snapshot()
    monkeypatch.setattr(ctx.worker, "snapshot", lambda: snapshot)
    original = snapshot.artifacts[receipt["artifact_id"]]
    snapshot.artifacts[receipt["artifact_id"]] = {**original, "classification": "restricted"}
    assert OutputReads(ctx.worker).list("task", ctx.task).items == ()
    snapshot.artifacts[receipt["artifact_id"]] = original
    run = snapshot.delegations[ctx.run]
    snapshot.delegations[ctx.run] = {**run, "child_session_id": "session." + "f" * 32}
    assert OutputReads(ctx.worker).list("task", ctx.task).items == ()
    assert OutputReads(ctx.worker).list("agent", ctx.agent).items == ()


def test_restoring_old_source_never_substitutes_an_older_historical_version(
    published_context, storage
):
    ctx = published_context
    first = publish_image(ctx, storage)
    original = ctx.source.read_bytes()
    Image.new("RGB", (4, 3), (90, 120, 10)).save(ctx.source)
    second = publish_image(ctx, storage, idempotency_key="homepage-002")
    current = ctx.worker.snapshot().tasks[ctx.task]
    ctx.parent.complete_task(
        ctx.task, current["effective_revision"], summary="Two image versions recorded."
    )
    ctx.source.write_bytes(original)
    reads = OutputReads(ctx.worker)
    latest = reads.list("task", ctx.task).items
    assert len(latest) == 1 and latest[0].artifact_id == second["artifact_id"]
    assert latest[0].reason == (
        "producer_task_revision_changed"
        if storage == "retained"
        else "artifact_preview_stale_source"
    )
    assert latest[0].historical_preview_verified is (storage == "retained")
    history = {
        item.artifact_id: item for item in reads.list("task", ctx.task, versions="all").items
    }
    assert history[first["artifact_id"]].historical_preview_verified is True
    assert history[first["artifact_id"]].latest is False
    assert history[second["artifact_id"]].historical_preview_verified is (storage == "retained")


@pytest.mark.parametrize("content_change", ["missing", "tampered"])
def test_retained_historical_content_refuses_without_source_or_older_version_fallback(
    published_context, content_change
):
    ctx = published_context
    old_bytes = ctx.source.read_bytes()
    first = publish_image(ctx, "retained")
    Image.new("RGB", (4, 3), (90, 120, 10)).save(ctx.source)
    second = publish_image(ctx, "retained", idempotency_key="homepage-002")
    current = ctx.worker.snapshot().tasks[ctx.task]
    ctx.parent.complete_task(
        ctx.task, current["effective_revision"], summary="Two retained image versions recorded."
    )
    # The latest source is still a valid exact fallback candidate, but retained
    # metadata promises CAS verification rather than silently reopening source.
    assert (
        "sha256:" + hashlib.sha256(ctx.source.read_bytes()).hexdigest()
        == second["content_revision"]
    )
    store = OutputContentStore(ctx.worker)
    latest_object = store.root / second["content_revision"].removeprefix("sha256:")
    if content_change == "missing":
        latest_object.unlink()
    else:
        content = bytearray(latest_object.read_bytes())
        content[-1] ^= 1  # Same length: exercise digest verification, not just size.
        latest_object.write_bytes(content)
    reopened = CommonsManager(
        ctx.worker.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=ctx.worker.session_id,
    )
    reads = OutputReads(reopened)
    for kind, scope in (("task", ctx.task), ("agent", ctx.agent)):
        latest = reads.list(kind, scope).items
        assert len(latest) == 1 and latest[0].artifact_id == second["artifact_id"]
        assert latest[0].retained and latest[0].historical_preview_verified is False
        assert latest[0].reason == "artifact_preview_missing_source"
        history = {item.artifact_id: item for item in reads.list(kind, scope, versions="all").items}
        assert history[first["artifact_id"]].historical_preview_verified is True
        assert history[first["artifact_id"]].latest is False
        assert history[second["artifact_id"]].historical_preview_verified is False
    reader = ArtifactPreviewReader(reopened)
    assert reader.read(first["artifact_id"]).content == old_bytes
    with pytest.raises(ArtifactPreviewRefusal) as refusal:
        reader.read(second["artifact_id"])
    assert refusal.value.code == "artifact_preview_missing_source"
