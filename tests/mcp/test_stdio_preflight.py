from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent_commons.mcp import entrypoint
from agent_commons.mcp.server import _parser, main
from agent_commons.services import CommonsManager


def test_server_reexports_the_mcp_entrypoint() -> None:
    assert main is entrypoint.main
    assert _parser is entrypoint._parser
    assert _parser().prog == "agent-commons-mcp"


def test_server_module_entrypoint_still_runs_the_preflight(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="mcp-module-preflight")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_commons.mcp.server",
            "--repo",
            str(repo),
            "--state-root",
            str(tmp_path / "absent-state"),
            "--git-executable",
            "/usr/bin/git",
            "--preflight",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema"] == "agent_commons.mcp_preflight.v2"


def test_mcp_preflight_builds_real_fastmcp_catalog_without_state_writes(
    tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="mcp-preflight")
    absent_state = tmp_path / "absent-state"

    exit_code = main(
        [
            "--repo",
            str(repo),
            "--state-root",
            str(absent_state),
            "--git-executable",
            "/usr/bin/git",
            "--preflight",
        ]
    )

    assert exit_code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["schema"] == "agent_commons.mcp_preflight.v2"
    assert len(body["agent_commons_source_sha256"]) == 64
    assert body["tool_count"] > 0
    assert len(body["tool_catalog_sha256"]) == 64
    reviewer = body["worker_catalogs"]["independent_review"]
    assert reviewer["tool_names"] == sorted(reviewer["tool_names"])
    assert "commons_repo_read" in reviewer["tool_names"]
    assert "commons_record_verification" in reviewer["tool_names"]
    assert len(reviewer["tool_catalog_sha256"]) == 64
    assert not absent_state.exists()
