"""Characterization tests for provider-only delegation instruction composition."""

from __future__ import annotations

import hashlib

from agent_commons.runtime import BuiltinProfileId
from agent_commons.services.delegation_instruction import (
    DelegationInstructionInput,
    compose_delegation_instruction,
)


def test_independent_review_instruction_is_byte_stable() -> None:
    """A structural seam must not silently change the instruction workers receive."""

    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.characterization",
            target_kind="task",
            target_id="task.characterization",
            target_revision="evt.characterization",
            purpose="independent_review",
            target_profile="claude-independent-reviewer",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=50_000,
            budget_unit="micro_usd",
        ),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
    )

    # This is provider-visible behavior. Any change requires an explicit
    # compatibility decision and a behavioral canary, not a silent refactor.
    assert "first use commons_list_verifications" in instruction
    assert "inspect only their evidence refs" in instruction
    assert "use commons_list_tasks once to locate the exact target id" in instruction
    assert "commons_read_artifact exactly once for each necessary artifact_ref" in instruction
    assert "do not call commons_show_artifact first" in instruction
    assert "Do not inventory unrelated workspace files" in instruction
    assert "Begin\nthe required terminal sequence no later than 40 seconds" in instruction
    assert "mandatory soft deadline" in instruction
    assert "commons_finalize_review" in instruction
    assert "commons_succeed_delegation" not in instruction
    assert "Supply only verdict and summary" in instruction
    assert "satisfy every evidence\nprecondition" in instruction
    assert "attempt finalization first" in instruction
    assert "server derives the operation identity" in instruction
    assert (
        "Set verdict to exactly one of approved, changes_requested, rejected, or abstained"
        in instruction
    )
    assert "do not invent a synonym such as approve, accept, pass, or needs_changes" in instruction
    assert "If the call succeeds, stop immediately" in instruction
    # ADR 0025 adds exact image/build reads; next profile requires a fresh canary.
    assert "commons_read_output_image" in instruction
    assert "manifest-only reads do not satisfy approval evidence" in instruction
    assert "read every text entry fully" in instruction
    assert hashlib.sha256(instruction.encode()).hexdigest() == (
        "f6e97fad21818e0a21e151a966070e10a471ce3d76f864d26729accf51d0937f"
    )


def test_verification_instruction_requires_exact_verification_terminal_protocol() -> None:
    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.verification",
            target_kind="task",
            target_id="task.verification",
            target_revision="evt.verification",
            purpose="verification",
            target_profile="claude-independent-reviewer",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
    )

    record = instruction.index("commons_record_verification")
    refresh = instruction.index("commons_show_delegation", record)
    succeed = instruction.index("commons_succeed_delegation", refresh)
    assert record < refresh < succeed
    assert "verification:<id>" in instruction
    assert "commons_show_review" not in instruction
    assert "prose-only answer or successful process exit" in instruction
    assert "typed needs-operator or input-needed outcome" in instruction


def test_implementation_instruction_gives_one_exact_terminal_sequence() -> None:
    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.implementation",
            target_kind="task",
            target_id="task.implementation",
            target_revision="evt.implementation",
            purpose="implementation",
            target_profile="claude-builder",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
    )

    refresh = instruction.index("first call commons_show_delegation")
    succeed = instruction.index("commons_succeed_delegation", refresh)
    assert refresh < succeed
    assert 'delegation_id="delegation.implementation"' in instruction
    assert 'result_refs=["task:task.implementation"]' in instruction
    assert "immediately preceding read" in instruction
    assert "Do not try a stale revision or incomplete argument set" in instruction


def test_grok_instructions_use_provider_native_terminal_tool_names() -> None:
    review = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.grok-review",
            target_kind="review",
            target_id="review.grok",
            target_revision="evt.grok-review",
            purpose="independent_review",
            target_profile="grok-independent-reviewer",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
    )
    implementation = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.grok-builder",
            target_kind="task",
            target_id="task.grok",
            target_revision="evt.grok-builder",
            purpose="implementation",
            target_profile="grok-builder",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.GROK_BUILDER,
    )

    assert "agent-commons__commons_finalize_review" in review
    assert "agent-commons__commons_succeed_delegation" in implementation
    assert "mcp__agent-commons__commons_finalize_review" not in review
    assert "mcp__agent-commons__commons_succeed_delegation" not in implementation
    for instruction in (review, implementation):
        assert "Grok exposes MCP integrations through its native search_tool and\nuse_tool" in (
            instruction
        )
        assert "Agent Commons is\nconfigured as the MCP server named exactly agent-commons" in (
            instruction
        )
        assert "Call search_tool with a query equal to the exact fully-qualified tool name" in (
            instruction
        )
        assert "Call use_tool with tool_name equal to that exact fully-qualified name" in (
            instruction
        )
        assert "agent-commons__commons_show_delegation" in instruction
        assert "Never attempt to call an agent-commons__commons_* name as a direct" in instruction
        assert "successful process exit without the required terminal use_tool completion" in (
            instruction
        )
        assert "do not widen the worker tool catalog" in instruction
    assert "sole exception to the native-tool restriction" in review


def test_implementation_startup_commands_override_login_environment_without_writes(tmp_path):
    import json
    import os
    import subprocess
    import sys

    from agent_commons.services import CommonsManager
    from agent_commons.services.delegation_instruction import DelegationStartupInput

    repo = tmp_path / "repo space ' $(touch injected) `touch injected2`"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=())
    state = tmp_path / "state space ' $(touch injected3)"
    manager = CommonsManager(repo, state_root=state)
    child = manager.start_session(
        stable_instance_id="explicit-child",
        principal="worker",
        client="codex",
        software="tests",
        role="builder",
    )
    foreign_repo = tmp_path / "foreign-repo"
    foreign_repo.mkdir()
    CommonsManager.initialize(foreign_repo, integrations=())
    foreign = CommonsManager(foreign_repo, state_root=tmp_path / "foreign-state")
    foreign_session = foreign.start_session(
        stable_instance_id="foreign-child",
        principal="worker",
        client="codex",
        software="tests",
        role="builder",
    )
    executable = tmp_path / "cli space ' $(touch injected4)"
    executable.write_text(f"#!{sys.executable}\nfrom agent_commons.cli import cli\ncli()\n")
    executable.chmod(0o700)
    startup = DelegationStartupInput(repo, state, child["session_id"], str(executable))
    text = compose_delegation_instruction(
        DelegationInstructionInput(
            "delegation.startup",
            "task",
            "task.startup",
            "evt.startup",
            "implementation",
            "codex-builder",
            0,
            60,
            1,
            1,
            1,
            "provider_units",
        ),
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        startup=startup,
    )
    commands = text.split("```sh\n", 1)[1].split("\n```", 1)[0].splitlines()
    env = os.environ.copy()
    env.update(
        {
            "AGENT_COMMONS_STATE_ROOT": str(foreign.paths.state_root),
            "AGENT_COMMONS_STATE_BASE": str(tmp_path / "foreign-base"),
            "AGENT_COMMONS_SESSION_ID": foreign_session["session_id"],
        }
    )
    before = {
        str(path): path.read_bytes()
        for root in (repo / ".agent-commons", state)
        for path in root.rglob("*")
        if path.is_file()
    }
    # The old ambient startup command reproduces the refusal before the fix.
    failed = subprocess.run(
        [str(executable), "--repo", str(repo), "--read-only", "doctor"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert failed.returncode != 0 and "workspace" in (failed.stdout + failed.stderr).lower()
    for command in commands:
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert "--read-only" in commands[0]
    assert "All coordination writes and terminal outcomes must use the injected scoped MCP" in text
    assert not any(
        (tmp_path / name).exists() for name in ("injected", "injected2", "injected3", "injected4")
    )
    after = {
        str(path): path.read_bytes()
        for root in (repo / ".agent-commons", state)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert manager.sessions.require_active(child["session_id"]).session_id == child["session_id"]
    assert "nonce" not in json.dumps({"commands": commands}).lower()


def test_startup_missing_cli_is_honest_and_never_changes_reviewer_instructions(tmp_path):
    from agent_commons.services.delegation_instruction import DelegationStartupInput

    startup = DelegationStartupInput(tmp_path, tmp_path / "state", "session.bound", None)
    inputs = DelegationInstructionInput(
        "delegation.startup",
        "task",
        "task.startup",
        "evt.startup",
        "implementation",
        "codex-builder",
        0,
        60,
        1,
        1,
        1,
        "provider_units",
    )
    text = compose_delegation_instruction(
        inputs, profile_id=BuiltinProfileId.CODEX_BUILDER, startup=startup
    )
    assert "No trusted Agent Commons CLI was available" in text
    assert "Do not guess a CLI from PATH, install one, or create a session" in text
    assert "needs-operator outcome honestly" in text
    assert "```sh" not in text
    reviewer = BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER
    assert compose_delegation_instruction(
        inputs, profile_id=reviewer, startup=startup
    ) == compose_delegation_instruction(inputs, profile_id=reviewer)
