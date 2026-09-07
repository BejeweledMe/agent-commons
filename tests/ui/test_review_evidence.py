"""Exact evidence and response-loss regressions for the panel's review walk."""

from __future__ import annotations

from typing import Any

import pytest

from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from tests.ui.test_acceptance_chain import approve_independently


def completed_work(
    writable: UIContext, workspace: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], CommonsManager]:
    manager = writable.writer()
    session = manager.start_session(
        stable_instance_id="artifact-only-author",
        principal="artifact-only-author",
        client="codex",
        software="codex",
        role="builder",
    )
    author = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=session["session_id"],
    )
    artifact = author.register_artifact("README.md", media_type="text/markdown")
    created = manager.create_task(
        title="Review the document",
        description="Check the registered document",
        acceptance_criteria=["The document is accurate"],
    )
    task_id = created["entity_ref"]["id"]
    started = manager.start_task(task_id, created["revision"])
    completed = manager.complete_task(
        task_id,
        started["revision"],
        summary="Document is ready",
        artifact_refs=[artifact["entity_ref"]],
    )
    return completed, artifact, author


def test_review_preserves_exact_evidence_and_artifact_author_is_not_independent(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, artifact, author = completed_work(writable, workspace)
    manager = writable.writer()
    task_id = completed["entity_ref"]["id"]
    before = manager.snapshot().tasks[task_id]
    original = manager.request_review(
        target_ref=completed["entity_ref"],
        target_revision=completed["revision"],
        criteria=["Check the evidence"],
    )
    with pytest.raises(LifecycleConflictError):
        author.complete_review(
            original["entity_ref"]["id"],
            original["revision"],
            target_revision=completed["revision"],
            verdict="approved",
            summary="I authored this artifact",
        )
    result = writable.request_task_review(
        task_id=task_id, expected_revision=completed["revision"], idempotency_key="send"
    )
    after = manager.snapshot().tasks[task_id]
    assert (
        after["artifact_bindings"]
        == before["artifact_bindings"]
        == [{"ref": artifact["entity_ref"], "revision": artifact["revision"]}]
    )
    assert after["artifact_refs"] == before["artifact_refs"]
    with pytest.raises(LifecycleConflictError):
        author.complete_review(
            result["review_id"],
            result["review_revision"],
            target_revision=result["task_revision"],
            verdict="approved",
            summary="I still authored this artifact",
        )
    with pytest.raises(LifecycleConflictError, match="approved independent review"):
        manager.accept_task(task_id, result["task_revision"], summary="Cannot accept self-review")
    approve_independently(
        workspace,
        review_id=result["review_id"],
        review_revision=result["review_revision"],
        target_revision=result["task_revision"],
    )
    manager.accept_task(task_id, result["task_revision"], summary="Independent review passed")


def revise_source(
    workspace: dict[str, Any], artifact: dict[str, Any], author: CommonsManager
) -> None:
    (workspace["repo"] / "README.md").write_text("A different revision\n")
    author.revise_artifact(artifact["entity_ref"]["id"], artifact["revision"], source="README.md")


def test_stale_completed_evidence_is_refused_without_rebinding_or_writes(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, artifact, author = completed_work(writable, workspace)
    task_id = completed["entity_ref"]["id"]
    revise_source(workspace, artifact, author)
    manager = writable.writer()
    before = manager.snapshot()
    with pytest.raises(LifecycleConflictError, match="stale artifact evidence"):
        writable.request_task_review(
            task_id=task_id, expected_revision=completed["revision"], idempotency_key="stale"
        )
    after = manager.snapshot()
    assert after.known_event_ids == before.known_event_ids
    assert after.tasks[task_id]["artifact_bindings"] == before.tasks[task_id]["artifact_bindings"]
    assert after.tasks[task_id]["state"] == "completed"


def test_source_drift_stales_review_and_refuses_retry_and_acceptance(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, artifact, author = completed_work(writable, workspace)
    task_id = completed["entity_ref"]["id"]
    args = dict(task_id=task_id, expected_revision=completed["revision"], idempotency_key="send")
    result = writable.request_task_review(**args)
    approve_independently(
        workspace,
        review_id=result["review_id"],
        review_revision=result["review_revision"],
        target_revision=result["task_revision"],
    )
    revise_source(workspace, artifact, author)
    manager = writable.writer()
    before = manager.snapshot()
    assert before.reviews[result["review_id"]]["stale"] is True
    with pytest.raises(LifecycleConflictError, match="stale artifact evidence"):
        writable.request_task_review(**args)
    with pytest.raises(LifecycleConflictError, match="approved independent review"):
        manager.accept_task(task_id, result["task_revision"], summary="Cannot accept stale work")
    assert manager.snapshot().tasks[task_id]["state"] == "review"
    assert (
        manager.snapshot().tasks[task_id]["artifact_bindings"]
        == before.tasks[task_id]["artifact_bindings"]
    )


@pytest.mark.parametrize(
    "lost_step", ["start_task", "complete_task", "submit_task", "request_review"]
)
def test_review_walk_resumes_after_committed_step_response_loss(
    writable: UIContext, monkeypatch: pytest.MonkeyPatch, lost_step: str
) -> None:
    manager = writable.writer()
    created = manager.create_task(
        title="Ready work", description="Review this task", acceptance_criteria=["It works"]
    )
    args = dict(
        task_id=created["entity_ref"]["id"],
        expected_revision=created["revision"],
        idempotency_key="response-loss",
    )
    original = getattr(CommonsManager, lost_step)

    def lose_response(self: CommonsManager, *args: Any, **kwargs: Any) -> Any:
        original(self, *args, **kwargs)
        raise ConnectionError("Response lost after commit")

    with monkeypatch.context() as patch:
        patch.setattr(CommonsManager, lost_step, lose_response)
        with pytest.raises(ConnectionError):
            writable.request_task_review(**args)
    result = writable.request_task_review(**args)
    snapshot = manager.snapshot()
    replay = writable.request_task_review(**args)
    assert replay == result
    assert result["steps"] == ["start_task", "complete_task", "submit_task", "request_review"]
    assert manager.snapshot().known_event_ids == snapshot.known_event_ids
    assert len(snapshot.reviews) == 1


def test_completed_evidence_retry_is_exact_and_changed_criteria_conflicts(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, _, _ = completed_work(writable, workspace)
    args = dict(
        task_id=completed["entity_ref"]["id"],
        expected_revision=completed["revision"],
        idempotency_key="repeat-evidence",
    )
    result = writable.request_task_review(**args)
    before = writable.writer().snapshot()
    assert writable.request_task_review(**args) == result
    with pytest.raises(IdempotencyConflictError):
        writable.request_task_review(**args, criteria=["A different review intent"])
    assert writable.writer().snapshot().known_event_ids == before.known_event_ids
    assert (
        writable.writer().snapshot().tasks[args["task_id"]]["artifact_bindings"]
        == before.tasks[args["task_id"]]["artifact_bindings"]
    )


def test_review_retry_does_not_swallow_unrelated_task_changes(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, _, _ = completed_work(writable, workspace)
    task_id = completed["entity_ref"]["id"]
    writable.writer().revise_task(task_id, completed["revision"], changes={"title": "Other change"})
    with pytest.raises(LifecycleConflictError, match="stale expected revision"):
        writable.request_task_review(
            task_id=task_id,
            expected_revision=completed["revision"],
            idempotency_key="not-this-change",
        )


def test_reopened_work_keeps_evidence_through_completion_and_submission(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, artifact, _ = completed_work(writable, workspace)
    task_id = completed["entity_ref"]["id"]
    reopened = writable.writer().reopen_task(
        task_id, completed["revision"], reason="Recheck the finished work"
    )
    result = writable.request_task_review(
        task_id=task_id, expected_revision=reopened["revision"], idempotency_key="reopened"
    )
    assert "complete_task" in result["steps"]
    assert writable.writer().snapshot().tasks[task_id]["artifact_bindings"] == [
        {"ref": artifact["entity_ref"], "revision": artifact["revision"]}
    ]


def test_another_session_cannot_resume_the_same_key(
    writable: UIContext, workspace: dict[str, Any]
) -> None:
    completed, _, author = completed_work(writable, workspace)
    args = dict(
        task_id=completed["entity_ref"]["id"],
        expected_revision=completed["revision"],
        idempotency_key="same-spelling",
    )
    writable.request_task_review(**args)
    other = UIContext(
        workspace["repo"], state_root=workspace["state_root"], writer_session_id=author.session_id
    )
    with pytest.raises(LifecycleConflictError, match="stale expected revision"):
        other.request_task_review(**args)
