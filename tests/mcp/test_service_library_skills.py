from __future__ import annotations

import json

import pytest

from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.library import LibraryStore
from agent_commons.mcp.server import build_server
from agent_commons.services import CommonsManager
from tests.mcp.test_worker_scope import FakeRuntime, FakeServer, _workspace


def test_live_worker_reads_only_its_exact_retained_methods(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    workspace = _workspace(tmp_path)
    parent = workspace["parent"]
    store = LibraryStore()
    ref = next(
        item["ref"] for item in store.catalog()["roles"] if item["ref"]["id"] == "frontend-engineer"
    )
    role = parent.create_agent(
        name="Frontend",
        profile_id="claude-builder",
        rationale="Bound service methods",
        specialization_ref=ref,
    )
    task = parent.create_task(
        title="Implement a page",
        description="A scoped frontend task",
        acceptance_criteria=("The page works",),
    )
    delegation = parent.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    session = parent.start_session(
        stable_instance_id="service-library-worker",
        principal="worker",
        client="claude",
        software="claude-code",
        role="implementation-author",
    )
    started = parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=session["session_id"],
        attempt=1,
    )
    child = CommonsManager(
        workspace["repo"], session_id=session["session_id"], state_root=parent.paths.state_root
    )
    server = build_server(
        workspace["repo"],
        manager=child,
        runtime=FakeRuntime(),
        delegation_id=delegation["entity_ref"]["id"],
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    read = server.tools["commons_read_skill"]
    skill = "web-frontend-engineering"
    first = read(skill, limit=128)
    second = read(skill, offset=first["next_offset"], limit=128)
    exact = next(item for item in store.allowed_skill_refs(ref) if item["id"] == skill)
    assert first["skill_ref"] == exact
    assert first["text"] + second["text"] == store.read_file(exact)["text"][:256]
    with pytest.raises(LifecycleConflictError):
        read("computer-vision-modeling-and-training")
    with pytest.raises(ValidationError):
        read(skill, "../../private")
    with pytest.raises(ValidationError):
        read(skill, limit=16001)
    encoded = json.dumps([item.event for item in parent.events.iter_events()])
    assert first["text"] not in encoded and "system_prompt" not in encoded
    server.tools["commons_delegation_needs_operator"](
        delegation["entity_ref"]["id"],
        started["revision"],
        "invalid_result",
        "Stop this worker",
        "end-library-reader",
    )
    with pytest.raises(LifecycleConflictError):
        read(skill)
