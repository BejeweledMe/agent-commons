from pathlib import Path

import pytest

from agent_commons.domain.task_edits import TaskEditRefusal
from agent_commons.errors import IdempotencyConflictError, ValidationError
from agent_commons.services import CommonsManager


@pytest.fixture
def manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="task-editor-tests")
    value = CommonsManager(repo, state_root=tmp_path / "state")
    opened = value.start_session(
        stable_instance_id="task-editor-tests-12345678",
        principal="operator",
        client="codex",
        software="tests",
        role="builder",
    )
    value.session_id = opened["session_id"]
    return value


def task(manager: CommonsManager, key: str, dependencies: tuple[str, ...] = ()) -> dict:
    return manager.create_task(
        title=key,
        description="A complete editable task.",
        acceptance_criteria=("Observable result",),
        dependencies=dependencies,
        idempotency_key=key,
    )


def test_dependencies_are_revised_and_removed_in_replay(manager: CommonsManager) -> None:
    prerequisite = task(manager, "prerequisite")["entity_ref"]["id"]
    created = task(manager, "dependent")
    identifier = created["entity_ref"]["id"]
    edited = manager.edit_task(
        identifier,
        created["revision"],
        changes={"dependencies": [prerequisite]},
        idempotency_key="add-edge",
    )
    assert manager.snapshot().tasks[identifier]["dependencies"] == [prerequisite]
    assert manager.snapshot().tasks[identifier]["revision"] == edited["revision"]
    manager.edit_task(
        identifier, edited["revision"], changes={"dependencies": []}, idempotency_key="remove-edge"
    )
    snapshot = manager.snapshot()
    assert snapshot.tasks[identifier]["dependencies"] == []
    assert not [issue for issue in snapshot.issues if issue.severity == "error"]


def test_universal_dependency_validation_rejects_cycles_and_dangling_edges(
    manager: CommonsManager,
) -> None:
    first = task(manager, "first")
    first_id = first["entity_ref"]["id"]
    second_id = task(manager, "second", (first_id,))["entity_ref"]["id"]
    third_id = task(manager, "third", (second_id,))["entity_ref"]["id"]
    for index, (dependencies, code) in enumerate(
        [
            ([first_id], "task_dependency_cycle"),
            ([third_id], "task_dependency_cycle"),
            (["task." + "0" * 26], "task_dependency_missing"),
        ]
    ):
        with pytest.raises(TaskEditRefusal) as refused:
            manager.revise_task(
                first_id,
                first["revision"],
                changes={"dependencies": dependencies},
                idempotency_key=f"invalid-{index}",
            )
        assert refused.value.code == code
        assert manager.snapshot().tasks[first_id]["revision"] == first["revision"]
    with pytest.raises(ValidationError):
        manager.edit_task(
            first_id,
            first["revision"],
            changes={"dependencies": [second_id, second_id]},
            idempotency_key="duplicate-edge",
        )


def test_stale_editor_keeps_existing_text_and_successful_retry_survives_later_edit(
    manager: CommonsManager,
) -> None:
    created = task(manager, "original")
    identifier = created["entity_ref"]["id"]
    changed = manager.edit_task(
        identifier,
        created["revision"],
        changes={"title": "First edit"},
        idempotency_key="first-edit",
    )
    with pytest.raises(TaskEditRefusal) as refused:
        manager.edit_task(
            identifier,
            created["revision"],
            changes={"title": "Stale overwrite"},
            idempotency_key="stale-edit",
        )
    assert refused.value.code == "task_edit_stale"
    later = manager.edit_task(
        identifier,
        changed["revision"],
        changes={"title": "Later edit"},
        idempotency_key="later-edit",
    )
    retried = manager.edit_task(
        identifier,
        created["revision"],
        changes={"title": "First edit"},
        idempotency_key="first-edit",
    )
    assert retried["event_id"] == changed["event_id"]
    assert manager.snapshot().tasks[identifier]["revision"] == later["revision"]
    assert manager.snapshot().tasks[identifier]["title"] == "Later edit"
    with pytest.raises(IdempotencyConflictError):
        manager.edit_task(
            identifier,
            created["revision"],
            changes={"title": "Changed retry"},
            idempotency_key="first-edit",
        )


def test_cancel_preserves_task_and_idempotent_retry(manager: CommonsManager) -> None:
    created = task(manager, "cancel-me")
    identifier = created["entity_ref"]["id"]
    cancelled = manager.cancel_idle_task(
        identifier,
        created["revision"],
        reason="Operator no longer needs this work.",
        idempotency_key="cancel",
    )
    retried = manager.cancel_idle_task(
        identifier,
        created["revision"],
        reason="Operator no longer needs this work.",
        idempotency_key="cancel",
    )
    assert retried["event_id"] == cancelled["event_id"]
    record = manager.snapshot().tasks[identifier]
    assert record["state"] == "cancelled"
    assert record["title"] == "cancel-me"
    with pytest.raises(TaskEditRefusal) as refused:
        manager.edit_task(
            identifier,
            cancelled["revision"],
            changes={"title": "Cannot edit"},
            idempotency_key="edit-cancelled",
        )
    assert refused.value.code == "task_edit_state"


@pytest.mark.parametrize("state", ["requested", "active", "input_needed"])
def test_live_work_refuses_edit_and_cancel_without_changing_target(
    manager: CommonsManager, state: str
) -> None:
    created = task(manager, "live-target")
    identifier = created["entity_ref"]["id"]
    delegation = manager.create_delegation(
        target_ref=created["entity_ref"],
        target_revision=created["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 1000},
        },
        idempotency_key="live-delegation",
    )
    if state != "requested":
        child = manager.start_session(
            stable_instance_id="task-editor-child-12345678",
            principal="operator",
            client="codex",
            software="tests",
            role="builder",
        )
        delegation = manager.start_delegation(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            child_session_id=child["session_id"],
            attempt=1,
            idempotency_key="start-run",
        )
    if state == "input_needed":
        parent_session = manager.session_id
        manager.session_id = child["session_id"]
        manager.mark_delegation_input_needed(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            summary="Clarification needed.",
            idempotency_key="input-needed",
        )
        manager.session_id = parent_session
    for operation in [
        lambda: manager.edit_task(
            identifier,
            created["revision"],
            changes={"title": "Wrong time"},
            idempotency_key="edit-live",
        ),
        lambda: manager.cancel_idle_task(
            identifier, created["revision"], reason="Wrong time", idempotency_key="cancel-live"
        ),
    ]:
        with pytest.raises(TaskEditRefusal) as refused:
            operation()
        assert refused.value.code == "task_live_work"
        assert manager.snapshot().tasks[identifier]["revision"] == created["revision"]


def test_suggested_role_is_checked_on_universal_create(manager: CommonsManager) -> None:
    with pytest.raises(TaskEditRefusal) as refused:
        manager.create_task(
            title="Invalid hint",
            description="No assignment",
            acceptance_criteria=("No authority granted",),
            suggested_agent_id="agent." + "0" * 26,
            idempotency_key="missing-hint",
        )
    assert refused.value.code == "task_suggested_role_unavailable"
    created = task(manager, "legacy-no-hint")
    assert "suggested_agent_id" not in manager.snapshot().tasks[created["entity_ref"]["id"]].get(
        "extensions", {}
    )


def test_live_review_subject_also_protects_its_task_from_editor_changes(
    manager: CommonsManager,
) -> None:
    created = task(manager, "review-target")
    review = manager.request_review(
        target_ref=created["entity_ref"],
        target_revision=created["revision"],
        criteria=("Check exact task content",),
        idempotency_key="task-review",
    )
    manager.create_delegation(
        target_ref=review["entity_ref"],
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 1000},
        },
        idempotency_key="review-delegation",
    )
    with pytest.raises(TaskEditRefusal) as refused:
        manager.edit_task(
            created["entity_ref"]["id"],
            created["revision"],
            changes={"title": "Cannot change under review"},
            idempotency_key="edit-during-review",
        )
    assert refused.value.code == "task_live_work"


def test_explicit_hierarchy_is_independent_and_survives_cancellation(
    manager: CommonsManager,
) -> None:
    parent = manager.create_task(
        title="Component",
        description="Planning component",
        acceptance_criteria=["Reviewed component"],
        task_kind="component",
        idempotency_key="component",
    )
    pid = parent["entity_ref"]["id"]
    child = manager.create_task(
        title="Subtask",
        description="Work",
        acceptance_criteria=["Done"],
        parent_task_id=pid,
        idempotency_key="child",
    )
    cid = child["entity_ref"]["id"]
    snapshot = manager.snapshot()
    assert snapshot.tasks[pid]["task_kind"] == "component"
    assert snapshot.tasks[cid]["parent_task_id"] == pid
    assert snapshot.tasks[cid]["dependencies"] == []
    manager.cancel_idle_task(
        pid, parent["revision"], reason="No longer scheduled", idempotency_key="cancel-component"
    )
    assert manager.snapshot().tasks[cid]["parent_task_id"] == pid
    assert manager.snapshot().tasks[cid]["state"] == "ready"
    changed = manager.edit_task(
        cid, child["revision"], changes={"parent_task_id": None}, idempotency_key="detach"
    )
    assert manager.snapshot().tasks[cid]["parent_task_id"] is None
    retry = manager.edit_task(
        cid, child["revision"], changes={"parent_task_id": None}, idempotency_key="detach"
    )
    assert retry["event_id"] == changed["event_id"]
    with pytest.raises(TaskEditRefusal, match="changed"):
        manager.edit_task(
            cid, child["revision"], changes={"parent_task_id": pid}, idempotency_key="stale-parent"
        )


def test_parent_cycles_orphans_and_kind_mutation_fail_at_canonical_boundary(
    manager: CommonsManager,
) -> None:
    first = task(manager, "parent")
    pid = first["entity_ref"]["id"]
    child = manager.create_task(
        title="Child",
        description="Child",
        acceptance_criteria=["Done"],
        parent_task_id=pid,
        idempotency_key="child",
    )
    cid = child["entity_ref"]["id"]
    for parent, code in [
        (pid, "task_parent_cycle"),
        (cid, "task_parent_cycle"),
        ("task." + "0" * 26, "task_parent_missing"),
    ]:
        with pytest.raises(TaskEditRefusal) as refused:
            manager.revise_task(
                pid, first["revision"], changes={"parent_task_id": parent}, idempotency_key=parent
            )
        assert refused.value.code == code
    with pytest.raises(TaskEditRefusal):
        manager.create_task(
            title="Orphan",
            description="No parent",
            acceptance_criteria=["Done"],
            parent_task_id="task." + "0" * 26,
            idempotency_key="orphan",
        )
    for changes in [{"task_kind": "component"}, {"parent_task_id": 42}]:
        with pytest.raises(ValidationError):
            manager.revise_task(
                pid, first["revision"], changes=changes, idempotency_key=str(changes)
            )
    assert manager.snapshot().tasks[pid]["revision"] == first["revision"]


def test_hierarchy_completion_and_dependencies_remain_independent(manager: CommonsManager) -> None:
    parent = task(manager, "parent")
    pid = parent["entity_ref"]["id"]
    child = manager.create_task(
        title="Child",
        description="Child",
        acceptance_criteria=["Done"],
        parent_task_id=pid,
        dependencies=[pid],
        idempotency_key="child",
    )
    cid = child["entity_ref"]["id"]
    # Reverse containment is forbidden, but reverse dependency is a different relation.
    manager.edit_task(
        cid, child["revision"], changes={"dependencies": []}, idempotency_key="clear-wait"
    )
    current = manager.snapshot().tasks[cid]
    taken = manager.take_task(cid, current["revision"], idempotency_key="take-child")
    started = manager.start_task(cid, taken["revision"], idempotency_key="start-child")
    manager.complete_task(
        cid, started["revision"], summary="Child complete", idempotency_key="done-child"
    )
    assert manager.snapshot().tasks[pid]["state"] == "ready"
    assert manager.snapshot().tasks[pid]["revision"] == parent["revision"]
    assert manager.snapshot().tasks[cid]["parent_task_id"] == pid


def test_hierarchy_cannot_be_smuggled_through_lifecycle_event(manager: CommonsManager) -> None:
    created = task(manager, "immutable-kind")
    with pytest.raises(ValidationError, match="Hierarchy fields"):
        manager.record_event(
            "task.taken",
            {
                "task_id": created["entity_ref"]["id"],
                "expected_revision": created["revision"],
                "owner_session_id": manager.session_id,
                "task_kind": "component",
            },
            idempotency_key="smuggle-kind",
        )


@pytest.mark.parametrize("shared_manager", [True, False])
def test_concurrent_reparenting_cannot_commit_a_cycle(
    manager: CommonsManager, shared_manager: bool
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first, second = task(manager, "first"), task(manager, "second")
    barrier = Barrier(2)

    def move(source: dict, parent: dict) -> str:
        writer = (
            manager
            if shared_manager
            else CommonsManager(
                manager.repo_root,
                state_root=manager.paths.state_root,
                session_id=manager.session_id,
            )
        )
        barrier.wait(timeout=5)
        try:
            writer.edit_task(
                source["entity_ref"]["id"],
                source["revision"],
                changes={"parent_task_id": parent["entity_ref"]["id"]},
                idempotency_key="move-" + source["entity_ref"]["id"],
            )
            return "saved"
        except TaskEditRefusal as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(move, first, second), pool.submit(move, second, first)]
        assert sorted(future.result(timeout=10) for future in futures) == [
            "saved",
            "task_parent_cycle",
        ]
    assert not [issue for issue in manager.snapshot().issues if issue.severity == "error"]
