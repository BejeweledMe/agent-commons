from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agent_commons import __version__
from agent_commons.cli import cli
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
    assert body["supported_platform"] is True
    assert body["supported_operating_systems"] == ["darwin", "linux"]
    assert body["core_release_stage"] == "alpha"
    assert body["broker_release_stage"] == "experimental_manual_opt_in"
    assert body["state_root_explicit"] is True
    assert body["state_root_exists"] is False
    assert body["read_only"] is True
    assert str(tmp_path) not in result.output
    assert not state_root.exists()


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
