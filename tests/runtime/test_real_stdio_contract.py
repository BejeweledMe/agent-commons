from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    ProfileRegistry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def test_generated_claude_argv_crosses_real_mcp_stdio_and_records_terminal_outcome(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("/usr/bin/git", "init", "-q", str(repo)), check=True)
    (repo / ".gitignore").write_text(
        ".agent-commons/events/\n.agent-commons/manifests/\n.agent-commons/blobs/\n",
        encoding="utf-8",
    )
    source = repo / "src" / "answer.py"
    source.parent.mkdir()
    source.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    CommonsManager.initialize(repo, integrations=(), workspace_name="real-stdio-contract")

    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_claude_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-claude", provider_source)
    mcp = _executable(
        tmp_path / "agent-commons-mcp",
        "from agent_commons.mcp.server import main\nraise SystemExit(main())\n",
    )

    manager = CommonsManager(repo)
    parent = manager.start_session(
        stable_instance_id="real-stdio-parent-session",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Exercise the real provider MCP boundary",
        description="A hermetic provider must review source through real stdio MCP.",
        acceptance_criteria=("the review and delegation finish canonically",),
        priority="high",
        idempotency_key="real-stdio-task",
    )
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect the exact scoped source",),
        independent=True,
        idempotency_key="real-stdio-review",
    )
    delegation = manager.create_delegation(
        target_ref=review["entity_ref"],
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        idempotency_key="real-stdio-delegation",
    )
    profile_id = BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    profiles = ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable=str(provider),
                mcp_executable=str(mcp),
                git_executable="/usr/bin/git",
                permission_mode=ClaudePermissionMode.DONT_ASK,
                max_budget_microusd=1_000_000,
            )
        }
    )

    result = DelegationRuntimeService(manager, profiles=profiles).run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="real-stdio-launch",
    )

    assert result["process"]["outcome"] == "succeeded"
    assert result["delegation"]["state"] == "succeeded"
    assert manager.list_reviews(state="approved")[0]["id"] == review["entity_ref"]["id"]
    assert result["attempt"]["diagnostic_code"] == "none"
    assert "canonical outcome recorded" not in str(result)
