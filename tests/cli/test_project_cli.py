from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.project_registry import ProjectRegistry
from tests.ui.test_project_registry import _repo


def test_project_recovery_cli_and_read_only_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    repo = _repo(tmp_path / "repo")
    CommonsManager.initialize(repo, integrations=())
    old = CommonsManager(repo, state_root=tmp_path / "old")
    replacement = CommonsManager(repo, state_root=tmp_path / "replacement")
    registry = ProjectRegistry()
    project = registry.register_existing(repo, old.paths.state_root)["project"]
    old.paths.state_root.rename(tmp_path / "retained")
    runner = CliRunner()
    listed = runner.invoke(cli, ["--json", "project", "list"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["projects"][0]["state"] == "identity_conflict"
    assert str(tmp_path) not in listed.output
    command = [
        "--json",
        "project",
        "recover-state",
        project["id"],
        project["revision"],
        "--state-root",
        str(replacement.paths.state_root),
        "--idempotency-key",
        "recover",
    ]
    before = registry.path.read_bytes()
    denied = runner.invoke(cli, ["--read-only", *command])
    assert denied.exit_code == 1
    assert registry.path.read_bytes() == before
    recovered = runner.invoke(cli, command)
    assert recovered.exit_code == 0, recovered.output
    assert json.loads(recovered.output)["project"]["state"] == "ready"
    assert str(tmp_path) not in recovered.output
    retried = runner.invoke(cli, command)
    assert retried.exit_code == 0
    assert json.loads(retried.output) == json.loads(recovered.output)


def test_project_listing_does_not_create_operator_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    result = CliRunner().invoke(cli, ["--read-only", "--json", "project", "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["projects"] == []
    assert not config.exists()
