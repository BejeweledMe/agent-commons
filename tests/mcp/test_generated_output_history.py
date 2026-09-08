from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_commons.services.outputs import OutputReads
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from tests.mcp import test_design_output_tools as generated
from tests.ui.conftest import PORT, authorized
from tests.ui.test_output_routes import _client


@pytest.fixture
def published_context(tmp_path, monkeypatch):
    return generated.published_context.__wrapped__(tmp_path, monkeypatch)


def finish(ctx, transition="complete_task"):
    receipt = generated.publish(ctx)
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
def test_completed_generated_image_remains_viewable_as_verified_history(
    published_context, transition
):
    ctx = published_context
    receipt = finish(ctx, transition)
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
        assert response.content == ctx.source.read_bytes()
        Image.new("RGB", (4, 3), (220, 20, 20)).save(ctx.source)
        refused = client.get(url, headers=authorized())
        assert refused.status_code == 409
    item = reads.list("task", ctx.task).items[0]
    assert not item.historical_preview_verified
    assert item.reason == "artifact_preview_stale_source"


def test_historical_summary_never_reads_bytes_and_revoked_image_is_not_viewable(published_context):
    ctx = published_context
    finish(ctx)
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
    assert item.state == "stale" and item.reason == "artifact_preview_missing_source"
    assert item.historical_preview_verified is False


def test_historical_preview_respects_current_classification_and_exact_producer(
    published_context, monkeypatch
):
    ctx = published_context
    receipt = finish(ctx)
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


def test_latest_never_falls_back_to_an_older_verified_historical_image(published_context):
    ctx = published_context
    first = generated.publish(ctx)
    original = ctx.source.read_bytes()
    Image.new("RGB", (4, 3), (90, 120, 10)).save(ctx.source)
    second = generated.publish(ctx, idempotency_key="homepage-002")
    current = ctx.worker.snapshot().tasks[ctx.task]
    ctx.parent.complete_task(
        ctx.task, current["effective_revision"], summary="Two image versions recorded."
    )
    ctx.source.write_bytes(original)
    reads = OutputReads(ctx.worker)
    latest = reads.list("task", ctx.task).items
    assert len(latest) == 1 and latest[0].artifact_id == second["artifact_id"]
    assert latest[0].reason == "artifact_preview_stale_source"
    assert latest[0].historical_preview_verified is False
    history = {
        item.artifact_id: item for item in reads.list("task", ctx.task, versions="all").items
    }
    assert history[first["artifact_id"]].historical_preview_verified is True
    assert history[first["artifact_id"]].latest is False
    assert history[second["artifact_id"]].historical_preview_verified is False
