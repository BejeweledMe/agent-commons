from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def test_broker_cli_is_discoverable_bounded_and_feature_configurable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-cli")
    runner = CliRunner()

    help_result = runner.invoke(cli, ["broker", "--help"])
    assert help_result.exit_code == 0
    for command in ("profiles", "preflight", "canary", "attempts", "run", "reconcile"):
        assert command in help_result.output

    run_help = runner.invoke(cli, ["broker", "run", "--help"])
    assert run_help.exit_code == 0
    assert "--idempotency-key" in run_help.output
    assert "--retry" in run_help.output
    for forbidden in ("--command", "--prompt", "--environment", "--executable"):
        assert forbidden not in run_help.output

    canary_help = runner.invoke(cli, ["broker", "canary", "--help"])
    assert canary_help.exit_code == 0
    assert "--confirm-provider-run" in canary_help.output
    assert "--profile" in canary_help.output
    assert "--wall-time-seconds" in canary_help.output
    for forbidden in ("--command", "--prompt", "--environment", "--executable", "--model"):
        assert forbidden not in canary_help.output
    unconfirmed_canary = runner.invoke(cli, ["broker", "canary"])
    assert unconfirmed_canary.exit_code == 2
    assert "--confirm-provider-run" in unconfirmed_canary.output

    profiles = runner.invoke(
        cli,
        ["--repo", str(repo), "--json", "broker", "profiles"],
    )
    assert profiles.exit_code == 0, profiles.output
    values = json.loads(profiles.output)
    assert {item["profile_id"] for item in values} == {
        "codex-builder",
        "codex-independent-reviewer",
        "claude-builder",
        "claude-independent-reviewer",
    }
    assert all("executable" not in item and "argv" not in item for item in values)
    assert not (CommonsManager(repo).paths.state_root / "runtime").exists()

    attempts = runner.invoke(
        cli,
        ["--repo", str(repo), "--json", "broker", "attempts"],
    )
    assert attempts.exit_code == 0, attempts.output
    assert json.loads(attempts.output) == []


def test_broker_preflight_exits_nonzero_for_an_incompatible_runtime(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-preflight-failure")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /usr/bin/false\n"
        "    mcp_executable: /usr/bin/false\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "claude-independent-reviewer",
            "--purpose",
            "independent_review",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["consumed_delegation_attempt"] is False


def test_broker_preflight_validates_the_generated_codex_mcp_contract(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-codex-preflight")
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_codex_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-codex", provider_source)
    mcp = _executable(
        tmp_path / "agent-commons-mcp",
        "from agent_commons.mcp.server import main\nraise SystemExit(main())\n",
    )
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  codex-builder:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        "    git_executable: /usr/bin/git\n"
        "    sandbox: workspace-write\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "codex-builder",
            "--purpose",
            "implementation",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["checks"]["mcp_contract"]["ok"] is True
    assert body["provider_work_process_started"] is False


def test_broker_preflight_reports_a_missing_mcp_executable_precisely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-missing-mcp")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: agent-commons-mcp-missing-for-test\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "claude-independent-reviewer",
            "--purpose",
            "independent_review",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["checks"]["mcp_executable"]["diagnostic_code"] == ("mcp_executable_unavailable")
    assert body["provider_help_process_started"] is False
    assert body["consumed_delegation_attempt"] is False


def test_broker_canary_emits_its_safe_failure_before_status_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-canary-failure")
    monkeypatch.setattr(
        "agent_commons.cli.run_claude_compatibility_canary",
        lambda *_args, **_kwargs: {
            "schema": "agent_commons.provider_compatibility_canary.v1",
            "ok": False,
            "workflow_diagnostic_code": "terminal_tool_not_called",
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "canary",
            "--confirm-provider-run",
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["workflow_diagnostic_code"] == "terminal_tool_not_called"


def test_broker_canary_selects_the_codex_compatibility_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-codex-canary")
    monkeypatch.setattr(
        "agent_commons.cli.run_claude_compatibility_canary",
        lambda *_args, **_kwargs: pytest.fail("Claude canary should not run"),
    )
    monkeypatch.setattr(
        "agent_commons.cli.run_codex_compatibility_canary",
        lambda *_args, **_kwargs: {
            "schema": "agent_commons.provider_compatibility_canary.v1",
            "ok": True,
            "profile_id": "codex-independent-reviewer",
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "canary",
            "--profile",
            "codex-independent-reviewer",
            "--confirm-provider-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile_id"] == "codex-independent-reviewer"


def test_broker_profile_config_rejects_unknown_authority_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-config")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: claude\n"
        "    arbitrary_environment:\n"
        "      SECRET: value\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["error"]["type"] == "ConfigurationError"
    assert error["error"]["safe_next_actions"]
    assert "unsupported fields" in error["error"]["message"]
    assert "SECRET" not in result.output


def test_broker_profile_config_exposes_effective_operator_caps(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-limits")
    config = tmp_path / "runtime.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /usr/bin/false\n"
        "    mcp_executable: /usr/bin/false\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n"
        "limits:\n"
        "  global_concurrency: 1\n"
        "  queue_capacity: 2\n"
        "  provider_concurrency:\n"
        "    claude: 1\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    limits = json.loads(result.output)[0]["operator_limits"]
    assert limits["global_concurrency"] == 1
    assert limits["provider_concurrency"] == 1
    assert limits["queue_capacity"] == 2

    link = tmp_path / "profiles-link.yaml"
    link.symlink_to(config)
    symlinked = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(link),
        ],
    )
    assert symlinked.exit_code == 1
    assert json.loads(symlinked.output)["error"]["type"] == "ConfigurationError"

    writable = tmp_path / "profiles-writable.yaml"
    writable.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    writable.chmod(0o666)
    unsafe_mode = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(writable),
        ],
    )
    assert unsafe_mode.exit_code == 1
    assert "group/world writable" in json.loads(unsafe_mode.output)["error"]["message"]

    workspace_config = repo / "profiles.yaml"
    workspace_config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )
    inside_workspace = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(workspace_config),
        ],
    )
    assert inside_workspace.exit_code == 1
    assert (
        "outside the delegated workspace" in json.loads(inside_workspace.output)["error"]["message"]
    )
