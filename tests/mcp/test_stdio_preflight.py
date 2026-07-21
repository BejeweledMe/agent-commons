from __future__ import annotations

import json
from pathlib import Path

from agent_commons.mcp.server import main
from agent_commons.services import CommonsManager


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
    assert "commons_workspace_read" in reviewer["tool_names"]
    assert "commons_record_verification" in reviewer["tool_names"]
    assert len(reviewer["tool_catalog_sha256"]) == 64
    assert not absent_state.exists()
