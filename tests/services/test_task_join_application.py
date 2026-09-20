from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.services import CommonsManager


def _manager(tmp_path: Path, *, capabilities: tuple[str, ...] = ()) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    state_root = tmp_path / "state"
    CommonsManager.initialize(repo, integrations=())
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id="application-join-test-window",
        principal="operator",
        client="codex",
        software="pytest",
        role="operator",
        capabilities=capabilities,
    )
    manager.session_id = session["session_id"]
    return manager


def _task(manager: CommonsManager, key: str, *, objective_id: str | None = None) -> dict:
    return manager.create_task(
        title="Join a blueprint application",
        description="Record durable provenance.",
        acceptance_criteria=("The application is auditable.",),
        objective_id=objective_id,
        idempotency_key=key,
    )


def _application(manager: CommonsManager, task_id: str, *, objective_id: str | None = None) -> dict:
    application_id = "application." + ("a" * 64)
    return manager.record_event(
        "blueprint_application.created",
        {
            "application_id": application_id,
            "blueprint": {"id": "test", "version": "1", "revision": "1"},
            "objective_id": objective_id,
            "created_task_ids": [task_id],
            "created_agent_ids": [],
            "created_role_refs": [],
        },
        idempotency_key="application-created",
    )


def test_join_application_requires_explicit_capability_before_writing(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    initial = _task(manager, "initial")
    application = _application(manager, initial["entity_ref"]["id"])
    joining = _task(manager, "joining")
    before = list(manager.events.iter_events())

    with pytest.raises(LifecycleConflictError, match="required capability"):
        manager.join_task_to_application(
            joining["entity_ref"]["id"],
            application["entity_ref"]["id"],
            application["event_id"],
            reason="The task belongs to this delivery.",
            idempotency_key="join-without-capability",
        )

    assert list(manager.events.iter_events()) == before


def test_join_application_records_membership_for_authorized_operator(tmp_path: Path) -> None:
    manager = _manager(tmp_path, capabilities=("task:join_application",))
    initial = _task(manager, "initial")
    application = _application(manager, initial["entity_ref"]["id"])
    joining = _task(manager, "joining")

    joined = manager.join_task_to_application(
        joining["entity_ref"]["id"],
        application["entity_ref"]["id"],
        application["event_id"],
        reason="The task belongs to this delivery.",
        idempotency_key="join-authorized",
    )

    snapshot = manager.snapshot()
    assert joined["entity_ref"] == {
        "kind": "blueprint_application",
        "id": application["entity_ref"]["id"],
    }
    assert (
        snapshot.tasks[joining["entity_ref"]["id"]]["application_id"]
        == application["entity_ref"]["id"]
    )


def _join(manager, task_id: str, application: dict, *, reason: str, key: str, revision=None):
    return manager.join_task_to_application(
        task_id,
        application["entity_ref"]["id"],
        revision if revision is not None else application["event_id"],
        reason=reason,
        idempotency_key=key,
    )


def test_join_refuses_every_unsafe_branch_without_appending_an_event(tmp_path: Path) -> None:
    manager = _manager(tmp_path, capabilities=("task:join_application",))
    created = _task(manager, "created")
    application = _application(manager, created["entity_ref"]["id"])
    joined = _task(manager, "joined")
    _join(manager, joined["entity_ref"]["id"], application, reason="First join.", key="first")
    current = manager.snapshot().applications[application["entity_ref"]["id"]]["revision"]

    outsider = _task(manager, "outsider")
    before = list(manager.events.iter_events())

    # Already a member of this application.
    with pytest.raises(LifecycleConflictError):
        _join(
            manager,
            joined["entity_ref"]["id"],
            application,
            reason="Joining twice.",
            key="again",
            revision=current,
        )
    # The application revision moved since the caller read it.
    with pytest.raises(LifecycleConflictError):
        _join(
            manager,
            outsider["entity_ref"]["id"],
            application,
            reason="Stale revision.",
            key="stale",
        )
    # An audit reason is not optional.
    with pytest.raises((LifecycleConflictError, ValidationError)):
        _join(
            manager,
            outsider["entity_ref"]["id"],
            application,
            reason="   ",
            key="empty-reason",
            revision=current,
        )

    assert list(manager.events.iter_events()) == before


def test_join_refuses_a_task_that_carries_another_objective(tmp_path: Path) -> None:
    manager = _manager(tmp_path, capabilities=("task:join_application",))
    first = manager.create_objective(
        title="The application objective",
        description="What the application is for.",
        acceptance_criteria=("It is reachable from its tasks.",),
        idempotency_key="objective-one",
    )
    second = manager.create_objective(
        title="Another objective",
        description="Something else entirely.",
        acceptance_criteria=("It stays separate.",),
        idempotency_key="objective-two",
    )
    created = _task(manager, "created", objective_id=first["entity_ref"]["id"])
    application = _application(
        manager, created["entity_ref"]["id"], objective_id=first["entity_ref"]["id"]
    )
    foreign = _task(manager, "foreign", objective_id=second["entity_ref"]["id"])
    before = list(manager.events.iter_events())

    with pytest.raises(LifecycleConflictError):
        _join(
            manager,
            foreign["entity_ref"]["id"],
            application,
            reason="A task of another objective.",
            key="join-foreign",
        )

    assert list(manager.events.iter_events()) == before


def test_join_refuses_beyond_the_hundred_task_ceiling(tmp_path: Path) -> None:
    manager = _manager(tmp_path, capabilities=("task:join_application",))
    seed = _task(manager, "seed")
    application_id = "application." + ("e" * 64)
    application = manager.record_event(
        "blueprint_application.created",
        {
            "application_id": application_id,
            "blueprint": {"id": "test", "version": "1", "revision": "1"},
            "objective_id": None,
            "created_task_ids": [seed["entity_ref"]["id"]]
            + [f"task.{'0' * 20}{index:06d}" for index in range(99)],
            "created_agent_ids": [],
            "created_role_refs": [],
        },
        idempotency_key="full-application",
    )
    overflow = _task(manager, "overflow")
    before = list(manager.events.iter_events())

    with pytest.raises(LifecycleConflictError):
        _join(
            manager,
            overflow["entity_ref"]["id"],
            application,
            reason="One task too many.",
            key="join-overflow",
        )

    assert list(manager.events.iter_events()) == before
