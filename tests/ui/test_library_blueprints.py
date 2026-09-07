from __future__ import annotations

import copy
import json

import pytest

from agent_commons.ui.library_blueprints import apply_blueprint, blueprint_catalog
from tests.ui.conftest import authorized


def _selection(plan):
    return {
        "title": "Our project",
        "brief": "Build a customer portal for teams to track and resolve support requests.",
        "locale": "en",
        "expected_version": plan["version"],
        "idempotency_key": "create-workflow",
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


@pytest.fixture(autouse=True)
def private_library(writable, tmp_path):
    writable._library_root = tmp_path / "library"


@pytest.mark.parametrize(
    "identifier",
    ["web-app", "mobile-app", "telegram-mini-app", "grounded-ai-assistant", "improve-service"],
)
def test_each_blueprint_creates_exact_roles_and_dependency_graph_without_launch(
    writable, identifier
):
    plan = next(
        p
        for p in blueprint_catalog(writable.library_store())["blueprints"]
        if p["id"] == identifier
    )
    body = _selection(plan)
    result = apply_blueprint(writable, identifier, body)
    assert apply_blueprint(writable, identifier, body) == result
    snapshot = writable.writer().snapshot()
    assert len(snapshot.tasks) == len(plan["tasks"])
    assert len(snapshot.agents) == len(plan["slots"])
    assert not snapshot.delegations and not snapshot.issues
    tasks = {item["node_id"]: item["task_id"] for item in result["tasks"]}
    for node in plan["tasks"]:
        task = snapshot.tasks[tasks[node["id"]]]
        assert task["state"] == "ready"
        assert body["brief"] in task["description"]
        assert task["dependencies"] == [tasks[item] for item in node["depends_on"]]
        assert task["extensions"]["suggested_agent_id"] in snapshot.agents
    assert all(
        role["specialization_ref"]["source"] == "builtin" for role in snapshot.agents.values()
    )
    encoded = json.dumps([item.event for item in writable.writer().events.iter_events()])
    assert "system_prompt" not in encoded and "SKILL.md" not in encoded


def test_blueprint_http_retry_binds_the_entire_selection(writable, writable_client):
    plan = writable_client.get("/api/library/blueprints", headers=authorized()).json()[
        "blueprints"
    ][0]
    body = _selection(plan)
    url = f"/api/library/blueprints/{plan['id']}/apply"
    assert writable_client.post(url, json=body).status_code == 401
    first = writable_client.post(url, json=body, headers=authorized())
    assert first.status_code == 200, first.text
    before = writable.writer().snapshot()
    changed = copy.deepcopy(body)
    changed["bindings"][-1]["profile_id"] = "claude-builder"
    refused = writable_client.post(url, json=changed, headers=authorized())
    assert refused.status_code == 409
    after = writable.writer().snapshot()
    assert after.agents == before.agents and after.tasks == before.tasks
    assert writable_client.post(url, json=body, headers=authorized()).json() == first.json()


def test_partial_blueprint_resumes_without_duplicate_team(writable, monkeypatch):
    plan = blueprint_catalog(writable.library_store())["blueprints"][0]
    body = _selection(plan)
    manager = writable.writer()
    monkeypatch.setattr(writable, "writer", lambda: manager)
    original = manager.create_task
    count = 0

    def interrupt(**fields):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("interrupted")
        return original(**fields)

    monkeypatch.setattr(manager, "create_task", interrupt)
    with pytest.raises(RuntimeError):
        apply_blueprint(writable, plan["id"], body)
    monkeypatch.setattr(manager, "create_task", original)
    apply_blueprint(writable, plan["id"], body)
    snapshot = manager.snapshot()
    assert len(snapshot.agents) == len(plan["slots"])
    assert len(snapshot.tasks) == len(plan["tasks"])


def test_same_key_cannot_switch_blueprints_and_create_a_partial_team(writable):
    from agent_commons.errors import IdempotencyConflictError

    plans = blueprint_catalog(writable.library_store())["blueprints"]
    first, second = plans[0], plans[3]
    apply_blueprint(writable, first["id"], _selection(first))
    before = writable.writer().snapshot()
    with pytest.raises(IdempotencyConflictError):
        apply_blueprint(writable, second["id"], _selection(second))
    after = writable.writer().snapshot()
    assert before.agents == after.agents and before.tasks == after.tasks


@pytest.mark.parametrize("uncertain", ["child", "integrity"])
def test_library_edit_authority_refuses_child_or_uncertain_projection(
    writable, monkeypatch, uncertain
):
    from types import SimpleNamespace

    from agent_commons.errors import ConfigurationError

    snapshot = SimpleNamespace(
        issues=[SimpleNamespace(severity="error")] if uncertain == "integrity" else [],
        delegations={"one": {"child_session_id": "operator"}} if uncertain == "child" else {},
    )
    monkeypatch.setattr(
        writable,
        "writer",
        lambda: SimpleNamespace(
            _active_session=lambda: SimpleNamespace(session_id="operator"),
            snapshot=lambda: snapshot,
        ),
    )
    with pytest.raises(ConfigurationError):
        writable.authorize_library_edit()
