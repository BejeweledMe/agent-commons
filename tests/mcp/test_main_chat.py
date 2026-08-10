"""The main chat: where a person states the work and hears back.

A per-role panel is for stepping in on one role.  This is the standing
conversation with everyone at the top, and it is only a conversation if the
roles can answer -- so the worker's own tools are the seam these tests use.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.errors import LifecycleConflictError
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


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("/usr/bin/git", "init", "-q", str(repo)), check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="main-chat")
    human = CommonsManager(repo, state_root=tmp_path / "state")
    session = human.start_session(
        stable_instance_id="chat-human-window-000001",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    human.session_id = session["session_id"]
    return {"repo": repo, "state_root": tmp_path / "state", "human": human}


def _run_as(workspace: dict[str, Any], agent_id: str, key: str) -> tuple[CommonsManager, str]:
    human: CommonsManager = workspace["human"]
    task = human.create_task(
        title=f"work for {key}",
        description="binds a session to a role",
        acceptance_criteria=("bound",),
        idempotency_key=f"{key}-task",
    )
    delegation = human.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        on_behalf_of_agent_id=agent_id,
        idempotency_key=f"{key}-delegation",
    )
    holder = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    child = holder.start_session(
        stable_instance_id=f"chat-child-{key}".ljust(24, "0")[:40],
        principal="operator",
        client="claude",
        software="claude-code",
        role="builder",
    )
    human.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child["session_id"],
        attempt=1,
        idempotency_key=f"{key}-start",
    )
    worker = CommonsManager(
        workspace["repo"],
        session_id=child["session_id"],
        state_root=workspace["state_root"],
    )
    server = build_server(
        workspace["repo"],
        manager=worker,
        delegation_id=delegation["entity_ref"]["id"],
        binding_wait_seconds=0,
        server_factory=FakeServer,
    )
    assert isinstance(server, FakeServer)
    return worker, server  # type: ignore[return-value]


def _architects(workspace: dict[str, Any]) -> tuple[str, str]:
    human: CommonsManager = workspace["human"]
    first = human.create_agent(
        name="System design architect",
        profile_id="claude-builder",
        rationale="owns the shape of the system",
        idempotency_key="chat-arch-1",
    )
    second = human.create_agent(
        name="Delivery architect",
        profile_id="claude-builder",
        rationale="owns how it ships",
        idempotency_key="chat-arch-2",
    )
    return first["entity_ref"]["id"], second["entity_ref"]["id"]


def test_two_architects_share_one_chat_rather_than_two(workspace: dict[str, Any]) -> None:
    """One thread with several recipients, not two threads merged in a view.

    Merging separate threads would invent an ordering that no record has.
    """

    first, second = _architects(workspace)
    human: CommonsManager = workspace["human"]
    human.open_engagement(
        subject="Ship the payments rewrite",
        body="Start with the parts that touch settlement.",
        idempotency_key="chat-open",
    )
    chats = human.list_engagements()

    assert len(chats) == 1
    assert chats[0]["addressed_roles"] == sorted([first, second])
    assert [message["body"] for message in chats[0]["messages"]] == [
        "Start with the parts that touch settlement."
    ]


def test_a_role_reads_the_chat_it_is_addressed_in_and_answers(
    workspace: dict[str, Any],
) -> None:
    """Feedback has to be able to come back, or it is not a chat."""

    first, _ = _architects(workspace)
    human: CommonsManager = workspace["human"]
    human.open_engagement(
        subject="Ship the payments rewrite",
        body="Start with settlement.",
        idempotency_key="chat-open",
    )
    _, server = _run_as(workspace, first, "arch1")

    visible = server.tools["commons_list_my_threads"]()
    assert [item["thread_type"] for item in visible] == ["engagement"]
    assert visible[0]["messages"][0]["body"] == "Start with settlement."

    server.tools["commons_reply_thread"](
        thread_id=visible[0]["thread_id"],
        expected_revision=visible[0]["revision"],
        body="Settlement first means the reconciliation job has to move too.",
        idempotency_key="chat-reply",
    )
    chats = human.list_engagements()

    assert len(chats[0]["messages"]) == 2
    assert "reconciliation" in chats[0]["messages"][1]["body"]


def test_a_role_cannot_reply_where_it_was_not_addressed(workspace: dict[str, Any]) -> None:
    """The reply tool must not become a way into every conversation."""

    first, _ = _architects(workspace)
    human: CommonsManager = workspace["human"]
    private = human.open_thread(
        thread_type="decision_request",
        subject="A conversation with somebody else",
        desired_outcome="not this role's business",
        to=("operator",),
        idempotency_key="chat-private",
    )
    _, server = _run_as(workspace, first, "arch1")

    assert server.tools["commons_list_my_threads"]() == []
    with pytest.raises(LifecycleConflictError, match="addressed in"):
        server.tools["commons_reply_thread"](
            thread_id=private["entity_ref"]["id"],
            expected_revision=private["revision"],
            body="reading somebody else's mail",
            idempotency_key="chat-intrude",
        )


def test_a_role_created_after_the_chat_is_reported_as_unaddressed(
    workspace: dict[str, Any],
) -> None:
    """Recipients are canonical; a view must not quietly rewrite them."""

    _architects(workspace)
    human: CommonsManager = workspace["human"]
    human.open_engagement(
        subject="Ship the payments rewrite",
        body="Start with settlement.",
        idempotency_key="chat-open",
    )
    latecomer = human.create_agent(
        name="Security architect",
        profile_id="claude-builder",
        rationale="joined after the chat opened",
        idempotency_key="chat-arch-3",
    )
    chats = human.list_engagements()

    assert chats[0]["unaddressed_roles"] == [latecomer["entity_ref"]["id"]]
    assert latecomer["entity_ref"]["id"] not in chats[0]["addressed_roles"]


def test_the_chat_is_reachable_from_the_command_line(workspace: dict[str, Any]) -> None:
    _architects(workspace)
    runner = CliRunner()
    base = [
        "--repo", str(workspace["repo"]), "--state-root", str(workspace["state_root"]),
        "--session-id", workspace["human"].session_id, "--json",
    ]
    opened = runner.invoke(
        cli,
        [*base, "chat", "open", "--subject", "Ship it", "--message", "Here is the task.",
         "--idempotency-key", "cli-chat"],
    )
    assert opened.exit_code == 0, opened.output

    shown = runner.invoke(cli, [*base, "chat", "show"])
    assert shown.exit_code == 0, shown.output
    chats = json.loads(shown.output)
    assert chats[0]["subject"] == "Ship it"
    assert [message["body"] for message in chats[0]["messages"]] == ["Here is the task."]
