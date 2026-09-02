from __future__ import annotations

import dataclasses
import json
import os
import random
import tomllib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.mcp.server import (
    IMPLEMENTATION_WORKER_TOOL_NAMES,
    INDEPENDENT_REVIEW_WORKER_TOOL_NAMES,
    VERIFICATION_WORKER_TOOL_NAMES,
)
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    CodexSandbox,
    GrokRunnerProfile,
    GrokSandbox,
    OperatorLimits,
    PolicyViolationError,
    ProfileRegistry,
    RuntimePolicy,
    RuntimeUsage,
    default_profile_registry,
    validate_model_name,
)
from agent_commons.runtime.model import _grok_launch_sandbox, _provider_from_profile_value


def _codex_overrides(argv: tuple[str, ...]) -> dict[str, object]:
    prefix = "mcp_servers.agent-commons."
    values: dict[str, object] = {}
    for index, argument in enumerate(argv):
        if argument != "-c":
            continue
        key, separator, raw_value = argv[index + 1].partition("=")
        if separator and key.startswith(prefix):
            values[key.removeprefix(prefix)] = tomllib.loads(f"value = {raw_value}")["value"]
    return values


def test_builtin_profile_provider_mapping_is_explicit_for_every_profile() -> None:
    expected = {
        BuiltinProfileId.CODEX_BUILDER: "codex",
        BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER: "codex",
        BuiltinProfileId.CLAUDE_BUILDER: "claude",
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER: "claude",
        BuiltinProfileId.GROK_BUILDER: "grok",
        BuiltinProfileId.GROK_INDEPENDENT_REVIEWER: "grok",
    }
    assert {profile_id: profile_id.provider.value for profile_id in BuiltinProfileId} == expected
    with pytest.raises(ConfigurationError, match="unsupported provider prefix"):
        _provider_from_profile_value("future-builder")


def test_profiles_build_fixed_argv_and_keep_instruction_on_stdin(tmp_path) -> None:
    registry = default_profile_registry()
    assert registry.profile_ids == tuple(sorted(BuiltinProfileId, key=lambda item: item.value))
    assert len(registry.profile_ids) == 6

    codex = registry.get(BuiltinProfileId.CODEX_BUILDER)
    with pytest.raises(ConfigurationError, match="trusted_workspace"):
        codex.build_invocation("Implement the exact submitted task", workspace_root=tmp_path)

    trusted_codex = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    )
    invocation = trusted_codex.build_invocation(
        "Implement the exact submitted task",
        workspace_root=tmp_path,
        state_root=tmp_path / "external-state",
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        child_session_id="session.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    assert invocation.argv[1:5] == (
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
    )
    assert "exec" in invocation.argv
    assert "--ignore-user-config" in invocation.argv
    assert "--strict-config" in invocation.argv
    assert invocation.argv[-4:] == ("--json", "--color", "never", "-")
    overrides = _codex_overrides(invocation.argv)
    assert overrides["command"] == str(Path("/bin/echo").resolve())
    assert overrides["required"] is True
    assert set(overrides["enabled_tools"]) == IMPLEMENTATION_WORKER_TOOL_NAMES
    assert overrides["args"] == [
        "--repo",
        str(tmp_path.resolve()),
        "--state-root",
        str(tmp_path / "external-state"),
        "--delegation-id",
        "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        "--git-executable",
        "/usr/bin/true",
        "--session-id",
        "session.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    ]
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
        child_session_id="session.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        max_budget_microusd=250_000,
    )
    assert invocation.stdin == b"Review it"
    assert "--max-budget-usd" in invocation.argv
    assert invocation.argv[invocation.argv.index("--max-budget-usd") + 1] == "0.25"
    assert "dontAsk" in invocation.argv
    assert "--strict-mcp-config" in invocation.argv
    assert "--setting-sources" in invocation.argv
    mcp_config = invocation.argv[invocation.argv.index("--mcp-config") + 1]
    parsed_mcp_config = json.loads(mcp_config)
    assert parsed_mcp_config["mcpServers"]["agent-commons"]["alwaysLoad"] is True
    assert f'"command":"{Path("/bin/echo").resolve()}"' in mcp_config
    assert '"--git-executable","/usr/bin/true"' in mcp_config
    assert '"--state-root"' in mcp_config
    assert str(tmp_path / "external-state") in mcp_config
    assert str(tmp_path.resolve()) in mcp_config
    assert "delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ" in mcp_config
    assert '"--session-id","session.01KXZZZZZZZZZZZZZZZZZZZZZZ"' in mcp_config
    assert "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" in (
        invocation.argv
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]
    assert "mcp__agent-commons__commons_orient" in allowed
    assert "mcp__agent-commons__*" not in allowed
    assert "Bash" not in allowed


def test_grok_profiles_build_fixed_headless_prompt_and_isolated_tools(tmp_path: Path) -> None:
    builder = GrokRunnerProfile(
        profile_id=BuiltinProfileId.GROK_BUILDER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    )
    with pytest.raises(ConfigurationError, match="delegation binding"):
        builder.build_invocation("Implement", workspace_root=tmp_path)

    invocation = builder.build_invocation(
        "Implement the exact submitted task",
        workspace_root=tmp_path,
        state_root=tmp_path / "external-state",
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        child_session_id="session.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    assert invocation.argv[0] == str(Path("/bin/echo").resolve())
    for flag in (
        "--no-auto-update",
        "--no-alt-screen",
        "--always-approve",
        "--sandbox",
        "--cwd",
        "--no-plan",
        "--no-subagents",
        "--disable-web-search",
    ):
        assert flag in invocation.argv
    assert "--worktree" not in invocation.argv
    assert "--resume" not in invocation.argv
    assert "--continue" not in invocation.argv
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == "workspace"
    assert invocation.argv[invocation.argv.index("--cwd") + 1] == str(tmp_path.resolve())
    assert invocation.argv[-2:] == ("-p", "Implement the exact submitted task")
    assert invocation.stdin == b""
    assert invocation.extra_env is not None
    assert invocation.extra_env["GROK_FOLDER_TRUST"] == "0"
    assert invocation.extra_env["AGENT_COMMONS_GROK_MCP_COMMAND"] == str(
        Path("/bin/echo").resolve()
    )
    assert "XAI_API_KEY" not in invocation.extra_env
    grok_config = json.loads(invocation.extra_env["GROK_CONFIG"])
    shell_env = set(grok_config["shell_environment_policy"]["include_only"])
    assert (
        not {
            "AGENT_COMMONS_DELEGATION_ID",
            "AGENT_COMMONS_SESSION_ID",
            "AGENT_COMMONS_STATE_ROOT",
        }
        & shell_env
    )
    builder_native = invocation.argv[invocation.argv.index("--tools") + 1].split(",")
    builder_denied = invocation.argv[invocation.argv.index("--disallowed-tools") + 1].split(",")
    assert "run_terminal_cmd" not in builder_native
    assert "run_terminal_cmd" in builder_denied
    assert {"search_tool", "use_tool"} <= set(builder_native)

    narrowed = builder.build_invocation(
        "Implement narrowly",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        role_tools=("commons_orient",),
    )
    full_rules = {
        value
        for index, value in enumerate(invocation.argv)
        if index > 0 and invocation.argv[index - 1] == "--allow"
    }
    narrow_rules = {
        value
        for index, value in enumerate(narrowed.argv)
        if index > 0 and narrowed.argv[index - 1] == "--allow"
    }
    assert narrow_rules < full_rules
    with pytest.raises(ConfigurationError, match="not part of this profile"):
        builder.build_invocation(
            "Do not widen",
            workspace_root=tmp_path,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
            role_tools=("unknown_tool",),
        )


def test_grok_profile_validation_keeps_builder_trusted_and_reviewer_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigurationError, match="Grok profile"):
        GrokRunnerProfile(profile_id=BuiltinProfileId.CLAUDE_BUILDER)
    with pytest.raises(ConfigurationError, match="Grok profile"):
        GrokRunnerProfile(profile_id=BuiltinProfileId.CODEX_BUILDER)
    with pytest.raises(ConfigurationError, match="read-only"):
        GrokRunnerProfile(
            profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
            sandbox=GrokSandbox.WORKSPACE,
        )
    with pytest.raises(ConfigurationError, match="trusted_workspace"):
        GrokRunnerProfile(
            profile_id=BuiltinProfileId.GROK_BUILDER,
            executable="/bin/echo",
            mcp_executable="/bin/echo",
        ).build_invocation(
            "Implement",
            workspace_root=tmp_path,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        )

    reviewer = GrokRunnerProfile(
        profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        sandbox=GrokSandbox.READ_ONLY,
    )
    if os.sys.platform == "darwin":
        sandbox = tmp_path / ".grok" / "sandbox.toml"
        sandbox.parent.mkdir()
        sandbox.write_text(
            "[profiles.agent-commons-read-only-macos-v1]\n"
            'extends = "read-only"\n'
            "restrict_network = false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    invocation = reviewer.build_invocation(
        "Review",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    expected_sandbox = (
        "agent-commons-read-only-macos-v1" if os.sys.platform == "darwin" else "read-only"
    )
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == expected_sandbox
    native = invocation.argv[invocation.argv.index("--tools") + 1].split(",")
    denied = invocation.argv[invocation.argv.index("--disallowed-tools") + 1].split(",")
    assert not {"run_terminal_cmd", "search_replace", "write_file"} & set(native)
    assert {"run_terminal_cmd", "search_replace", "write_file", "task", "web_search"} <= set(denied)

    with pytest.raises(ValidationError, match="model"):
        dataclasses.replace(reviewer, model="--hostile")
    with pytest.raises(ConfigurationError, match="basename or an absolute path"):
        dataclasses.replace(reviewer, executable="../grok")


def test_grok_reviewer_projects_read_only_around_macos_network_noop() -> None:
    reviewer = GrokRunnerProfile(
        profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
        sandbox=GrokSandbox.READ_ONLY,
    )
    builder = GrokRunnerProfile(
        profile_id=BuiltinProfileId.GROK_BUILDER,
        sandbox=GrokSandbox.WORKSPACE,
        trusted_workspace=True,
    )

    assert _grok_launch_sandbox(reviewer, platform_name="darwin") == (
        "agent-commons-read-only-macos-v1"
    )
    assert _grok_launch_sandbox(reviewer, platform_name="linux") == "read-only"
    assert _grok_launch_sandbox(builder, platform_name="darwin") == "workspace"


def test_grok_macos_reviewer_requires_exact_unshadowed_managed_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_commons.runtime.model import _validate_grok_macos_read_only_profile

    user_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    project_policy = tmp_path / ".grok" / "sandbox.toml"
    project_policy.parent.mkdir()
    expected = (
        "[profiles.agent-commons-read-only-macos-v1]\n"
        'extends = "read-only"\n'
        "restrict_network = false\n"
    )

    with pytest.raises(ConfigurationError, match="missing or invalid"):
        _validate_grok_macos_read_only_profile(tmp_path)

    project_policy.write_text(expected, encoding="utf-8")
    _validate_grok_macos_read_only_profile(tmp_path)

    user_policy = user_home / ".grok" / "sandbox.toml"
    user_policy.parent.mkdir(parents=True)
    user_policy.write_text(
        "[profiles.agent-commons-read-only-macos-v1]\n"
        'extends = "workspace"\n'
        "restrict_network = false\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="conflicts"):
        _validate_grok_macos_read_only_profile(tmp_path)


def test_profile_config_rejects_arbitrary_command_environment_and_unsafe_reviewers() -> None:
    with pytest.raises(ConfigurationError, match="unsupported fields: argv"):
        ProfileRegistry.from_mapping(
            {"profiles": {"codex-builder": {"argv": ["sh", "-c", "danger"]}}}
        )
    with pytest.raises(ConfigurationError, match="unsupported fields: env"):
        ProfileRegistry.from_mapping({"profiles": {"claude-builder": {"env": {"TOKEN": "secret"}}}})
    with pytest.raises(ConfigurationError, match="grok-builder has unsupported fields: env"):
        ProfileRegistry.from_mapping({"profiles": {"grok-builder": {"env": {"TOKEN": "secret"}}}})
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


def test_replacing_a_profiles_model_re_runs_every_rule_that_made_it_safe() -> None:
    """The hire path swaps a model with ``dataclasses.replace(profile,
    model=...)``, and everything that makes a profile safe has to survive it.

    That is the reason the delivery is a ``replace`` on a frozen dataclass and
    not an override threaded through ``RunnerProfile.build_invocation``:
    ``__post_init__`` runs on every reconstruction, so the independent
    reviewer's fixed sandbox and permission mode are re-checked rather than
    inherited from an object validated once, long ago -- and so is the model
    itself, which is the value that becomes an element of a provider's argv.
    """

    reviewer = ProfileRegistry.from_mapping(
        {"profiles": {"codex-independent-reviewer": {"sandbox": CodexSandbox.READ_ONLY.value}}}
    ).get(BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER)

    swapped = dataclasses.replace(reviewer, model="gpt-5.2-codex")
    assert swapped.model == "gpt-5.2-codex"
    assert swapped.sandbox is CodexSandbox.READ_ONLY

    # The reviewer rules are re-run on the replacement, not carried over.
    with pytest.raises(ConfigurationError, match="read-only"):
        dataclasses.replace(reviewer, sandbox=CodexSandbox.WORKSPACE_WRITE)
    claude_reviewer = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    assert dataclasses.replace(claude_reviewer, model="claude-opus-4-6").permission_mode is (
        ClaudePermissionMode.DONT_ASK
    )
    with pytest.raises(ConfigurationError, match="dontAsk"):
        dataclasses.replace(claude_reviewer, permission_mode=ClaudePermissionMode.PLAN)

    # And the model is validated by the replacement itself, so a name that
    # slipped past every earlier gate still cannot reach argv as a flag.
    for hostile in ("--dangerously-skip-permissions", "-m", "two words", ""):
        with pytest.raises(ValidationError, match="model"):
            dataclasses.replace(reviewer, model=hostile)


def test_validate_model_name_is_the_same_rule_the_profiles_run() -> None:
    """One rule with one implementation.  The hire form refuses a model before
    it is recorded and the profile refuses it again at launch; if those were
    two rules, a name could be accepted by the form and rejected by the run --
    or, far worse, the other way round."""

    for accepted in ("claude-opus-4-6", "gpt-5.2-codex", "a", "x" * 256, "vendor/model:v1"):
        assert validate_model_name(accepted) == accepted
        assert (
            dataclasses.replace(
                ClaudeRunnerProfile(profile_id=BuiltinProfileId.CLAUDE_BUILDER), model=accepted
            ).model
            == accepted
        )
    assert validate_model_name(None) is None

    for refused in ("-m", "--model", "", " ", "a b", "a\nb", "x" * 257, ";id", "$(id)"):
        with pytest.raises(ValidationError, match="model"):
            validate_model_name(refused)


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
            mcp_executable="/bin/echo",
            trusted_workspace=True,
        ).build_invocation(
            "Implement",
            workspace_root=workspace,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        )

    unsafe = tmp_path / "unsafe-provider"
    unsafe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unsafe.chmod(0o777)
    with pytest.raises(ConfigurationError, match="group/world writable"):
        CodexRunnerProfile(
            profile_id=BuiltinProfileId.CODEX_BUILDER,
            executable=str(unsafe),
            mcp_executable="/bin/echo",
            trusted_workspace=True,
        ).build_invocation(
            "Implement",
            workspace_root=workspace,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        )

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


@pytest.mark.parametrize(
    "values",
    (
        {"global_concurrency": 0},
        {"queue_wait_seconds": 0},
        {"parent_provider_units": 0},
        {"global_concurrency": 2**60},
        {"queue_capacity": 2**60},
    ),
)
def test_operator_limits_are_positive_or_explicitly_zero_and_javascript_safe(
    values: dict[str, int],
) -> None:
    with pytest.raises(PolicyViolationError, match="JavaScript-safe"):
        OperatorLimits(**values)


def test_zero_queue_capacity_is_the_only_zero_operator_availability_limit() -> None:
    assert OperatorLimits(queue_capacity=0).queue_capacity == 0


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
        "mcp__agent-commons__commons_repo_files",
        "mcp__agent-commons__commons_repo_read",
        "mcp__agent-commons__commons_repo_search",
        "mcp__agent-commons__commons_check_input",
        "mcp__agent-commons__commons_finalize_review",
        "mcp__agent-commons__commons_record_verification",
        "mcp__agent-commons__commons_delegation_input_needed",
        "mcp__agent-commons__commons_delegation_needs_operator",
        "mcp__agent-commons__commons_request_input",
        "mcp__agent-commons__commons_share_progress",
        "mcp__agent-commons__commons_report_blocker",
        "mcp__agent-commons__commons_ack_input",
        "mcp__agent-commons__commons_ack_control",
        "mcp__agent-commons__commons_list_my_threads",
        "mcp__agent-commons__commons_reply_thread",
    }
    assert "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,Agent,WebFetch,WebSearch" in (
        invocation.argv
    )
    assert invocation.argv[invocation.argv.index("--tools") + 1] == "ToolSearch"
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
    assert "mcp__agent-commons__commons_finalize_review" not in allowed


def test_codex_reviewer_matches_the_claude_worker_scope(tmp_path: Path) -> None:
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        sandbox=CodexSandbox.READ_ONLY,
        trusted_workspace=True,
    )

    invocation = profile.build_invocation(
        "Review the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    )
    enabled_tools = set(_codex_overrides(invocation.argv)["enabled_tools"])

    assert enabled_tools == INDEPENDENT_REVIEW_WORKER_TOOL_NAMES
    assert "commons_request_delegation" not in enabled_tools
    assert "commons_cancel_delegation" not in enabled_tools


def test_codex_verifier_receives_only_the_verification_write_tool(tmp_path: Path) -> None:
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        sandbox=CodexSandbox.READ_ONLY,
        trusted_workspace=True,
    )

    invocation = profile.build_invocation(
        "Verify the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        worker_purpose="verification",
    )
    enabled_tools = set(_codex_overrides(invocation.argv)["enabled_tools"])

    assert enabled_tools == VERIFICATION_WORKER_TOOL_NAMES
    assert "commons_complete_review" not in enabled_tools


def test_a_role_tool_selection_narrows_the_launched_argv(tmp_path: Path) -> None:
    """The narrowing has to reach argv, not just a settings object.

    A role selection that stopped at configuration would be the exact shape of
    a guarantee without the guarantee.
    """

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
        role_tools=("commons_repo_read", "commons_finalize_review"),
    )
    allowed = set(invocation.argv[invocation.argv.index("--allowed-tools") + 1].split(","))

    assert "mcp__agent-commons__commons_repo_read" in allowed
    assert "mcp__agent-commons__commons_finalize_review" in allowed
    assert "mcp__agent-commons__commons_repo_search" not in allowed
    # A role that cannot report a terminal outcome is broken, not narrower.
    assert "mcp__agent-commons__commons_succeed_delegation" not in allowed


def test_a_role_cannot_select_a_tool_the_profile_never_had(tmp_path: Path) -> None:
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    with pytest.raises(ConfigurationError, match="not part of this profile"):
        profile.build_invocation(
            "Review the exact target",
            workspace_root=tmp_path,
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
            role_tools=("commons_request_delegation",),
        )


def test_a_codex_role_selection_narrows_the_enabled_mcp_tools(tmp_path: Path) -> None:
    profile = CodexRunnerProfile(
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        sandbox=CodexSandbox.READ_ONLY,
        trusted_workspace=True,
    )
    invocation = profile.build_invocation(
        "Review the exact target",
        workspace_root=tmp_path,
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        role_tools=("commons_repo_read", "commons_finalize_review"),
    )
    enabled = set(_codex_overrides(invocation.argv)["enabled_tools"])

    assert enabled < INDEPENDENT_REVIEW_WORKER_TOOL_NAMES
    assert "commons_repo_search" not in enabled
    assert "commons_finalize_review" in enabled
    assert "commons_succeed_delegation" not in enabled


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


def test_a_fresh_policy_refuses_nothing() -> None:
    """A default policy must not refuse the very first delegation."""

    RuntimePolicy().assert_launch_allowed(RuntimeUsage())


def test_the_stored_policy_shape_is_unchanged_by_operator_ceilings() -> None:
    """Operator ceilings are not granted authority: they must stay out of the
    stored request document and out of its semantic digest, or an operator
    editing config would both break rollback and invalidate every retry key."""

    assert set(RuntimePolicy().as_dict()) == {
        "remaining_depth",
        "max_fanout",
        "max_attempts",
        "max_concurrency",
        "timeout_seconds",
        "max_output_bytes",
        "max_budget_microusd",
    }


def test_operator_limits_bound_the_delegation_tree() -> None:
    """Depth bounds how deep a tree grows and fanout how wide one node is;
    neither bounds the total, which is what this ceiling is for."""

    limits = OperatorLimits(max_delegations_total=2)
    limits.assert_subtree_allowed(RuntimeUsage(subtree_delegations=1))
    with pytest.raises(PolicyViolationError, match="subtree"):
        limits.assert_subtree_allowed(RuntimeUsage(subtree_delegations=2))


def test_admission_guards_exclude_queueable_capacity() -> None:
    """Fanout and concurrency are transient: the admission queue exists to
    absorb them, so they must not fail closed here."""

    policy = RuntimePolicy()
    policy.assert_admission_allowed(RuntimeUsage(active_fanout=5, active_concurrency=5))
    with pytest.raises(PolicyViolationError, match="fanout"):
        policy.assert_launch_allowed(RuntimeUsage(active_fanout=5))


def test_no_policy_field_can_widen_in_any_generated_child() -> None:
    """Reflective over the dataclass, so a field added later and forgotten in
    assert_reduction_of fails here rather than silently amplifying authority."""

    optional = {"max_budget_microusd"}
    names = [
        item.name for item in dataclasses.fields(RuntimePolicy) if item.name != "remaining_depth"
    ]
    rng = random.Random(20260809)
    for _ in range(200):
        values = {name: rng.randint(2, 4096) for name in names}
        parent = RuntimePolicy(remaining_depth=rng.randint(1, 4), **values)
        child = parent.derive_child()
        child.assert_reduction_of(parent)
        assert child.remaining_depth == parent.remaining_depth - 1
        for name in names:
            assert getattr(child, name) <= getattr(parent, name)
        widened = rng.choice(names)
        with pytest.raises(PolicyViolationError):
            parent.derive_child(**{widened: getattr(parent, widened) + 1})
        for name in optional:
            with pytest.raises(PolicyViolationError):
                parent.derive_child(**{name: None})


def test_runtime_policy_round_trips_through_from_mapping() -> None:
    policy = RuntimePolicy(
        remaining_depth=3,
        max_fanout=4,
        max_attempts=2,
        max_concurrency=2,
        timeout_seconds=600,
        max_output_bytes=2048,
        max_budget_microusd=500,
    )
    assert RuntimePolicy.from_mapping(policy.as_dict()) == policy


def test_operator_limits_round_trip_and_reject_unknown_fields() -> None:
    limits = OperatorLimits(max_delegations_total=6)
    restored = OperatorLimits.from_mapping(limits.as_dict())
    assert restored.max_delegations_total == 6
    with pytest.raises(PolicyViolationError, match="unsupported fields"):
        OperatorLimits.from_mapping({"max_waves": 3})


def test_raising_writable_profile_concurrency_fails_at_configuration_load() -> None:
    """The operator learns the guard is missing before a run starts, not after
    two processes are already writing to one checkout."""

    with pytest.raises(PolicyViolationError, match="worktree isolation"):
        OperatorLimits(profile_concurrency={"claude-builder": 2})
    with pytest.raises(PolicyViolationError, match="codex-builder"):
        OperatorLimits.from_mapping({"profile_concurrency": {"codex-builder": 3}})


def test_raising_read_only_profile_concurrency_is_allowed() -> None:
    """Independent reviewers never write, so they need no worktree isolation.
    The cap is still the minimum across all three operator tiers."""

    limits = OperatorLimits(
        global_concurrency=4,
        provider_concurrency={"claude": 3},
        profile_concurrency={"claude-independent-reviewer": 3},
    )
    assert limits.profile_concurrency_cap("claude-independent-reviewer") == 3
    assert limits.profile_concurrency_cap("claude-builder") == 1

    narrowed = OperatorLimits(
        global_concurrency=4,
        provider_concurrency={"claude": 2},
        profile_concurrency={"claude-independent-reviewer": 3},
    )
    assert narrowed.profile_concurrency_cap("claude-independent-reviewer") == 2


def test_runtime_config_parses_and_validates_the_demo_flag(tmp_path: Path) -> None:
    from agent_commons.services.delegation_runtime import load_runtime_configuration

    body = (
        "profiles:\n"
        "  claude-builder:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n"
    )

    without = tmp_path / "plain.yaml"
    without.write_text(body, encoding="utf-8")
    assert load_runtime_configuration(without).demo is False

    demo = tmp_path / "demo.yaml"
    demo.write_text("demo: true\n" + body, encoding="utf-8")
    assert load_runtime_configuration(demo).demo is True

    bad = tmp_path / "bad.yaml"
    bad.write_text("demo: yes-please\n" + body, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="demo must be a boolean"):
        load_runtime_configuration(bad)
