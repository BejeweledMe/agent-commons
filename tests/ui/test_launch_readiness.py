from __future__ import annotations

from types import SimpleNamespace

from agent_commons.runtime.budget import OperatorBudgetExhaustedError, ProviderUnitBudget
from agent_commons.runtime.model import BuiltinProfileId, Provider
from agent_commons.ui.context import UIContext
from tests.ui.conftest import authorized, tree_digest
from tests.ui.test_launch import _client, _launch_workspace


def test_budget_http_refusal_is_exact_private_and_precedes_delegation(workspace):
    fixture = _launch_workspace(workspace)
    context = fixture["context"]
    config = context._profile_config
    config.write_text(config.read_text() + "limits:\n  parent_provider_units: 1\n")
    args = {
        "agent_id": fixture["role_id"],
        "task_id": fixture["task_id"],
        "idempotency_key": "budget-first",
    }
    with _client(context) as client:
        route = "/api/work/launch-budget/claude-builder"
        assert client.get(route).status_code == 401
        before = tree_digest(workspace["state_root"])
        available = client.get(route, headers=authorized())
        assert available.status_code == 200
        assert tree_digest(workspace["state_root"]) == before
        assert available.json()["remaining"] == 1
        assert client.post("/api/delegations", json=args, headers=authorized()).status_code == 200
        context.await_launches()
        before_events = len(list(fixture["manager"].events.iter_events()))
        exhausted = client.get(route, headers=authorized()).json()
        assert exhausted == {
            "schema": "agent_commons.launch-budget.v1",
            "profile_id": "claude-builder",
            "provider": "claude",
            "unit": "provider_units",
            "limit": 1,
            "used": 1,
            "remaining": 0,
            "exhausted": True,
        }
        refused = client.post(
            "/api/delegations",
            json={**args, "idempotency_key": "budget-next"},
            headers=authorized(),
        )
        assert refused.status_code == 409
        assert refused.json() == {
            "error": {
                "code": "operator_budget_exhausted",
                "message": "operator provider_units budget is exhausted",
                "safe_next_actions": ["review_operator_budget"],
                "budget": exhausted,
            }
        }
        assert len(list(fixture["manager"].events.iter_events())) == before_events
        assert fixture["runner"].calls == 1
        replay = client.post("/api/delegations", json=args, headers=authorized())
        assert replay.status_code == 200, replay.text
        context.await_launches()
        assert fixture["runner"].calls == 1


def test_budget_read_without_operator_scope_is_a_pure_typed_refusal(workspace):
    context = UIContext(workspace["repo"], state_root=workspace["state_root"])
    with _client(context) as client:
        before = tree_digest(workspace["state_root"])
        response = client.get("/api/work/launch-budget/claude-builder", headers=authorized())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "launch_budget_unavailable"
        assert tree_digest(workspace["state_root"]) == before


def test_launch_task_channel_exists_before_provider_work_and_reuses_scope(workspace):
    fixture = _launch_workspace(workspace)
    context = fixture["context"]
    original = fixture["runner"].after_start
    observed = []

    def after_start(child_session_id):
        matching = [
            thread
            for thread in fixture["manager"].snapshot().threads.values()
            if thread.get("conversation_scope") == {"kind": "task", "id": fixture["task_id"]}
        ]
        assert len(matching) == 1
        assert set(matching[0]["to"]) == {"operator", "task:" + fixture["task_id"]}
        observed.append(matching[0]["id"])
        original(child_session_id)

    fixture["runner"].after_start = after_start
    args = {
        "agent_id": fixture["role_id"],
        "task_id": fixture["task_id"],
        "idempotency_key": "channel-launch",
        "background": False,
    }
    context.run_role_on_task(**args)
    context.run_role_on_task(**args)
    assert len(observed) == 1
    matching = [
        thread
        for thread in fixture["manager"].snapshot().threads.values()
        if thread.get("conversation_scope") == {"kind": "task", "id": fixture["task_id"]}
    ]
    assert [thread["id"] for thread in matching] == observed


def test_late_budget_race_preserves_canonical_budget_reason(workspace):
    fixture = _launch_workspace(workspace)
    context = fixture["context"]

    def refuse(*args, **kwargs):
        raise OperatorBudgetExhaustedError(
            ProviderUnitBudget(Provider.CLAUDE, 1, 1), BuiltinProfileId.CLAUDE_BUILDER
        )

    context._runtime_factory = lambda manager: SimpleNamespace(run=refuse)
    result = context.run_role_on_task(
        agent_id=fixture["role_id"],
        task_id=fixture["task_id"],
        idempotency_key="late-budget-race",
        background=False,
    )
    delegation = fixture["manager"].get_delegation(result["delegation_id"])
    assert delegation["state"] == "needs_operator"
    assert delegation["reason_code"] == "budget_exhausted"
    assert delegation["summary"] == "operator provider_units budget is exhausted"
    assert fixture["runner"].calls == 0


def test_environment_route_is_authenticated_read_only_and_does_not_write(workspace):
    context = UIContext(workspace["repo"], state_root=workspace["state_root"])
    with _client(context) as client:
        assert client.get("/api/work/project-environment").status_code == 401
        before = tree_digest(workspace["repo"]), tree_digest(workspace["state_root"])
        response = client.get("/api/work/project-environment", headers=authorized())
        assert response.status_code == 200
        assert response.json()["build_verified"] is False
        assert (tree_digest(workspace["repo"]), tree_digest(workspace["state_root"])) == before
