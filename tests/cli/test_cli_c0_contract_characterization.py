"""C0 characterization of the legacy CLI's public transport boundary.

These tests intentionally exercise the Click application as a caller does.  They
pin the current compatibility surface; they do not prescribe a replacement UI,
MCP, or service API.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager

TOP_LEVEL_COMMANDS = (
    "init",
    "ui",
    "chat",
    "search",
    "support",
    "session",
    "orient",
    "inbox",
    "objective",
    "task",
    "delegation",
    "agent",
    "broker",
    "thread",
    "artifact",
    "review",
    "verification",
    "finding",
    "decision",
    "handoff",
    "claim",
    "event",
    "receipt",
    "views",
    "index",
    "doctor",
)


def test_each_registered_top_level_command_has_a_help_surface() -> None:
    """Every currently registered family stays reachable for migration inventory."""

    runner = CliRunner()
    top_level = runner.invoke(cli, ["--help"])

    assert top_level.exit_code == 0, top_level.output
    for command in TOP_LEVEL_COMMANDS:
        assert command in top_level.output
        command_help = runner.invoke(cli, [command, "--help"])
        assert command_help.exit_code == 0, command_help.output


def test_json_support_success_is_compact_and_read_only_without_a_workspace(tmp_path: Path) -> None:
    """A diagnostic success is machine-readable without operational side effects."""

    repo = tmp_path / "uninitialized-repo"
    state_root = tmp_path / "unavailable-state"
    repo.mkdir()

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
    payload = json.loads(result.output)
    assert (
        result.output
        == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert payload["schema"] == "agent_commons.support.v1"
    assert payload["read_only"] is True
    assert payload["canonical_workspace_available"] is False
    assert payload["state_root_exists"] is False
    assert not state_root.exists()


def test_json_refusal_preserves_typed_error_and_exit_one(tmp_path: Path) -> None:
    """A malformed typed reference fails through the shared JSON error transport."""

    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="c0-cli-contract")

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "review",
            "request",
            "--target-ref",
            "not-a-ref",
            "--target-revision",
            "evt.0123456789ABCDEFGHJKMNPQRS",
            "--criterion",
            "correct",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "ValidationError"
    assert payload["error"]["code"] == "invalid_typed_ref"
    assert payload["error"]["details"] == {
        "allowed_kinds": [
            "artifact",
            "decision",
            "delegation",
            "event",
            "finding",
            "handoff",
            "manifest",
            "objective",
            "review",
            "task",
            "thread",
            "verification",
        ],
        "example": "artifact:<id>",
        "field": "target_ref",
    }
