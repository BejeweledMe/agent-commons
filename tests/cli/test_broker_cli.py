from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager


def test_broker_cli_is_discoverable_bounded_and_feature_configurable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-cli")
    runner = CliRunner()

    help_result = runner.invoke(cli, ["broker", "--help"])
    assert help_result.exit_code == 0
    for command in ("profiles", "attempts", "run", "reconcile"):
        assert command in help_result.output

    run_help = runner.invoke(cli, ["broker", "run", "--help"])
    assert run_help.exit_code == 0
    assert "--idempotency-key" in run_help.output
    assert "--retry" in run_help.output
    for forbidden in ("--command", "--prompt", "--environment", "--executable"):
        assert forbidden not in run_help.output

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
    assert "unsupported fields" in error["error"]["message"]
    assert "SECRET" not in result.output

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
