from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import collaboration_storage
from agent_commons.runtime.collaboration_storage import private_collaboration_root


def test_external_state_keeps_existing_layout_and_does_not_create_it(tmp_path):
    state = tmp_path / "external"
    assert (
        private_collaboration_root(
            state, project_root=tmp_path / "project", workspace_id="workspace.test"
        )
        == state
    )
    assert not state.exists()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_default_platform_roots_are_private_and_deterministic(tmp_path, monkeypatch, platform):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(collaboration_storage.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    project = tmp_path / "project"
    args = {"project_root": project, "workspace_id": "workspace.test"}
    root = private_collaboration_root(project / ".git" / "state", **args)
    expected = (
        tmp_path
        / "user"
        / ("Library/Application Support" if platform == "darwin" else ".local/state")
    )
    assert root.is_relative_to(expected / "agent-commons" / "collaboration-v1")
    assert not root.exists()
    assert private_collaboration_root(project / ".agent-commons/.state", **args) == root
    assert (
        private_collaboration_root(
            project / ".git/state", project_root=project, workspace_id="workspace.other"
        )
        != root
    )
    assert (
        private_collaboration_root(
            tmp_path / "clone/.git/state",
            project_root=tmp_path / "clone",
            workspace_id="workspace.test",
        )
        != root
    )


def test_xdg_override_must_stay_outside_checkout_even_through_symlink(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "user-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(outside))
    args = {"project_root": project, "workspace_id": "workspace.test"}
    assert private_collaboration_root(project / ".state", **args).is_relative_to(outside)
    outside.symlink_to(project, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="outside the workspace"):
        private_collaboration_root(project / ".state", **args)


def test_external_symlinks_are_not_resolved_away_before_store_validation(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    assert (
        private_collaboration_root(
            link, project_root=tmp_path / "project", workspace_id="workspace.test"
        )
        == link
    )
