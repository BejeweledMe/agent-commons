from __future__ import annotations

import copy

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.library_blueprints import apply_blueprint, blueprint_catalog


def _selection(plan: dict, *, objective_id: str | None = None) -> dict:
    body = {
        "title": "Provenance workflow",
        "brief": "Deliver a durable blueprint application record.",
        "locale": "en",
        "expected_version": plan["version"],
        "idempotency_key": "application-retry",
        "bindings": [
            {
                "slot_id": slot["id"],
                "profile_id": "codex-builder",
                "model": None,
                "name": slot["name"],
            }
            for slot in plan["slots"]
        ],
    }
    if objective_id is not None:
        body["objective_id"] = objective_id
    return body


@pytest.fixture
def writable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state_root = tmp_path / "state"
    CommonsManager.initialize(repo, integrations=())
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id="blueprint-application-test-window",
        principal="operator",
        client="codex",
        software="pytest",
        role="operator",
    )
    context = UIContext(repo, state_root=state_root, writer_session_id=session["session_id"])
    context._library_root = tmp_path / "library"
    return context


def test_apply_creates_deterministic_application_and_replays_mapping(writable) -> None:
    plan = blueprint_catalog(writable.library_store())["blueprints"][0]
    body = _selection(plan)

    first = apply_blueprint(writable, plan["id"], body)
    second = apply_blueprint(writable, plan["id"], body)

    expected = "application." + canonical_sha256(
        {"namespace": "application", "idempotency_key": body["idempotency_key"]}
    )
    snapshot = writable.writer().snapshot()
    assert first == second
    assert first["application_id"] == expected
    assert snapshot.applications[expected]["created_task_ids"] == [
        item["task_id"] for item in first["tasks"]
    ]
    assert len(snapshot.applications) == 1


def test_apply_rejects_changed_body_for_same_identity(writable) -> None:
    plan = blueprint_catalog(writable.library_store())["blueprints"][0]
    body = _selection(plan)
    apply_blueprint(writable, plan["id"], body)
    changed = copy.deepcopy(body)
    changed["brief"] = "A different durable provenance request."

    with pytest.raises(IdempotencyConflictError):
        apply_blueprint(writable, plan["id"], changed)


def test_apply_stamps_active_objective_and_refuses_closed_objective(writable) -> None:
    manager = writable.writer()
    objective = manager.create_objective(
        title="Ship provenance",
        description="Make application membership durable.",
        acceptance_criteria=("Events retain the objective.",),
        idempotency_key="application-objective",
    )
    objective_id = objective["entity_ref"]["id"]
    plan = blueprint_catalog(writable.library_store())["blueprints"][0]
    result = apply_blueprint(writable, plan["id"], _selection(plan, objective_id=objective_id))
    snapshot = manager.snapshot()
    assert snapshot.applications[result["application_id"]]["objective_id"] == objective_id
    assert all(task["objective_id"] == objective_id for task in snapshot.tasks.values())

    closed = manager.close_objective(
        objective_id,
        objective["event_id"],
        reason="The initial outcome is complete.",
        idempotency_key="close-application-objective",
    )
    with pytest.raises(LifecycleConflictError, match="objective must exist and be active"):
        apply_blueprint(
            writable,
            plan["id"],
            _selection(plan, objective_id=objective_id)
            | {"idempotency_key": "closed-application-objective"},
        )
    assert closed["event_id"]


def test_a_partial_earlier_write_is_completed_without_a_second_application(writable) -> None:
    """A crash between the last task and the record leaves one application, not two."""

    plan = next(
        item
        for item in blueprint_catalog(writable.library_store())["blueprints"]
        if item["id"] == "feature-delivery"
    )
    body = _selection(plan)
    first = apply_blueprint(writable, "feature-delivery", body)
    snapshot = writable.writer().snapshot()
    application_ids = set(snapshot.applications)
    assert len(application_ids) == 1

    # The same identity replays: the mapping and the application are the ones
    # already recorded, and no second record appears.
    replay = apply_blueprint(writable, "feature-delivery", body)
    assert replay["application_id"] == first["application_id"]
    assert [row["task_id"] for row in replay["tasks"]] == [row["task_id"] for row in first["tasks"]]
    assert set(writable.writer().snapshot().applications) == application_ids
