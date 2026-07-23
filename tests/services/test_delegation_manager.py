from __future__ import annotations

from datetime import datetime
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


def _create(
    manager: CommonsManager,
    task: dict,
    *,
    key: str = "delegate-review",
    request_review: bool = True,
) -> dict:
    if request_review:
        manager.request_review(
            target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
            target_revision=task["revision"],
            criteria=("Inspect the exact task revision",),
            idempotency_key=f"{key}-open-review",
        )
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
    requested = _create(parent, task, request_review=False)
    repeated = _create(parent, task, request_review=False)
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


def test_independent_review_delegation_requires_an_open_review_request(
    tmp_path: Path,
) -> None:
    """Fail fast at create instead of burning the child's attempt and budget."""

    parent, _, _, _ = _workspace(tmp_path)
    task = _task(parent, key="missing-review-target")

    with pytest.raises(LifecycleConflictError, match="open independent review"):
        _create(parent, task, key="missing-review-request", request_review=False)

    completed = parent.request_review(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        criteria=("Inspect the exact task revision",),
        independent=False,
        idempotency_key="missing-review-not-independent",
    )
    assert completed["entity_ref"]["id"]
    with pytest.raises(LifecycleConflictError, match="open independent review"):
        _create(parent, task, key="missing-review-request-2", request_review=False)

    requested = _create(parent, task, key="missing-review-request-3")
    assert requested["entity_ref"]["id"]


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
        _create(parent, task, key="stale-target-request", request_review=False)

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


def test_authorized_recovery_requires_an_unavailable_requester_and_is_idempotent(
    tmp_path: Path,
) -> None:
    parent, _, parent_session, _ = _workspace(tmp_path)
    task = _task(parent, key="recovery-target")
    requested = _create(parent, task, key="recovery-request")
    delegation_id = requested["entity_ref"]["id"]

    recovery = CommonsManager(parent.repo_root, state_root=parent.paths.state_root)
    recovery_session = recovery.start_session(
        stable_instance_id="delegation-recovery-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator-recovery",
        capabilities=("delegation:recover",),
    )
    recovery.session_id = recovery_session["session_id"]

    with pytest.raises(LifecycleConflictError, match="requester session"):
        recovery.recover_delegation(
            delegation_id,
            requested["revision"],
            reason="The requester is still live.",
            idempotency_key="recover-live-requester",
        )

    parent.sessions.close(parent_session["session_id"], nonce=parent_session["nonce"])
    without_capability = CommonsManager(parent.repo_root, state_root=parent.paths.state_root)
    ordinary_session = without_capability.start_session(
        stable_instance_id="delegation-ordinary-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator-recovery",
    )
    without_capability.session_id = ordinary_session["session_id"]
    before = len(list(parent.events.iter_events()))
    with pytest.raises(LifecycleConflictError, match="required capability"):
        without_capability.recover_delegation(
            delegation_id,
            requested["revision"],
            reason="This session lacks explicit recovery capability.",
            idempotency_key="recover-without-capability",
        )
    assert len(list(parent.events.iter_events())) == before

    recovered = recovery.recover_delegation(
        delegation_id,
        requested["revision"],
        reason="The requester expired before canonical provider start.",
        idempotency_key="recover-unavailable-requester",
    )
    repeated = recovery.recover_delegation(
        delegation_id,
        requested["revision"],
        reason="The requester expired before canonical provider start.",
        idempotency_key="recover-unavailable-requester",
    )

    assert recovered["event_type"] == "delegation.recovered"
    assert repeated["event_id"] == recovered["event_id"]
    assert parent.get_delegation(delegation_id)["state"] == "cancelled"
    assert len(list(parent.events.iter_events())) == before + 1


def test_session_close_rejects_owned_nonterminal_delegations(tmp_path: Path) -> None:
    parent, _, parent_session, child_session = _workspace(tmp_path)
    task = _task(parent, key="session-close-target")
    requested = _create(parent, task, key="session-close-request")

    with pytest.raises(LifecycleConflictError, match="non-terminal delegations"):
        parent.end_session(nonce=parent_session["nonce"])

    started = parent.start_delegation(
        requested["entity_ref"]["id"],
        requested["revision"],
        child_session_id=child_session["session_id"],
        idempotency_key="session-close-start",
    )
    with pytest.raises(LifecycleConflictError, match="stop and reconcile active work"):
        parent.end_session(nonce=parent_session["nonce"])
    assert parent.get_delegation(requested["entity_ref"]["id"])["revision"] == started["revision"]


def test_authorized_recovery_accepts_an_effectively_expired_requester(tmp_path: Path) -> None:
    parent, _, parent_session, _ = _workspace(tmp_path)
    task = _task(parent, key="expired-recovery-target")
    requested = _create(parent, task, key="expired-recovery-request")
    recovery = CommonsManager(parent.repo_root, state_root=parent.paths.state_root)
    recovery_session = recovery.start_session(
        stable_instance_id="delegation-expiry-recovery-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator-recovery",
        capabilities=("delegation:recover",),
        ttl_seconds=86_400,
    )
    recovery.session_id = recovery_session["session_id"]
    parent_expiry = datetime.fromisoformat(
        parent_session["expires_at"].replace("Z", "+00:00")
    ).timestamp()
    recovery.sessions.clock = lambda: parent_expiry + 1

    recovered = recovery.recover_delegation(
        requested["entity_ref"]["id"],
        requested["revision"],
        reason="The requester TTL elapsed before launch.",
        idempotency_key="recover-expired-requester",
    )

    assert recovered["event_type"] == "delegation.recovered"
    assert recovery.get_delegation(requested["entity_ref"]["id"])["state"] == "cancelled"


def test_session_listing_uses_effective_expiry_and_explicit_status(tmp_path: Path) -> None:
    parent, _, parent_session, _ = _workspace(tmp_path)
    observer = CommonsManager(parent.repo_root, state_root=parent.paths.state_root)
    expires_at = parent_session["expires_at"]
    observer.sessions.clock = lambda: (
        datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() + 1
    )

    assert observer.show_session() == []
    explicit = observer.show_session(parent_session["session_id"])
    assert isinstance(explicit, dict)
    assert explicit["status"] == "active"
    assert explicit["effective_status"] == "expired"
