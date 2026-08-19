"""The panel can now close the loop: send for review, then accept or send back.

Both round-3 cold-run testers stopped at the same wall -- a run reaches
`succeeded` and nothing anywhere accepts the work.  These tests drive the whole
chain over HTTP, and the most important one is the refusal: accepting straight
after sending for review must fail, because a panel that rubber-stamps its own
request would delete the one property both testers praised.
"""

from __future__ import annotations

from typing import Any

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from tests.ui.conftest import authorized

_LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "provider_units", "limit": 1},
}


def create_task(client, **overrides: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = {
        "title": "Собрать страницу-визитку",
        "description": "one page, one address block",
        "acceptance_criteria": ["the page loads and shows the address"],
        **overrides,
    }
    response = client.post("/api/tasks", json=body, headers=authorized())
    assert response.status_code == 200, response.text
    return {"id": response.json()["entity_ref"]["id"], "revision": response.json()["revision"]}


def send_for_review(client, task_id: str, revision: str, **body: Any):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/tasks/{task_id}/review-request",
        json={"expected_revision": revision, **body},
        headers=authorized(),
    )


def approve_independently(
    workspace: dict[str, Any],
    *,
    review_id: str,
    review_revision: str,
    target_revision: str,
) -> dict[str, Any]:
    """Answer the open review from a second session.

    Independence is judged by principal, so the verdict has to come from a
    session the operator does not hold: this is the reviewer a real workspace
    would launch, standing in for one.
    """

    opener = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = opener.start_session(
        stable_instance_id="ui-reviewer-window-0001",
        principal="independent-reviewer",
        client="claude",
        software="claude-code",
        role="independent-reviewer",
    )
    reviewer = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(session["session_id"]),
    )
    return reviewer.complete_review(
        review_id,
        review_revision,
        target_revision=target_revision,
        verdict="approved",
        summary="the page loads and the address is on it",
    )


def accept_through_a_real_review(
    client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
    task: dict[str, Any],
):  # type: ignore[no-untyped-def]
    """The whole chain, end to end, exactly as the panel walks it."""

    sent = send_for_review(client, task["id"], task["revision"])
    assert sent.status_code == 200, sent.text
    chain = sent.json()
    approve_independently(
        workspace,
        review_id=chain["review_id"],
        review_revision=chain["review_revision"],
        target_revision=chain["task_revision"],
    )
    return client.post(
        f"/api/tasks/{task['id']}/accept",
        json={"expected_revision": chain["task_revision"], "summary": "принято, страница живая"},
        headers=authorized(),
    )


def test_a_fresh_task_walks_to_review_and_opens_an_independent_request(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    task = create_task(writable_client)
    sent = send_for_review(writable_client, task["id"], task["revision"])
    assert sent.status_code == 200, sent.text
    chain = sent.json()

    # Every step is a manager transition, and the route says which ones it ran.
    assert chain["steps"] == ["start_task", "complete_task", "submit_task", "request_review"]
    assert chain["task_state"] == "review"

    shown = writable_client.get(f"/api/entities/task/{task['id']}", headers=authorized())
    assert shown.status_code == 200
    assert shown.json()["record"]["state"] == "review"

    review = writable_client.get(f"/api/entities/review/{chain['review_id']}", headers=authorized())
    assert review.status_code == 200, review.text
    record = review.json()["record"]
    assert record["state"] == "requested"
    assert record["independent"] is True
    assert record["target_ref"] == {"kind": "task", "id": task["id"]}
    # Bound to the revision the walk produced, not the one the drawer held.
    assert record["target_revision"] == chain["task_revision"]
    assert record["criteria"] == ["the page loads and shows the address"]


def test_revising_a_task_marks_the_old_review_stale_and_blocks_acceptance(
    writable_client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
) -> None:
    task = create_task(writable_client)
    sent = send_for_review(writable_client, task["id"], task["revision"])
    assert sent.status_code == 200, sent.text
    chain = sent.json()
    approve_independently(
        workspace,
        review_id=chain["review_id"],
        review_revision=chain["review_revision"],
        target_revision=chain["task_revision"],
    )

    revised = writable_client.post(
        f"/api/tasks/{task['id']}/revise",
        json={
            "expected_revision": chain["task_revision"],
            "changes": {
                "description": "corrected page copy",
                "acceptance_criteria": ["the corrected copy is visible"],
            },
        },
        headers=authorized(),
    )
    assert revised.status_code == 200, revised.text
    revised_revision = revised.json()["revision"]
    assert revised_revision != chain["task_revision"]

    shown_review = writable_client.get(
        f"/api/entities/review/{chain['review_id']}", headers=authorized()
    )
    assert shown_review.status_code == 200, shown_review.text
    assert shown_review.json()["record"]["stale"] is True
    refused = writable_client.post(
        f"/api/tasks/{task['id']}/accept",
        json={"expected_revision": revised_revision, "summary": "accept stale verdict"},
        headers=authorized(),
    )
    assert refused.status_code == 409, refused.text
    assert "current approved independent review" in refused.json()["error"]["message"]


def test_a_task_with_no_criteria_still_gets_something_to_judge_against(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    task = create_task(writable_client, acceptance_criteria=["-"])
    sent = send_for_review(writable_client, task["id"], task["revision"], criteria=[])
    assert sent.status_code == 200, sent.text
    review = writable_client.get(
        f"/api/entities/review/{sent.json()['review_id']}", headers=authorized()
    )
    assert review.json()["record"]["criteria"] == ["-"]


def test_accepting_straight_after_the_request_is_refused(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    """The property the whole design exists to protect.

    The operator requested this review, so the operator cannot answer it; until
    somebody independent does, acceptance is not a thing the panel can do.
    """

    task = create_task(writable_client)
    chain = send_for_review(writable_client, task["id"], task["revision"]).json()

    refused = writable_client.post(
        f"/api/tasks/{task['id']}/accept",
        json={"expected_revision": chain["task_revision"], "summary": "выглядит нормально"},
        headers=authorized(),
    )
    assert refused.status_code == 409, refused.text
    message = refused.json()["error"]["message"]
    assert "approved independent review" in message

    shown = writable_client.get(f"/api/entities/task/{task['id']}", headers=authorized())
    assert shown.json()["record"]["state"] == "review"


def test_an_acceptance_needs_a_summary(writable_client) -> None:  # type: ignore[no-untyped-def]
    task = create_task(writable_client)
    chain = send_for_review(writable_client, task["id"], task["revision"]).json()
    refused = writable_client.post(
        f"/api/tasks/{task['id']}/accept",
        json={"expected_revision": chain["task_revision"], "summary": "   "},
        headers=authorized(),
    )
    assert refused.status_code == 409, refused.text
    assert "summary" in refused.json()["error"]["message"]


def test_an_approved_independent_review_lets_the_acceptance_land(
    writable_client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
) -> None:
    task = create_task(writable_client)
    accepted = accept_through_a_real_review(writable_client, workspace, task)
    assert accepted.status_code == 200, accepted.text

    shown = writable_client.get(f"/api/entities/task/{task['id']}", headers=authorized())
    assert shown.json()["record"]["state"] == "accepted"


def test_sending_the_work_back_takes_the_task_out_of_review(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    task = create_task(writable_client)
    chain = send_for_review(writable_client, task["id"], task["revision"]).json()

    blank = writable_client.post(
        f"/api/tasks/{task['id']}/reopen",
        json={"expected_revision": chain["task_revision"], "reason": ""},
        headers=authorized(),
    )
    assert blank.status_code == 409, blank.text
    assert "reason" in blank.json()["error"]["message"]

    sent_back = writable_client.post(
        f"/api/tasks/{task['id']}/reopen",
        json={
            "expected_revision": chain["task_revision"],
            "reason": "на странице нет адреса",
        },
        headers=authorized(),
    )
    assert sent_back.status_code == 200, sent_back.text

    shown = writable_client.get(f"/api/entities/task/{task['id']}", headers=authorized())
    record = shown.json()["record"]
    assert record["state"] != "review"
    assert record["state"] == "ready"


def test_a_blocked_task_is_refused_in_words_a_person_can_act_on(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    task = create_task(writable_client)
    manager = writable.writer()
    started = manager.start_task(task["id"], task["revision"])
    manager.block_task(task["id"], started["revision"], reason="the address is unknown")

    blocked = writable.manager().snapshot().tasks[task["id"]]
    refused = send_for_review(writable_client, task["id"], str(blocked["revision"]))
    assert refused.status_code == 409, refused.text
    assert "blocked" in refused.json()["error"]["message"]


def test_an_accepted_task_has_nothing_left_to_send_for_review(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
    workspace: dict[str, Any],
) -> None:
    task = create_task(writable_client)
    accepted = accept_through_a_real_review(writable_client, workspace, task)
    assert accepted.status_code == 200, accepted.text

    current = writable.manager().snapshot().tasks[task["id"]]
    refused = send_for_review(writable_client, task["id"], str(current["revision"]))
    assert refused.status_code == 409, refused.text
    assert "nothing to send for review" in refused.json()["error"]["message"]


def test_a_stale_drawer_is_refused_rather_than_silently_overwritten(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    task = create_task(writable_client)
    first = send_for_review(writable_client, task["id"], task["revision"])
    assert first.status_code == 200, first.text

    # The drawer still holds the revision the task had when it was created.
    stale = send_for_review(writable_client, task["id"], task["revision"])
    assert stale.status_code == 409, stale.text
    assert "stale expected revision" in stale.json()["error"]["message"]


def _run_succeeded_against(
    workspace: dict[str, Any], writable: UIContext, task_id: str
) -> dict[str, str]:
    """A role's run that finished against this task, built the canonical way."""

    manager = writable.writer()
    role = manager.create_agent(
        name="Верстальщик",
        profile_id="claude-builder",
        rationale="owns the landing page surface",
    )
    agent_id = str(role["entity_ref"]["id"])
    task = manager.snapshot().tasks[task_id]
    delegation = manager.create_delegation(
        target_ref={"kind": "task", "id": task_id},
        target_revision=str(task.get("effective_revision") or task["revision"]),
        target_profile="claude-builder",
        purpose="implementation",
        limits=_LIMITS,
        on_behalf_of_agent_id=agent_id,
    )
    delegation_id = str(delegation["entity_ref"]["id"])
    opener = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    worker_session = opener.start_session(
        stable_instance_id="ui-worker-window-0001",
        principal="worker",
        client="claude",
        software="claude-code",
        role="implementation-author",
    )
    started = manager.start_delegation(
        delegation_id,
        delegation["revision"],
        child_session_id=str(worker_session["session_id"]),
    )
    child = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(worker_session["session_id"]),
    )
    child.succeed_delegation(
        delegation_id,
        started["revision"],
        summary="the page is up",
        result_refs=({"kind": "task", "id": task_id},),
    )
    return {"agent_id": agent_id, "delegation_id": delegation_id}


def test_finished_work_waits_in_attention_until_somebody_accepts_it(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
    workspace: dict[str, Any],
) -> None:
    """Round-3 finding 3: a succeeded run left the queue empty and the loop
    looked closed while the work was still waiting on a person."""

    task = create_task(writable_client)
    run = _run_succeeded_against(workspace, writable, task["id"])

    queue = writable_client.get("/api/attention", headers=authorized()).json()
    returned = [item for item in queue["items"] if item["kind"] == "work_returned"]
    assert len(returned) == 1, queue
    item = returned[0]
    assert item["id"] == task["id"]
    assert item["task_id"] == task["id"]
    assert item["title"] == "Собрать страницу-визитку"
    assert item["task_state"] == "ready"
    assert item["delegation_id"] == run["delegation_id"]
    assert item["agent_id"] == run["agent_id"]
    assert item["agent_name"] == "Верстальщик"
    # The footer count and the amber ring read this same list, so the item has
    # to be inside the count, not beside it.
    assert queue["count"] == len(queue["items"])
    graph = writable_client.get("/api/graph", headers=authorized()).json()
    waiting = set(graph["awaiting_human"])
    assert {task["id"], run["delegation_id"], run["agent_id"]} <= waiting

    accepted = accept_through_a_real_review(writable_client, workspace, task)
    assert accepted.status_code == 200, accepted.text

    after = writable_client.get("/api/attention", headers=authorized()).json()
    assert [item for item in after["items"] if item["kind"] == "work_returned"] == []


def test_the_ledger_says_which_of_the_two_claims_this_was(
    writable_client,
    workspace: dict[str, Any],  # type: ignore[no-untyped-def]
) -> None:
    """`complete_task` asserts the work is done, and there are two ways to
    arrive at it: a run finished, or the operator judged it done by hand. Both
    are legitimate — an operator may have written the code themselves — but they
    are not the same claim, so the summary the ledger keeps says which one it
    was rather than implying a run that never happened."""

    from agent_commons.services import CommonsManager

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])

    unrun = create_task(writable_client, title="Никто не запускался")
    send_for_review(writable_client, unrun["id"], unrun["revision"])
    said = [
        str(record.get("summary", ""))
        for record in manager.snapshot().tasks.values()
        if record["id"] == unrun["id"]
    ]
    assert said and "no run had finished" in said[0], said
    assert "judged this work done" in said[0]


def test_a_language_switch_keeps_a_half_typed_acceptance(  # noqa: D401
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    """The panel's own rule, asserted on the surface table: a repaint driven by
    the language picker may rewrite labels but must never take an operator's
    half-finished input with it. `paintTaskAcceptance` used to close the confirm
    unconditionally, which hid a typed summary and then wiped it."""

    from agent_commons.ui import read_spa

    body = read_spa()
    table = body.split("const LANGUAGE_SURFACES", 1)[1].split("];", 1)[0]
    assert "paintTaskAcceptance" in table, "the acceptance surface must repaint on a switch"
    guard = body.split("function paintTaskAcceptance", 1)[1].split("\n}", 1)[0]
    # The reset is conditional on the task actually changing.
    assert "sameTask" in guard, guard[:400]
    assert "setAcceptanceMode(null)" not in guard.replace(
        "setAcceptanceMode(sameTask ? acceptanceMode : null)", ""
    )


def test_the_task_sent_for_review_is_still_pickable_for_a_reviewer(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    """The panel tells the operator to run an independent reviewer against a
    task it has just moved to `review`. A vibecoder followed that instruction
    and hit "no such task": the launch picker offered only open states and
    dropped the task the moment it was sent. The instruction and the picker
    have to agree, or the chain has a step nobody can take."""

    task = create_task(writable_client)
    chain = send_for_review(writable_client, task["id"], task["revision"]).json()
    assert chain["task_state"] == "review"

    options = writable_client.get("/api/launch", headers=authorized()).json()
    offered = {item["id"]: item["state"] for item in options["tasks"]}
    assert task["id"] in offered, offered
    assert offered[task["id"]] == "review"
