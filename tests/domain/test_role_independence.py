"""One standing role cannot approve work it authored, however many sessions it used.

Independence has been repaired three times in this branch, each time for the
exact target kind that was reported.  Persistent roles reopen it at a new level:
the same role authors in one run and judges in the next, and every session
identifier differs.  These tests are written against the *class* -- a principal
that authored the subject -- rather than against the two shapes that happen to
be reachable today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.services import CommonsManager

LIMITS = {
    "max_depth": 1,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 8000},
}


def _open(repo: Path, state_root: Path, *, name: str, role: str) -> CommonsManager:
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id=f"role-independence-{name}-1234",
        principal=f"operator-{name}",
        client="claude",
        software="claude-code",
        role=role,
    )
    manager.session_id = session["session_id"]
    return manager


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="role-independence")
    operator = _open(repo, state_root, name="operator", role="operator")
    return {"repo": repo, "state_root": state_root, "operator": operator}


def _bind_run(
    workspace: dict[str, Any],
    *,
    agent_id: str,
    worker: CommonsManager,
    target_ref: dict[str, str],
    target_revision: str,
    profile: str,
    purpose: str,
    key: str,
) -> dict[str, Any]:
    """Put `worker`'s session under `agent_id` for the rest of the test."""

    operator: CommonsManager = workspace["operator"]
    delegation = operator.create_delegation(
        target_ref=target_ref,
        target_revision=target_revision,
        target_profile=profile,
        purpose=purpose,
        limits=LIMITS,
        on_behalf_of_agent_id=agent_id,
        idempotency_key=f"{key}-delegation",
    )
    operator.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=str(worker.session_id),
        idempotency_key=f"{key}-start",
    )
    return delegation


def test_one_role_cannot_approve_the_task_it_authored_in_an_earlier_session(
    workspace: dict[str, Any],
) -> None:
    operator: CommonsManager = workspace["operator"]
    role = operator.create_agent(
        name="Full-stack owner",
        profile_id="claude-builder",
        rationale="one standing role doing both halves",
        idempotency_key="role-both-halves",
    )
    role_id = role["entity_ref"]["id"]

    author = _open(workspace["repo"], workspace["state_root"], name="first", role="builder")
    task = author.create_task(
        title="Ship the endpoint",
        description="authored by the role in its first run",
        acceptance_criteria=("works",),
        idempotency_key="role-task",
    )
    task_id = task["entity_ref"]["id"]
    _bind_run(
        workspace,
        agent_id=role_id,
        worker=author,
        target_ref={"kind": "task", "id": task_id},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="authoring-run",
    )
    started = author.start_task(task_id, task["revision"], idempotency_key="role-task-start")
    completed = author.complete_task(
        task_id, started["revision"], summary="done", idempotency_key="role-task-complete"
    )
    submitted = operator.submit_task(
        task_id, completed["revision"], summary="submitted", idempotency_key="role-task-submit"
    )
    requested = operator.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="role-review-request",
    )

    # A brand new session, a brand new delegation -- and the same standing role.
    judge = _open(workspace["repo"], workspace["state_root"], name="second", role="reviewer")
    assert judge.session_id != author.session_id
    _bind_run(
        workspace,
        agent_id=role_id,
        worker=judge,
        target_ref={"kind": "review", "id": requested["entity_ref"]["id"]},
        target_revision=requested["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="judging-run",
    )
    with pytest.raises(LifecycleConflictError, match=f"agent:{role_id}"):
        judge.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="approving my own earlier run",
            idempotency_key="role-self-review",
        )


def test_a_session_that_served_two_roles_carries_both_principals(
    workspace: dict[str, Any],
) -> None:
    """One session, two roles across two runs, and the earlier one still counts.

    Keeping only the most recent binding would let the role that authored the
    work review it later through the same session under a different name.
    """

    operator: CommonsManager = workspace["operator"]
    authoring_role = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="writes the code",
        idempotency_key="two-hats-author",
    )
    later_role = operator.create_agent(
        name="Reviewer",
        profile_id="claude-builder",
        rationale="the same window is reused for review",
        idempotency_key="two-hats-reviewer",
    )
    worker = _open(workspace["repo"], workspace["state_root"], name="reused", role="builder")
    task = worker.create_task(
        title="Work done wearing the first hat",
        description="authored under one role, judged under another by one session",
        acceptance_criteria=("works",),
        idempotency_key="two-hats-task",
    )
    task_id = task["entity_ref"]["id"]
    _bind_run(
        workspace,
        agent_id=authoring_role["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task_id},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="two-hats-authoring",
    )
    started = worker.start_task(task_id, task["revision"], idempotency_key="two-hats-start")
    completed = worker.complete_task(
        task_id, started["revision"], summary="done", idempotency_key="two-hats-complete"
    )
    submitted = operator.submit_task(
        task_id, completed["revision"], summary="submitted", idempotency_key="two-hats-submit"
    )
    requested = operator.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="two-hats-review-request",
    )
    # The same session comes back under a second role.
    _bind_run(
        workspace,
        agent_id=later_role["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="two-hats-second",
    )
    with pytest.raises(LifecycleConflictError, match="authored the subject"):
        worker.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="a new hat over the same hands",
            idempotency_key="two-hats-review",
        )


def test_a_different_role_in_the_same_workspace_still_reviews_freely(
    workspace: dict[str, Any],
) -> None:
    operator: CommonsManager = workspace["operator"]
    builder_role = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="writes the code",
        idempotency_key="role-builder",
    )
    reviewer_role = operator.create_agent(
        name="Independent reviewer",
        profile_id="claude-independent-reviewer",
        rationale="judges the code",
        idempotency_key="role-reviewer",
    )
    author = _open(workspace["repo"], workspace["state_root"], name="writer", role="builder")
    task = author.create_task(
        title="Ship the other endpoint",
        description="authored by one role, judged by another",
        acceptance_criteria=("works",),
        idempotency_key="two-role-task",
    )
    task_id = task["entity_ref"]["id"]
    _bind_run(
        workspace,
        agent_id=builder_role["entity_ref"]["id"],
        worker=author,
        target_ref={"kind": "task", "id": task_id},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="two-role-authoring",
    )
    started = author.start_task(task_id, task["revision"], idempotency_key="two-role-start")
    completed = author.complete_task(
        task_id, started["revision"], summary="done", idempotency_key="two-role-complete"
    )
    submitted = operator.submit_task(
        task_id, completed["revision"], summary="submitted", idempotency_key="two-role-submit"
    )
    requested = operator.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="two-role-review-request",
    )
    judge = _open(workspace["repo"], workspace["state_root"], name="judge", role="reviewer")
    _bind_run(
        workspace,
        agent_id=reviewer_role["entity_ref"]["id"],
        worker=judge,
        target_ref={"kind": "review", "id": requested["entity_ref"]["id"]},
        target_revision=requested["revision"],
        profile="claude-independent-reviewer",
        purpose="independent_review",
        key="two-role-judging",
    )
    approved = judge.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="a different role, so a real second opinion",
        idempotency_key="two-role-review",
    )
    assert approved["event_type"] == "review.completed"


def test_acceptance_refuses_a_review_from_the_role_that_did_the_work(
    workspace: dict[str, Any],
) -> None:
    """The same rule, one layer later: acceptance compares principals too.

    Checking only `review.completed` would leave the hole open for any review
    recorded before the delegation binding existed.
    """

    operator: CommonsManager = workspace["operator"]
    role = operator.create_agent(
        name="Solo",
        profile_id="claude-builder",
        rationale="the only role on this task",
        idempotency_key="accept-role",
    )
    author = _open(workspace["repo"], workspace["state_root"], name="solo", role="builder")
    task = author.create_task(
        title="Accepted without a second opinion",
        description="authoring and judging by one role",
        acceptance_criteria=("works",),
        idempotency_key="accept-task",
    )
    task_id = task["entity_ref"]["id"]
    started = author.start_task(task_id, task["revision"], idempotency_key="accept-start")
    completed = author.complete_task(
        task_id, started["revision"], summary="done", idempotency_key="accept-complete"
    )
    submitted = operator.submit_task(
        task_id, completed["revision"], summary="submitted", idempotency_key="accept-submit"
    )
    requested = operator.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="accept-review-request",
    )
    judge = _open(workspace["repo"], workspace["state_root"], name="judge2", role="reviewer")
    approved = judge.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="approved before either session was bound to the role",
        idempotency_key="accept-review",
    )
    assert approved["event_type"] == "review.completed"

    # Both sessions turn out to have been the same role all along.
    for name, worker in (("author", author), ("judge", judge)):
        _bind_run(
            workspace,
            agent_id=role["entity_ref"]["id"],
            worker=worker,
            target_ref={"kind": "task", "id": task_id},
            target_revision=submitted["revision"],
            profile="claude-builder",
            purpose="implementation",
            key=f"accept-bind-{name}",
        )
    with pytest.raises(LifecycleConflictError, match="work-author principals"):
        operator.accept_task(
            task_id,
            submitted["revision"],
            summary="accepting a review from the authoring role",
            idempotency_key="accept-task-accept",
        )


def test_a_role_cannot_staff_a_run_with_a_role_it_did_not_create(
    workspace: dict[str, Any],
) -> None:
    """Acting for a role is holding its authority, so naming one is a privilege.

    Without this, any session able to open a delegation could name the most
    privileged role in the workspace and hand a session of its choosing
    everything that role may do.
    """

    operator: CommonsManager = workspace["operator"]
    privileged = operator.create_agent(
        name="Tech lead",
        profile_id="claude-builder",
        rationale="high authority",
        grants={"create_roles": "auto", "retire_roles": "auto", "open_links": "auto"},
        turnover_budget=8,
        idempotency_key="staff-privileged",
    )
    modest = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="ordinary authority",
        idempotency_key="staff-modest",
    )
    worker = _open(workspace["repo"], workspace["state_root"], name="worker", role="builder")
    task = worker.create_task(
        title="Ordinary work",
        description="run for the modest role",
        acceptance_criteria=("done",),
        idempotency_key="staff-task",
    )
    parent = _bind_run(
        workspace,
        agent_id=modest["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="staff-modest-run",
    )

    # The worker now acts as the modest role and, from inside its own lineage,
    # reaches for the privileged one.
    follow_on = worker.create_task(
        title="Follow-on work",
        description="the target of the reach",
        acceptance_criteria=("done",),
        idempotency_key="staff-follow-task",
    )
    with pytest.raises(LifecycleConflictError, match="may staff only itself"):
        worker.create_delegation(
            target_ref={"kind": "task", "id": follow_on["entity_ref"]["id"]},
            target_revision=follow_on["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            parent_delegation_id=parent["entity_ref"]["id"],
            on_behalf_of_agent_id=privileged["entity_ref"]["id"],
            idempotency_key="staff-reach",
        )

    # Its own role is still fine, which is what keeps multi-step work possible.
    allowed = worker.create_delegation(
        target_ref={"kind": "task", "id": follow_on["entity_ref"]["id"]},
        target_revision=follow_on["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        parent_delegation_id=parent["entity_ref"]["id"],
        on_behalf_of_agent_id=modest["entity_ref"]["id"],
        idempotency_key="staff-own",
    )
    assert allowed["event_type"] == "delegation.requested"


def test_a_human_window_may_still_staff_any_active_role(workspace: dict[str, Any]) -> None:
    """The ordinary way work starts must keep working."""

    operator: CommonsManager = workspace["operator"]
    role = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="staffed by a person",
        idempotency_key="staff-human",
    )
    task = operator.create_task(
        title="Work",
        description="staffed from a human window",
        acceptance_criteria=("done",),
        idempotency_key="staff-human-task",
    )
    created = operator.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="staff-human-delegation",
    )
    assert created["event_type"] == "delegation.requested"


def test_a_handoff_link_is_what_widens_staffing_beyond_a_lineage(
    workspace: dict[str, Any],
) -> None:
    """The typed action earns its keep: it grants exactly the refused thing.

    Before the link the same call is refused, after it the same call succeeds,
    and an `ask` link does not do it -- which is why the record carries an
    action rather than an open/closed flag.
    """

    operator: CommonsManager = workspace["operator"]
    backend = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="hands work over",
        idempotency_key="link-backend",
    )
    frontend = operator.create_agent(
        name="Frontend",
        profile_id="claude-builder",
        rationale="receives handed-over work",
        idempotency_key="link-frontend",
    )
    worker = _open(workspace["repo"], workspace["state_root"], name="handoff", role="builder")
    task = worker.create_task(
        title="Backend work",
        description="the run that will hand something over",
        acceptance_criteria=("done",),
        idempotency_key="link-task",
    )
    parent = _bind_run(
        workspace,
        agent_id=backend["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="link-run",
    )
    handed_over = worker.create_task(
        title="Frontend work",
        description="the piece being handed over",
        acceptance_criteria=("done",),
        idempotency_key="link-handed-task",
    )

    def hand_over(key: str) -> dict[str, Any]:
        return worker.create_delegation(
            target_ref={"kind": "task", "id": handed_over["entity_ref"]["id"]},
            target_revision=handed_over["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            parent_delegation_id=parent["entity_ref"]["id"],
            on_behalf_of_agent_id=frontend["entity_ref"]["id"],
            idempotency_key=key,
        )

    with pytest.raises(LifecycleConflictError, match="handoff_work link"):
        hand_over("link-before")

    # An `ask` link is a different grant and does not widen staffing.
    asking = operator.open_agent_link(
        from_agent_id=backend["entity_ref"]["id"],
        to_agent_id=frontend["entity_ref"]["id"],
        allowed_action="ask",
        deadline_seconds=900,
        reason="one bounded question",
        idempotency_key="link-ask",
    )
    with pytest.raises(LifecycleConflictError, match="handoff_work link"):
        hand_over("link-with-ask")
    operator.close_agent_link(
        asking["entity_ref"]["id"],
        asking["revision"],
        reason="superseded by a handoff link",
        idempotency_key="link-ask-close",
    )

    operator.open_agent_link(
        from_agent_id=backend["entity_ref"]["id"],
        to_agent_id=frontend["entity_ref"]["id"],
        allowed_action="handoff_work",
        deadline_seconds=900,
        reason="the frontend half is theirs",
        idempotency_key="link-handoff",
    )
    assert hand_over("link-after")["event_type"] == "delegation.requested"


def test_closing_the_link_takes_the_widening_back(workspace: dict[str, Any]) -> None:
    operator: CommonsManager = workspace["operator"]
    backend = operator.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="hands work over",
        idempotency_key="revoke-backend",
    )
    frontend = operator.create_agent(
        name="Frontend",
        profile_id="claude-builder",
        rationale="receives handed-over work",
        idempotency_key="revoke-frontend",
    )
    link = operator.open_agent_link(
        from_agent_id=backend["entity_ref"]["id"],
        to_agent_id=frontend["entity_ref"]["id"],
        allowed_action="handoff_work",
        deadline_seconds=900,
        reason="temporary",
        idempotency_key="revoke-link",
    )
    operator.close_agent_link(
        link["entity_ref"]["id"],
        link["revision"],
        reason="the loan ended",
        idempotency_key="revoke-close",
    )
    worker = _open(workspace["repo"], workspace["state_root"], name="revoked", role="builder")
    task = worker.create_task(
        title="Backend work",
        description="after the link closed",
        acceptance_criteria=("done",),
        idempotency_key="revoke-task",
    )
    parent = _bind_run(
        workspace,
        agent_id=backend["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="revoke-run",
    )
    with pytest.raises(LifecycleConflictError, match="handoff_work link"):
        worker.create_delegation(
            target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
            target_revision=task["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            parent_delegation_id=parent["entity_ref"]["id"],
            on_behalf_of_agent_id=frontend["entity_ref"]["id"],
            idempotency_key="revoke-attempt",
        )
