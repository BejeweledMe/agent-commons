from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import ClaimConflictError, LifecycleConflictError
from agent_commons.services import CommonsManager


def start_agent(
    repo: Path,
    *,
    stable_instance_id: str,
    client: str,
    role: str,
) -> CommonsManager:
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id=stable_instance_id,
        principal="local-operator",
        client=client,
        software=f"{client}-cli",
        role=role,
    )
    manager.session_id = session["session_id"]
    return manager


def test_two_isolated_agents_coordinate_review_and_handoff_end_to_end(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=("codex", "claude"))
    codex = start_agent(
        repo,
        stable_instance_id="codex-e2e-window-12345678",
        client="codex",
        role="builder",
    )
    claude = start_agent(
        repo,
        stable_instance_id="claude-e2e-window-12345678",
        client="claude",
        role="reviewer",
    )

    created = codex.create_task(
        title="Two-agent workflow",
        description="Exercise coordination through independent acceptance",
        acceptance_criteria=("independent review passes",),
        idempotency_key="e2e-task",
    )
    task_id = created["entity_ref"]["id"]
    taken = codex.take_task(
        task_id,
        created["revision"],
        idempotency_key="e2e-take",
    )
    claim = codex.acquire_claim(
        ("path:src/service",),
        idempotency_key="e2e-claim",
    )
    with pytest.raises(ClaimConflictError, match="overlaps"):
        claude.acquire_claim(
            ("path:src/service/api",),
            idempotency_key="e2e-overlap",
        )

    thread = codex.open_thread(
        thread_type="question",
        subject="Review boundary",
        desired_outcome="confirm the exact review criterion",
        to=("reviewer",),
        idempotency_key="e2e-thread",
    )
    thread_id = thread["entity_ref"]["id"]
    assert [item["id"] for item in claude.inbox()["threads"]] == [thread_id]
    reply = claude.reply_thread(
        thread_id,
        thread["revision"],
        body="Use the submitted task revision.",
        idempotency_key="e2e-reply",
    )
    codex.resolve_thread(
        thread_id,
        reply["revision"],
        resolution="resolved",
        summary="Criterion confirmed",
        idempotency_key="e2e-resolve",
    )

    started = codex.start_task(
        task_id,
        taken["revision"],
        idempotency_key="e2e-start",
    )
    completed = codex.complete_task(
        task_id,
        started["revision"],
        summary="Implementation complete",
        idempotency_key="e2e-complete",
    )
    submitted = codex.submit_task(
        task_id,
        completed["revision"],
        summary="Ready for independent review",
        idempotency_key="e2e-submit",
    )
    review = codex.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="e2e-review-request",
    )
    review_id = review["entity_ref"]["id"]
    with pytest.raises(LifecycleConflictError, match="independent review"):
        codex.complete_review(
            review_id,
            review["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="self review must fail",
            idempotency_key="e2e-self-review",
        )
    claude.complete_review(
        review_id,
        review["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="Independent checks passed",
        idempotency_key="e2e-review-complete",
    )
    accepted = codex.accept_task(
        task_id,
        submitted["revision"],
        summary="Independent approval recorded",
        idempotency_key="e2e-accept",
    )

    handoff = codex.create_handoff(
        to=(claude.session_id,),
        completed=("Task accepted",),
        next_actions=("Monitor follow-up work",),
        related_refs=({"kind": "task", "id": task_id},),
        idempotency_key="e2e-handoff",
    )
    handoff_id = handoff["entity_ref"]["id"]
    assert [item["id"] for item in claude.inbox()["handoffs"]] == [handoff_id]
    claude.acknowledge_handoff(
        handoff_id,
        handoff["revision"],
        note="Context received",
        idempotency_key="e2e-handoff-ack",
    )
    codex.release_claim(claim["claim_id"], nonce=claim["nonce"])

    task = next(item for item in claude.list_tasks() if item["id"] == task_id)
    approved_review = next(item for item in claude.list_reviews() if item["id"] == review_id)
    acknowledged = next(item for item in claude.list_handoffs() if item["id"] == handoff_id)
    doctor = claude.doctor()
    assert task["state"] == "accepted"
    assert task["revision"] == accepted["revision"]
    assert approved_review["state"] == "approved"
    assert approved_review["stale"] is False
    assert acknowledged["state"] == "acknowledged"
    assert doctor["ok"] is True
    assert doctor["event_count"] == 13


def test_work_author_cannot_approve_after_submitter_handoff(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=("codex", "claude"))
    author = start_agent(
        repo,
        stable_instance_id="codex-author-window-12345678",
        client="codex",
        role="builder",
    )
    submitter = start_agent(
        repo,
        stable_instance_id="codex-submitter-window-12345678",
        client="codex",
        role="coordinator",
    )
    reviewer = start_agent(
        repo,
        stable_instance_id="claude-reviewer-window-12345678",
        client="claude",
        role="reviewer",
    )

    created = author.create_task(
        title="Handoff review boundary",
        description="Preserve the original work author across submission handoff",
        acceptance_criteria=("independent review",),
        idempotency_key="handoff-e2e-create",
    )
    started = author.start_task(
        created["entity_ref"]["id"],
        created["revision"],
        idempotency_key="handoff-e2e-start",
    )
    completed = author.complete_task(
        created["entity_ref"]["id"],
        started["revision"],
        summary="work authored",
        idempotency_key="handoff-e2e-complete",
    )
    submitted = submitter.submit_task(
        created["entity_ref"]["id"],
        completed["revision"],
        summary="submitted by coordinator",
        idempotency_key="handoff-e2e-submit",
    )
    requested = submitter.request_review(
        target_ref={"kind": "task", "id": created["entity_ref"]["id"]},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="handoff-e2e-review-request",
    )
    with pytest.raises(LifecycleConflictError, match="work-author session"):
        author.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="author self-review",
            idempotency_key="handoff-e2e-author-review",
        )

    reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="independent review",
        idempotency_key="handoff-e2e-independent-review",
    )
    submitter.accept_task(
        created["entity_ref"]["id"],
        submitted["revision"],
        summary="accepted",
        idempotency_key="handoff-e2e-accept",
    )
    task = next(
        item for item in submitter.list_tasks() if item["id"] == created["entity_ref"]["id"]
    )
    assert task["state"] == "accepted"
    assert task["work_author_session_ids"] == [author.session_id]
