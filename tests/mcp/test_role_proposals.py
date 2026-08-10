"""`ask` means the role asks, and the asking is what the record is bound to.

Before this, a role at `ask` received the tool that records directly and every
call refused -- a surface that read as working and could not be.  These tests
enter through the worker's MCP tool and the operator's CLI, the two seams a
real proposal crosses.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.errors import CommonsError, LifecycleConflictError
from agent_commons.mcp.server import build_server
from agent_commons.services import CommonsManager

LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 300,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "provider_units", "limit": 1},
}


class FakeServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any) -> Any:
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register

    def run(self, *, transport: str) -> None:  # pragma: no cover - never reached
        raise AssertionError(f"unexpected transport run: {transport}")


def _workspace(tmp_path: Path, *, level: str, suffix: str = "a") -> dict[str, Any]:
    repo = tmp_path / "repo"
    if not repo.exists():
        repo.mkdir()
        subprocess.run(("/usr/bin/git", "init", "-q", str(repo)), check=True, capture_output=True)
        CommonsManager.initialize(repo, integrations=(), workspace_name="role-proposals")
    parent = CommonsManager(repo, state_root=tmp_path / "state")
    session = parent.start_session(
        stable_instance_id=f"proposal-parent-{suffix}-12345",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    parent.session_id = session["session_id"]
    role = parent.create_agent(
        name="Tech lead",
        profile_id="claude-builder",
        rationale="runs the programme",
        grants={"create_roles": level, "retire_roles": "deny", "open_links": "deny"},
        turnover_budget=4,
        idempotency_key=f"proposal-role-{suffix}",
    )
    task = parent.create_task(
        title="Programme work",
        description="target",
        acceptance_criteria=("done",),
        idempotency_key=f"proposal-task-{suffix}",
    )
    delegation = parent.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key=f"proposal-delegation-{suffix}",
    )
    holder = CommonsManager(repo, state_root=parent.paths.state_root)
    child_session = holder.start_session(
        stable_instance_id=f"proposal-child-{suffix}-123456",
        principal="operator",
        client="claude",
        software="claude-code",
        role="builder",
    )
    parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child_session["session_id"],
        attempt=1,
        idempotency_key=f"proposal-start-{suffix}",
    )
    child = CommonsManager(
        repo, session_id=child_session["session_id"], state_root=parent.paths.state_root
    )
    server = build_server(
        repo,
        manager=child,
        delegation_id=delegation["entity_ref"]["id"],
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    assert isinstance(server, FakeServer)
    return {
        "repo": repo,
        "state_root": tmp_path / "state",
        "parent": parent,
        "child": child,
        "role": role,
        "server": server,
    }


def test_an_ask_role_gets_the_proposing_tool_and_not_the_recording_one(
    tmp_path: Path,
) -> None:
    tools = _workspace(tmp_path, level="ask")["server"].tools
    assert "commons_propose_agent" in tools
    assert "commons_create_agent" not in tools


def test_an_auto_role_gets_the_recording_tool_and_not_the_proposing_one(
    tmp_path: Path,
) -> None:
    tools = _workspace(tmp_path, level="auto")["server"].tools
    assert "commons_create_agent" in tools
    assert "commons_propose_agent" not in tools


def test_a_proposal_changes_nothing_until_a_person_confirms_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, level="ask")
    parent: CommonsManager = workspace["parent"]

    workspace["server"].tools["commons_propose_agent"](
        name="Backend",
        profile_id="claude-builder",
        rationale="the payments surface needs its own owner",
        idempotency_key="proposal-one",
    )
    # A proposal is not a record.
    assert [item["name"] for item in parent.list_agents()] == ["Tech lead"]

    runner = CliRunner()
    listed = runner.invoke(
        cli,
        ["--repo", str(workspace["repo"]), "--state-root", str(workspace["state_root"]),
         "--session-id", parent.session_id, "--json", "agent", "proposals"],
    )
    assert listed.exit_code == 0, listed.output
    pending = json.loads(listed.output)
    assert len(pending) == 1
    assert pending[0]["proposal"]["name"] == "Backend"

    approved = runner.invoke(
        cli,
        ["--repo", str(workspace["repo"]), "--state-root", str(workspace["state_root"]),
         "--session-id", parent.session_id, "--json", "agent", "approve",
         pending[0]["thread_id"], "--idempotency-key", "approve-one"],
    )
    assert approved.exit_code == 0, approved.output
    created = parent.get_agent(json.loads(approved.output)["entity_ref"]["id"])

    assert created["name"] == "Backend"
    assert created["origin"] == "agent"
    assert created["approval"] == "human_confirmed"
    assert created["created_by_agent_id"] == workspace["role"]["entity_ref"]["id"]
    assert created["proposal_ref"] == {"kind": "thread", "id": pending[0]["thread_id"]}


def test_provenance_cannot_be_claimed_without_a_proposal_to_point_at(
    tmp_path: Path,
) -> None:
    """`created_by_agent_id` stops being free text once confirmation is bound."""

    workspace = _workspace(tmp_path, level="ask")
    parent: CommonsManager = workspace["parent"]
    with pytest.raises(CommonsError, match="bind the proposal thread"):
        parent.create_agent(
            name="Attributed to a role that never asked",
            profile_id="claude-builder",
            rationale="laundering provenance",
            created_by_agent_id=workspace["role"]["entity_ref"]["id"],
            approval="human_confirmed",
            idempotency_key="unbound-confirm",
        )


def test_a_confirmation_cannot_change_what_was_proposed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, level="ask")
    parent: CommonsManager = workspace["parent"]
    thread = workspace["server"].tools["commons_propose_agent"](
        name="Backend",
        profile_id="claude-builder",
        rationale="a narrow helper",
        idempotency_key="proposal-two",
    )
    with pytest.raises(LifecycleConflictError, match="cannot change what was proposed"):
        parent.create_agent(
            name="Backend with more authority",
            profile_id="claude-builder",
            rationale="a narrow helper",
            grants={"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
            turnover_budget=2,
            created_by_agent_id=workspace["role"]["entity_ref"]["id"],
            approval="human_confirmed",
            proposal_ref={"kind": "thread", "id": thread["entity_ref"]["id"]},
            idempotency_key="swapped-confirm",
        )


def test_a_proposal_credited_to_a_role_that_did_not_open_it_is_refused(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, level="ask")
    parent: CommonsManager = workspace["parent"]
    other = parent.create_agent(
        name="Bystander",
        profile_id="claude-builder",
        rationale="never proposed anything",
        idempotency_key="bystander",
    )
    thread = workspace["server"].tools["commons_propose_agent"](
        name="Backend",
        profile_id="claude-builder",
        rationale="a narrow helper",
        idempotency_key="proposal-three",
    )
    with pytest.raises(LifecycleConflictError, match="not opened by a session running as"):
        parent.create_agent(
            name="Backend",
            profile_id="claude-builder",
            rationale="a narrow helper",
            created_by_agent_id=other["entity_ref"]["id"],
            approval="human_confirmed",
            proposal_ref={"kind": "thread", "id": thread["entity_ref"]["id"]},
            idempotency_key="misattributed-confirm",
        )
