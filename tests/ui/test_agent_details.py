"""Agent editors read exact fields instead of the shortened entity inspector."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_commons.ui.context import UIContext
from tests.ui.conftest import authorized, tree_digest


@pytest.fixture(autouse=True)
def isolated_library(writable, tmp_path):
    writable._library_root = tmp_path / "library"


def test_editor_preserves_more_than_32_skills_and_tools(writable, writable_client):
    skills = [f"skill-{i:02d}" for i in range(48)]
    tools = [f"tool-{i:02d}" for i in range(48)]
    result = writable.writer().create_agent(
        name="Full configuration",
        profile_id="codex-builder",
        skills=skills,
        tool_allowlist=tools,
        rationale="Exercise exact editor buffers",
    )
    agent_id = result["entity_ref"]["id"]
    before = tree_digest(writable.repo / ".agent-commons")
    response = writable_client.get(f"/api/work/agents/{agent_id}", headers=authorized())
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["skills"] == skills
    assert value["tool_allowlist"] == tools
    assert value["revision"] == result["revision"]
    assert value["schema"] == "agent_commons.agent-details.v1"
    assert value["specialization"] is None
    assert value["specialization_state"] == "none"
    assert value["supervisor_agent_id"] is None
    assert value["created_by_agent_id"] is None
    assert value["grants"] == value["effective_grants"]
    assert response.headers["cache-control"] == "no-store"
    assert tree_digest(writable.repo / ".agent-commons") == before


def test_agent_detail_missing_invalid_and_authentication(writable_client):
    assert (
        writable_client.get("/api/work/agents/not-an-agent", headers=authorized()).status_code
        == 400
    )
    missing = writable_client.get("/api/work/agents/agent." + "0" * 26, headers=authorized())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "agent_not_found"
    assert writable_client.get("/api/work/agents/agent." + "0" * 26).status_code == 401


def test_specialization_returns_resolved_methods_without_instruction_text(
    writable, writable_client
):
    library = writable.library_store()
    role = next(
        item for item in library.catalog()["roles"] if item["ref"]["id"] == "frontend-engineer"
    )
    result = writable.writer().create_agent(
        name="Frontend",
        profile_id="codex-builder",
        specialization_ref=role["ref"],
        library_store=library,
        rationale="Exact specialization editor",
    )
    agent_id = result["entity_ref"]["id"]
    response = writable_client.get(f"/api/work/agents/{agent_id}", headers=authorized())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["specialization_ref"] == role["ref"]
    option = next(item for item in writable.launch_options()["roles"] if item["id"] == agent_id)
    assert option["specialization_ref"] == role["ref"]
    assert option["model"] is None
    assert body["specialization_state"] == "ready"
    methods = body["specialization"]["methods"]
    assert methods[0]["kind"] == "entry"
    assert methods[0]["ref"]["id"] == "web-frontend-engineering"
    assert all(set(item) == {"ref", "name", "kind", "when"} for item in methods)
    assert "instruction" not in body["specialization"]
    assert "commons_read_skill" in body["profile_tools"]


def test_readonly_context_can_read_agent_metadata_without_authority(writable):
    result = writable.writer().create_agent(
        name="Visible role", profile_id="codex-builder", rationale="Readonly metadata"
    )
    reader = UIContext(
        writable.repo, state_root=writable._state_root, library_root=writable._library_root
    )
    data = reader.agent_details(result["entity_ref"]["id"])
    assert data["name"] == "Visible role"
    assert data["links"] == []
    assert data["organization_children"] == []


@pytest.mark.parametrize("modern", [False, True])
def test_http_child_creation_reparent_and_graph_keep_authority_separate(
    writable, writable_client, monkeypatch, modern
):
    """The form's parent is an org edge, never forged creator provenance."""
    headers = authorized()
    lead = writable.writer().create_agent(
        name="Team lead", profile_id="codex-builder", rationale="Own team organization"
    )
    lead_id = lead["entity_ref"]["id"]
    body = {
        "name": "Frontend child",
        "profile_id": "codex-builder",
        "rationale": "Add a child in the agent map",
        "supervisor_agent_id": lead_id,
        "idempotency_key": "child-from-map",
    }
    if modern:
        body["specialization_ref"] = next(
            entry["ref"]
            for entry in writable.library_store().catalog()["roles"]
            if entry["ref"]["id"] == "frontend-engineer"
        )
        # Provider readiness has separate behavioral tests. This test follows
        # a qualified selection through the real route and canonical writer.
        monkeypatch.setattr(
            writable,
            "worker_eligibility",
            lambda **_: {"workers": [{"profile_id": "codex-builder", "eligibility": "eligible"}]},
        )
    created = writable_client.post("/api/agents", json=body, headers=headers)
    assert created.status_code == 200, created.text
    receipt = created.json()
    child_id = receipt["entity_ref"]["id"]
    assert writable_client.post("/api/agents", json=body, headers=headers).json() == {
        **receipt,
        "created": False,
    }
    detail = writable_client.get(f"/api/work/agents/{child_id}", headers=headers).json()
    assert detail["supervisor_agent_id"] == lead_id
    assert detail["created_by_agent_id"] is None
    assert detail["origin"] == "human"
    assert detail["grants"] == detail["effective_grants"]
    parent = writable_client.get(f"/api/work/agents/{lead_id}", headers=headers).json()
    assert parent["organization_children"] == [{"id": child_id, "name": body["name"]}]
    graph = writable_client.get("/api/graph", headers=headers).json()
    relations = {(item["kind"], item["from"], item["to"]) for item in graph["edges"]}
    assert ("supervised_by", child_id, lead_id) in relations
    assert ("reports_to", child_id, lead_id) not in relations

    cycle = writable_client.post(
        f"/api/agents/{lead_id}/reconfigure",
        json={
            "expected_revision": parent["revision"],
            "changes": {"supervisor_agent_id": child_id},
            "reason": "A cycle must be refused",
            "idempotency_key": "reject-map-cycle",
        },
        headers=headers,
    )
    assert cycle.status_code == 409, cycle.text
    assert "cycle" in cycle.json()["error"]["message"].lower()
    detach = {
        "expected_revision": detail["revision"],
        "changes": {"supervisor_agent_id": None},
        "reason": "Move child to the top level",
        "idempotency_key": "detach-map-child",
    }
    saved = writable_client.post(
        f"/api/agents/{child_id}/reconfigure", json=detach, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert writable_client.post(
        f"/api/agents/{child_id}/reconfigure", json=detach, headers=headers
    ).json() == {**saved.json(), "created": False}
    after = writable_client.get(f"/api/work/agents/{child_id}", headers=headers).json()
    assert after["supervisor_agent_id"] is None
    assert after["created_by_agent_id"] is None
    assert after["grants"] == detail["grants"]
    assert after["specialization_ref"] == detail["specialization_ref"]
    changed_intent = {**detach, "idempotency_key": "stale-reparent", "changes": {"name": "Stale"}}
    refused = writable_client.post(
        f"/api/agents/{child_id}/reconfigure", json=changed_intent, headers=headers
    )
    assert refused.status_code == 409
    assert writable_client.get(f"/api/work/agents/{child_id}", headers=headers).json() == after


def test_http_method_edit_forks_exact_role_and_retries_binding_without_changing_peers(
    writable, writable_client
):
    library = writable.library_store()
    role_ref = next(
        entry["ref"]
        for entry in library.catalog()["roles"]
        if entry["ref"]["id"] == "frontend-engineer"
    )
    agents = [
        writable.writer().create_agent(
            name=name,
            profile_id="codex-builder",
            specialization_ref=role_ref,
            library_store=library,
            rationale="Independent exact role bindings",
        )
        for name in ("Edited frontend", "Untouched frontend")
    ]
    headers = authorized()
    role_path = "/api/library/role/{source}/{id}/{version}".format(**role_ref)
    original = writable_client.get(role_path, headers=headers)
    assert original.status_code == 200, original.text
    content = deepcopy(original.json()["content"])
    extra = next(
        item["ref"] for item in library.catalog()["skills"] if item["ref"]["id"] == "qa-testing"
    )
    content["core_skills"] = [extra]
    content["conditional_skills"] = []
    library_body = {
        "id": "frontend-agent-methods",
        "expected_version": None,
        "idempotency_key": "agent-methods-library-version",
        "fork_ref": role_ref,
        "content": content,
    }
    saved_role = writable_client.post("/api/library/role", json=library_body, headers=headers)
    assert saved_role.status_code == 200, saved_role.text
    assert (
        writable_client.post("/api/library/role", json=library_body, headers=headers).json()
        == saved_role.json()
    )
    new_ref = saved_role.json()["ref"]
    target_id = agents[0]["entity_ref"]["id"]
    body = {
        "expected_revision": agents[0]["revision"],
        "changes": {"specialization_ref": new_ref},
        "reason": "Use selected methods for this agent",
        "idempotency_key": "agent-methods-bind-version",
    }
    response = writable_client.post(
        f"/api/agents/{target_id}/reconfigure", json=body, headers=headers
    )
    assert response.status_code == 200, response.text
    assert writable_client.post(
        f"/api/agents/{target_id}/reconfigure", json=body, headers=headers
    ).json() == {**response.json(), "created": False}
    edited = writable_client.get(f"/api/work/agents/{target_id}", headers=headers).json()
    other_id = agents[1]["entity_ref"]["id"]
    peer = writable_client.get(f"/api/work/agents/{other_id}", headers=headers).json()
    assert edited["specialization_ref"] == new_ref
    assert peer["specialization_ref"] == role_ref
    assert edited["skills"] == peer["skills"] == []
    assert [
        item["ref"] for item in edited["specialization"]["methods"] if item["kind"] == "core"
    ] == [extra]
    assert writable_client.get(role_path, headers=headers).json() == original.json()
    assert library.compose_role(new_ref).role_ref.as_dict() == new_ref


def test_settings_link_details_support_exact_close_and_keep_action_distinct(
    writable, writable_client
):
    headers = authorized()
    agents = [
        writable.writer().create_agent(
            name=name, profile_id="codex-builder", rationale="Agent settings links"
        )["entity_ref"]["id"]
        for name in ("Lead", "Contributor")
    ]
    for action in ("ask", "handoff_work"):
        opened = writable_client.post(
            "/api/agent-links",
            json={
                "from_agent_id": agents[0],
                "to_agent_id": agents[1],
                "allowed_action": action,
                "deadline_seconds": 3600,
                "reason": "Explicit action selected in settings",
                "idempotency_key": "settings-link-" + action,
            },
            headers=headers,
        )
        assert opened.status_code == 200, opened.text
    sender = writable_client.get(f"/api/work/agents/{agents[0]}", headers=headers).json()
    recipient = writable_client.get(f"/api/work/agents/{agents[1]}", headers=headers).json()
    assert sender["links"] == recipient["links"]
    assert {item["allowed_action"] for item in sender["links"]} == {"ask", "handoff_work"}
    question_link = next(item for item in sender["links"] if item["allowed_action"] == "ask")
    closed = writable_client.post(
        f"/api/agent-links/{question_link['id']}/close",
        json={
            "expected_revision": question_link["revision"],
            "reason": "Question access finished",
            "idempotency_key": "settings-close-question",
        },
        headers=headers,
    )
    assert closed.status_code == 200, closed.text
    refreshed = writable_client.get(f"/api/work/agents/{agents[0]}", headers=headers).json()
    assert [item["allowed_action"] for item in refreshed["links"]] == ["handoff_work"]
    assert refreshed["supervisor_agent_id"] is None
    assert refreshed["grants"] == sender["grants"]
