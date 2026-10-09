from __future__ import annotations

import concurrent.futures
import inspect
import subprocess

import pytest

from agent_commons.errors import (
    IdempotencyConflictError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.mcp.server import (
    IMPLEMENTATION_WORKER_TOOL_NAMES,
    INDEPENDENT_REVIEW_WORKER_TOOL_NAMES,
    VERIFICATION_WORKER_TOOL_NAMES,
    build_server,
)
from agent_commons.mcp.text_result_tools import register_text_result_tools
from agent_commons.runtime.model import BuiltinProfileId, _worker_tools
from agent_commons.services import CommonsManager
from agent_commons.services.output_content import OutputContentStore
from agent_commons.services.outputs import OutputReadRefusal, OutputReads
from agent_commons.services.result_details import task_result_details
from agent_commons.services.text_results import WorkerTextResults
from agent_commons.ui.output_routes import OutputResponseDTO
from tests.mcp import test_design_output_tools as generated
from tests.mcp.test_server import FakeServer
from tests.mcp.test_worker_scope import FakeRuntime, _worker_server, _workspace


@pytest.fixture
def context(tmp_path, monkeypatch):
    ctx = generated.published_context.__wrapped__(tmp_path, monkeypatch)
    ctx.binding = dict(ctx.worker.snapshot().delegations[ctx.run])
    ctx.revision = ctx.binding["target_revision"]
    ctx.service = WorkerTextResults(ctx.worker, ctx.binding)
    return ctx


def publish(ctx, **changes):
    return ctx.service.publish(
        **{
            "title": "Implementation report",
            "summary": "Fixed the request handling.",
            "checks": ["Focused tests passed", "Manual inspection completed"],
            "content": "Report\nThe request handler now preserves its operation identity.\n",
            "expected_task_revision": ctx.revision,
            "idempotency_key": "result-report-v1",
            **changes,
        }
    )


def complete(ctx, published, **changes):
    return ctx.service.transition(
        "complete",
        **{
            "expected_task_revision": ctx.revision,
            "summary": "Implementation finished.",
            "artifact_ids": [published["artifact_id"]],
            "idempotency_key": "result-complete-v1",
            **changes,
        },
    )


def submit(ctx, published, completed, **changes):
    return ctx.service.transition(
        "submit",
        **{
            "expected_task_revision": completed["revision"],
            "summary": "Ready for independent review.",
            "artifact_ids": [published["artifact_id"]],
            "idempotency_key": "result-submit-v1",
            **changes,
        },
    )


def test_publish_retained_text_bound_to_worker_task_and_agent(context):
    ctx = context
    before = ctx.worker.snapshot().tasks[ctx.task].to_dict()
    result = publish(ctx)
    assert publish(ctx) == result
    assert ctx.worker.snapshot().tasks[ctx.task].to_dict() == before
    assert result["content_copied"] is True
    for kind, scope in (("task", ctx.task), ("agent", ctx.agent)):
        wire = OutputResponseDTO(OutputReads(ctx.worker).list(kind, scope)).to_wire()["items"][0]
        assert wire["kind"] == "text_result" and wire["state"] == "ready"
        assert wire["summary"] == "Fixed the request handling."
        assert len(wire["checks"]) == 2
        text = OutputReads(ctx.worker).read_text(
            kind, scope, result["artifact_id"], result["artifact_revision"]
        )
        assert text["content"].startswith("Report\n")
    manifest = ctx.worker.get_artifact_bundle(result["artifact_id"])["manifest"]
    assert not (ctx.worker.paths.repo_root / manifest["source"]["path"]).exists()
    restarted = CommonsManager(
        ctx.worker.paths.repo_root,
        state_root=ctx.worker.paths.state_root,
        session_id=ctx.worker.session_id,
    )
    assert (
        OutputReads(restarted).read_text(
            "task", ctx.task, result["artifact_id"], result["artifact_revision"]
        )["content"]
        == text["content"]
    )


def test_explicit_complete_submit_finalize_preserve_independent_acceptance(context):
    ctx = context
    result = publish(ctx)
    completed = complete(ctx, result)
    assert ctx.worker.snapshot().tasks[ctx.task]["state"] == "completed"
    assert not ctx.worker.snapshot().reviews
    assert complete(ctx, result)["revision"] == completed["revision"]
    submitted = submit(ctx, result, completed)
    assert submit(ctx, result, completed)["revision"] == submitted["revision"]
    assert ctx.worker.snapshot().tasks[ctx.task]["state"] == "review"
    assert ctx.worker.snapshot().delegations[ctx.run]["state"] == "active"
    args = {
        "expected_task_revision": submitted["revision"],
        "summary": "Report submitted for review.",
        "idempotency_key": "result-finalize-v1",
    }
    final = ctx.service.finalize(**args)
    assert ctx.service.finalize(**args)["revision"] == final["revision"]
    assert ctx.worker.snapshot().delegations[ctx.run]["state"] == "succeeded"
    assert ctx.worker.snapshot().tasks[ctx.task]["state"] == "review"
    assert not ctx.worker.snapshot().reviews
    with pytest.raises(LifecycleConflictError):
        ctx.parent.accept_task(ctx.task, submitted["revision"], summary="Cannot self-approve")


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", "x" * 65537),
        ("summary", "x" * 4097),
        ("title", "line\nbreak"),
        ("checks", ["check"] * 33),
        ("content", "null\x00byte"),
    ],
)
def test_publication_bounds_fail_before_storage(context, field, value):
    with pytest.raises(ValidationError):
        publish(context, **{field: value})
    assert not context.worker.snapshot().artifacts
    assert not OutputContentStore(context.worker).root.exists()


def test_secret_is_never_retained(context):
    with pytest.raises(SecurityPolicyError):
        publish(
            context,
            content=(
                "-----BEGIN OPENSSH PRIVATE KEY-----\n"
                "SYNTHETICKEY\n-----END OPENSSH PRIVATE KEY-----"
            ),
        )
    assert not OutputContentStore(context.worker).root.exists()
    assert not context.worker.snapshot().artifacts


def test_retries_preserve_original_revision_and_reject_changed_input(context):
    ctx = context
    result = publish(ctx)
    completed = complete(ctx, result)
    assert publish(ctx) == result
    with pytest.raises(IdempotencyConflictError):
        publish(ctx, title="Changed report")
    with pytest.raises(IdempotencyConflictError):
        publish(ctx, expected_task_revision=completed["revision"])
    with pytest.raises(IdempotencyConflictError):
        complete(ctx, result, summary="Changed completion")
    with pytest.raises(LifecycleConflictError):
        publish(ctx, idempotency_key="another-report-v2")


@pytest.mark.parametrize(
    "phase", ["artifact.registered", "task.completed", "task.submitted", "delegation.succeeded"]
)
def test_lost_response_retry_after_canonical_write_and_task_revision_change(
    context, monkeypatch, phase
):
    ctx = context
    result = None if phase == "artifact.registered" else publish(ctx)
    completed = (
        complete(ctx, result) if phase in {"task.submitted", "delegation.succeeded"} else None
    )
    submitted = submit(ctx, result, completed) if phase == "delegation.succeeded" else None
    original = ctx.worker.record_event
    crashed = False

    def crash(event_type, *args, **kwargs):
        nonlocal crashed
        receipt = original(event_type, *args, **kwargs)
        if event_type == phase and not crashed:
            crashed = True
            raise OSError("synthetic response interruption")
        return receipt

    monkeypatch.setattr(ctx.worker, "record_event", crash)
    call = {
        "artifact.registered": lambda: publish(ctx),
        "task.completed": lambda: complete(ctx, result),
        "task.submitted": lambda: submit(ctx, result, completed),
        "delegation.succeeded": lambda: ctx.service.finalize(
            expected_task_revision=submitted["revision"],
            summary="Ready",
            idempotency_key="finalize-crash-v1",
        ),
    }[phase]
    with pytest.raises(OSError):
        call()
    if phase == "artifact.registered":
        result = publish(ctx)
        complete(ctx, result)
    elif phase == "task.completed":
        current = ctx.worker.snapshot().tasks[ctx.task]
        ctx.parent.submit_task(
            ctx.task, current["revision"], summary="Operator submits", artifact_refs=None
        )
    elif phase == "task.submitted":
        current = ctx.worker.snapshot().tasks[ctx.task]
        ctx.parent.revise_task(ctx.task, current["revision"], changes={"title": "Refined task"})
    else:
        current = ctx.worker.snapshot().tasks[ctx.task]
        ctx.parent.revise_task(
            ctx.task, current["revision"], changes={"title": "Refined after success"}
        )
    count = len(list(ctx.worker.events.iter_events()))
    recovered = call()
    assert recovered.get("artifact_id") or recovered["revision"]
    assert len(list(ctx.worker.events.iter_events())) == count


def test_scope_rejects_wrong_child_purpose_task_and_unowned_evidence(context):
    ctx = context
    result = publish(ctx)
    wrong = WorkerTextResults(ctx.parent, ctx.binding)
    with pytest.raises(LifecycleConflictError):
        wrong.publish(
            title="Report",
            summary="Summary",
            checks=[],
            content="Text",
            expected_task_revision=ctx.revision,
            idempotency_key="wrong-parent-v1",
        )
    for field, value in (
        ("purpose", "verification"),
        ("target_revision", result["artifact_revision"]),
        ("agent_id", "agent.00000000000000000000000000"),
    ):
        wrong = WorkerTextResults(ctx.worker, {**ctx.binding, field: value})
        with pytest.raises(LifecycleConflictError):
            wrong.transition(
                "complete",
                expected_task_revision=ctx.revision,
                summary="Done",
                artifact_ids=[result["artifact_id"]],
                idempotency_key="wrong-binding-v1",
            )
    ctx.source.with_suffix(".txt").write_text("Operator report")
    artifact = ctx.parent.register_artifact(
        ctx.source.with_suffix(".txt"), media_type="text/plain", classification="internal"
    )
    with pytest.raises(LifecycleConflictError):
        complete(ctx, result, artifact_ids=[artifact["entity_ref"]["id"]])
    assert ctx.worker.snapshot().tasks[ctx.task]["state"] == "active"


def test_concurrent_identical_publication_has_one_canonical_result(context):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(context), range(2)))
    assert results[0] == results[1]
    assert len(context.worker.snapshot().artifacts) == 1


def test_all_52_evidence_refs_and_full_metadata_are_available(context):
    ctx = context
    refs = []
    for index in range(52):
        path = ctx.worker.paths.repo_root / f"evidence-{index}.txt"
        path.write_text(f"Evidence {index}")
        artifact = ctx.parent.register_artifact(
            path,
            media_type="text/plain",
            classification="internal",
            metadata={"title": f"Check {index}"},
        )
        refs.append(artifact["entity_ref"])
    completed = ctx.parent.complete_task(
        ctx.task, ctx.revision, summary="Full completion summary. " * 80, artifact_refs=refs
    )
    first = task_result_details(ctx.parent, ctx.task, completed["revision"])
    second = task_result_details(
        ctx.parent, ctx.task, completed["revision"], offset=first["next_offset"]
    )
    assert first["total"] == 52 and first["next_offset"] == 32 and second["next_offset"] is None
    assert len(first["evidence"] + second["evidence"]) == 52
    assert [row["ref"] for row in first["evidence"] + second["evidence"]] == refs
    assert first["summary"] == "Full completion summary. " * 80
    assert first["evidence"][0]["title"] == "Check 0"
    ctx.parent.revise_task(ctx.task, completed["revision"], changes={"title": "Task changed"})
    with pytest.raises(OutputReadRefusal, match="changed"):
        task_result_details(ctx.parent, ctx.task, completed["revision"], offset=32)


def test_implementation_only_tools_in_mcp_and_provider_catalogs(context, tmp_path):
    names = {
        "commons_publish_text_result",
        "commons_complete_task_result",
        "commons_submit_task_result",
        "commons_finalize_task_result",
    }
    assert names <= IMPLEMENTATION_WORKER_TOOL_NAMES
    assert not names & VERIFICATION_WORKER_TOOL_NAMES
    assert not names & INDEPENDENT_REVIEW_WORKER_TOOL_NAMES
    for profile in (BuiltinProfileId.CODEX_BUILDER, BuiltinProfileId.CLAUDE_BUILDER):
        assert names <= {
            name.removeprefix("mcp__agent-commons__")
            for name in _worker_tools(profile, "implementation")
        }
    for purpose in ("verification", "independent_review"):
        assert not names & {
            name.removeprefix("mcp__agent-commons__")
            for name in _worker_tools(BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER, purpose)
        }
    ctx = context
    subprocess.run(["git", "init", "-q", str(ctx.worker.paths.repo_root)], check=True)
    server = build_server(
        ctx.worker.paths.repo_root,
        manager=ctx.worker,
        runtime=FakeRuntime(),
        delegation_id=ctx.run,
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    assert names <= set(server.tools)
    assert (
        "source_path"
        not in inspect.signature(server.tools["commons_publish_text_result"]).parameters
    )
    (tmp_path / "reviewer").mkdir()
    review_workspace = _workspace(tmp_path / "reviewer")
    assert not names & set(_worker_server(review_workspace).tools)


def test_tool_registration_and_direct_call_enforce_implementation(context):
    ctx = context
    registered = {}

    def register(*args, **kwargs):
        assert kwargs["worker_only"] is True
        assert kwargs["worker_purposes"] == ("implementation",)

        def decorate(fn):
            registered[fn.__name__] = fn
            return fn

        return decorate

    register_text_result_tools(
        register, ctx.worker, worker_binding={**ctx.binding, "purpose": "verification"}
    )
    with pytest.raises(LifecycleConflictError):
        registered["commons_publish_text_result"](
            "Report", "Summary", [], "Text", ctx.revision, "wrong-purpose-v1"
        )


def test_exact_retained_text_is_readable_by_independent_reviewer(context):
    ctx = context
    result = publish(ctx)
    reviewer, server, review = generated._output_reviewer(ctx, result)
    with pytest.raises(LifecycleConflictError):
        server.tools["commons_finalize_review"]("approved", "Unread report")
    read = server.tools["commons_read_artifact"](result["artifact_id"])
    assert read["content"].startswith("Report\n") and read["content_complete"] is True
    assert read["sha256"] == result["content_revision"][7:]
    server.tools["commons_finalize_review"]("approved", "Read exact retained report")
    assert reviewer.snapshot().reviews[review["entity_ref"]["id"]]["state"] == "approved"


def test_http_text_and_evidence_routes_require_auth_and_exact_scopes(context):
    from tests.ui.test_output_routes import _client

    ctx = context
    result = publish(ctx)
    completed = complete(ctx, result)
    client = _client(lambda: ctx.parent)
    route = f"/api/outputs/task-results/{ctx.task}"
    params = {"task_revision": completed["revision"]}
    assert client.get(route, params=params).status_code == 401
    response = client.get(route, params=params, headers={"X-Session": "allowed"})
    assert response.status_code == 200
    row = response.json()["evidence"][0]
    assert row["title"] == "Implementation report" and row["summary"]
    assert row["checks"] == ["Focused tests passed", "Manual inspection completed"]
    assert response.headers["Cache-Control"] == "no-store"
    content_route = f"/api/outputs/text/{result['artifact_id']}/{result['artifact_revision']}"
    assert client.get(content_route).status_code == 401
    content = client.get(
        content_route,
        params={"scope_kind": "task", "scope_id": ctx.task},
        headers={"X-Session": "allowed"},
    )
    assert content.status_code == 200 and content.json()["content"].startswith("Report\n")
    unrelated = ctx.parent.create_task(
        title="Other task", description="Unrelated", acceptance_criteria=("Separate",)
    )
    assert (
        client.get(
            content_route,
            params={"scope_kind": "task", "scope_id": unrelated["entity_ref"]["id"]},
            headers={"X-Session": "allowed"},
        ).status_code
        == 409
    )
    assert (
        client.get(
            route, params={**params, "limit": 65}, headers={"X-Session": "allowed"}
        ).status_code
        == 400
    )


def test_tool_finalization_retries_do_not_regrant_other_worker_actions(context):
    ctx = context
    subprocess.run(["git", "init", "-q", str(ctx.worker.paths.repo_root)], check=True)
    server = build_server(
        ctx.worker.paths.repo_root,
        manager=ctx.worker,
        runtime=FakeRuntime(),
        delegation_id=ctx.run,
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    result = publish(ctx)
    completed = complete(ctx, result)
    submitted = submit(ctx, result, completed)
    finish = server.tools["commons_finalize_task_result"]
    args = (submitted["revision"], "Submitted report", "tool-finalize-v1")
    receipt = finish(*args)
    assert finish(*args)["revision"] == receipt["revision"]
    with pytest.raises(IdempotencyConflictError):
        finish(submitted["revision"], "Changed terminal summary", "tool-finalize-v1")
    with pytest.raises(LifecycleConflictError):
        finish(submitted["revision"], "Submitted report", "tool-finalize-v2")
    with pytest.raises(LifecycleConflictError):
        server.tools["commons_publish_text_result"](
            "Report", "Summary", [], "Text", ctx.revision, "after-terminal-v1"
        )


def test_retained_content_failure_never_reads_synthetic_source(context):
    ctx = context
    result = publish(ctx)
    manifest = ctx.worker.get_artifact_bundle(result["artifact_id"])["manifest"]
    source = ctx.worker.paths.repo_root / manifest["source"]["path"]
    source.parent.mkdir(exist_ok=True)
    source.write_text("Should never be read")
    (OutputContentStore(ctx.worker).root / result["content_revision"][7:]).unlink()
    with pytest.raises(OutputReadRefusal):
        OutputReads(ctx.worker).read_text(
            "task", ctx.task, result["artifact_id"], result["artifact_revision"]
        )
    assert OutputReads(ctx.worker).list("task", ctx.task).items[0].state == "unavailable"


def test_cas_written_before_canonical_crash_is_reused(context, monkeypatch):
    ctx = context
    original = ctx.worker.record_event

    def interrupt(*args, **kwargs):
        raise OSError("synthetic pre-event interruption")

    monkeypatch.setattr(ctx.worker, "record_event", interrupt)
    with pytest.raises(OSError):
        publish(ctx)
    assert not ctx.worker.snapshot().artifacts
    before = {p.name for p in OutputContentStore(ctx.worker).root.iterdir()}
    monkeypatch.setattr(ctx.worker, "record_event", original)
    publish(ctx)
    assert {p.name for p in OutputContentStore(ctx.worker).root.iterdir()} == before


def test_text_read_rejects_changed_bundle_between_scope_and_content_checks(context, monkeypatch):
    ctx = context
    result = publish(ctx)
    second = publish(
        ctx, title="Second report", content="Different content", idempotency_key="second-report-v1"
    )
    replacement = ctx.worker.get_artifact_bundle(second["artifact_id"])
    monkeypatch.setattr(ctx.worker, "get_artifact_bundle", lambda _identifier: replacement)
    with pytest.raises(OutputReadRefusal):
        OutputReads(ctx.worker).read_text(
            "task", ctx.task, result["artifact_id"], result["artifact_revision"]
        )


def test_finalization_telemetry_remains_content_free(context):
    from agent_commons.runtime.tool_audit import TerminalToolAuditStore

    ctx = context
    subprocess.run(["git", "init", "-q", str(ctx.worker.paths.repo_root)], check=True)
    server = build_server(
        ctx.worker.paths.repo_root,
        manager=ctx.worker,
        runtime=FakeRuntime(),
        delegation_id=ctx.run,
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    result = publish(ctx)
    completed = complete(ctx, result)
    submitted = submit(ctx, result, completed)
    server.tools["commons_finalize_task_result"](
        submitted["revision"], "SYNTHETIC_REPORT_CONTENT", "finalize-audit-v1"
    )
    audit = TerminalToolAuditStore(ctx.worker.paths.state_root).get(ctx.run)
    assert audit.terminal_tool_calls == 1 and audit.terminal_tool_completions == 1
    assert audit.last_tool == "commons_finalize_task_result"
    assert "SYNTHETIC_REPORT_CONTENT" not in str(audit.as_dict())


def test_report_to_independent_review_and_owner_acceptance_over_real_stdio(context):
    """Exercise the installed MCP protocol and explicit owner CLI, with no provider."""
    import asyncio
    import sys
    from pathlib import Path

    import yaml
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    ctx = context
    repo = ctx.worker.paths.repo_root
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".agent-commons/events/\n.agent-commons/manifests/\n")
    environment = {"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}

    def parameters(session_id, delegation_id):
        return StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent_commons.mcp.server",
                "--repo",
                str(repo),
                "--state-root",
                str(ctx.worker.paths.state_root),
                "--session-id",
                session_id,
                "--delegation-id",
                delegation_id,
                "--git-executable",
                "/usr/bin/git",
            ],
            env=environment,
        )

    async def call(session, name, arguments):
        result = await session.call_tool(name, arguments)
        assert not result.isError, result
        assert result.structuredContent is not None
        return result.structuredContent

    async def implement():
        async with stdio_client(parameters(ctx.worker.session_id, ctx.run)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                report = await call(
                    session,
                    "commons_publish_text_result",
                    {
                        "title": "Transport report",
                        "summary": "Implemented the scoped change.",
                        "checks": ["Deterministic fixture passed"],
                        "content": "Exact retained report body.",
                        "expected_task_revision": ctx.revision,
                        "idempotency_key": "stdio-publish-v1",
                    },
                )
                completed = await call(
                    session,
                    "commons_complete_task_result",
                    {
                        "expected_task_revision": ctx.revision,
                        "summary": "Implementation complete",
                        "artifact_ids": [report["artifact_id"]],
                        "idempotency_key": "stdio-complete-v1",
                    },
                )
                submitted = await call(
                    session,
                    "commons_submit_task_result",
                    {
                        "expected_task_revision": completed["revision"],
                        "summary": "Submitted for independent review",
                        "artifact_ids": [report["artifact_id"]],
                        "idempotency_key": "stdio-submit-v1",
                    },
                )
                final_args = {
                    "expected_task_revision": submitted["revision"],
                    "summary": "Report submitted",
                    "idempotency_key": "stdio-finalize-v1",
                }
                finalized = await call(session, "commons_finalize_task_result", final_args)
                assert (await call(session, "commons_finalize_task_result", final_args))[
                    "revision"
                ] == finalized["revision"]
                return report, submitted

    report, submitted = asyncio.run(asyncio.wait_for(implement(), timeout=45))
    assert ctx.parent.snapshot().tasks[ctx.task]["state"] == "review"
    assert not ctx.parent.snapshot().reviews
    review = ctx.parent.request_review(
        target_ref={"kind": "task", "id": ctx.task},
        target_revision=submitted["revision"],
        criteria=("Read and judge the exact retained report",),
        independent=True,
    )
    delegation = ctx.parent.create_delegation(
        target_ref=review["entity_ref"],
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    reviewer = ctx.parent.start_session(
        stable_instance_id="stdio-independent-reviewer",
        principal="reviewer",
        client="claude",
        software="test-transport",
        role="independent-reviewer",
    )
    ctx.parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=reviewer["session_id"],
        attempt=1,
    )

    async def review_report():
        async with stdio_client(
            parameters(reviewer["session_id"], delegation["entity_ref"]["id"])
        ) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                denied = await session.call_tool(
                    "commons_finalize_review", {"verdict": "approved", "summary": "Unread"}
                )
                assert denied.isError
                body = await call(
                    session, "commons_read_artifact", {"artifact_id": report["artifact_id"]}
                )
                assert body["content"] == "Exact retained report body."
                await call(
                    session,
                    "commons_finalize_review",
                    {"verdict": "approved", "summary": "Exact report inspected."},
                )

    asyncio.run(asyncio.wait_for(review_report(), timeout=45))
    snapshot = ctx.parent.snapshot()
    assert snapshot.tasks[ctx.task]["state"] == "review"
    assert snapshot.reviews[review["entity_ref"]["id"]]["state"] == "approved"
    assert snapshot.delegations[delegation["entity_ref"]["id"]]["state"] == "succeeded"
    accepted = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_commons",
            "--repo",
            str(repo),
            "--state-root",
            str(ctx.worker.paths.state_root),
            "--session-id",
            ctx.parent.session_id,
            "task",
            "accept",
            ctx.task,
            submitted["revision"],
            "--summary",
            "Owner accepts independently reviewed result",
            "--idempotency-key",
            "stdio-owner-accept-v1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert yaml.safe_load(accepted.stdout)["event_type"] == "task.accepted"
    assert ctx.parent.snapshot().tasks[ctx.task]["state"] == "accepted"


def test_evidence_details_never_forward_untyped_title_metadata(context):
    ctx = context
    source = ctx.worker.paths.repo_root / "report.txt"
    source.write_text("Legacy evidence")
    artifact = ctx.parent.register_artifact(
        source,
        media_type="text/plain",
        classification="internal",
        metadata={
            "title": {"internal_note": "UNRELATED_METADATA"},
            "summary": "Human summary",
            "checks": ["Check passed"],
        },
    )
    completed = ctx.parent.complete_task(
        ctx.task, ctx.revision, summary="Done", artifact_refs=[artifact["entity_ref"]]
    )
    result = task_result_details(ctx.parent, ctx.task, completed["revision"])
    assert result["evidence"][0]["title"] == "report.txt"
    assert result["evidence"][0]["summary"] == "Human summary"
    assert result["evidence"][0]["checks"] == ["Check passed"]
    assert "UNRELATED_METADATA" not in str(result)


def test_fresh_stdio_reconnect_exposes_only_identical_finalizer_receipt(context):
    import asyncio
    import sys
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    ctx = context
    result = publish(ctx)
    completed = complete(ctx, result)
    submitted = submit(ctx, result, completed)
    args = {
        "expected_task_revision": submitted["revision"],
        "summary": "Original terminal summary",
        "idempotency_key": "fresh-process-finalize-v1",
    }
    receipt = ctx.service.finalize(**args)
    # A later task revision does not alter this completed run's retry receipt.
    ctx.parent.revise_task(
        ctx.task, submitted["revision"], changes={"title": "Later task revision"}
    )
    count = len(list(ctx.parent.events.iter_events()))

    async def reconnect():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent_commons.mcp.server",
                "--repo",
                str(ctx.worker.repo_root),
                "--state-root",
                str(ctx.worker.paths.state_root),
                "--session-id",
                ctx.worker.session_id,
                "--delegation-id",
                ctx.run,
                "--git-executable",
                "/usr/bin/git",
            ],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    "commons_finalize_task_result"
                }
                replay = await session.call_tool("commons_finalize_task_result", args)
                assert not replay.isError, replay
                assert replay.structuredContent["revision"] == receipt["revision"]
                for changes in (
                    {"summary": "Changed"},
                    {"idempotency_key": "fresh-process-finalize-v2"},
                    {"expected_task_revision": ctx.revision},
                ):
                    assert (
                        await session.call_tool("commons_finalize_task_result", {**args, **changes})
                    ).isError
                for forbidden in (
                    "commons_publish_text_result",
                    "commons_complete_task_result",
                    "commons_repo_read",
                    "commons_succeed_delegation",
                    "commons_list_tasks",
                ):
                    assert (await session.call_tool(forbidden, {})).isError

    asyncio.run(asyncio.wait_for(reconnect(), timeout=30))
    assert len(list(ctx.parent.events.iter_events())) == count


def test_terminal_reconnect_rejects_wrong_child_or_generic_success(context):
    from agent_commons.errors import ConfigurationError

    ctx = context
    run = ctx.worker.snapshot().delegations[ctx.run]
    ctx.worker.succeed_delegation(
        ctx.run,
        run["revision"],
        summary="Generic outcome",
        result_refs=[{"kind": "task", "id": ctx.task}],
    )
    for manager in (ctx.worker, ctx.parent):
        with pytest.raises(ConfigurationError, match="live canonical child"):
            build_server(
                manager.repo_root,
                manager=manager,
                delegation_id=ctx.run,
                binding_wait_seconds=0,
                server_factory=FakeServer,
            )


def test_task_results_preserve_full_description_and_acceptance_criteria(context):
    from tests.ui.test_output_routes import _client

    ctx = context
    description = "Подробное описание задачи с проверяемыми требованиями. " * 200
    criteria = [
        f"Criterion {index}: " + ("Preserve the entire requirement. " * 160).rstrip()
        for index in range(52)
    ]
    assert len(description.encode("utf-8")) > 4096
    assert all(len(item.encode("utf-8")) > 4096 for item in criteria)
    task = ctx.parent.create_task(
        title="Complete task source",
        description=description,
        acceptance_criteria=criteria,
    )
    client = _client(lambda: ctx.parent)
    route = f"/api/outputs/task-results/{task['entity_ref']['id']}"
    params = {"task_revision": task["revision"]}
    response = client.get(route, params=params, headers={"X-Session": "allowed"})
    assert response.status_code == 200
    details = response.json()
    assert details["description"] == description
    assert details["acceptance_criteria"] == criteria
    assert details["task_revision"] == task["revision"]
    revised = ctx.parent.revise_task(
        task["entity_ref"]["id"], task["revision"], changes={"description": "Revised source"}
    )
    stale = client.get(route, params=params, headers={"X-Session": "allowed"})
    assert stale.status_code == 409
    current = client.get(
        route, params={"task_revision": revised["revision"]}, headers={"X-Session": "allowed"}
    )
    assert current.status_code == 200
    assert current.json()["description"] == "Revised source"
    assert current.json()["acceptance_criteria"] == criteria


@pytest.mark.parametrize("oversize_field", ["description", "acceptance_criteria"])
def test_task_results_refuse_oversize_task_source_without_truncation(context, oversize_field):
    from tests.ui.test_output_routes import _client

    ctx = context
    oversized = "Detailed requirement with explicit acceptance evidence. " * 10000
    assert len(oversized.encode("utf-8")) > 512 * 1024
    task = ctx.parent.create_task(
        title="Oversize task source",
        description=oversized if oversize_field == "description" else "Normal description",
        acceptance_criteria=[oversized] if oversize_field == "acceptance_criteria" else ["Done"],
    )
    task_id = task["entity_ref"]["id"]
    with pytest.raises(OutputReadRefusal) as refusal:
        task_result_details(ctx.parent, task_id, task["revision"], limit=1)
    assert refusal.value.code == "results_bounds_exceeded"
    assert refusal.value.status_code == 409
    client = _client(lambda: ctx.parent)
    response = client.get(
        f"/api/outputs/task-results/{task_id}",
        params={"task_revision": task["revision"], "limit": 1},
        headers={"X-Session": "allowed"},
    )
    assert response.status_code == 409
    assert len(response.content) < 1024
    assert "Detailed requirement" not in response.text
