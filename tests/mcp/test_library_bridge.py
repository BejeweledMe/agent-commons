from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_commons.library import LibraryStore
from agent_commons.runtime import (
    BudgetUnit,
    BuiltinProfileId,
    LaunchPlan,
    LaunchPlanner,
    LaunchPurpose,
    TypedRefusal,
    default_profile_registry,
)
from agent_commons.runtime.launch import invocation_fingerprint
from agent_commons.runtime.subprocess_runner import SafeEnvironment
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


def _mcp_arguments(invocation, environment):
    if invocation.provider.value == "claude":
        config = json.loads(invocation.argv[invocation.argv.index("--mcp-config") + 1])
        return config["mcpServers"]["agent-commons"]["args"]
    if invocation.provider.value == "codex":
        values = [
            value
            for index, value in enumerate(invocation.argv)
            if index > 0 and invocation.argv[index - 1] == "-c"
        ]
        return tomllib.loads("\n".join(values))["mcp_servers"]["agent-commons"]["args"]
    template = files("agent_commons").joinpath("resources/templates/GROK_CONFIG.toml").read_text()
    config = tomllib.loads(template)["mcp_servers"]["agent-commons"]
    return [
        re.sub(r"\$\{([^}:]+):-([^}]*)\}", lambda match: environment.get(match[1]) or match[2], arg)
        for arg in config["args"]
    ]


@pytest.mark.parametrize("explicit_root", [True, False])
@pytest.mark.parametrize(
    "profile_id",
    [
        BuiltinProfileId.CODEX_BUILDER,
        BuiltinProfileId.CLAUDE_BUILDER,
        BuiltinProfileId.GROK_BUILDER,
    ],
)
def test_generated_mcp_bridge_reads_exact_custom_companion_without_ambient_library(
    tmp_path: Path,
    monkeypatch,
    profile_id: BuiltinProfileId,
    explicit_root: bool,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="library-bridge")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="library-bridge-parent",
        principal="operator",
        client="codex",
        software="test",
        role="operator",
    )
    manager.session_id = parent["session_id"]
    store = LibraryStore(tmp_path / "private-library") if explicit_root else LibraryStore()
    entry = store.save(
        kind="skill",
        id="local-entry",
        expected_version=None,
        idempotency_key="entry",
        content={"name": "Entry", "description": "", "instruction": "Use the conditional method."},
    )
    companion = store.save(
        kind="skill",
        id="local-companion",
        expected_version=None,
        idempotency_key="companion",
        content={
            "name": "Companion",
            "description": "",
            "instruction": "Pinned companion content.",
        },
    )
    specialization = store.save(
        kind="role",
        id="local-specialist",
        expected_version=None,
        idempotency_key="role",
        content={
            "name": "Specialist",
            "description": "",
            "system_prompt": "Perform a scoped task.",
            "entry_skill": entry["ref"],
            "core_skills": [],
            "conditional_skills": [
                {"ref": companion["ref"], "when": "When verification is needed"}
            ],
        },
    )
    role = manager.create_agent(
        name="Specialist",
        profile_id=profile_id.value,
        rationale="Read service methods",
        specialization_ref=specialization["ref"],
        library_store=store,
    )
    task = manager.create_task(
        title="Read a method",
        description="Use the pinned companion.",
        acceptance_criteria=("The exact companion is readable.",),
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile=profile_id.value,
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
    runtime = DelegationRuntimeService(manager, library_store=store if explicit_root else None)
    scope = runtime._role_scope(manager.snapshot().delegations[delegation["entity_ref"]["id"]])
    assert scope.library_root == store.root.resolve()
    assert scope.library_bundle is not None
    assert "Pinned companion content." not in scope.library_bundle.instruction
    child = manager.start_session(
        stable_instance_id="library-bridge-worker",
        principal="worker",
        client=profile_id.provider.value,
        software="test",
        role="builder",
    )
    manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child["session_id"],
        attempt=1,
    )
    profile = default_profile_registry(
        codex_executable="/bin/echo",
        claude_executable="/bin/echo",
        grok_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/git",
        trusted_workspace=True,
    ).get(profile_id)
    planner = LaunchPlanner.default()
    validation = planner.validate_static(
        LaunchPlan(
            profile_id=profile_id,
            purpose=LaunchPurpose.IMPLEMENTATION,
            instruction=scope.library_bundle.instruction,
            budget_unit=BudgetUnit.PROVIDER_UNITS,
            budget_limit=1,
        ),
        profile,
        workspace_root=manager.repo_root,
    )
    assert not isinstance(validation, TypedRefusal)
    built = planner.build(
        validation,
        workspace_root=manager.repo_root,
        state_root=manager.paths.state_root,
        library_root=scope.library_root,
        delegation_id=delegation["entity_ref"]["id"],
        child_session_id=child["session_id"],
        max_budget_microusd=None,
        worker_purpose="implementation",
        role_tools=(),
        role_grants={},
    )
    environment = SafeEnvironment.from_host().for_child_session(
        child["session_id"],
        provider=profile_id.provider,
        delegation_id=delegation["entity_ref"]["id"],
        state_root=manager.paths.state_root,
        extra_env=built.invocation.extra_env,
    )
    assert "XDG_CONFIG_HOME" not in environment
    args = _mcp_arguments(built.invocation, environment)
    assert args[args.index("--library-root") + 1] == str(scope.library_root)
    assert str(scope.library_root) not in json.dumps(built.as_dict())
    # A later edit cannot change what this exact hired role reads.
    store.save(
        kind="skill",
        id="local-companion",
        expected_version=companion["ref"]["version"],
        idempotency_key="edit",
        content={**companion["content"], "instruction": "New content."},
    )
    if profile_id is BuiltinProfileId.GROK_BUILDER:
        changed = replace(
            built.invocation,
            extra_env={
                **built.invocation.extra_env,
                "AGENT_COMMONS_LIBRARY_ROOT": str(tmp_path / "different"),
            },
        )
    else:
        changed = replace(
            built.invocation,
            argv=tuple(
                item.replace(str(scope.library_root), str(tmp_path / "different"))
                for item in built.invocation.argv
            ),
        )
    assert invocation_fingerprint(changed) != built.invocation_fingerprint

    async def read():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "agent_commons.mcp.server", *args], env=environment
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(
                    "commons_read_skill", {"skill_id": "local-companion"}
                )
                assert not result.isError, result
                body = result.structuredContent
                assert body is not None
                assert body["skill_ref"] == companion["ref"]
                assert body["text"] == "Pinned companion content."
                denied = await session.call_tool("commons_read_skill", {"skill_id": "other-method"})
                assert denied.isError

    asyncio.run(asyncio.wait_for(read(), timeout=20))
    events = json.dumps([event.event for event in manager.events.iter_events()])
    assert "Pinned companion content." not in events and str(scope.library_root) not in events
