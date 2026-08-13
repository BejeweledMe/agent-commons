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


def test_the_attention_queue_shows_a_canonical_blocker_with_no_live_operation(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """H4: one source for 'waiting on you', and it agrees with the ring.

    An open decision-request thread lights the amber ring and the footer count,
    but has no entry in the operational communication store.  The old Blocked
    tab read only that store, so it was empty while the graph glowed and then
    hid itself.  The attention queue is canonical, so the blocker appears there
    and in awaiting_human from the same source.
    """

    thread = owner_session.open_thread(
        thread_type="decision_request",
        subject="Which region hosts the primary?",
        desired_outcome="one region named",
        to=("operator",),
        idempotency_key="attention-thread",
    )
    thread_id = thread["entity_ref"]["id"]

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(owner_session.session_id),
    )
    with _client(context) as client:
        operations = client.get("/api/operations", headers=authorized()).json()
        attention = client.get("/api/attention", headers=authorized()).json()

    # No live operation exists for a plain decision thread, so the old Blocked
    # tab (which read only this store) was empty...
    assert operations == []
    # ...yet the attention queue lists the thread, from canonical state.
    attention_ids = {item["id"] for item in attention["items"]}
    assert thread_id in attention_ids
    assert attention["count"] >= 1

    # And the ring is lit from that same source: the graph shows the thread's
    # session waiting on a person.  The list and the ring agree now -- both
    # non-empty -- where the operational store left one empty and one glowing.
    graph = context.rebuild_graph()
    assert graph["awaiting_human"], "the canonical ring is dark while the attention queue is not"


def test_the_attention_queue_carries_a_role_proposal(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """Blocked and Proposals merged into one queue: a proposal is an attention
    item, confirmable in place, rather than a second hidden tab."""

    role = owner_session.create_agent(
        name="Proposer",
        profile_id="claude-builder",
        grants={"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
        turnover_budget=4,
        rationale="a role that may ask for staff",
        idempotency_key="attn-role",
    )
    task = owner_session.create_task(
        title="Work",
        description="binds a run",
        acceptance_criteria=("d",),
        idempotency_key="attn-t",
    )
    delegation = owner_session.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits={**LIMITS, "max_concurrency": 1},
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="attn-d",
    )
    worker_session = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    child = worker_session.start_session(
        stable_instance_id="attn-worker-window-0001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="builder",
    )
    owner_session.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child["session_id"],
        idempotency_key="attn-start",
    )
    worker = CommonsManager(
        workspace["repo"], session_id=child["session_id"], state_root=workspace["state_root"]
    )
    worker.propose_agent(
        name="Requested helper",
        profile_id="claude-builder",
        rationale="please hire this",
        idempotency_key="attn-proposal",
    )

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(owner_session.session_id),
    )
    with _client(context) as client:
        attention = client.get("/api/attention", headers=authorized()).json()

    proposals = [item for item in attention["items"] if item["kind"] == "proposal"]
    assert len(proposals) == 1
    assert proposals[0]["proposal"]["name"] == "Requested helper"


def test_a_directive_the_operator_sends_a_role_does_not_inflate_their_own_queue(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """Round 2: messaging a role opens a decision_request addressed to that role.
    It waits on the role, not the operator, so it must not appear in the human
    attention queue nor ring the operator's own session."""

    role = owner_session.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="owns the surface",
        idempotency_key="dir-role",
    )
    agent_id = role["entity_ref"]["id"]
    # The human directs the role — a decision_request addressed to the role.
    owner_session.open_thread(
        thread_type="decision_request",
        subject="start with payments",
        desired_outcome="the role acts on this direction",
        to=(agent_id,),
        related_refs=({"kind": "agent", "id": agent_id},),
        idempotency_key="dir-thread",
    )

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(owner_session.session_id),
    )
    with _client(context) as client:
        attention = client.get("/api/attention", headers=authorized()).json()
    assert attention["count"] == 0, attention
    graph = context.rebuild_graph()
    assert graph["awaiting_human"] == []


def test_a_role_asking_the_operator_still_appears_in_the_queue(
    workspace: dict[str, Any], owner_session: CommonsManager
) -> None:
    """The other direction: a thread addressed to the operator does wait on them."""

    owner_session.open_thread(
        thread_type="question",
        subject="which region hosts the primary?",
        desired_outcome="one region named",
        to=("operator",),
        idempotency_key="ask-operator",
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(owner_session.session_id),
    )
    with _client(context) as client:
        attention = client.get("/api/attention", headers=authorized()).json()
    assert attention["count"] == 1


def test_a_role_holding_a_vanished_skill_is_flagged_before_any_run(
    workspace: dict[str, Any], tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Wave 1 item 8: the catalogue is an operator file and can be edited by
    hand, outside the panel. A role granted a skill the catalogue no longer
    defines will fail its NEXT launch fail-closed — the attention queue says
    so first, naming the role and the missing ids."""

    from pathlib import Path

    from agent_commons.catalog import write_role_catalog

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="preflight-window-0001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    manager.session_id = session["session_id"]
    catalog_file = Path(tmp_path) / "catalog.yaml"
    write_role_catalog(
        catalog_file,
        {
            "skills": [{"id": "pytest-runner", "title": "Pytest", "instruction": "run tests"}],
            "tools": [],
        },
    )
    manager.create_agent(
        name="Backend owner",
        profile_id="claude-builder",
        rationale="owns the surface",
        skills=("pytest-runner",),
        idempotency_key="preflight-role",
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        catalog_path=catalog_file,
    )
    assert not [
        item for item in context.attention()["items"] if item["kind"] == "config_broken"
    ]

    # The operator edits the file by hand and drops the skill.
    write_role_catalog(catalog_file, {"skills": [], "tools": []})

    broken = [
        item for item in context.attention()["items"] if item["kind"] == "config_broken"
    ]
    assert len(broken) == 1
    assert broken[0]["missing_skills"] == ["pytest-runner"]
    assert broken[0]["name"] == "Backend owner"
