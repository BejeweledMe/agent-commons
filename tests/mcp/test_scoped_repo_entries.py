"""Scoped snapshots exclude non-regular Git entries without following them."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.mcp.scoped_repo import ScopedRepoReader
from agent_commons.security import SecurityPolicy


def _git(repo, *arguments):
    return subprocess.run(
        ("/usr/bin/git", "-C", str(repo), *arguments), check=True, capture_output=True
    )


def test_snapshot_skips_nested_worktree_symlink_directory_and_missing_index_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Scoped Repo Test")
    (repo / "safe.txt").write_text("safe\n")
    (repo / "deleted.txt").write_text("gone\n")
    _git(repo, "add", "safe.txt", "deleted.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / "deleted.txt").unlink()
    nested_parent = repo / ".claude" / "worktrees"
    nested_parent.mkdir(parents=True)
    for index in range(7):
        nested = nested_parent / f"x{index}"
        _git(repo, "worktree", "add", "--detach", "-q", str(nested), "HEAD")
        (nested / "private.txt").write_text("must not read\n")
    os.symlink("safe.txt", repo / "link.txt")
    (repo / "directory-entry").mkdir()
    head = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},directory-entry")

    reader = ScopedRepoReader(SimpleNamespace(repo_root=repo, policy=SecurityPolicy()))

    assert reader.list_files() == [
        {"path": "safe.txt", "sha256": reader.files["safe.txt"][0], "size_bytes": 5}
    ]
    assert reader.snapshot_diagnostic == {
        "code": "skipped_non_regular_entries",
        "count": 10,
        "examples": [
            ".claude/worktrees/x0",
            ".claude/worktrees/x1",
            ".claude/worktrees/x2",
            ".claude/worktrees/x3",
            ".claude/worktrees/x4",
        ],
    }
    assert "private.txt" not in reader.files


def test_snapshot_refuses_a_symlinked_workspace_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    alias = tmp_path / "repo-alias"
    os.symlink(repo, alias)

    with pytest.raises(ConfigurationError, match="root must not be a symlink"):
        ScopedRepoReader(SimpleNamespace(repo_root=alias, policy=SecurityPolicy()))
