"""`agent-commons ui` is the seam a user crosses to reach the role panel.

Every other test of the writable UI builds `UIContext` directly, which is one
layer beside the real path: the command can stop wiring a path through to the
context entirely and those tests stay green.  These enter through the command.
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


def test_the_ui_command_opens_and_owns_its_own_session_by_default(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody runs `session start` for the panel: it is the session's owner."""

    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--port", "0", "--no-browser"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["read_only"] is False
    assert str(payload["writer_session_id"]).startswith("session.")
    assert captured["context"].writes_enabled is True
    # Ctrl-C (serve returning) closed the panel's session behind itself.
    shown = CommonsManager(repo, read_only=True).show_session(payload["writer_session_id"])
    assert shown["status"] == "closed"


def test_read_only_serves_a_context_that_cannot_write(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--port", "0", "--no-browser", "--read-only"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["read_only"] is True
    assert payload["writer_session_id"] is None
    assert captured["context"].writes_enabled is False


def test_an_externally_selected_session_is_not_adopted(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel holds no nonce for a borrowed session, so it cannot renew or
    close it; it warns and opens its own instead."""

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
        ],
    )

    assert result.exit_code == 0, result.output
    # The warning goes to stderr; the started document is the last stdout line.
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["writer_session_id"] != session_id
    assert session_id in result.output
    assert "cannot renew" in result.output
    # The borrowed session was left strictly alone.
    shown = CommonsManager(repo, read_only=True).show_session(session_id)
    assert shown["status"] == "active"


def test_the_command_carries_no_capability_flags(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the panel may do follows from what is configured, not from a switch.

    The flags that used to gate writes, catalogue editing and launching are
    gone; the two paths that remain only say where a file is read from.
    """

    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    help_text = CliRunner().invoke(cli, ["--repo", str(repo), "ui", "--help"]).output
    for gone in ("--enable-writes", "--enable-catalog-editing", "--enable-launch"):
        assert gone not in help_text
    for kept in ("--read-only", "--role-catalog", "--profile-config"):
        assert kept in help_text
    assert "context" not in captured


def test_a_catalogue_path_is_what_makes_the_panel_able_to_edit_one(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing presets and changing what a run is told to do are still separate:
    a panel that records roles edits no catalogue until one is configured."""

    catalogue = tmp_path / "catalog.yaml"
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert captured["context"].writes_enabled is True
    assert captured["context"].catalog_editing_enabled is False
    assert json.loads(result.output)["catalog_editing"] is False

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
    assert captured["context"].catalog_editing_enabled is True
    assert json.loads(result.output)["catalog_editing"] is True


def test_a_read_only_panel_edits_no_catalogue_even_when_given_one(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--role-catalog says where the catalogue is read from, not that it may be
    written: a read-only panel shows it and registers no route to change it."""

    catalogue = tmp_path / "catalog.yaml"
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
            "--read-only",
            "--role-catalog",
            str(catalogue),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["context"].catalog_editing_enabled is False
    assert json.loads(result.output)["catalog_editing"] is False


def test_launching_needs_no_flag_only_a_runtime_profile_config(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writing panel is launch-capable exactly when a profile config is in
    effect; without one it serves and refuses runs rather than failing to start."""

    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert captured["context"].launch_enabled is False

    config = tmp_path / "runtime.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-builder:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "ui",
            "--no-browser",
            "--profile-config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["context"].launch_enabled is True


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
