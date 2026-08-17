"""A staff-changing tool exists for a worker only when its role may use it.

The grant is the switch.  A tool that were always present and refused itself at
call time would be a wider surface than the model claims, and a grant with no
tool behind it would be configuration nothing can act on -- the two failure
modes this branch is trying not to repeat.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.mcp.server import build_server
from agent_commons.runtime.model import BuiltinProfileId, ClaudeRunnerProfile
from agent_commons.services import CommonsManager

GOVERNANCE_TOOLS = ("commons_create_agent", "commons_retire_agent", "commons_open_agent_link")
LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 300,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "provider_units", "limit": 1},
}


_AUTOMATIC_LEVEL_WITHHELD = (
    "the automatic grant level is withheld until its guarantees hold "
    "(docs/audits/2026-08-10-standing-roles-review.md, remediation step 1); "
    "restored later in this branch"
)


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


def _workspace(tmp_path: Path, *, grants: dict[str, str], budget: int | None) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("/usr/bin/git", "init", "-q", str(repo)), check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="role-governance")
    parent = CommonsManager(repo, state_root=tmp_path / "state")
    session = parent.start_session(
        stable_instance_id="role-governance-parent-1234",
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
        grants=grants,
        turnover_budget=budget,
        idempotency_key="governance-role",
    )
    task = parent.create_task(
        title="Programme work",
        description="target for the role-bound run",
        acceptance_criteria=("done",),
        idempotency_key="governance-task",
    )
    delegation = parent.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="governance-delegation",
    )
    child_manager = CommonsManager(repo, state_root=parent.paths.state_root)
    child_session = child_manager.start_session(
        stable_instance_id="role-governance-child-12345",
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
        idempotency_key="governance-start",
    )
    child = CommonsManager(
        repo,
        session_id=child_session["session_id"],
        state_root=parent.paths.state_root,
    )
    return {
        "repo": repo,
        "parent": parent,
        "child": child,
        "role": role,
        "delegation": delegation,
    }


def _server(workspace: dict[str, Any]) -> FakeServer:
    server = build_server(
        workspace["repo"],
        manager=workspace["child"],
        delegation_id=workspace["delegation"]["entity_ref"]["id"],
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    assert isinstance(server, FakeServer)
    return server


def test_a_stored_auto_grant_is_effective_at_ask_while_the_level_is_withheld(
    tmp_path: Path,
) -> None:
    """The automatic level is withheld (remediation step 1), so a stored `auto`
    behaves exactly like `ask`: the worker receives the proposing tool, never
    the recording one, and an automatic action refuses naming the withhold."""

    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        budget=4,
    )
    tools = _server(workspace).tools
    assert "commons_create_agent" not in tools
    assert "commons_propose_agent" in tools

    with pytest.raises(LifecycleConflictError, match="withheld"):
        workspace["child"].create_agent(
            name="Backend",
            profile_id="claude-builder",
            rationale="an automatic hire under a stored auto grant",
            idempotency_key="withheld-automatic-create",
        )

    proposed = tools["commons_propose_agent"](
        name="Backend",
        profile_id="claude-builder",
        rationale="the payments surface needs its own owner",
        idempotency_key="withheld-proposal",
    )
    approved = workspace["parent"].approve_agent_proposal(
        proposed["entity_ref"]["id"],
        idempotency_key="withheld-approve",
    )
    record = workspace["parent"].get_agent(approved["entity_ref"]["id"])
    assert record["origin"] == "agent"
    assert record["approval"] == "human_confirmed"
    assert record["created_by_agent_id"] == workspace["role"]["entity_ref"]["id"]


def test_a_role_without_the_grant_never_sees_the_staff_tools(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "deny", "retire_roles": "deny", "open_links": "deny"},
        budget=None,
    )
    tools = _server(workspace).tools
    for name in GOVERNANCE_TOOLS:
        assert name not in tools


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_the_grant_brings_exactly_its_own_tool(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        budget=4,
    )
    tools = _server(workspace).tools
    assert "commons_create_agent" in tools
    assert "commons_retire_agent" not in tools
    assert "commons_open_agent_link" not in tools


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_the_worker_tool_creates_a_narrower_role_with_its_lineage(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        budget=4,
    )
    server = _server(workspace)
    created = server.tools["commons_create_agent"](
        name="Backend",
        profile_id="claude-builder",
        rationale="the payments surface needs its own owner",
        idempotency_key="worker-created-role",
        create_roles="ask",
        turnover_budget=2,
    )
    record = workspace["parent"].get_agent(created["entity_ref"]["id"])

    assert record["origin"] == "agent"
    assert record["approval"] == "automatic"
    assert record["created_by_agent_id"] == workspace["role"]["entity_ref"]["id"]
    assert record["rationale"] == "the payments surface needs its own owner"


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_the_worker_tool_cannot_reach_past_its_own_authority(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        budget=4,
    )
    server = _server(workspace)
    with pytest.raises(LifecycleConflictError, match="strictly narrower"):
        server.tools["commons_create_agent"](
            name="Peer",
            profile_id="claude-builder",
            rationale="wants the same authority",
            idempotency_key="worker-peer-role",
            create_roles="auto",
            turnover_budget=2,
        )


@pytest.mark.skip(reason=_AUTOMATIC_LEVEL_WITHHELD)
def test_a_granted_tool_reaches_the_launched_argv(tmp_path: Path) -> None:
    """The grant has to change what the provider is actually allowed to call."""

    workspace = _workspace(
        tmp_path,
        grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
        budget=4,
    )
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        trusted_workspace=True,
    )
    granted = profile.build_invocation(
        "Do the bounded work",
        workspace_root=workspace["repo"],
        delegation_id=workspace["delegation"]["entity_ref"]["id"],
        role_grants={"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"},
    )
    plain = profile.build_invocation(
        "Do the bounded work",
        workspace_root=workspace["repo"],
        delegation_id=workspace["delegation"]["entity_ref"]["id"],
    )
    granted_tools = set(granted.argv[granted.argv.index("--allowed-tools") + 1].split(","))
    plain_tools = set(plain.argv[plain.argv.index("--allowed-tools") + 1].split(","))

    assert "mcp__agent-commons__commons_create_agent" in granted_tools
    assert "mcp__agent-commons__commons_retire_agent" not in granted_tools
    assert granted_tools - plain_tools == {"mcp__agent-commons__commons_create_agent"}
