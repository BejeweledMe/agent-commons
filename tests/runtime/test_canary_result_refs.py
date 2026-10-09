from __future__ import annotations

import pytest

from agent_commons.services.provider_canary import _implementation_result_refs_match
from tests.mcp.test_text_result_tools import complete, publish, submit
from tests.mcp.test_text_result_tools import context as text_result_context


@pytest.fixture
def result_case(tmp_path, monkeypatch):
    ctx = text_result_context.__wrapped__(tmp_path, monkeypatch)
    ctx.report = publish(ctx)
    ctx.completed = complete(ctx, ctx.report)
    return ctx


def _grade(ctx, **changes):
    return _implementation_result_refs_match(
        ctx.parent,
        **{
            "task_ref": {"kind": "task", "id": ctx.task},
            "target_revision": ctx.revision,
            "delegation": dict(ctx.parent.snapshot().delegations[ctx.run]),
            "child_session_id": ctx.worker.session_id,
            **changes,
        },
    )


def _succeed_with_refs(ctx, refs):
    ctx.worker.succeed_delegation(
        ctx.run,
        ctx.worker.snapshot().entity_revision("delegation", ctx.run),
        summary="Synthetic terminal result",
        result_refs=refs,
    )


def _finalize(ctx):
    submitted = submit(ctx, ctx.report, ctx.completed)
    ctx.service.finalize(
        expected_task_revision=submitted["revision"],
        summary="Submitted",
        idempotency_key="final-result-v1",
    )
    return submitted


def test_canary_grades_exact_report_chain(result_case):
    _finalize(result_case)
    assert _grade(result_case) is True


def test_canary_preserves_generic_task_reference_protocol(result_case):
    ctx = result_case
    _succeed_with_refs(ctx, [{"kind": "task", "id": ctx.task}])
    assert _grade(ctx) is True


@pytest.mark.parametrize("event", ["completed", "unrelated"])
def test_canary_rejects_non_submission_event_in_canonical_terminal_result(result_case, event):
    ctx = result_case
    revision = (
        ctx.completed["revision"] if event == "completed" else ctx.report["artifact_revision"]
    )
    _succeed_with_refs(ctx, [{"kind": "task", "id": ctx.task}, {"kind": "event", "id": revision}])
    assert _grade(ctx) is False


def test_canary_rejects_submission_authored_by_another_session(result_case):
    ctx = result_case
    submitted = ctx.parent.submit_task(
        ctx.task,
        ctx.completed["revision"],
        summary="Owner submitted",
        artifact_refs=[{"kind": "artifact", "id": ctx.report["artifact_id"]}],
    )
    _succeed_with_refs(
        ctx,
        [
            {"kind": "task", "id": ctx.task},
            {"kind": "event", "id": submitted["revision"]},
        ],
    )
    assert _grade(ctx) is False


def test_canary_rejects_changed_submission_evidence(result_case):
    ctx = result_case
    source = ctx.parent.paths.repo_root / "other.txt"
    source.write_text("Different evidence")
    artifact = ctx.parent.register_artifact(
        source, media_type="text/plain", classification="internal"
    )
    submitted = ctx.worker.submit_task(
        ctx.task,
        ctx.completed["revision"],
        summary="Changed evidence",
        artifact_refs=[artifact["entity_ref"]],
    )
    _succeed_with_refs(
        ctx,
        [
            {"kind": "task", "id": ctx.task},
            {"kind": "event", "id": submitted["revision"]},
        ],
    )
    assert _grade(ctx) is False


@pytest.mark.parametrize("change", ["wrong-child", "stale-original", "extra-ref", "later-task"])
def test_canary_rejects_unbound_or_stale_report_results(result_case, change):
    ctx = result_case
    submitted = _finalize(ctx)
    changes = {}
    if change == "wrong-child":
        changes["child_session_id"] = ctx.parent.session_id
    elif change == "stale-original":
        changes["target_revision"] = ctx.completed["revision"]
        changes["delegation"] = {
            **dict(ctx.parent.snapshot().delegations[ctx.run]),
            "target_revision": ctx.completed["revision"],
        }
    elif change == "extra-ref":
        record = dict(ctx.parent.snapshot().delegations[ctx.run])
        record["result_refs"].append({"kind": "artifact", "id": ctx.report["artifact_id"]})
        changes["delegation"] = record
    else:
        ctx.parent.revise_task(ctx.task, submitted["revision"], changes={"title": "Later revision"})
    assert _grade(ctx, **changes) is False
