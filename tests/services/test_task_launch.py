from __future__ import annotations

import pytest

from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
from agent_commons.services import CommonsManager
from agent_commons.services.task_launch import create_task_delegation


@pytest.fixture
def launch_context(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=())
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="launch-owner",
        principal="owner",
        client="codex",
        software="tests",
        role="operator",
    )
    manager.session_id = session["session_id"]
    task = manager.create_task(
        title="Ready work", description="Launch from UI", acceptance_criteria=("Publish report",)
    )
    request = {
        "target_ref": task["entity_ref"],
        "target_revision": task["revision"],
        "target_profile": "codex-builder",
        "purpose": "implementation",
        "limits": {
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        "on_behalf_of_agent_id": None,
        "idempotency_key": "ready-task-launch-v1",
    }
    return manager, task, request


def test_ready_activation_binds_active_revision_and_preserves_original_replay(launch_context):
    manager, task, request = launch_context
    first = create_task_delegation(manager, **request)
    active = manager.snapshot().tasks[task["entity_ref"]["id"]]
    run = manager.get_delegation(first["entity_ref"]["id"])
    assert active["state"] == "active"
    assert run["target_revision"] == active["revision"] != task["revision"]
    assert create_task_delegation(manager, **request)["revision"] == first["revision"]
    manager.complete_task(active["id"], active["revision"], summary="Finished")
    assert create_task_delegation(manager, **request)["revision"] == first["revision"]
    assert len(manager.snapshot().delegations) == 1
    with pytest.raises(IdempotencyConflictError):
        create_task_delegation(
            manager, **{**request, "limits": {**request["limits"], "wall_time_seconds": 90}}
        )


@pytest.mark.parametrize("after_write", [False, True])
def test_launch_recovers_after_start_or_delegation_write(launch_context, monkeypatch, after_write):
    manager, task, request = launch_context
    original = manager.create_delegation

    def crash(**kwargs):
        if after_write:
            original(**kwargs)
        raise OSError("synthetic launch response interruption")

    monkeypatch.setattr(manager, "create_delegation", crash)
    with pytest.raises(OSError):
        create_task_delegation(manager, **request)
    active = manager.snapshot().tasks[task["entity_ref"]["id"]]
    assert active["state"] == "active"
    monkeypatch.setattr(manager, "create_delegation", original)
    for revision in (request["target_revision"], active["revision"]):
        create_task_delegation(manager, **{**request, "target_revision": revision})
    assert len(manager.snapshot().delegations) == 1
    assert (
        len(
            [
                record
                for record in manager.events.iter_events()
                if record.event["event_type"] == "task.started"
            ]
        )
        == 1
    )


def test_partial_activation_refuses_changed_arguments_or_unrelated_task_edits(
    launch_context, monkeypatch
):
    manager, task, request = launch_context
    original = manager.create_delegation
    monkeypatch.setattr(
        manager, "create_delegation", lambda **kwargs: (_ for _ in ()).throw(OSError("stop"))
    )
    with pytest.raises(OSError):
        create_task_delegation(manager, **request)
    monkeypatch.setattr(manager, "create_delegation", original)
    with pytest.raises(IdempotencyConflictError):
        create_task_delegation(
            manager, **{**request, "limits": {**request["limits"], "wall_time_seconds": 90}}
        )
    with pytest.raises(IdempotencyConflictError):
        create_task_delegation(manager, **{**request, "purpose": "verification"})
    active = manager.snapshot().tasks[task["entity_ref"]["id"]]
    edited = manager.revise_task(
        active["id"], active["revision"], changes={"title": "Unrelated edit"}
    )
    for revision in (request["target_revision"], edited["revision"]):
        with pytest.raises(LifecycleConflictError, match="changed after launch"):
            create_task_delegation(manager, **{**request, "target_revision": revision})
    assert not manager.snapshot().delegations


def test_assigned_task_cannot_be_taken_over_by_another_session(launch_context):
    manager, task, request = launch_context
    assigned = manager.take_task(task["entity_ref"]["id"], task["revision"])
    other = manager.start_session(
        stable_instance_id="different-launch-owner",
        principal="other",
        client="codex",
        software="tests",
        role="operator",
    )
    manager.session_id = other["session_id"]
    with pytest.raises(LifecycleConflictError, match="task owner"):
        create_task_delegation(manager, **{**request, "target_revision": assigned["revision"]})
    assert manager.snapshot().tasks[task["entity_ref"]["id"]]["state"] == "assigned"
    assert not manager.snapshot().delegations


@pytest.mark.parametrize("state", ["blocked", "completed", "review", "cancelled", "accepted"])
def test_launch_never_activates_finished_or_blocked_work(launch_context, state):
    manager, task, request = launch_context
    changed = manager.start_task(task["entity_ref"]["id"], task["revision"])
    identifier = task["entity_ref"]["id"]
    if state == "blocked":
        changed = manager.block_task(identifier, changed["revision"], reason="Paused")
    elif state == "cancelled":
        changed = manager.cancel_task(identifier, changed["revision"], reason="Cancelled")
    else:
        changed = manager.complete_task(identifier, changed["revision"], summary="Done")
        if state in {"review", "accepted"}:
            changed = manager.submit_task(identifier, changed["revision"], summary="Review")
        if state == "accepted":
            owner = manager.session_id
            review = manager.request_review(
                target_ref=task["entity_ref"],
                target_revision=changed["revision"],
                criteria=("Check",),
            )
            session = manager.start_session(
                stable_instance_id="independent-review",
                principal="reviewer",
                client="claude",
                software="tests",
                role="reviewer",
            )
            manager.session_id = session["session_id"]
            manager.complete_review(
                review["entity_ref"]["id"],
                review["revision"],
                target_revision=changed["revision"],
                verdict="approved",
                summary="Checked",
            )
            manager.session_id = owner
            changed = manager.accept_task(identifier, changed["revision"], summary="Accepted")
    with pytest.raises(LifecycleConflictError, match="requires ready"):
        create_task_delegation(manager, **{**request, "target_revision": changed["revision"]})
    assert manager.snapshot().tasks[identifier]["state"] == state
    assert not manager.snapshot().delegations


def test_assigned_owner_can_activate_but_stale_ready_revision_cannot(launch_context):
    manager, task, request = launch_context
    assigned = manager.take_task(task["entity_ref"]["id"], task["revision"])
    with pytest.raises(LifecycleConflictError, match="changed before"):
        create_task_delegation(manager, **request)
    run = create_task_delegation(manager, **{**request, "target_revision": assigned["revision"]})
    active = manager.snapshot().tasks[task["entity_ref"]["id"]]
    assert active["owner_session_id"] == manager.session_id
    assert manager.get_delegation(run["entity_ref"]["id"])["target_revision"] == active["revision"]


def test_concurrent_ui_retries_create_one_activation_and_delegation(launch_context):
    from concurrent.futures import ThreadPoolExecutor

    manager, _, request = launch_context
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: create_task_delegation(manager, **request), range(2)))
    assert receipts[0]["revision"] == receipts[1]["revision"]
    assert len(manager.snapshot().delegations) == 1
    assert (
        len(
            [
                record
                for record in manager.events.iter_events()
                if record.event["event_type"] == "task.started"
            ]
        )
        == 1
    )
