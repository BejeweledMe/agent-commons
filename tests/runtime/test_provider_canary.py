from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    CodexSandbox,
    GrokRunnerProfile,
    GrokSandbox,
    ProfileRegistry,
    ProviderQualificationStore,
    SubprocessRunner,
)
from agent_commons.services.provider_canary import (
    CANARY_SCHEMA,
    _provider_version,
    run_claude_builder_compatibility_canary,
    run_claude_compatibility_canary,
    run_codex_builder_compatibility_canary,
    run_codex_compatibility_canary,
    run_grok_builder_compatibility_canary,
    run_grok_compatibility_canary,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _mcp_executable(tmp_path: Path) -> Path:
    return _executable(
        tmp_path / "agent-commons-mcp",
        "from agent_commons.mcp.server import main\nraise SystemExit(main())\n",
    )


def _profiles(provider: Path, mcp: Path) -> ProfileRegistry:
    profile_id = BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    return ProfileRegistry(
        {
            profile_id: ClaudeRunnerProfile(
                profile_id=profile_id,
                executable=str(provider),
                mcp_executable=str(mcp),
                git_executable="/usr/bin/git",
                model="canary-model",
                permission_mode=ClaudePermissionMode.DONT_ASK,
            )
        }
    )


def _codex_profiles(provider: Path, mcp: Path) -> ProfileRegistry:
    profile_id = BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER
    return ProfileRegistry(
        {
            profile_id: CodexRunnerProfile(
                profile_id=profile_id,
                executable=str(provider),
                mcp_executable=str(mcp),
                git_executable="/usr/bin/git",
                model="canary-model",
                sandbox=CodexSandbox.READ_ONLY,
                trusted_workspace=True,
            )
        }
    )


def _grok_profiles(provider: Path, mcp: Path) -> ProfileRegistry:
    profile_id = BuiltinProfileId.GROK_INDEPENDENT_REVIEWER
    return ProfileRegistry(
        {
            profile_id: GrokRunnerProfile(
                profile_id=profile_id,
                executable=str(provider),
                mcp_executable=str(mcp),
                git_executable="/usr/bin/git",
                model="canary-model",
                sandbox=GrokSandbox.READ_ONLY,
            )
        }
    )


def _builder_profiles(provider: Path, mcp: Path, *, provider_name: str) -> ProfileRegistry:
    if provider_name == "claude":
        profile_id = BuiltinProfileId.CLAUDE_BUILDER
        profile = ClaudeRunnerProfile(
            profile_id=profile_id,
            executable=str(provider),
            mcp_executable=str(mcp),
            git_executable="/usr/bin/git",
            model="canary-model",
            permission_mode=ClaudePermissionMode.ACCEPT_EDITS,
            trusted_workspace=True,
        )
    else:
        profile_id = BuiltinProfileId.CODEX_BUILDER
        profile = CodexRunnerProfile(
            profile_id=profile_id,
            executable=str(provider),
            mcp_executable=str(mcp),
            git_executable="/usr/bin/git",
            model="canary-model",
            sandbox=CodexSandbox.WORKSPACE_WRITE,
            trusted_workspace=True,
        )
    return ProfileRegistry({profile_id: profile})


def test_provider_canary_proves_one_real_terminal_mcp_completion(tmp_path: Path) -> None:
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_claude_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-claude", provider_source)

    report = run_claude_compatibility_canary(
        _profiles(provider, _mcp_executable(tmp_path)),
        wall_time_seconds=60,
    )

    assert report["schema"] == CANARY_SCHEMA
    assert report["ok"] is True, report
    assert report["provider"] == "claude"
    assert report["provider_version"] == "0.0.0 (Claude Code)"
    assert report["model"] == "canary-model"
    assert report["skill_refs"] == ["commons-start"]
    assert report["preflight"]["ok"] is True
    assert report["provider_work_process_started"] is True
    assert report["canonical_state"] == "succeeded"
    assert report["workflow_diagnostic_code"] == "none"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0
    assert report["terminal_tool_rejection_details"] == []
    assert report["terminal_tool_rejection_details_truncated"] is False
    assert report["child_session_closed"] is True


def test_codex_provider_canary_proves_one_real_terminal_mcp_completion(
    tmp_path: Path,
) -> None:
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_codex_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-codex", provider_source)

    report = run_codex_compatibility_canary(
        _codex_profiles(provider, _mcp_executable(tmp_path)),
        wall_time_seconds=60,
    )

    assert report["schema"] == CANARY_SCHEMA
    assert report["ok"] is True, report
    assert report["provider"] == "codex"
    assert report["provider_version"] == "codex-cli 0.0.0"
    assert report["model"] == "canary-model"
    assert report["skill_refs"] == ["commons-start"]
    assert report["preflight"]["ok"] is True
    assert report["provider_work_process_started"] is True
    assert report["canonical_state"] == "succeeded"
    assert report["workflow_diagnostic_code"] == "none"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0
    assert report["child_session_closed"] is True


def test_grok_provider_canary_proves_skill_aware_real_terminal_mcp_completion(
    tmp_path: Path,
) -> None:
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_grok_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-grok", provider_source)

    report = run_grok_compatibility_canary(
        _grok_profiles(provider, _mcp_executable(tmp_path)),
        wall_time_seconds=60,
    )

    assert report["schema"] == CANARY_SCHEMA
    assert report["ok"] is True, report
    assert report["provider"] == "grok"
    assert report["provider_version"] == "grok 0.0.0"
    assert report["model"] == "canary-model"
    assert report["skill_refs"] == ["commons-start"]
    assert report["preflight"]["ok"] is True
    assert report["provider_work_process_started"] is True
    assert report["canonical_state"] == "succeeded"
    assert report["workflow_diagnostic_code"] == "none"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0
    assert report["child_session_closed"] is True


@pytest.mark.parametrize("provider_name", ("claude", "codex"))
def test_builder_canary_proves_scoped_terminal_flow_and_records_receipt(
    tmp_path: Path,
    provider_name: str,
) -> None:
    fixture = f"fake_{provider_name}_mcp_provider.py"
    provider_source = (Path(__file__).parents[1] / "fixtures" / fixture).read_text(encoding="utf-8")
    executable = _executable(tmp_path / f"fake-{provider_name}", provider_source)
    profiles = _builder_profiles(
        executable,
        _mcp_executable(tmp_path),
        provider_name=provider_name,
    )
    canary = (
        run_claude_builder_compatibility_canary
        if provider_name == "claude"
        else run_codex_builder_compatibility_canary
    )

    report = canary(
        profiles,
        wall_time_seconds=60,
        qualification_state_root=tmp_path / "qualified-state",
    )

    assert report["ok"] is True, report
    assert report["purpose"] == "implementation"
    assert report["skill_refs"] == ["commons-start"]
    assert report["initialization"] == {
        "state": "ready",
        "supported": True,
        "blocks_launch": False,
    }
    receipt = ProviderQualificationStore(tmp_path / "qualified-state").read(
        next(iter(profiles.profile_ids))
    )
    assert receipt is not None
    assert receipt.qualified is True
    assert receipt.static_preflight is True
    assert receipt.initialization_probe is True
    assert receipt.behavioral_canary is True


def test_claude_root_session_can_qualify_a_codex_builder(tmp_path: Path) -> None:
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_codex_mcp_provider.py"
    ).read_text(encoding="utf-8")
    executable = _executable(tmp_path / "fake-codex", provider_source)
    profiles = _builder_profiles(
        executable,
        _mcp_executable(tmp_path),
        provider_name="codex",
    )

    report = run_codex_builder_compatibility_canary(
        profiles,
        wall_time_seconds=60,
        requester_client="claude",
    )

    assert report["ok"] is True, report
    assert report["requester_client"] == "claude"
    assert report["provider"] == "codex"
    assert report["canonical_state"] == "succeeded"
    assert report["process_canonical_mismatch"] is False


@pytest.mark.parametrize("provider", ("claude", "codex"))
def test_provider_verification_canary_records_exact_verification(
    tmp_path: Path,
    provider: str,
) -> None:
    fixture = f"fake_{provider}_mcp_provider.py"
    provider_source = (Path(__file__).parents[1] / "fixtures" / fixture).read_text(encoding="utf-8")
    executable = _executable(tmp_path / f"fake-{provider}", provider_source)
    profiles = (
        _profiles(executable, _mcp_executable(tmp_path))
        if provider == "claude"
        else _codex_profiles(executable, _mcp_executable(tmp_path))
    )
    canary = (
        run_claude_compatibility_canary if provider == "claude" else run_codex_compatibility_canary
    )

    report = canary(profiles, purpose="verification", wall_time_seconds=60)

    assert report["ok"] is True, report
    assert report["provider"] == provider
    assert report["purpose"] == "verification"
    assert report["canonical_state"] == "succeeded"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0


def test_grok_builder_canary_proves_skill_aware_scoped_terminal_flow(
    tmp_path: Path,
) -> None:
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_grok_mcp_provider.py"
    ).read_text(encoding="utf-8")
    executable = _executable(tmp_path / "fake-grok", provider_source)

    report = run_grok_builder_compatibility_canary(
        ProfileRegistry(
            {
                BuiltinProfileId.GROK_BUILDER: GrokRunnerProfile(
                    profile_id=BuiltinProfileId.GROK_BUILDER,
                    executable=str(executable),
                    mcp_executable=str(_mcp_executable(tmp_path)),
                    git_executable="/usr/bin/git",
                    model="canary-model",
                    sandbox=GrokSandbox.WORKSPACE,
                    trusted_workspace=True,
                )
            }
        ),
        wall_time_seconds=60,
    )

    assert report["ok"] is True, report
    assert report["provider"] == "grok"
    assert report["purpose"] == "implementation"
    assert report["skill_refs"] == ["commons-start"]
    assert report["canonical_state"] == "succeeded"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0


def test_provider_canary_fails_when_process_exits_without_terminal_tool(
    tmp_path: Path,
) -> None:
    provider = _executable(
        tmp_path / "fake-claude-no-tool",
        """
import json
import sys

if sys.argv[1:] == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": True}))
elif "--version" in sys.argv:
    print("0.0.0 (No Tool Fixture)")
elif "--help" in sys.argv:
    print(
        "--print --verbose --output-format --permission-mode "
        "--no-session-persistence --disable-slash-commands --setting-sources "
        "--mcp-config --strict-mcp-config --allowed-tools --disallowed-tools "
        "--tools --max-budget-usd"
    )
else:
    print(json.dumps({"type": "result", "result": "prose only"}))
""".lstrip(),
    )

    report = run_claude_compatibility_canary(
        _profiles(provider, _mcp_executable(tmp_path)),
        wall_time_seconds=60,
    )

    assert report["ok"] is False
    assert report["process"]["outcome"] == "succeeded", report
    assert report["canonical_state"] == "needs_operator"
    assert report["workflow_diagnostic_code"] == "terminal_tool_not_called"
    assert report["process_canonical_mismatch"] is True
    assert report["terminal_tool_calls"] == 0
    assert report["child_session_closed"] is True


def test_provider_canary_reports_auth_refusal_without_child_or_attempt(tmp_path: Path) -> None:
    provider = _executable(
        tmp_path / "fake-claude-signed-out",
        """
import json
import sys

if sys.argv[1:] == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": False}))
elif sys.argv[1:] == ["mcp", "list"]:
    print("No MCP servers configured")
elif "--version" in sys.argv:
    print("0.0.0 (Claude Code)")
elif "--help" in sys.argv:
    print(
        "--print --verbose --output-format --permission-mode "
        "--no-session-persistence --disable-slash-commands --setting-sources "
        "--mcp-config --strict-mcp-config --allowed-tools --disallowed-tools "
        "--tools --max-budget-usd"
    )
else:
    raise RuntimeError("provider work must not start while signed out")
""".lstrip(),
    )

    report = run_claude_compatibility_canary(
        _profiles(provider, _mcp_executable(tmp_path)),
        wall_time_seconds=60,
    )

    assert report["ok"] is False
    assert report["provider_auth_state"] == "authentication_required"
    assert report["workflow_diagnostic_code"] == "provider_auth_required"
    assert report["provider_work_process_started"] is False
    assert report["canonical_state_before_cleanup"] == "requested"
    assert report["attempt_reserved"] is False
    assert report["child_session_created"] is False


@pytest.mark.parametrize(
    "reported",
    (
        "Claude Code 2.1.0 /Users/example/project",
        "token-shaped-placeholder",
        "provider diagnostics are not a version",
        "2.1.0+token-shaped-placeholder (Claude Code)",
        "02.1.0 (Claude Code)",
        "1000000.1.0 (Claude Code)",
    ),
)
def test_provider_version_drops_noncanonical_provider_content(
    tmp_path: Path,
    reported: str,
) -> None:
    provider = _executable(
        tmp_path / "fake-claude-version",
        f"print({reported!r})\n",
    )
    profile = _profiles(provider, _mcp_executable(tmp_path)).get(
        BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER
    )
    assert isinstance(profile, ClaudeRunnerProfile)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert (
        _provider_version(
            profile,
            workspace_root=workspace,
            runner=SubprocessRunner(),
        )
        is None
    )


@pytest.mark.parametrize(
    "reported",
    (
        "grok 1.2.3",
        "grok 1.0.13 (5e9a58528b76)",
        "grok 1.0.13 (5e9a58528b76) [linux-x64]",
    ),
)
def test_grok_provider_version_accepts_canonical_shapes(
    tmp_path: Path,
    reported: str,
) -> None:
    provider = _executable(tmp_path / "fake-grok-version", f"print({reported!r})\n")
    profile = _grok_profiles(provider, _mcp_executable(tmp_path)).get(
        BuiltinProfileId.GROK_INDEPENDENT_REVIEWER
    )
    assert isinstance(profile, GrokRunnerProfile)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert (
        _provider_version(
            profile,
            workspace_root=workspace,
            runner=SubprocessRunner(),
        )
        == "grok " + reported.split()[1]
    )


@pytest.mark.parametrize(
    "reported",
    (
        "grok 1.2.3 /Users/example/project",
        "grok 1.2.3 token-shaped-placeholder",
        "grok 01.2.3",
        "grok 1000000.2.3",
        "grok 1.2.3 (unterminated",
        "provider diagnostics are not a version",
    ),
)
def test_grok_provider_version_drops_noncanonical_provider_content(
    tmp_path: Path,
    reported: str,
) -> None:
    provider = _executable(tmp_path / "fake-grok-version", f"print({reported!r})\n")
    profile = _grok_profiles(provider, _mcp_executable(tmp_path)).get(
        BuiltinProfileId.GROK_INDEPENDENT_REVIEWER
    )
    assert isinstance(profile, GrokRunnerProfile)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert (
        _provider_version(
            profile,
            workspace_root=workspace,
            runner=SubprocessRunner(),
        )
        is None
    )
