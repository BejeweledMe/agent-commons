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
    ProfileRegistry,
    SubprocessRunner,
)
from agent_commons.services.provider_canary import (
    CANARY_SCHEMA,
    _provider_version,
    run_claude_compatibility_canary,
    run_codex_compatibility_canary,
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
    assert report["provider_version"] == "0.0.0 (Claude Code)"
    assert report["model"] == "canary-model"
    assert report["preflight"]["ok"] is True
    assert report["provider_work_process_started"] is True
    assert report["canonical_state"] == "succeeded"
    assert report["workflow_diagnostic_code"] == "none"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0
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
    assert report["provider_version"] == "codex-cli 0.0.0"
    assert report["model"] == "canary-model"
    assert report["preflight"]["ok"] is True
    assert report["provider_work_process_started"] is True
    assert report["canonical_state"] == "succeeded"
    assert report["workflow_diagnostic_code"] == "none"
    assert report["process_canonical_mismatch"] is False
    assert report["terminal_tool_calls"] == 1
    assert report["terminal_tool_completions"] == 1
    assert report["terminal_tool_rejections"] == 0
    assert report["child_session_closed"] is True


def test_provider_canary_fails_when_process_exits_without_terminal_tool(
    tmp_path: Path,
) -> None:
    provider = _executable(
        tmp_path / "fake-claude-no-tool",
        """
import json
import sys

if "--version" in sys.argv:
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
