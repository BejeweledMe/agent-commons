"""A ledger written before provenance replays unchanged (ADR 0021)."""

from __future__ import annotations

from pathlib import Path

from agent_commons.services import CommonsManager


def _manager(tmp_path: Path, name: str) -> CommonsManager:
    repo = tmp_path / name
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=())
    manager = CommonsManager(repo, state_root=tmp_path / f"{name}-state")
    session = manager.start_session(
        stable_instance_id=f"replay-{name}",
        principal="operator",
        client="codex",
        software="pytest",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def test_tasks_written_without_provenance_keep_a_null_objective_and_create_no_application(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, "legacy")
    for ordinal in range(3):
        manager.create_task(
            title=f"Task {ordinal}",
            description="Written before objective provenance existed.",
            acceptance_criteria=("It replays unchanged.",),
            idempotency_key=f"legacy-task-{ordinal}",
        )

    snapshot = manager.snapshot()
    assert len(snapshot.tasks) == 3
    assert snapshot.applications == {}
    for task in snapshot.tasks.values():
        # Absent, never guessed from a title, a key or an ordering.
        assert task.get("objective_id") is None

    # A projection rebuilt from the same events reads the old payloads and
    # reaches the same state: no backfill, no invented application.
    rebuilt = CommonsManager(
        manager.paths.repo_root, state_root=manager.paths.state_root
    ).snapshot()
    assert {identifier: dict(task) for identifier, task in rebuilt.tasks.items()} == {
        identifier: dict(task) for identifier, task in snapshot.tasks.items()
    }
    assert rebuilt.applications == {}


def test_later_provenance_leaves_the_earlier_tasks_untouched(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "mixed")
    before = manager.create_task(
        title="Older task",
        description="Created before any application existed.",
        acceptance_criteria=("It keeps a null objective.",),
        idempotency_key="mixed-before",
    )
    after = manager.create_task(
        title="Newer task",
        description="Created as part of an application.",
        acceptance_criteria=("It carries the application objective.",),
        idempotency_key="mixed-after",
    )
    application_id = "application." + ("b" * 64)
    manager.record_event(
        "blueprint_application.created",
        {
            "application_id": application_id,
            "blueprint": {"id": "test", "version": "1", "revision": "1"},
            "objective_id": None,
            "created_task_ids": [str(after["entity_ref"]["id"])],
            "created_agent_ids": [],
            "created_role_refs": [],
        },
        idempotency_key="mixed-application",
    )

    snapshot = manager.snapshot()
    assert set(snapshot.applications) == {application_id}
    older = snapshot.tasks[str(before["entity_ref"]["id"])]
    assert older.get("objective_id") is None
    assert older.get("application_id") is None
    newer = snapshot.tasks[str(after["entity_ref"]["id"])]
    assert newer.get("objective_id") is None
