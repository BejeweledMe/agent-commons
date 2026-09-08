from __future__ import annotations

import inspect
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
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
    assert len(history) == 2 and sum(row.state == "ready" for row in history) == 1
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
    assert detail.state == "stale" and detail.reason == "artifact_preview_missing_source"


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
