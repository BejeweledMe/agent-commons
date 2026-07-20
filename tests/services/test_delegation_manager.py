from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import LifecycleConflictError, SecurityPolicyError
from agent_commons.services import CommonsManager

LIMITS = {
    "max_depth": 1,
    "wall_time_seconds": 900,
    "max_attempts": 2,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 10000},
}


def _workspace(tmp_path: Path) -> tuple[CommonsManager, CommonsManager, dict, dict]:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="delegation-tests")
    parent = CommonsManager(repo, state_root=state_root)
    parent_session = parent.start_session(
        stable_instance_id="delegation-parent-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    parent.session_id = parent_session["session_id"]
    child = CommonsManager(repo, state_root=state_root)
    child_session = child.start_session(
        stable_instance_id="delegation-child-session-12345678",
        principal="operator",
        client="claude",
        software="claude-code",
        role="independent-reviewer",
    )
    child.session_id = child_session["session_id"]
    return parent, child, parent_session, child_session


def _task(manager: CommonsManager, *, key: str = "delegation-target") -> dict:
    return manager.create_task(
        title="Implement bounded delegation",
        description="Provide one exact target for another agent.",
        acceptance_criteria=("independent review passes",),
        priority="high",
        idempotency_key=key,
    )


def _create(manager: CommonsManager, task: dict, *, key: str = "delegate-review") -> dict:
    return manager.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits=LIMITS,
        idempotency_key=key,
    )


def test_manager_delegation_lifecycle_uses_exact_cas_and_result_refs(tmp_path: Path) -> None:
    parent, child, _, child_session = _workspace(tmp_path)
    task = _task(parent)
    review = parent.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect the exact task revision",),
        idempotency_key="delegate-review-request",
    )
    requested = _create(parent, task)
    repeated = _create(parent, task)
    assert repeated["event_id"] == requested["event_id"]
    delegation_id = requested["entity_ref"]["id"]

    started = parent.start_delegation(
        delegation_id,
        requested["revision"],
        child_session_id=child_session["session_id"],
        attempt=1,
        idempotency_key="delegate-review-start",
    )
    with pytest.raises(LifecycleConflictError, match="stale expected revision"):
        parent.mark_delegation_input_needed(
            delegation_id,
            requested["revision"],
            summary="This uses an obsolete delegation revision.",
            idempotency_key="delegate-review-stale-input",
        )

    completed_review = child.complete_review(
        review["entity_ref"]["id"],
        review["revision"],
        target_revision=task["revision"],
        verdict="approved",
        summary="The exact target satisfies the requested criterion.",
        idempotency_key="delegate-review-complete",
    )
    succeeded = child.succeed_delegation(
        delegation_id,
        started["revision"],
        summary="The requested validation completed.",
        result_refs=(review["entity_ref"],),
        idempotency_key="delegate-review-succeed",
    )

    shown = parent.get_delegation(delegation_id)
    assert shown["state"] == "succeeded"
    assert shown["revision"] == succeeded["revision"]
    assert shown["child_session_id"] == child_session["session_id"]
    assert shown["result_refs"] == [review["entity_ref"]]
    assert completed_review["revision"]
    assert parent.list_delegations(state="active") == []
    assert parent.list_delegations(state="succeeded")[0]["id"] == delegation_id


def test_stale_target_is_rejected_at_request_and_again_before_start(tmp_path: Path) -> None:
    parent, _, _, child_session = _workspace(tmp_path)
    task = _task(parent)
    task_id = task["entity_ref"]["id"]
    moved = parent.start_task(
        task_id,
        task["revision"],
        idempotency_key="move-target-before-request",
    )
    with pytest.raises(LifecycleConflictError, match="target_revision"):
        _create(parent, task, key="stale-target-request")

    current_target = {**task, "revision": moved["revision"]}
    requested = _create(parent, current_target, key="current-target-request")
    changed_again = parent.complete_task(
        task_id,
        moved["revision"],
        summary="The target moved after delegation request.",
        idempotency_key="move-target-before-start",
    )
    assert changed_again["revision"] != requested["revision"]
    with pytest.raises(LifecycleConflictError, match="target_revision"):
        parent.start_delegation(
            requested["entity_ref"]["id"],
            requested["revision"],
            child_session_id=child_session["session_id"],
            idempotency_key="start-stale-target",
        )


def test_start_requires_a_distinct_active_child_session(tmp_path: Path) -> None:
    parent, _, parent_session, child_session = _workspace(tmp_path)
    task = _task(parent)
    requested = _create(parent, task)
    delegation_id = requested["entity_ref"]["id"]

    with pytest.raises(LifecycleConflictError, match="distinct"):
        parent.start_delegation(
            delegation_id,
            requested["revision"],
            child_session_id=parent_session["session_id"],
            idempotency_key="self-child-binding",
        )
    with pytest.raises(LifecycleConflictError, match="absent, expired, or closed"):
        parent.start_delegation(
            delegation_id,
            requested["revision"],
            child_session_id="session." + "f" * 32,
            idempotency_key="unknown-child-binding",
        )

    started = parent.start_delegation(
        delegation_id,
        requested["revision"],
        child_session_id=child_session["session_id"],
        idempotency_key="distinct-child-binding",
    )
    assert parent.get_delegation(delegation_id)["revision"] == started["revision"]


def test_delegation_transition_security_failure_writes_no_event(tmp_path: Path) -> None:
    parent, _, _, child_session = _workspace(tmp_path)
    task = _task(parent)
    requested = _create(parent, task)
    started = parent.start_delegation(
        requested["entity_ref"]["id"],
        requested["revision"],
        child_session_id=child_session["session_id"],
        idempotency_key="secure-start",
    )
    before = list(parent.events.iter_events())
    secret = "sk-proj-" + "Z" * 24

    with pytest.raises(SecurityPolicyError) as caught:
        parent.mark_delegation_input_needed(
            requested["entity_ref"]["id"],
            started["revision"],
            summary=f"provider credential={secret}",
            idempotency_key="unsafe-delegation-summary",
        )

    assert secret not in str(caught.value)
    assert len(list(parent.events.iter_events())) == len(before)
    assert parent.get_delegation(requested["entity_ref"]["id"])["state"] == "active"
