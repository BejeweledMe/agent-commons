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
    TelemetryEvent,
    TelemetryKind,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


class CollectingTelemetry:
    capture_content = False

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def test_behavioral_canary_crosses_generated_real_mcp_stdio_and_finalizes_canonically(
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

    external_state_root = tmp_path / "external-state"
    manager = CommonsManager(repo, state_root=external_state_root)
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

    telemetry = CollectingTelemetry()
    service = DelegationRuntimeService(manager, profiles=profiles, telemetry=telemetry)
    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="real-stdio-launch",
    )

    assert result["process"]["outcome"] == "succeeded"
    assert result["delegation"]["state"] == "succeeded"
    assert result["delegation"]["result_refs"] == [review["entity_ref"]]
    assert manager.list_reviews(state="approved")[0]["id"] == review["entity_ref"]["id"]
    assert result["attempt"]["diagnostic_code"] == "none"
    assert "canonical outcome recorded" not in str(result)
    joined = service.list_attempts(diagnostic=True)
    assert joined[0]["canonical_state"] == "succeeded"
    assert joined[0]["process_canonical_mismatch"] is False
    assert joined[0]["terminal_tool_calls"] == 1
    assert joined[0]["terminal_tool_rejections"] == 0
    assert joined[0]["terminal_tool_completions"] == 1
    assert [event.kind for event in telemetry.events][-2:] == [
        TelemetryKind.CANONICAL_FINALIZATION_STARTED,
        TelemetryKind.CANONICAL_FINALIZATION_COMPLETED,
    ]
    assert telemetry.events[-1].terminal_tool_calls == 1
    assert telemetry.events[-1].process_canonical_mismatch is False
