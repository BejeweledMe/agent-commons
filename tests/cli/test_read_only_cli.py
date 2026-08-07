from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from agent_commons import __version__
from agent_commons.cli import cli
from agent_commons.runtime.source_contract import agent_commons_source_sha256
from agent_commons.services import CommonsManager


def test_version_is_available_without_opening_a_workspace() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"agent-commons, version {__version__}"


def test_support_report_is_secret_free_and_does_not_disclose_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo-with-private-name"
    repo.mkdir()
    state_root = tmp_path / "state-with-private-name"

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(state_root),
            "--read-only",
            "--json",
            "support",
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["schema"] == "agent_commons.support.v1"
    assert body["agent_commons_version"] == __version__
    assert body["agent_commons_source_sha256"] == agent_commons_source_sha256()
    assert body["supported_platform"] is True
    assert body["supported_operating_systems"] == ["darwin", "linux"]
    assert body["core_release_stage"] == "alpha"
    assert body["broker_release_stage"] == "experimental_manual_opt_in"
    assert body["state_root_explicit"] is True
    assert body["state_config_source"] == "flag:state-root"
    assert body["state_mode"] == "exact"
    assert body["state_owner_status"] == "workspace-unavailable"
    assert body["state_root_exists"] is False
    assert body["read_only"] is True
    assert str(tmp_path) not in result.output
    assert not state_root.exists()


def test_support_paths_are_opt_in_and_command_line_base_overrides_root_env(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "private-repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="support")
    environment_root = tmp_path / "environment-root"
    command_base = tmp_path / "command-base"
    runner = CliRunner()

    hidden = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(command_base),
            "--read-only",
            "--json",
            "support",
        ],
        env={"AGENT_COMMONS_STATE_ROOT": str(environment_root)},
    )
    shown = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(command_base),
            "--read-only",
            "--json",
            "support",
            "--show-paths",
        ],
        env={"AGENT_COMMONS_STATE_ROOT": str(environment_root)},
    )

    hidden_body = json.loads(hidden.output)
    shown_body = json.loads(shown.output)
    assert hidden_body["state_config_source"] == "flag:state-base"
    assert hidden_body["state_mode"] == "base"
    assert "resolved_state_root" not in hidden_body
    assert str(tmp_path) not in hidden.output
    assert shown_body["resolved_state_base"] == str(command_base)
    assert shown_body["resolved_state_root"].startswith(str(command_base / "workspaces"))
    assert not environment_root.exists()
    assert not command_base.exists()


def test_support_does_not_build_state_paths_from_an_invalid_workspace_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "private-repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="support")
    workspace_config = repo / ".agent-commons" / "workspace.yaml"
    config = yaml.safe_load(workspace_config.read_text(encoding="utf-8"))
    config["workspace_id"] = "../../outside"
    workspace_config.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    state_base = tmp_path / "operator-state"

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(state_base),
            "--read-only",
            "--json",
            "support",
            "--show-paths",
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["workspace_id"] is None
    assert body["state_owner_status"] == "workspace-unavailable"
    assert body["resolved_state_root"] == str(state_base)
    assert "outside" not in body["resolved_state_root"]


def test_read_only_inspection_never_creates_operational_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="read-only")
    writer_state = tmp_path / "writer-state"
    writer = CommonsManager(repo, state_root=writer_state)
    session = writer.start_session(
        stable_instance_id="read-only-test-writer",
        principal="operator",
        client="test",
        software="pytest",
        role="builder",
    )
    writer.session_id = session["session_id"]
    writer.create_task(
        title="Canonical task",
        description="This task remains readable without operational state.",
        acceptance_criteria=("canonical inspection succeeds",),
        idempotency_key="read-only-canonical-task",
    )
    unavailable_state = tmp_path / "must-not-be-created"
    runner = CliRunner()

    tasks = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(unavailable_state),
            "--read-only",
            "--json",
            "task",
            "list",
        ],
    )
    doctor = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(unavailable_state),
            "--read-only",
            "--json",
            "doctor",
        ],
    )
    sessions = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(unavailable_state),
            "--read-only",
            "--json",
            "session",
            "show",
        ],
    )
    attempts = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(unavailable_state),
            "--read-only",
            "--json",
            "broker",
            "attempts",
            "--diagnostic",
        ],
    )

    assert tasks.exit_code == 0, tasks.output
    assert json.loads(tasks.output)[0]["title"] == "Canonical task"
    assert doctor.exit_code == 0, doctor.output
    assert json.loads(doctor.output)["ok"] is True
    assert sessions.exit_code == 0
    assert json.loads(sessions.output) == []
    assert attempts.exit_code == 0, attempts.output
    assert json.loads(attempts.output) == []
    assert not unavailable_state.exists()


def test_read_only_mode_rejects_writes_before_operational_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="read-only-write")
    unavailable_state = tmp_path / "must-not-be-created"

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-root",
            str(unavailable_state),
            "--read-only",
            "--json",
            "task",
            "create",
            "--title",
            "Forbidden",
            "--description",
            "No write is allowed.",
            "--acceptance-criterion",
            "never written",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["message"] == "this manager was opened read-only"
    assert not unavailable_state.exists()
