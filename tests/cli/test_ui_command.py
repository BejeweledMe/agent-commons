"""`agent-commons ui` is the seam a user crosses to reach the role panel.

Every other test of the writable UI builds `UIContext` directly, which is one
layer beside the real path: the command can lose its flag entirely and those
tests stay green.  These enter through the command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    CommonsManager.initialize(root, integrations=(), workspace_name="ui-command")
    return root


def _session(repo: Path) -> str:
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id="ui-command-operator-1234",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
    )
    return str(session["session_id"])


def _serve_spy(captured: dict[str, Any]) -> Any:
    def serve(context: Any, *, port: int, open_browser: bool, emit: Any) -> None:
        captured["context"] = context
        captured["port"] = port
        captured["open_browser"] = open_browser
        emit(port or 49999, "test-token")

    return serve


def test_the_ui_command_defaults_to_a_context_that_cannot_write(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--port", "0", "--no-browser"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["read_only"] is True
    assert captured["context"].writes_enabled is False


def test_enable_writes_binds_the_operator_session_the_cli_resolves(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag has to exist *and* carry a real session into the context.

    A writable server bound to no session would record canonical events under a
    nameless actor, which the ledger has no way to represent honestly.
    """

    session_id = _session(repo)
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--session-id",
            session_id,
            "--json",
            "ui",
            "--port",
            "0",
            "--no-browser",
            "--enable-writes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["read_only"] is False
    assert payload["writer_session_id"] == session_id
    assert captured["context"].writes_enabled is True
    assert captured["context"].writer_session_id == session_id


def test_enable_writes_without_a_session_is_refused_before_binding_a_socket(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--no-browser", "--enable-writes"]
    )

    assert result.exit_code != 0
    assert "session" in result.output
    assert "context" not in captured


def test_catalogue_editing_requires_naming_the_file_it_edits(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse at the command, not by silently editing nothing."""

    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--no-browser", "--enable-catalog-editing"]
    )

    assert result.exit_code != 0
    assert "--role-catalog" in result.output
    assert "context" not in captured


def test_catalogue_editing_is_a_separate_switch_from_role_writes(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing presets and changing what a run is told to do are not one flag."""

    catalogue = tmp_path / "catalog.yaml"
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--session-id",
            _session(repo),
            "--json",
            "ui",
            "--no-browser",
            "--enable-writes",
            "--role-catalog",
            str(catalogue),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["context"].writes_enabled is True
    assert captured["context"].catalog_editing_enabled is False


def test_a_role_catalogue_path_reaches_the_context(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalogue = tmp_path / "catalog.yaml"
    catalogue.write_text("skills: []\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "ui",
            "--no-browser",
            "--role-catalog",
            str(catalogue),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["context"].catalog()["catalog_path"] == str(catalogue)
