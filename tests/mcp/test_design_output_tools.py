from __future__ import annotations

import inspect
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from agent_commons.errors import IdempotencyConflictError, IntegrityError, LifecycleConflictError
from agent_commons.mcp.design_output_tools import register_design_output_tools
from agent_commons.services import CommonsManager
from agent_commons.services.outputs import OutputReads
from agent_commons.ui.output_routes import OutputResponseDTO


@pytest.fixture
def published_context(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=())
    parent = CommonsManager(repo, state_root=tmp_path / "state")
    session = parent.start_session(
        stable_instance_id="output-parent",
        principal="operator",
        client="codex",
        software="tests",
        role="operator",
    )
    parent.session_id = session["session_id"]
    agent = parent.create_agent(
        name="Designer", profile_id="codex-builder", rationale="Produce task images"
    )
    task = parent.create_task(
        title="Design page",
        description="Generate a screen",
        acceptance_criteria=("Preview screen",),
    )
    task = parent.start_task(task["entity_ref"]["id"], task["revision"])
    run = parent.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        on_behalf_of_agent_id=agent["entity_ref"]["id"],
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    session = parent.start_session(
        stable_instance_id="output-child",
        principal="worker",
        client="codex",
        software="tests",
        role="builder",
    )
    parent.start_delegation(
        run["entity_ref"]["id"], run["revision"], child_session_id=session["session_id"], attempt=1
    )
    worker = CommonsManager(
        repo, state_root=parent.paths.state_root, session_id=session["session_id"]
    )
    functions = {}

    def register(*args, **kwargs):
        def decorate(function):
            functions[function.__name__] = function
            return function

        return decorate

    def current():
        return dict(worker.snapshot().delegations[run["entity_ref"]["id"]])

    register_design_output_tools(register, worker, current, worker_binding=current())
    source = repo / "outputs/screen.png"
    source.parent.mkdir()
    Image.new("RGB", (4, 3), (11, 22, 33)).save(source)
    return SimpleNamespace(
        parent=parent,
        worker=worker,
        source=source,
        task=task["entity_ref"]["id"],
        agent=agent["entity_ref"]["id"],
        run=run["entity_ref"]["id"],
        tool=functions["commons_publish_design_image"],
        build_tool=functions["commons_publish_static_build"],
    )


def publish(ctx, **changes):
    return ctx.tool(
        **{
            "source_path": "outputs/screen.png",
            "title": "Homepage",
            "idempotency_key": "homepage-001",
            **changes,
        }
    )


def test_generated_image_appears_for_exact_task_and_agent_without_completing_task(
    published_context,
):
    ctx = published_context
    before = ctx.worker.snapshot().tasks[ctx.task].to_dict()
    receipt = publish(ctx)
    assert publish(ctx) == receipt
    snapshot = ctx.worker.snapshot()
    assert snapshot.tasks[ctx.task].to_dict() == before
    assert not snapshot.design_packages
    assert len(snapshot.artifacts) == 1
    assert set(inspect.signature(ctx.tool).parameters) == {
        "source_path",
        "title",
        "idempotency_key",
    }
    for kind, scope in (("task", ctx.task), ("agent", ctx.agent)):
        result = OutputReads(ctx.worker).list(kind, scope)
        assert len(result.items) == 1 and result.items[0].state == "ready"
        wire = OutputResponseDTO(result).to_wire()["items"][0]
        assert set(wire) == {
            "kind",
            "output_id",
            "series_id",
            "title",
            "artifact_id",
            "artifact_revision",
            "content_revision",
            "task_id",
            "task_revision",
            "producer_session_id",
            "producer_agent_id",
            "producer_delegation_id",
            "delegation_revision",
            "historical_preview_verified",
            "review_state",
            "result_review_state",
            "retained",
            "recorded_at",
            "media_type",
            "classification",
            "state",
            "reason",
            "latest",
            "version_count",
            "width",
            "height",
        }
        assert wire["kind"] == "artifact_image" and wire["width"] == 4 and wire["height"] == 3
        assert wire["producer_session_id"] == ctx.worker.session_id
        assert wire["producer_agent_id"] == ctx.agent and wire["producer_delegation_id"] == ctx.run
        assert "outputs/screen.png" not in json.dumps(wire)
    assert str(ctx.source) not in json.dumps(receipt)
    assert ctx.source.read_bytes() not in b"".join(
        p.read_bytes() for p in (ctx.worker.repo_root / ".agent-commons").rglob("*") if p.is_file()
    )


def test_retry_after_commit_uncertainty_and_changed_intent(published_context, monkeypatch):
    ctx = published_context
    original = ctx.worker.register_artifact

    def fail_after(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("Synthetic private diagnostic")

    monkeypatch.setattr(ctx.worker, "register_artifact", fail_after)
    with pytest.raises(LifecycleConflictError, match="Generated image publication is unavailable"):
        publish(ctx)
    monkeypatch.setattr(ctx.worker, "register_artifact", original)
    assert publish(ctx)["artifact_id"] in ctx.worker.snapshot().artifacts
    assert len(ctx.worker.snapshot().artifacts) == 1
    with pytest.raises(IdempotencyConflictError):
        publish(ctx, title="Changed")
    Image.new("RGB", (4, 3), (99, 88, 77)).save(ctx.source)
    with pytest.raises(IdempotencyConflictError):
        publish(ctx)
    newest = publish(ctx, idempotency_key="homepage-002")
    latest = OutputReads(ctx.worker).list("task", ctx.task).items
    assert len(latest) == 1 and latest[0].artifact_id == newest["artifact_id"]
    history = OutputReads(ctx.worker).list("task", ctx.task, versions="all").items
    assert len(history) == 2 and sum(row.state == "ready" for row in history) == 2
    assert all(row.version_count == 2 for row in history)


@pytest.mark.parametrize(
    "source",
    [
        "/outside.png",
        "../outside.png",
        ".env.png",
        ".agent-commons/private.png",
        "outputs/screen.gif",
        "outputs/link.png",
        "outputs/dir/screen.png",
        "outputs/fifo.png",
        "outputs/hard.png",
        "outputs/fake.png",
        "outputs/huge.png",
    ],
)
def test_invalid_sources_fail_before_any_artifact(published_context, tmp_path, source):
    ctx = published_context
    (ctx.source.parent / "link.png").symlink_to(ctx.source)
    (ctx.source.parent / "dir").symlink_to(ctx.source.parent, target_is_directory=True)
    os.mkfifo(ctx.source.parent / "fifo.png")
    os.link(ctx.source, ctx.source.parent / "hard.png")
    (ctx.source.parent / "fake.png").write_bytes(b"not an image")
    (ctx.source.parent / "huge.png").write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(LifecycleConflictError):
        publish(ctx, source_path=source)
    assert not ctx.worker.snapshot().artifacts


def test_summary_is_metadata_only_and_changed_source_is_truthful(published_context, monkeypatch):
    ctx = published_context
    publish(ctx)
    ctx.source.unlink()

    def no_reader(_):
        pytest.fail("summary opened image bytes")

    summary = OutputReads(ctx.worker, preview_reader_factory=no_reader).summary("task", ctx.task)
    assert summary.total == summary.unchecked == 1
    detail = OutputReads(ctx.worker).list("task", ctx.task).items[0]
    assert detail.state == "ready" and detail.retained
    digest = ctx.worker.get_artifact_bundle(detail.artifact_id)["manifest"]["revision"][7:]
    (ctx.worker.paths.state_root / "output-content" / digest).unlink()
    missing = OutputReads(ctx.worker).list("task", ctx.task).items[0]
    assert missing.state == "stale" and missing.reason == "artifact_preview_missing_source"


@pytest.mark.parametrize("change", ["task", "agent", "child", "ambiguous", "revision"])
def test_manifest_metadata_cannot_override_canonical_producer(
    published_context, monkeypatch, change
):
    ctx = published_context
    publish(ctx)
    snapshot = ctx.worker.snapshot()
    run = dict(snapshot.delegations[ctx.run])
    if change == "task":
        run["target_ref"] = {"kind": "task", "id": "task." + "0" * 26}
    elif change == "agent":
        run["agent_id"] = "agent." + "0" * 26
    elif change == "child":
        run["child_session_id"] = ctx.parent.session_id
    elif change == "revision":
        snapshot.entity_revision_actor_session_ids.pop(
            ("delegation", ctx.run, run["effective_revision"])
        )
    else:
        snapshot.delegations["delegation." + "0" * 26] = dict(run)
    snapshot.delegations[ctx.run] = run
    monkeypatch.setattr(ctx.worker, "snapshot", lambda: snapshot)
    assert OutputReads(ctx.worker).summary("task", ctx.task).total == 0
    assert OutputReads(ctx.worker).summary("agent", ctx.agent).total == 0


def test_other_task_and_project_get_no_output_and_task_change_is_stale(published_context, tmp_path):
    ctx = published_context
    publish(ctx)
    other = ctx.parent.create_task(
        title="Other", description="Separate task", acceptance_criteria=("Separate",)
    )
    assert OutputReads(ctx.worker).summary("task", other["entity_ref"]["id"]).total == 0
    task = ctx.parent.snapshot().tasks[ctx.task]
    ctx.parent.block_task(ctx.task, task["effective_revision"], reason="Wait")
    assert (
        OutputReads(ctx.worker).list("task", ctx.task).items[0].reason
        == "producer_task_revision_changed"
    )
    clone = tmp_path / "other-project"
    clone.mkdir()
    CommonsManager.initialize(clone, integrations=())
    manager = CommonsManager(clone, state_root=tmp_path / "other-state")
    assert not manager.snapshot().artifacts


@pytest.mark.parametrize("change", ["read_only", "purpose", "child", "target", "ended"])
def test_publication_cannot_escape_live_worker_binding(published_context, monkeypatch, change):
    ctx = published_context
    snapshot = ctx.worker.snapshot()
    run = dict(snapshot.delegations[ctx.run])
    if change == "read_only":
        ctx.worker.read_only = True
    elif change == "purpose":
        run["purpose"] = "independent_review"
    elif change == "child":
        run["child_session_id"] = ctx.parent.session_id
    elif change == "target":
        run["target_ref"] = {"kind": "task", "id": "task." + "0" * 26}
    else:
        run["state"] = "succeeded"
    snapshot.delegations[ctx.run] = run
    monkeypatch.setattr(ctx.worker, "snapshot", lambda: snapshot)
    with pytest.raises(LifecycleConflictError):
        publish(ctx)
    assert not ctx.parent.snapshot().artifacts


def test_corrupt_png_header_does_not_pass_full_decode(published_context):
    ctx = published_context
    ctx.source.write_bytes(ctx.source.read_bytes()[:24])
    with pytest.raises(LifecycleConflictError):
        publish(ctx)
    assert not ctx.parent.snapshot().artifacts


def test_source_swap_to_private_symlink_cannot_record_even_matching_bytes(
    published_context, monkeypatch
):
    from agent_commons.services.artifact_content import ArtifactPreviewReader

    ctx = published_context
    hidden = ctx.worker.repo_root / ".private/screen.png"
    hidden.parent.mkdir()
    hidden.write_bytes(ctx.source.read_bytes())
    original = ArtifactPreviewReader.inspect_generated_source

    def swap(reader, source_path):
        result = original(reader, source_path)
        ctx.source.parent.rename(ctx.worker.repo_root / "original-outputs")
        ctx.source.parent.symlink_to(hidden.parent, target_is_directory=True)
        return result

    monkeypatch.setattr(ArtifactPreviewReader, "inspect_generated_source", swap)
    with pytest.raises(LifecycleConflictError):
        publish(ctx)
    assert not ctx.worker.snapshot().artifacts


def test_supported_worker_mcp_server_exposes_publisher(published_context):
    import subprocess

    from agent_commons.mcp.server import build_server
    from tests.mcp.test_worker_scope import FakeRuntime, FakeServer

    ctx = published_context
    subprocess.run(
        ["git", "init", "--quiet", str(ctx.worker.repo_root)], check=True, capture_output=True
    )
    server = build_server(
        ctx.worker.repo_root,
        manager=ctx.worker,
        runtime=FakeRuntime(),
        delegation_id=ctx.run,
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    tool = server.tools["commons_publish_design_image"]
    result = tool(source_path="outputs/screen.png", title="Homepage", idempotency_key="mounted-001")
    assert result["producer_agent_id"] == ctx.agent
    assert OutputReads(ctx.worker).summary("agent", ctx.agent).total == 1


def test_versions_survive_source_overwrite_and_manager_restart(published_context):
    from agent_commons.services.artifact_content import ArtifactPreviewReader

    ctx = published_context
    first_bytes = ctx.source.read_bytes()
    first = publish(ctx)
    Image.new("RGB", (5, 6), (70, 80, 90)).save(ctx.source)
    second_bytes = ctx.source.read_bytes()
    second = publish(ctx, idempotency_key="homepage-002")
    ctx.source.unlink()
    restarted = CommonsManager(ctx.worker.repo_root, state_root=ctx.worker.paths.state_root)
    reader = ArtifactPreviewReader(restarted)
    assert reader.read(first["artifact_id"]).content == first_bytes
    assert reader.read(second["artifact_id"]).content == second_bytes
    rows = OutputReads(restarted).list("agent", ctx.agent, versions="all").items
    assert len(rows) == 2 and all(item.retained and item.state == "ready" for item in rows)
    assert len({item.content_revision for item in rows}) == 2
    assert restarted.doctor()["ok"]


def test_static_build_download_is_exact_durable_attachment_and_never_html(published_context):
    import io
    import zipfile

    from tests.ui.test_output_routes import _client

    ctx = published_context
    dist = ctx.worker.repo_root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<script src="app.js"></script>')
    (dist / "app.js").write_text('document.body.textContent = "v1";')
    first = ctx.build_tool("dist", "Frontend", "frontend-001")
    assert ctx.build_tool("dist", "Frontend", "frontend-001") == first
    (dist / "app.js").write_text('document.body.textContent = "v2";')
    second = ctx.build_tool("dist", "Frontend", "frontend-002")
    (dist / "index.html").unlink()
    (dist / "app.js").unlink()
    restarted = CommonsManager(ctx.worker.repo_root, state_root=ctx.worker.paths.state_root)
    reads = OutputReads(restarted)
    for output, text in ((first, b'"v1"'), (second, b'"v2"')):
        content = reads.read_build(
            "task", ctx.task, output["artifact_id"], output["artifact_revision"]
        )
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert text in archive.read("app.js")
            manifest = json.loads(archive.read("commons-build-manifest.json"))
            assert manifest["schema"] == "agent_commons.static-build.v1"
            assert len(manifest["files"]) == 2
    client = _client(lambda: restarted)
    url = f"/api/outputs/builds/{first['artifact_id']}/{first['artifact_revision']}"
    query = {"scope_kind": "task", "scope_id": ctx.task}
    assert client.get(url, params=query).status_code == 401
    response = client.get(url, params=query, headers={"X-Session": "allowed"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.get(
            url, params={**query, "scope_id": ctx.agent}, headers={"X-Session": "allowed"}
        ).status_code
        == 400
    )
    assert (
        client.get(
            url.replace(first["artifact_revision"], second["artifact_revision"]),
            params=query,
            headers={"X-Session": "allowed"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "unsafe",
    ["symlink", "ancestor", "hardlink", "hidden", "special", "oversize", "directory-bounds"],
)
def test_static_build_refuses_unsafe_content_before_publication(published_context, unsafe):
    ctx = published_context
    dist = ctx.worker.repo_root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("safe")
    if unsafe == "symlink":
        (dist / "escape.js").symlink_to(ctx.source)
    elif unsafe == "ancestor":
        alias = ctx.worker.repo_root / "alias"
        alias.symlink_to(dist, target_is_directory=True)
        with pytest.raises(LifecycleConflictError):
            ctx.build_tool("alias", "Frontend", "frontend-001")
        assert not ctx.worker.snapshot().artifacts
        return
    elif unsafe == "hardlink":
        os.link(dist / "index.html", dist / "copy.html")
    elif unsafe == "hidden":
        (dist / ".env").write_text("synthetic")
    elif unsafe == "special":
        os.mkfifo(dist / "pipe.js")
    elif unsafe == "oversize":
        (dist / "big.js").write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    else:
        for number in range(130):
            (dist / f"dir-{number}").mkdir()
    with pytest.raises(LifecycleConflictError):
        ctx.build_tool("dist", "Frontend", "frontend-001")
    assert not ctx.worker.snapshot().artifacts


def test_partial_store_failure_and_quota_do_not_claim_durable_publication(
    published_context, monkeypatch
):
    import agent_commons.services.output_content as storage

    ctx = published_context
    original = storage.os.link

    def interrupt(*args, **kwargs):
        raise OSError("synthetic interruption")

    monkeypatch.setattr(storage.os, "link", interrupt)
    with pytest.raises(LifecycleConflictError):
        publish(ctx)
    assert not ctx.worker.snapshot().artifacts
    assert [p.name for p in (ctx.worker.paths.state_root / "output-content").iterdir()] == [
        "store.lock"
    ]
    monkeypatch.setattr(storage.os, "link", original)
    first = publish(ctx)
    monkeypatch.setattr(storage, "MAX_STORE_OBJECTS", 1)
    Image.new("RGB", (4, 3), (99, 88, 77)).save(ctx.source)
    with pytest.raises(LifecycleConflictError):
        publish(ctx, idempotency_key="homepage-002")
    assert len(ctx.worker.snapshot().artifacts) == 1
    assert (
        OutputReads(ctx.worker).list("task", ctx.task).items[0].artifact_id == first["artifact_id"]
    )


def test_retained_store_symlink_is_refused_without_source_fallback(published_context, tmp_path):
    ctx = published_context
    receipt = publish(ctx)
    retained = ctx.worker.paths.state_root / "output-content" / receipt["content_revision"][7:]
    external = tmp_path / "external"
    external.write_bytes(ctx.source.read_bytes())
    retained.unlink()
    retained.symlink_to(external)
    row = OutputReads(ctx.worker).list("task", ctx.task).items[0]
    assert row.state == "stale" and row.reason == "artifact_preview_missing_source"
    with pytest.raises(LifecycleConflictError):
        publish(ctx)


def test_exact_result_review_does_not_transfer_to_new_output(published_context):
    ctx = published_context
    first = publish(ctx)
    review = ctx.parent.request_review(
        target_ref={"kind": "artifact", "id": first["artifact_id"]},
        target_revision=first["artifact_revision"],
        criteria=("Check exact pixels",),
    )
    with pytest.raises(LifecycleConflictError):
        ctx.worker.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=first["artifact_revision"],
            verdict="approved",
            summary="Self check",
        )
    session = ctx.parent.start_session(
        stable_instance_id="output-reviewer",
        principal="independent-reviewer",
        client="claude",
        software="tests",
        role="reviewer",
    )
    reviewer = CommonsManager(
        ctx.worker.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=session["session_id"],
    )
    reviewer.complete_review(
        review["entity_ref"]["id"],
        review["revision"],
        target_revision=first["artifact_revision"],
        verdict="approved",
        summary="Exact pixels checked",
    )
    Image.new("RGB", (4, 3), (99, 88, 77)).save(ctx.source)
    second = publish(ctx, idempotency_key="homepage-002")
    rows = {
        item.artifact_id: item
        for item in OutputReads(ctx.worker).list("task", ctx.task, versions="all").items
    }
    assert rows[first["artifact_id"]].result_review_state == "approved"
    assert rows[second["artifact_id"]].result_review_state is None
    assert all(item.review_state is None for item in rows.values())


def _output_reviewer(ctx, output):
    import subprocess

    subprocess.run(
        ["/usr/bin/git", "init", "-q", str(ctx.worker.repo_root)], check=True, capture_output=True
    )
    from agent_commons.mcp.server import build_server
    from tests.mcp.test_worker_scope import FakeServer

    review = ctx.parent.request_review(
        target_ref={"kind": "artifact", "id": output["artifact_id"]},
        target_revision=output["artifact_revision"],
        criteria=("Inspect exact retained content",),
    )
    delegation = ctx.parent.create_delegation(
        target_ref=review["entity_ref"],
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    session = ctx.parent.start_session(
        stable_instance_id="scoped-output-reviewer",
        principal="reviewer-independent",
        client="claude",
        software="tests",
        role="reviewer",
    )
    ctx.parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=session["session_id"],
        attempt=1,
    )
    reviewer = CommonsManager(
        ctx.worker.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=session["session_id"],
    )
    server = build_server(
        reviewer.repo_root,
        manager=reviewer,
        delegation_id=delegation["entity_ref"]["id"],
        server_factory=FakeServer,
    )
    return reviewer, server, review


@pytest.mark.parametrize("build", [False, True])
def test_exact_output_review_reads_real_content_before_terminal_finalization(
    published_context, build
):
    import base64

    ctx = published_context
    pixels_before = ctx.source.read_bytes()
    if build:
        dist = ctx.worker.repo_root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text('<script src="app.js"></script>')
        (dist / "app.js").write_text('document.body.textContent="demo"')
        output = ctx.build_tool("dist", "Frontend", "frontend-001")
        (dist / "index.html").unlink()
        (dist / "app.js").unlink()
    else:
        output = publish(ctx)
        ctx.source.unlink()
    reviewer, server, review = _output_reviewer(ctx, output)
    with pytest.raises(LifecycleConflictError, match="evidence is incomplete"):
        server.tools["commons_finalize_review"]("approved", "Inspected result")
    if build:
        manifest = server.tools["commons_read_build_file"](output["artifact_id"])
        assert manifest["content_verified"] and not manifest["review_content_complete"]
        with pytest.raises(LifecycleConflictError, match="evidence is incomplete"):
            server.tools["commons_finalize_review"]("approved", "Manifest alone")
        first = server.tools["commons_read_build_file"](output["artifact_id"], "index.html")
        assert not first["review_content_complete"]
        skipped = server.tools["commons_read_build_file"](output["artifact_id"], "app.js", offset=4)
        assert not skipped["review_content_complete"]
        last = server.tools["commons_read_build_file"](output["artifact_id"], "app.js", limit=4)
        assert last["review_content_complete"]
        assert "document" in skipped["content"] or last["content"] == "docu"
    else:
        pixels = server.tools["commons_read_output_image"](output["artifact_id"])
        assert base64.b64decode(pixels.content[0].data) == pixels_before
        with pytest.raises(LifecycleConflictError):
            server.tools["commons_read_output_image"]("artifact.00000000000000000000000000")
    completed = server.tools["commons_finalize_review"](
        "approved", "Exact retained content inspected; execution not tested"
    )
    assert completed["delegation_outcome"]["event_type"] == "delegation.succeeded"
    row = OutputReads(reviewer).list("task", ctx.task).items[0]
    assert row.result_review_state == "approved" and row.review_state is None
    assert (
        reviewer.snapshot().reviews[review["entity_ref"]["id"]]["target_revision"]
        == output["artifact_revision"]
    )


def test_orphan_delegated_output_verdict_has_no_approval_and_changed_binding_refuses_read(
    published_context,
):
    ctx = published_context
    output = publish(ctx)
    reviewer, server, review = _output_reviewer(ctx, output)
    server.tools["commons_read_output_image"](output["artifact_id"])
    reviewer.complete_review(
        review["entity_ref"]["id"],
        review["revision"],
        target_revision=output["artifact_revision"],
        verdict="approved",
        summary="Before terminal outcome",
    )
    assert OutputReads(ctx.parent).list("task", ctx.task).items[0].result_review_state is None
    ctx.worker.revise_artifact(
        output["artifact_id"],
        output["artifact_revision"],
        "outputs/screen.png",
        media_type="image/png",
    )
    with pytest.raises(LifecycleConflictError, match="binding has changed"):
        server.tools["commons_read_output_image"](output["artifact_id"])


def test_same_content_retention_serializes_threads_and_subprocesses(published_context):
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    from agent_commons.services.output_content import OutputContentStore

    ctx = published_context
    content = b"synthetic retained bytes"
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(lambda _: OutputContentStore(ctx.worker).put(content), range(8)))
    assert all(value == values[0] for value in values)
    script = (
        "from pathlib import Path; from types import SimpleNamespace; "
        "from agent_commons.services.output_content import OutputContentStore; import sys; "
        "OutputContentStore(SimpleNamespace(paths=SimpleNamespace(state_root=Path(sys.argv[1]))))"
        ".put(b'synthetic retained bytes')"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(ctx.worker.paths.state_root)],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    for child in children:
        _, diagnostic = child.communicate(timeout=30)
        assert child.returncode == 0, diagnostic
    names = [
        entry.name
        for entry in (ctx.worker.paths.state_root / "output-content").iterdir()
        if entry.name != "store.lock"
    ]
    assert len(names) == 1
    assert OutputContentStore(ctx.worker).read(values[0]["revision"], len(content)) == content


def test_identical_retry_recovers_kill_after_link_without_overwriting_content(published_context):
    import hashlib

    from agent_commons.services.output_content import OutputContentStore

    ctx = published_context
    content = b"synthetic interrupted retained result"
    digest = hashlib.sha256(content).hexdigest()
    store = OutputContentStore(ctx.worker)
    store.root.mkdir()
    partial = store.root / ("partial-" + digest + "-" + "a" * 32)
    partial.write_bytes(content)
    os.link(partial, store.root / digest)
    assert (store.root / digest).stat().st_nlink == 2
    retained = store.put(content)
    assert not partial.exists()
    assert store.read(retained["revision"], len(content)) == content
    assert (store.root / digest).stat().st_nlink == 1


def test_static_capture_refuses_mixed_versions_before_retention(published_context, monkeypatch):
    from agent_commons.services.artifact_content import ArtifactPreviewReader

    ctx = published_context
    dist = ctx.worker.repo_root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<script src="app.js"></script>')
    (dist / "app.js").write_text("old")
    original = ArtifactPreviewReader._read_verified_source
    changed = False

    def swap(reader, relative, *args, **kwargs):
        nonlocal changed
        content = original(reader, relative, *args, **kwargs)
        if relative.name == "index.html" and not changed:
            changed = True
            (dist / "app.js").write_text("new")
        return content

    monkeypatch.setattr(ArtifactPreviewReader, "_read_verified_source", swap)
    with pytest.raises(LifecycleConflictError):
        ctx.build_tool("dist", "Frontend", "frontend-001")
    assert not ctx.worker.snapshot().artifacts
    assert not (ctx.worker.paths.state_root / "output-content").exists()


def _register_legacy_image(ctx, *, metadata_changes=None, author=None):
    import hashlib

    from agent_commons.services.generated_outputs import generated_series

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
    metadata.update(metadata_changes or {})
    key = "generated-image-" + hashlib.sha256(f"{ctx.run}:homepage-001".encode()).hexdigest()
    return (author or ctx.worker).register_artifact(
        "outputs/screen.png",
        media_type="image/png",
        classification="internal",
        metadata=metadata,
        idempotency_key=key,
    )


def test_legacy_identical_retry_preserves_metadata_history_without_copying(published_context):
    ctx = published_context
    original = _register_legacy_image(ctx)
    bundle = ctx.worker.get_artifact_bundle(original["entity_ref"]["id"])
    retry = publish(ctx)
    assert retry["artifact_id"] == original["entity_ref"]["id"]
    assert retry["artifact_revision"] == original["revision"]
    assert retry["content_copied"] is False
    assert ctx.worker.get_artifact_bundle(retry["artifact_id"]) == bundle
    assert not (ctx.worker.paths.state_root / "output-content").exists()
    # A lost-response retry after constructing a new tool/server manager stays identical.
    functions = {}

    def register(*args, **kwargs):
        def decorate(function):
            functions[function.__name__] = function
            return function

        return decorate

    resumed = CommonsManager(
        ctx.worker.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=ctx.worker.session_id,
    )

    def current():
        return dict(resumed.snapshot().delegations[ctx.run])

    register_design_output_tools(register, resumed, current, worker_binding=current())
    assert (
        functions["commons_publish_design_image"]("outputs/screen.png", "Homepage", "homepage-001")
        == retry
    )
    retained = publish(ctx, idempotency_key="homepage-retained-002")
    assert retained["artifact_id"] != retry["artifact_id"] and retained["content_copied"]
    assert ctx.worker.get_artifact_bundle(retry["artifact_id"]) == bundle


@pytest.mark.parametrize("change", ["bytes", "title", "source", "producer", "extra", "binding"])
def test_legacy_retry_still_refuses_changed_intent_or_malformed_provenance(
    published_context, change
):
    ctx = published_context
    changes = None
    if change == "producer":
        snapshot = ctx.parent.snapshot()
        other = ctx.parent.create_delegation(
            target_ref={"kind": "task", "id": ctx.task},
            target_revision=snapshot.entity_revision("task", ctx.task),
            target_profile="codex-builder",
            purpose="implementation",
            on_behalf_of_agent_id=ctx.agent,
            limits={
                "max_depth": 0,
                "wall_time_seconds": 300,
                "max_attempts": 1,
                "max_concurrency": 1,
                "budget": {"unit": "provider_units", "limit": 1},
            },
        )
        changes = {
            "output_delegation": {"id": other["entity_ref"]["id"], "revision": other["revision"]}
        }
    elif change == "extra":
        changes = {"unexpected": "value"}
    elif change == "binding":
        changes = {"output_task": {"id": ctx.task, "revision": "evt.invalid"}}
    _register_legacy_image(ctx, metadata_changes=changes)
    args = {}
    if change == "bytes":
        Image.new("RGB", (4, 3), (99, 22, 33)).save(ctx.source)
    elif change == "title":
        args["title"] = "Changed"
    elif change == "source":
        other = ctx.source.with_name("other.png")
        other.write_bytes(ctx.source.read_bytes())
        args["source_path"] = "outputs/other.png"
    with pytest.raises(IdempotencyConflictError):
        publish(ctx, **args)
    assert not (ctx.worker.paths.state_root / "output-content").exists()


def test_build_unicode_chunks_reuse_checked_text_but_still_verify_bytes(
    published_context, monkeypatch
):
    from agent_commons.mcp.scoped_repo import ScopedRepoReader
    from agent_commons.services.output_content import OutputContentStore

    ctx = published_context
    dist = ctx.worker.repo_root / "dist"
    dist.mkdir()
    expected = "a" * 65535 + "😀Жe\u0301終" + "b" * 10
    (dist / "index.html").write_text(expected)
    output = ctx.build_tool("dist", "Unicode", "unicode-cache-001")
    reviewer, server, _ = _output_reviewer(ctx, output)
    calls = []
    original = ScopedRepoReader._review_content

    def inspect(reader, text, **kwargs):
        if kwargs.get("context") == "scoped static build content":
            calls.append(text)
        return original(reader, text, **kwargs)

    monkeypatch.setattr(ScopedRepoReader, "_review_content", inspect)
    read = server.tools["commons_read_build_file"]
    tail = read(output["artifact_id"], "index.html", offset=65536)
    assert not tail["review_content_complete"]
    with pytest.raises(LifecycleConflictError, match="evidence is incomplete"):
        server.tools["commons_finalize_review"]("approved", "Tail only")
    first = read(output["artifact_id"], "index.html", limit=65536)
    assert first["content"] + tail["content"] == expected
    assert first["review_content_complete"] and len(calls) == 1
    store = OutputContentStore(reviewer)
    retained = store.root / output["content_revision"].removeprefix("sha256:")
    retained.write_bytes(b"tampered")
    with pytest.raises(IntegrityError, match="Retained content is unavailable"):
        read(output["artifact_id"], "index.html")
    assert len(calls) == 1


def test_build_scan_redacts_cross_chunk_and_cross_line_secret_before_caching(published_context):
    ctx = published_context
    dist = ctx.worker.repo_root / "dist"
    dist.mkdir()
    raw = "a " * 32765 + '\npassword\n =\n "synthetic-credential"\n' + "tail " * 50
    (dist / "index.html").write_text(raw)
    output = ctx.build_tool("dist", "Redaction", "cross-chunk-001")
    _, server, _ = _output_reviewer(ctx, output)
    read = server.tools["commons_read_build_file"]
    first = read(output["artifact_id"], "index.html", limit=65536)
    assert first["next_offset"] == 65536 and first["redactions"]
    tail = read(output["artifact_id"], "index.html", offset=first["next_offset"])
    sanitized = first["content"] + tail["content"]
    assert "synthetic-credential" not in sanitized and "password" not in sanitized
    assert sanitized.count("[agent-commons redacted source line]") == 3
    assert tail["review_content_complete"]
    assert (
        server.tools["commons_finalize_review"](
            "approved", "Inspected policy-redacted text; hidden content and execution not reviewed"
        )["delegation_outcome"]["event_type"]
        == "delegation.succeeded"
    )
