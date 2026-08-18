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
    "max_depth": 0,
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


def test_a_bound_worker_cannot_staff_any_follow_on_run(
    workspace: dict[str, Any],
) -> None:
    """A leaf worker cannot escape its bound run through a new root request."""

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
    _bind_run(
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
    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
        worker.create_delegation(
            target_ref={"kind": "task", "id": follow_on["entity_ref"]["id"]},
            target_revision=follow_on["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            on_behalf_of_agent_id=privileged["entity_ref"]["id"],
            idempotency_key="staff-reach",
        )

    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
        worker.create_delegation(
            target_ref={"kind": "task", "id": follow_on["entity_ref"]["id"]},
            target_revision=follow_on["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            on_behalf_of_agent_id=modest["entity_ref"]["id"],
            idempotency_key="staff-own",
        )


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


def test_a_handoff_link_does_not_reenable_worker_delegation(
    workspace: dict[str, Any],
) -> None:
    """A staffing link cannot widen a leaf worker into an orchestrator."""

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
    _bind_run(
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
            on_behalf_of_agent_id=frontend["entity_ref"]["id"],
            idempotency_key=key,
        )

    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
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
    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
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
    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
        hand_over("link-after")


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
    _bind_run(
        workspace,
        agent_id=backend["entity_ref"]["id"],
        worker=worker,
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        profile="claude-builder",
        purpose="implementation",
        key="revoke-run",
    )
    with pytest.raises(LifecycleConflictError, match="cannot escape its lineage"):
        worker.create_delegation(
            target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
            target_revision=task["revision"],
            target_profile="claude-builder",
            purpose="implementation",
            limits=LIMITS,
            on_behalf_of_agent_id=frontend["entity_ref"]["id"],
            idempotency_key="revoke-attempt",
        )


def test_one_role_cannot_request_a_review_and_then_approve_it(
    workspace: dict[str, Any],
) -> None:
    """H2, hole one: the requester/completer check is over principals now.

    A standing role that requested an independent review in one run and
    completed it in the next used two different session ids and slipped past a
    raw session comparison.  It is the same judgment either way.
    """

    operator: CommonsManager = workspace["operator"]
    role = operator.create_agent(
        name="Reviewer role",
        profile_id="claude-independent-reviewer",
        rationale="requests in one run, approves in the next",
        idempotency_key="h2a-role",
    )
    role_id = role["entity_ref"]["id"]
    task = operator.create_task(
        title="Subject of review",
        description="something to review",
        acceptance_criteria=("done",),
        idempotency_key="h2a-task",
    )
    task_id = task["entity_ref"]["id"]

    run1 = _open(workspace["repo"], workspace["state_root"], name="h2a1", role="reviewer")
    _bind_run(
        workspace,
        agent_id=role_id,
        worker=run1,
        target_ref={"kind": "task", "id": task_id},
        target_revision=task["revision"],
        profile="claude-independent-reviewer",
        purpose="verification",
        key="h2a-run1",
    )
    review = run1.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=task["revision"],
        criteria=("looks right",),
        independent=True,
        idempotency_key="h2a-review",
    )
    review_id = review["entity_ref"]["id"]

    run2 = _open(workspace["repo"], workspace["state_root"], name="h2a2", role="reviewer")
    _bind_run(
        workspace,
        agent_id=role_id,
        worker=run2,
        target_ref={"kind": "review", "id": review_id},
        target_revision=review["revision"],
        profile="claude-independent-reviewer",
        purpose="independent_review",
        key="h2a-run2",
    )
    with pytest.raises(LifecycleConflictError, match="requested it"):
        run2.complete_review(
            review_id,
            review["revision"],
            verdict="approved",
            summary="the same role approves its own request",
            target_revision=task["revision"],
            idempotency_key="h2a-complete",
        )


def test_an_authors_identity_survives_a_later_unrelated_event(
    workspace: dict[str, Any],
) -> None:
    """H2, hole two: authorship accumulates, it does not track the last actor.

    A decision's author used to be read off `actor`, which the projection
    overwrites on every event.  One unrelated `decision.deferred` by anyone
    else made the original proposer look independent of its own decision.  A
    genuinely uninvolved reviewer must still be free to approve, so this checks
    both directions.
    """

    operator: CommonsManager = workspace["operator"]
    author = _open(workspace["repo"], workspace["state_root"], name="h2b-author", role="builder")
    decision = author.propose_decision(
        scope="h2b-scope",
        proposal="choose the approach",
        alternatives=("a", "b"),
        idempotency_key="h2b-decision",
    )
    decision_id = decision["entity_ref"]["id"]

    # Someone unrelated touches the decision, overwriting `actor`.
    toucher = _open(workspace["repo"], workspace["state_root"], name="h2b-touch", role="operator")
    deferred = toucher.defer_decision(
        decision_id, decision["revision"], reason="park it", idempotency_key="h2b-defer"
    )

    review = operator.request_review(
        target_ref={"kind": "decision", "id": decision_id},
        target_revision=deferred["revision"],
        criteria=("sound",),
        independent=True,
        idempotency_key="h2b-review",
    )

    with pytest.raises(LifecycleConflictError, match="authored the subject"):
        author.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            verdict="approved",
            summary="the proposer approves its own decision after an unrelated defer",
            target_revision=deferred["revision"],
            idempotency_key="h2b-complete",
        )

    # Independence is not over-broad: a reviewer that never touched the decision
    # still approves it.
    independent = _open(
        workspace["repo"], workspace["state_root"], name="h2b-indep", role="reviewer"
    )
    approved = independent.complete_review(
        review["entity_ref"]["id"],
        review["revision"],
        verdict="approved",
        summary="a third party that never touched the decision",
        target_revision=deferred["revision"],
        idempotency_key="h2b-complete-indep",
    )
    assert approved["event_type"] == "review.completed"


def test_a_re_review_carries_the_producing_roles_context_and_prior_count(
    workspace: dict[str, Any],
) -> None:
    """P7.3 at the consumer: a review shows the producing role's context mode
    and how many times it has judged this subject before.

    Re-review is allowed -- only authoring then judging is refused -- so the
    same accumulated-context role reviews one subject twice, and the second
    review carries a prior-verdict count of one.  The count used to be dead code
    with a type bug that reported nothing for anyone (M7, 2026-08-10 review).
    """

    operator: CommonsManager = workspace["operator"]
    reviewer_role = operator.create_agent(
        name="Standing reviewer",
        profile_id="claude-independent-reviewer",
        context_mode="accumulated",
        rationale="an accumulated-context judge, so its verdict reads differently",
        idempotency_key="m7-role",
    )
    role_id = reviewer_role["entity_ref"]["id"]

    author = _open(workspace["repo"], workspace["state_root"], name="m7-author", role="builder")
    task = author.create_task(
        title="Subject reviewed twice",
        description="the operator authors it; the role only judges",
        acceptance_criteria=("works",),
        idempotency_key="m7-task",
    )
    task_id = task["entity_ref"]["id"]

    def review_once(key: str, verdict: str) -> str:
        requested = operator.request_review(
            target_ref={"kind": "task", "id": task_id},
            target_revision=task["revision"],
            criteria=("correctness",),
            independent=True,
            idempotency_key=f"{key}-request",
        )
        judge = _open(workspace["repo"], workspace["state_root"], name=key, role="reviewer")
        _bind_run(
            workspace,
            agent_id=role_id,
            worker=judge,
            target_ref={"kind": "review", "id": requested["entity_ref"]["id"]},
            target_revision=requested["revision"],
            profile="claude-independent-reviewer",
            purpose="independent_review",
            key=f"{key}-run",
        )
        judge.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=task["revision"],
            verdict=verdict,
            summary=f"verdict for {key}",
            idempotency_key=f"{key}-complete",
        )
        return requested["entity_ref"]["id"]

    first_id = review_once("m7-first", "changes_requested")
    second_id = review_once("m7-second", "approved")

    reviews = operator.snapshot().reviews
    first = reviews[first_id]
    second = reviews[second_id]

    assert first["producer_context_mode"] == "accumulated"
    assert first["producer_agent_ids"] == [role_id]
    assert first["producer_prior_verdict_count"] == 0

    # The second verdict knows this role has judged this subject once before, so
    # it does not read as a clean-slate opinion.
    assert second["producer_context_mode"] == "accumulated"
    assert second["producer_prior_verdict_count"] == 1


def test_a_role_binding_on_a_non_requested_delegation_event_is_ignored_on_replay(
    workspace: dict[str, Any],
) -> None:
    """Round 2 (architecture): the run/role binding is authorised only on
    delegation.requested.  A forged on_behalf_of relation on delegation.started
    used to rebind the run to any role on replay with no authority check.  The
    projection now reads the binding only on the requested event."""

    import copy

    from agent_commons.domain.lifecycle import acting_agent_id
    from agent_commons.domain.projection import project_events

    operator: CommonsManager = workspace["operator"]
    privileged = operator.create_agent(
        name="Privileged",
        profile_id="claude-builder",
        rationale="the role a forged relation would try to reach",
        idempotency_key="obo-role",
    )
    task = operator.create_task(
        title="Unbound work",
        description="a delegation opened for no role",
        acceptance_criteria=("done",),
        idempotency_key="obo-task",
    )
    worker = _open(workspace["repo"], workspace["state_root"], name="obo", role="builder")
    # A delegation with NO on_behalf_of binding.
    delegation = operator.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        idempotency_key="obo-delegation",
    )
    operator.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=str(worker.session_id),
        idempotency_key="obo-start",
    )

    events = [copy.deepcopy(record.event) for record in operator.events.iter_events()]
    for record in events:
        if record.get("event_type") == "delegation.started":
            record.setdefault("relations", []).append(
                {
                    "subject": {"kind": "delegation", "id": delegation["entity_ref"]["id"]},
                    "predicate": "on_behalf_of",
                    "object": {"kind": "agent", "id": privileged["entity_ref"]["id"]},
                }
            )

    snapshot = project_events(events)
    # The forged binding is ignored: the run acts for no role.
    assert snapshot.delegations[delegation["entity_ref"]["id"]].get("agent_id") is None
    assert acting_agent_id(snapshot, str(worker.session_id)) is None


def test_a_link_needs_no_deadline_and_never_expires(workspace: dict[str, Any]) -> None:
    """A permission nothing can enforce on a schedule should not demand a
    number for one. `deadline_seconds` is optional now: a link opened without
    it is valid and stays open — it ends only when someone closes it — while
    an operator who does state a horizon still gets it recorded."""

    operator: CommonsManager = workspace["operator"]
    author = operator.create_agent(
        name="Author",
        profile_id="claude-builder",
        rationale="writes the docs",
        idempotency_key="deadline-author",
    )
    reviewer = operator.create_agent(
        name="Reviewer",
        profile_id="claude-independent-reviewer",
        rationale="reviews the docs",
        idempotency_key="deadline-reviewer",
    )

    opened = operator.open_agent_link(
        from_agent_id=author["entity_ref"]["id"],
        to_agent_id=reviewer["entity_ref"]["id"],
        allowed_action="handoff_work",
        reason="hand the docs over for review",
        idempotency_key="link-without-deadline",
    )
    record = operator.snapshot().agent_links[opened["entity_ref"]["id"]]
    assert record["state"] == "open"
    assert record.get("deadline_seconds") is None

    # An operator may still record an intended horizon; it is bounds-checked
    # and stored, and it still does not end the link.
    with_horizon = operator.open_agent_link(
        from_agent_id=reviewer["entity_ref"]["id"],
        to_agent_id=author["entity_ref"]["id"],
        allowed_action="ask",
        deadline_seconds=3600,
        reason="ask the author back",
        idempotency_key="link-with-horizon",
    )
    stored = operator.snapshot().agent_links[with_horizon["entity_ref"]["id"]]
    assert stored["deadline_seconds"] == 3600
    assert stored["state"] == "open"
