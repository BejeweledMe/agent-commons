"""`agent-commons ui` is the seam a user crosses to reach the role panel.

Every other test of the writable UI builds `UIContext` directly, which is one
layer beside the real path: the command can stop wiring a path through to the
context entirely and those tests stay green.  These enter through the command.
"""

from __future__ import annotations

import json
import subprocess
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
    # A git repository, because the panel's first-run state begins by asking
    # whether there is one: without it every state here would read
    # `setup_not_a_repository` regardless of the workspace.
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True, capture_output=True)
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


@pytest.fixture(autouse=True)
def _isolated_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The operator's own runtime config must not take part in these tests.

    The panel now finds `$XDG_CONFIG_HOME/agent-commons/runtime.yaml` by itself,
    which is the point -- and which would otherwise make every assertion here
    depend on whether the developer running the suite happens to have one.
    """

    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


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


def test_the_panel_starts_on_a_repository_with_no_workspace_and_writes_after_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head scenario of this whole wave, driven through the real command.

    `agent-commons ui` used to refuse here: building `ProjectSessionOwner`
    built a `CommonsManager`, which refuses a directory with no workspace, so
    the one command meant to remove "go back to the terminal and run init"
    ended at the terminal telling you to run init.  Every UI test that thought
    it covered this handed `UIContext` a fabricated `writer_session_id`, which
    is exactly the piece that was broken.

    Everything below happens inside one `serve` call -- one process, one route
    table -- because that is the property under test: the panel is not restarted
    between being unusable and being usable.
    """

    from fastapi.testclient import TestClient

    from agent_commons.ui.server import create_app

    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    seen: dict[str, Any] = {}

    def serve(context: Any, *, port: int, open_browser: bool, emit: Any) -> None:
        emit(port or 49999, "test-token")
        app = create_app(context, token="test-token", port=49999)
        headers = {"Authorization": "Bearer test-token"}
        with TestClient(app, base_url="http://127.0.0.1:49999") as client:
            seen["before"] = client.get("/api/setup", headers=headers).json()
            seen["refused"] = client.post(
                "/api/tasks",
                headers=headers,
                json={"title": "Too early", "description": "before the workspace exists"},
            ).json()
            seen["initialized"] = client.post("/api/setup/initialize", headers=headers)
            seen["after"] = client.get("/api/setup", headers=headers).json()
            seen["recorded"] = client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "title": "Now it works",
                    "description": "recorded after first run",
                    "acceptance_criteria": ["the panel recorded it without a restart"],
                },
            )
            seen["meta"] = client.get("/api/meta", headers=headers).json()

    monkeypatch.setattr("agent_commons.ui.server.serve", serve)
    result = CliRunner().invoke(
        cli, ["--repo", str(repo), "--json", "ui", "--port", "0", "--no-browser"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # The panel came up writable in intent and sessionless in fact: there was
    # nowhere to open a session, and that is a state rather than a failure.
    assert payload["read_only"] is False
    assert payload["writer_session_id"] is None

    assert seen["before"]["state"] == "setup_uninitialized"
    # Refused by name, from the route that exists -- not a 404, and not a raw
    # ConfigurationError from somewhere inside the manager.
    assert seen["refused"]["error"]["code"] == "setup_uninitialized"
    assert seen["initialized"].status_code == 200, seen["initialized"].text
    assert seen["after"]["state"] != "setup_uninitialized"
    assert (repo / ".agent-commons" / "workspace.yaml").is_file()

    # The same process, the same route table, and now a real operator session
    # the panel opened for itself.
    assert seen["recorded"].status_code == 200, seen["recorded"].text
    assert str(seen["recorded"].json()["entity_ref"]["id"]).startswith("task.")
    session_id = seen["meta"]["writer_session_id"]
    assert str(session_id).startswith("session.")
    assert seen["meta"]["writes_enabled"] is True
    shown = CommonsManager(repo, read_only=True).show_session(session_id)
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


def _runtime_config(path: Path, *, catalog: Path | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "profiles:\n"
        "  claude-builder:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n" + (f"catalog: {catalog}\n" if catalog else ""),
        encoding="utf-8",
    )
    return path


def test_launching_needs_no_flag_and_no_second_first_run(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start finds the operator config the panel itself wrote last time.

    This used to pin the opposite -- `launch_enabled is False` without the flag
    was recorded as the desired behaviour -- and that made the whole first-run
    story work exactly once per project.  `setup_state` answers off
    `$XDG_CONFIG_HOME/agent-commons/runtime.yaml` whether or not a flag was
    given, so the second `agent-commons ui` served a panel that reported
    `configured` while `launch_enabled` read the flag and stayed false: set up,
    and unable to launch or edit anything.

    The flag is now only an override.  What it overrides is where the file is
    read from, not whether one is read.
    """

    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))
    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])
    assert result.exit_code == 0, result.output
    # Nothing written yet: unconfigured is a state, and the panel says so
    # instead of refusing to start.
    assert captured["context"].launch_enabled is False
    assert captured["context"].setup_status()["state"] == "setup_unconfigured"

    # What first run leaves behind, at the path first run writes it to.
    _runtime_config(tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml")

    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert captured["context"].launch_enabled is True
    assert captured["context"].setup_status()["state"] == "configured"

    # And the flag still overrides where the file is read from.
    elsewhere = _runtime_config(tmp_path / "elsewhere" / "runtime.yaml")
    result = CliRunner().invoke(
        cli,
        ["--repo", str(repo), "--json", "ui", "--no-browser", "--profile-config", str(elsewhere)],
    )
    assert result.exit_code == 0, result.output
    assert captured["context"].launch_enabled is True


def test_a_config_the_loader_refuses_leaves_the_panel_unconfigured(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adopting whatever happens to sit at the default path would be worse than
    not looking: the loader decides, and a file it refuses is not adopted.

    The panel then serves its first-run screen -- which is where this file gets
    rewritten -- instead of failing at the operator's first Run with a parse
    error from inside the broker.
    """

    broken = tmp_path / "xdg-config" / "agent-commons" / "runtime.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("profiles: [this is not a mapping]\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))

    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert captured["context"].launch_enabled is False
    assert "ignoring the operator runtime config" in result.output


def test_the_catalogue_the_operator_config_names_is_read_without_a_flag(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated config seeds a `catalog.yaml` beside itself and names it.

    That name was only ever read by `adopt_runtime_config`, on the request that
    wrote the config -- so the catalogue the panel had just created went unread
    from the next start on, and `--role-catalog` was the only way back to it.
    """

    catalogue = tmp_path / "xdg-config" / "agent-commons" / "catalog.yaml"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text("skills: []\ntools: []\n", encoding="utf-8")
    _runtime_config(catalogue.with_name("runtime.yaml"), catalog=catalogue)
    captured: dict[str, Any] = {}
    monkeypatch.setattr("agent_commons.ui.server.serve", _serve_spy(captured))

    result = CliRunner().invoke(cli, ["--repo", str(repo), "--json", "ui", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert captured["context"].catalog()["catalog_path"] == str(catalogue)
    assert captured["context"].catalog_editing_enabled is True
    assert json.loads(result.output)["catalog_editing"] is True

    # An explicit path still wins: the flag says where to read the catalogue.
    override = tmp_path / "override.yaml"
    override.write_text("skills: []\ntools: []\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["--repo", str(repo), "--json", "ui", "--no-browser", "--role-catalog", str(override)],
    )
    assert result.exit_code == 0, result.output
    assert captured["context"].catalog()["catalog_path"] == str(override)


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
