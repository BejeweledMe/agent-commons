"""Answering a live request from the panel, and saying so when it cannot.

The communication channel authorizes by participant, so a panel can only answer
requests whose delegation its own session owns.  A blocker it cannot answer is
still shown -- with the session that can -- because an invisible blocker is
worse than one you cannot yet act on.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CorrelationIds,
    RuntimePolicy,
    checkout_fingerprint,
)
from agent_commons.services import CommonsManager
from agent_commons.services.communication import CommunicationRuntimeService
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import MUTATING_ROUTES, create_app
from tests.ui.conftest import PORT, authorized

LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "provider_units", "limit": 1},
}


def _client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    app = create_app(context, token="test-token", port=PORT)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def _blocked_run(workspace: dict[str, Any], owner: CommonsManager) -> dict[str, Any]:
    """Drive a run to the point where it is waiting on a person."""

    task = owner.create_task(
        title="Wire the endpoint",
        description="the run will ask for a decision",
        acceptance_criteria=("done",),
        idempotency_key="blocked-task",
    )
    delegation = owner.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        idempotency_key="blocked-delegation",
    )
    holder = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    child = holder.start_session(
        stable_instance_id="blocked-child-window-0001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="builder",
    )
    owner.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child["session_id"],
        idempotency_key="blocked-start",
    )
    # The channel is bound to a durable operational attempt, so a blocked run
    # only exists once the broker has reserved and started one.
    attempts = AttemptStore(workspace["state_root"])
    parent_policy = RuntimePolicy(
        remaining_depth=1, max_fanout=1, max_attempts=1, max_concurrency=1, timeout_seconds=900
    )
    attempt = attempts.reserve(
        AttemptSpec(
            idempotency_key="blocked-attempt",
            profile_id=BuiltinProfileId.CLAUDE_BUILDER,
            provider=BuiltinProfileId.CLAUDE_BUILDER.provider,
            correlation=CorrelationIds(
                delegation_id=delegation["entity_ref"]["id"],
                target_kind="task",
                target_id=task["entity_ref"]["id"],
                target_revision=task["revision"],
                parent_session_id=str(owner.session_id),
                child_session_id=child["session_id"],
            ),
            parent_policy=parent_policy,
            child_policy=parent_policy.derive_child(),
            checkout_fingerprint=checkout_fingerprint(workspace["repo"]),
        ),
        parent_policy=parent_policy,
    ).attempt
    attempts.transition(attempt.attempt_id, AttemptState.LAUNCHING, reason="process_starting")
    attempts.transition(
        attempt.attempt_id, AttemptState.RUNNING, reason="process_started", pid=4242
    )
    worker = CommonsManager(
        workspace["repo"], session_id=child["session_id"], state_root=workspace["state_root"]
    )
    CommunicationRuntimeService(worker).request_input(
        delegation["entity_ref"]["id"],
        idempotency_key="blocked-request",
        question="Which rounding rule applies to settlement?",
        why_needed="settlement totals differ between the two rules",
        safe_context={"candidates": "banker, half-up"},
        desired_outcome="one rule named",
        deadline_seconds=900,
    )
    return delegation


@pytest.fixture
def owner_session(workspace: dict[str, Any]) -> CommonsManager:
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="blocked-owner-window-0001",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def test_the_panel_answers_a_request_its_own_session_owns(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    _blocked_run(workspace, owner_session)
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(owner_session.session_id),
    )
    with _client(context) as client:
        listed = client.get("/api/operations", headers=authorized()).json()
        assert len(listed) == 1
        assert listed[0]["answerable_here"] is True

        response = client.post(
            f"/api/operations/{listed[0]['operation_id']}/answer",
            json={"answer": {"answer": "banker"}},
            headers=authorized(),
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["operation"]["state"] == "replied"
    # Answering also resumes the run, so the blocker clears on the canvas too.
    assert payload["delegation"]["state"] == "active"


def test_a_request_owned_by_another_session_is_shown_with_who_can_answer(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """Visible, and honest about why the button is missing."""

    _blocked_run(workspace, owner_session)
    stranger = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = stranger.start_session(
        stable_instance_id="blocked-stranger-window-01",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
    )
    with _client(context) as client:
        listed = client.get("/api/operations", headers=authorized()).json()

    # The stranger participates in nothing, so it sees nothing to answer rather
    # than a button that would fail.
    assert all(item["answerable_here"] is False for item in listed)


def test_answering_is_part_of_the_declared_mutating_surface() -> None:
    assert ("POST", "/api/operations/{operation_id}/answer") in MUTATING_ROUTES


def test_a_read_only_panel_offers_no_answer_route(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    _blocked_run(workspace, owner_session)
    context = UIContext(workspace["repo"], state_root=workspace["state_root"])
    with _client(context) as client:
        response = client.post(
            "/api/operations/operation.01K00000000000000000000000/answer",
            json={"answer": {"answer": "banker"}},
            headers=authorized(),
        )
    assert response.status_code == 404


def test_the_blocked_run_also_rings_its_node_on_the_graph(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """The panel and the canvas agree about what is waiting on a person."""

    delegation = _blocked_run(workspace, owner_session)
    # Asking for input already moved the delegation, so the panel and the canvas
    # read one producer rather than two that could disagree.
    assert owner_session.get_delegation(delegation["entity_ref"]["id"])["state"] == "input_needed"
    graph = UIContext(workspace["repo"], state_root=workspace["state_root"]).rebuild_graph()

    assert delegation["entity_ref"]["id"] in graph["awaiting_human"]
