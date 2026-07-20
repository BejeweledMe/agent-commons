from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.config import CommonsPaths
from agent_commons.errors import ConfigurationError


def test_layout_rejects_symlinked_canonical_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commons = repo / ".agent-commons"
    outside = tmp_path / "outside"
    commons.mkdir(parents=True)
    outside.mkdir()
    (commons / "events").symlink_to(outside, target_is_directory=True)

    paths = CommonsPaths.for_workspace(repo, state_root=tmp_path / "state")
    with pytest.raises(ConfigurationError, match="symlinked event directory"):
        paths.ensure_layout()

    assert list(outside.iterdir()) == []


def test_layout_rejects_symlinked_blob_parent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commons = repo / ".agent-commons"
    outside = tmp_path / "outside"
    commons.mkdir(parents=True)
    outside.mkdir()
    (commons / "blobs").symlink_to(outside, target_is_directory=True)

    paths = CommonsPaths.for_workspace(repo, state_root=tmp_path / "state")
    with pytest.raises(ConfigurationError, match="symlinked blob root directory"):
        paths.ensure_layout()

    assert list(outside.iterdir()) == []


def test_layout_rejects_symlinked_operational_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    state = tmp_path / "state"
    state.symlink_to(outside, target_is_directory=True)

    paths = CommonsPaths.for_workspace(repo, state_root=state)
    with pytest.raises(ConfigurationError, match="symlinked operational state"):
        paths.ensure_layout()

    assert list(outside.iterdir()) == []


def test_layout_rejects_symlinked_custom_canonical_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    canonical = repo / "shared-state"
    canonical.symlink_to(outside, target_is_directory=True)

    paths = CommonsPaths.for_workspace(
        repo,
        commons_root="shared-state",
        state_root=tmp_path / "state",
    )
    with pytest.raises(ConfigurationError, match="symlinked canonical workspace"):
        paths.ensure_layout()

    assert list(outside.iterdir()) == []
