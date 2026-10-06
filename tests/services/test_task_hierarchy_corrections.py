"""Hierarchy remains structural under correction and declares its replay floor."""

from copy import deepcopy
from pathlib import Path

import pytest

from agent_commons.domain.projection import project_events
from agent_commons.domain.task_edits import TaskEditRefusal
from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.services import CommonsManager


@pytest.fixture
def manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="hierarchy-corrections")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="hierarchy-tests-12345678",
        principal="operator",
        client="codex",
        software="tests",
        role="builder",
    )
    manager.session_id = session["session_id"]
    return manager


def create(manager: CommonsManager, key: str, **fields: object) -> dict:
    return manager.create_task(
        title=key,
        description="Planning work",
        acceptance_criteria=["Done"],
        idempotency_key=key,
        **fields,
    )


def correct(manager: CommonsManager, event: dict, replacement: dict, key: str) -> dict:
    shown = manager.show_event(event["event_id"])
    return manager.correct_event(
        event["event_id"],
        expected_target_sha256=shown["canonical_sha256"],
        replacement_payload=replacement,
        idempotency_key=key,
    )


def payload(manager: CommonsManager, event: dict) -> dict:
    return dict(manager.show_event(event["event_id"])["event"]["payload"])


@pytest.mark.parametrize("field", ["task_kind", "parent_task_id"])
def test_correction_cannot_change_hierarchy_after_lifecycle_progression(
    manager: CommonsManager, field: str
) -> None:
    parent = create(manager, "parent")
    child = create(manager, "child")
    cid = child["entity_ref"]["id"]
    manager.cancel_idle_task(cid, child["revision"], reason="Cancelled", idempotency_key="cancel")
    original = payload(manager, child)
    replacement = {
        **original,
        field: "component" if field == "task_kind" else parent["entity_ref"]["id"],
    }
    with pytest.raises(LifecycleConflictError, match=field):
        correct(manager, child, replacement, "structural-correction")
    assert manager.snapshot().tasks[cid]["state"] == "cancelled"
    assert manager.snapshot().tasks[cid].get("task_kind", "task") == "task"
    assert manager.snapshot().tasks[cid].get("parent_task_id") is None
    # Replay an imported version of a legitimate wording correction with the
    # same target/digest but unauthorized structure: write and replay agree.
    allowed = correct(manager, child, {**original, "title": "Correct spelling"}, "text-correction")
    events = [deepcopy(record.event) for record in manager.events.iter_events()]
    next(event for event in events if event["event_id"] == allowed["event_id"])["payload"][
        "replacement_payload"
    ] = replacement
    replayed = project_events(events)
    assert any(issue.code == "correction_structural_change" for issue in replayed.issues)


@pytest.mark.parametrize(
    "before,after", [("absent", None), (None, "absent"), (None, "parent"), ("parent", None)]
)
def test_nested_parent_revision_correction_preserves_presence_and_value(
    manager: CommonsManager, before: str | None, after: str | None
) -> None:
    parent = create(manager, "parent")["entity_ref"]["id"]
    child = create(manager, "child")
    changes = {"title": "Wording"}
    if before != "absent":
        changes["parent_task_id"] = parent if before == "parent" else None
    revised = manager.edit_task(
        child["entity_ref"]["id"], child["revision"], changes=changes, idempotency_key="revise"
    )
    original = payload(manager, revised)
    replacement = deepcopy(original)
    replacement["changes"].pop("parent_task_id", None)
    if after != "absent":
        replacement["changes"]["parent_task_id"] = parent if after == "parent" else None
    with pytest.raises(LifecycleConflictError, match="changes.parent_task_id"):
        correct(manager, revised, replacement, "bad-nested")
    safe = deepcopy(original)
    safe["changes"]["title"] = "Correct wording"
    corrected = correct(manager, revised, safe, "safe-nested")
    assert manager.snapshot().tasks[child["entity_ref"]["id"]]["title"] == "Correct wording"
    events = [deepcopy(record.event) for record in manager.events.iter_events()]
    next(event for event in events if event["event_id"] == corrected["event_id"])["payload"][
        "replacement_payload"
    ] = replacement
    assert any(
        issue.code == "correction_structural_change" for issue in project_events(events).issues
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_creation_correction_accepts_equivalent_defaults_and_wording(
    manager: CommonsManager, explicit: bool
) -> None:
    created = create(manager, "defaults")
    if explicit:
        created = manager.record_event(
            "task.created",
            {
                **payload(manager, created),
                "task_id": "task." + "2" * 26,
                "task_kind": "task",
                "parent_task_id": None,
            },
            idempotency_key="explicit-defaults",
        )
    replacement = payload(manager, created)
    if explicit:
        replacement.pop("task_kind")
        replacement.pop("parent_task_id")
    else:
        replacement.update(task_kind="task", parent_task_id=None)
    replacement["title"] = "Corrected title"
    correct(manager, created, replacement, "default-correction")
    snapshot = manager.snapshot()
    assert snapshot.tasks[created["entity_ref"]["id"]]["title"] == "Corrected title"
    assert snapshot.semantics_required == 1
    assert not [issue for issue in snapshot.issues if issue.severity == "error"]


@pytest.mark.parametrize("kind", ["component", "parented", "reparent", "detach"])
def test_universal_writer_stamps_hierarchy_after_validation_once(
    manager: CommonsManager, kind: str
) -> None:
    parent = create(manager, "parent")
    child = create(manager, "child")
    pid, cid = parent["entity_ref"]["id"], child["entity_ref"]["id"]
    assert manager.snapshot().semantics_required == 1
    if kind in {"component", "parented"}:
        body = {**payload(manager, child), "task_id": "task." + "1" * 26}
        body.update({"task_kind": "component"} if kind == "component" else {"parent_task_id": pid})
        event_type = "task.created"
    else:
        body = {
            "task_id": cid,
            "expected_revision": child["revision"],
            "changes": {"parent_task_id": pid if kind == "reparent" else None},
        }
        event_type = "task.revised"
    written = manager.record_event(event_type, body, idempotency_key="direct-hierarchy")
    assert manager.snapshot().semantics_required == 7
    assert (
        manager.record_event(event_type, body, idempotency_key="direct-hierarchy")["event_id"]
        == written["event_id"]
    )
    events = list(manager.events.iter_events())
    stamps = [
        record for record in events if record.event["event_type"] == "workspace.semantics_required"
    ]
    assert len(stamps) == 1
    assert stamps[0].event["payload"]["semantics_version"] == 7
    assert events.index(stamps[0]) < next(
        i for i, record in enumerate(events) if record.event_id == written["event_id"]
    )


def test_failed_hierarchy_default_writes_and_old_retry_do_not_raise_floor(
    manager: CommonsManager,
) -> None:
    created = create(manager, "legacy")
    assert (
        create(manager, "legacy", task_kind="task", parent_task_id=None)["event_id"]
        == created["event_id"]
    )
    assert "task_kind" not in payload(manager, created)
    assert "parent_task_id" not in payload(manager, created)
    with pytest.raises(TaskEditRefusal):
        create(manager, "orphan", parent_task_id="task." + "0" * 26)
    with pytest.raises(ValidationError):
        create(manager, "bad-kind", task_kind="folder")
    with pytest.raises(TaskEditRefusal):
        manager.record_event(
            "task.revised",
            {
                "task_id": created["entity_ref"]["id"],
                "expected_revision": created["revision"],
                "changes": {"parent_task_id": created["entity_ref"]["id"]},
            },
            idempotency_key="invalid-parent-revision",
        )
    manager.record_event(
        "task.revised",
        {
            "task_id": created["entity_ref"]["id"],
            "expected_revision": created["revision"],
            "changes": {"title": "Wording only"},
        },
        idempotency_key="wording",
    )
    assert manager.snapshot().semantics_required == 1
    assert not [
        record
        for record in manager.events.iter_events()
        if record.event["event_type"] == "workspace.semantics_required"
    ]


def test_v7_stamp_uses_existing_projection_upgrade_guard(
    manager: CommonsManager, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from agent_commons.domain import projection

    create(manager, "component", task_kind="component")
    events = [record.event for record in manager.events.iter_events()]
    assert projection.LEDGER_SEMANTICS_VERSION == 7
    monkeypatch.setattr(projection, "LEDGER_SEMANTICS_VERSION", 6)
    old_projection = projection.project_events(events)
    assert old_projection.semantics_required == 7
    assert [issue.code for issue in old_projection.issues] == ["ledger_ahead_of_code"]
    # This exercises projection only. A strict old storage schema can reject
    # task_kind before projection is reached; no doctor-message promise follows.
