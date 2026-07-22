from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    CodexSandbox,
    OperatorLimits,
    PolicyViolationError,
    ProfileRegistry,
    RuntimePolicy,
    RuntimeUsage,
    default_profile_registry,
)


def test_profiles_build_fixed_argv_and_keep_instruction_on_stdin(tmp_path) -> None:
    registry = default_profile_registry()
    assert registry.profile_ids == tuple(sorted(BuiltinProfileId, key=lambda item: item.value))

    codex = registry.get(BuiltinProfileId.CODEX_BUILDER)
    with pytest.raises(ConfigurationError, match="trusted_workspace"):
        codex.build_invocation("Implement the exact submitted task", workspace_root=tmp_path)

    trusted_codex = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        executable="/bin/echo",
        trusted_workspace=True,
    )
    invocation = trusted_codex.build_invocation(
        "Implement the exact submitted task", workspace_root=tmp_path
    )
    assert invocation.argv[1:] == (
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "exec",
        "--json",
        "--color",
        "never",
        "-",
    )
    assert invocation.stdin == b"Implement the exact submitted task"
    assert "Implement" not in " ".join(invocation.argv)

    claude = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    with pytest.raises(ConfigurationError, match="delegation binding"):
        claude.build_invocation("Review it", workspace_root=tmp_path)
    invocation = claude.build_invocation(
        "Review it",
        workspace_root=tmp_path,
        state_root=tmp_path / "external-state",
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        max_budget_microusd=250_000,
    )
    assert invocation.stdin == b"Review it"
    assert "--max-budget-usd" in invocation.argv
    assert invocation.argv[invocation.argv.index("--max-budget-usd") + 1] == "0.25"
    assert "dontAsk" in invocation.argv
    assert "--strict-mcp-config" in invocation.argv
    assert "--setting-sources" in invocation.argv
    mcp_config = invocation.argv[invocation.argv.index("--mcp-config") + 1]
    assert f'"command":"{Path("/bin/echo").resolve()}"' in mcp_config
    assert '"--git-executable","/usr/bin/true"' in mcp_config
    assert '"--state-root"' in mcp_config
    assert str(tmp_path / "external-state") in mcp_config
    assert str(tmp_path.resolve()) in mcp_config
    assert "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ" in mcp_config
    assert "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" in (
        invocation.argv
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]
    assert "mcp__agent-commons__commons_orient" in allowed
    assert "mcp__agent-commons__*" not in allowed
    assert "Bash" not in allowed


def test_profile_config_rejects_arbitrary_command_environment_and_unsafe_reviewers() -> None:
    with pytest.raises(ConfigurationError, match="unsupported fields: argv"):
        ProfileRegistry.from_mapping(
            {"profiles": {"codex-builder": {"argv": ["sh", "-c", "danger"]}}}
        )
    with pytest.raises(ConfigurationError, match="unsupported fields: env"):
        ProfileRegistry.from_mapping({"profiles": {"claude-builder": {"env": {"TOKEN": "secret"}}}})
    with pytest.raises(ConfigurationError, match="read-only"):
        CodexRunnerProfile(
            profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
            sandbox=CodexSandbox.WORKSPACE_WRITE,
        )
    with pytest.raises(ConfigurationError, match="dontAsk"):
        ProfileRegistry.from_mapping(
            {
                "profiles": {
                    "claude-independent-reviewer": {
                        "permission_mode": ClaudePermissionMode.PLAN.value
                    }
                }
            }
        )
    with pytest.raises(ConfigurationError, match="basename or an absolute path"):
        ProfileRegistry.from_mapping({"profiles": {"codex-builder": {"executable": "tools/codex"}}})


def test_profile_executables_reject_workspace_path_hijack_and_writable_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hijack = workspace / "bin" / "codex"
    hijack.parent.mkdir()
    hijack.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hijack.chmod(0o700)
    monkeypatch.setenv("PATH", f"{hijack.parent}{os.pathsep}/bin")

    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        CodexRunnerProfile(
            profile_id=BuiltinProfileId.CODEX_BUILDER,
            executable="codex",
            trusted_workspace=True,
        ).build_invocation("Implement", workspace_root=workspace)

    unsafe = tmp_path / "unsafe-provider"
    unsafe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unsafe.chmod(0o777)
    with pytest.raises(ConfigurationError, match="group/world writable"):
        CodexRunnerProfile(
            profile_id=BuiltinProfileId.CODEX_BUILDER,
            executable=str(unsafe),
            trusted_workspace=True,
        ).build_invocation("Implement", workspace_root=workspace)

    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        ClaudeRunnerProfile(
            profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
            executable="/bin/echo",
            mcp_executable=str(hijack),
            permission_mode=ClaudePermissionMode.DONT_ASK,
        ).build_invocation(
            "Review",
            workspace_root=workspace,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        )

    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        ClaudeRunnerProfile(
            profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
            executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable=str(hijack),
            permission_mode=ClaudePermissionMode.DONT_ASK,
        ).build_invocation(
            "Review",
            workspace_root=workspace,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        )


def test_profiles_are_immutable_and_only_fixed_ids_are_accepted() -> None:
    profile = default_profile_registry().get(BuiltinProfileId.CODEX_BUILDER)
    with pytest.raises(FrozenInstanceError):
        profile.executable = "other"  # type: ignore[misc]
    with pytest.raises(ConfigurationError, match="unsupported runner profile"):
        ProfileRegistry.from_mapping({"profiles": {"custom-shell": {}}})


def test_operator_limits_apply_partial_overrides_without_dropping_safe_defaults() -> None:
    limits = OperatorLimits.from_mapping(
        {
            "global_concurrency": 3,
            "provider_concurrency": {"claude": 1},
            "profile_concurrency": {"claude-independent-reviewer": 1},
        }
    )

    assert limits.global_concurrency == 3
    assert limits.provider_concurrency_cap("claude") == 1
    assert limits.provider_concurrency_cap("codex") == 2
    assert limits.profile_concurrency_cap("codex-builder") == 1
    with pytest.raises(PolicyViolationError, match="unsupported fields"):
        OperatorLimits.from_mapping({"shell_command": 1})


def test_claude_reviewer_allows_bounded_review_writes_but_not_test_execution(
    tmp_path,
) -> None:
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    invocation = profile.build_invocation(
        "Review the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]

    assert profile.permission_mode is ClaudePermissionMode.DONT_ASK
    assert "--max-budget-usd" not in invocation.argv
    assert set(allowed.split(",")) == {
        "mcp__agent-commons__commons_orient",
        "mcp__agent-commons__commons_inbox",
        "mcp__agent-commons__commons_list_tasks",
        "mcp__agent-commons__commons_list_delegations",
        "mcp__agent-commons__commons_show_delegation",
        "mcp__agent-commons__commons_list_reviews",
        "mcp__agent-commons__commons_show_review",
        "mcp__agent-commons__commons_list_verifications",
        "mcp__agent-commons__commons_show_verification",
        "mcp__agent-commons__commons_show_artifact",
        "mcp__agent-commons__commons_read_artifact",
        "mcp__agent-commons__commons_workspace_files",
        "mcp__agent-commons__commons_workspace_read",
        "mcp__agent-commons__commons_workspace_search",
        "mcp__agent-commons__commons_complete_review",
        "mcp__agent-commons__commons_record_verification",
        "mcp__agent-commons__commons_delegation_input_needed",
        "mcp__agent-commons__commons_succeed_delegation",
        "mcp__agent-commons__commons_delegation_needs_operator",
    }
    assert "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" in (
        invocation.argv
    )
    assert invocation.argv[invocation.argv.index("--tools") + 1] == ""
    assert "mcp__agent-commons__commons_request_delegation" not in allowed
    assert "mcp__agent-commons__commons_cancel_delegation" not in allowed


def test_claude_builder_cannot_escape_canonical_delegation_lineage(tmp_path) -> None:
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        permission_mode=ClaudePermissionMode.ACCEPT_EDITS,
        trusted_workspace=True,
    )
    invocation = profile.build_invocation(
        "Implement the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]

    assert "mcp__agent-commons__commons_delegation_input_needed" in allowed
    assert "mcp__agent-commons__commons_succeed_delegation" in allowed
    assert "mcp__agent-commons__commons_delegation_needs_operator" in allowed
    assert "mcp__agent-commons__commons_request_delegation" not in allowed
    assert "mcp__agent-commons__commons_cancel_delegation" not in allowed
    assert "Agent,WebFetch,WebSearch" in invocation.argv


def test_claude_verifier_receives_only_the_verification_write_tool(tmp_path: Path) -> None:
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    invocation = profile.build_invocation(
        "Verify the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        worker_purpose="verification",
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]

    assert "mcp__agent-commons__commons_record_verification" in allowed
    assert "mcp__agent-commons__commons_complete_review" not in allowed


def test_runtime_policy_can_only_shrink_and_consumes_depth() -> None:
    parent = RuntimePolicy(
        remaining_depth=2,
        max_fanout=3,
        max_attempts=3,
        max_concurrency=4,
        timeout_seconds=600,
        max_output_bytes=8_192,
        max_budget_microusd=2_000_000,
    )
    child = parent.derive_child(
        max_fanout=2,
        max_attempts=2,
        timeout_seconds=300,
        max_budget_microusd=1_000_000,
    )
    assert child.remaining_depth == 1
    assert child.timeout_seconds == 300

    with pytest.raises(PolicyViolationError, match="timeout_seconds"):
        parent.derive_child(timeout_seconds=601)
    with pytest.raises(PolicyViolationError, match="monetary budget"):
        parent.derive_child(max_budget_microusd=None)
    with pytest.raises(PolicyViolationError, match="depth"):
        RuntimePolicy(remaining_depth=0).derive_child()


@pytest.mark.parametrize(
    ("usage", "message"),
    [
        (RuntimeUsage(active_fanout=1), "fanout"),
        (RuntimeUsage(attempts_started=1), "attempt"),
        (RuntimeUsage(active_concurrency=1), "concurrency"),
    ],
)
def test_runtime_policy_rejects_exhausted_launch_limits(usage: RuntimeUsage, message: str) -> None:
    with pytest.raises(PolicyViolationError, match=message):
        RuntimePolicy().assert_launch_allowed(usage)
