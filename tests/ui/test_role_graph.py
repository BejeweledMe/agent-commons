"""Roles on the canvas: standing structure, and what is blocked on a human."""

from __future__ import annotations

from typing import Any

import pytest

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from tests.ui.conftest import authorized

LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 8000},
}


_AUTOMATIC_LEVEL_WITHHELD = (
    "the automatic grant level is withheld until its guarantees hold "
    "(docs/audits/2026-08-10-standing-roles-review.md, remediation step 1); "
    "restored later in this branch"
)


def _writer(workspace: dict[str, Any], suffix: str) -> CommonsManager:
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id=f"role-graph-{suffix}-12345678",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def _run_as(
    workspace: dict[str, Any], parent: CommonsManager, agent_id: str, key: str
) -> CommonsManager:
    """Return a manager whose session is running as `agent_id`."""

    task = parent.create_task(
        title=f"work for {key}",
        description="binds a session to a role",
        acceptance_criteria=("bound",),
        idempotency_key=f"{key}-task",
    )
    delegation = parent.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=agent_id,
        idempotency_key=f"{key}-delegation",
    )
    child = _writer(workspace, key)
    parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=str(child.session_id),
        idempotency_key=f"{key}-start",
    )
    return child


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_roles_are_nodes_and_their_lineage_is_the_reporting_edge(
    workspace: dict[str, Any],
) -> None:
    manager = _writer(workspace, "lineage")
    root = manager.create_agent(
        name="Tech lead",
        profile_id="claude-builder",
        rationale="owns the backend programme",
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        turnover_budget=8,
        idempotency_key="graph-root",
    )
    # The child is created the way an agent-created role really comes about:
    # by a session running as the root role, under its standing grant.
    root_session = _run_as(workspace, manager, root["entity_ref"]["id"], "graph-lineage")
    child = root_session.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="hired for the payments surface",
        created_by_agent_id=root["entity_ref"]["id"],
        idempotency_key="graph-child",
    )
    graph = UIContext(workspace["repo"], state_root=workspace["state_root"]).rebuild_graph()

    roles = {node["id"]: node for node in graph["nodes"] if node["kind"] == "agent"}
    assert set(roles) == {root["entity_ref"]["id"], child["entity_ref"]["id"]}
    assert roles[root["entity_ref"]["id"]]["band"] == 0
    assert roles[root["entity_ref"]["id"]]["reports_to_operator"] is True
    assert roles[child["entity_ref"]["id"]]["band"] == 1
    # Provenance is on the node, so a role an agent hired is distinguishable at
    # a glance from one a person did.
    assert roles[child["entity_ref"]["id"]]["attrs"]["origin"] == "agent"
    assert roles[root["entity_ref"]["id"]]["attrs"]["origin"] == "human"
    assert roles[root["entity_ref"]["id"]]["attrs"]["effective_grants"]["create_roles"] == "auto"

    edges = {(edge["kind"], edge["from"], edge["to"]) for edge in graph["edges"]}
    assert ("reports_to", child["entity_ref"]["id"], root["entity_ref"]["id"]) in edges
    reporting = next(edge for edge in graph["edges"] if edge["kind"] == "reports_to")
    assert reporting["relation"] == "permanent"


def test_a_delegation_waiting_for_input_rings_its_role_and_its_run(
    workspace: dict[str, Any],
) -> None:
    manager = _writer(workspace, "blocked")
    role = manager.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="owns the surface",
        idempotency_key="blocked-role",
    )
    task = manager.create_task(
        title="Wire the endpoint",
        description="the run will ask for a decision",
        acceptance_criteria=("done",),
        idempotency_key="blocked-task",
    )
    delegation = manager.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="blocked-delegation",
    )
    child = _writer(workspace, "child")
    started = manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=str(child.session_id),
        idempotency_key="blocked-start",
    )

    graph = UIContext(workspace["repo"], state_root=workspace["state_root"]).rebuild_graph()
    assert not any(node["awaits_human"] for node in graph["nodes"])

    child.mark_delegation_input_needed(
        delegation["entity_ref"]["id"],
        started["revision"],
        summary="which currency rounding rule applies",
        idempotency_key="blocked-input",
    )
    graph = UIContext(workspace["repo"], state_root=workspace["state_root"]).rebuild_graph()
    waiting = set(graph["awaiting_human"])
    assert delegation["entity_ref"]["id"] in waiting
    # The blocker reaches the standing role, so it is visible where an operator
    # looks for the org, not only on the transient run.
    assert role["entity_ref"]["id"] in waiting
    node = next(item for item in graph["nodes"] if item["id"] == role["entity_ref"]["id"])
    assert node["awaits_human"] is True


def test_a_run_hangs_under_the_role_it_acts_for(workspace: dict[str, Any]) -> None:
    manager = _writer(workspace, "acts")
    role = manager.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="owns the surface",
        idempotency_key="acts-role",
    )
    task = manager.create_task(
        title="Work",
        description="a run for the role",
        acceptance_criteria=("done",),
        idempotency_key="acts-task",
    )
    delegation = manager.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="acts-delegation",
    )
    graph = UIContext(workspace["repo"], state_root=workspace["state_root"]).rebuild_graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
    run_band = nodes[delegation["entity_ref"]["id"]]["band"]
    assert run_band == nodes[role["entity_ref"]["id"]]["band"] + 1
    acts = next(edge for edge in graph["edges"] if edge["kind"] == "acts_for")
    # A run is one bounded attempt: temporary, unlike the role it acts for.
    assert acts["relation"] == "temporary"
    assert acts["to"] == role["entity_ref"]["id"]


def test_the_read_only_ui_serves_roles_and_the_catalogue_without_writes(
    client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
) -> None:
    manager = _writer(workspace, "readonly")
    manager.create_agent(
        name="Backend",
        profile_id="claude-builder",
        rationale="visible without write access",
        idempotency_key="readonly-role",
    )
    catalog = client.get("/api/catalog", headers=authorized())
    assert catalog.status_code == 200
    payload = catalog.json()
    # Without the catalogue gate, everything but presets is operator-owned and
    # the panel says so instead of offering controls that would fail.
    assert payload["editable_here"] == ["presets"]
    assert payload["catalog_editing_enabled"] is False
    assert {"profiles", "skills", "tools"} <= set(payload["operator_owned"])
    assert payload["skills"] == []

    graph = client.get("/api/graph", headers=authorized()).json()
    assert any(node["kind"] == "agent" for node in graph["nodes"])
