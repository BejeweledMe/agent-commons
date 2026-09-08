from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_commons.ui.project_folder_picker import (
    FOLDER_SELECTION_SCHEMA,
    ProjectFolderPicker,
    expand_user_home,
    inferred_project_name,
)
from agent_commons.ui.project_registry import ProjectRegistryRefusal


def _completed(
    command: object, returncode: int, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    argv = list(command) if isinstance(command, list | tuple) else [str(command)]
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")


def test_headless_linux_and_ci_hosts_are_explicitly_unavailable() -> None:
    picker = ProjectFolderPicker(
        platform="linux",
        env={},
        which=lambda _name: "/usr/bin/zenity",
        runner=lambda *_args, **_kwargs: _completed([], 0, "/tmp/chosen"),
    )
    with pytest.raises(ProjectRegistryRefusal) as refused:
        picker.pick("existing")
    assert refused.value.code == "project_picker_unavailable"
    assert refused.value.status == 503

    ci = ProjectFolderPicker(
        platform="darwin",
        env={"CI": "true"},
        which=lambda _name: "/usr/bin/osascript",
        runner=lambda *_args, **_kwargs: _completed([], 0, "/Users/person/App"),
    )
    with pytest.raises(ProjectRegistryRefusal) as ci_refused:
        ci.pick("parent")
    assert ci_refused.value.code == "project_picker_unavailable"


def test_user_cancel_returns_a_closed_cancelled_dto() -> None:
    commands: list[list[str]] = []

    def runner(command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(command))
        return _completed(command, 0)

    picker = ProjectFolderPicker(
        platform="darwin",
        env={},
        which=lambda _name: "/usr/bin/osascript",
        runner=runner,
    )
    result = picker.pick("parent")
    assert result == {
        "schema": FOLDER_SELECTION_SCHEMA,
        "status": "cancelled",
        "path": None,
        "name": None,
    }
    assert commands[0][:2] == ["/usr/bin/osascript", "-e"]
    assert "Choose the parent folder" in commands[0][2]
    assert "on error number -128" in commands[0][2]


@pytest.mark.parametrize("platform,code", [("darwin", 1), ("linux", 2)])
def test_picker_failure_is_not_reported_as_user_cancel(platform, code):
    picker = ProjectFolderPicker(
        platform=platform,
        env={"DISPLAY": ":0"},
        which=lambda name: "/usr/bin/" + name,
        runner=lambda command, **kwargs: _completed(command, code),
    )
    with pytest.raises(ProjectRegistryRefusal) as refused:
        picker.pick("existing")
    assert refused.value.code == "project_picker_unavailable"


def test_selected_folder_strips_trailing_slash_and_infers_name() -> None:
    def runner(command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(command, 0, "/Users/person/My Project/\n")

    picker = ProjectFolderPicker(
        platform="darwin",
        env={},
        which=lambda _name: "/usr/bin/osascript",
        runner=runner,
    )
    result = picker.pick("existing")
    assert result == {
        "schema": FOLDER_SELECTION_SCHEMA,
        "status": "selected",
        "path": "/Users/person/My Project",
        "name": "My Project",
    }


def test_linux_zenity_is_preferred_and_timeouts_fall_back_explicitly() -> None:
    commands: list[list[str]] = []

    def runner(command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(command))
        return _completed(command, 0, "/home/person/Repo")

    def which(name: str) -> str | None:
        return {"zenity": "/usr/bin/zenity", "kdialog": "/usr/bin/kdialog"}.get(name)

    picker = ProjectFolderPicker(
        platform="linux",
        env={"DISPLAY": ":0"},
        which=which,
        runner=runner,
    )
    selected = picker.pick("existing")
    assert selected["path"] == "/home/person/Repo"
    assert commands[0] == [
        "/usr/bin/zenity",
        "--file-selection",
        "--directory",
        "--title",
        "Choose the project folder",
    ]

    def timed_out(command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(command), timeout=1)

    timed = ProjectFolderPicker(
        platform="linux",
        env={"WAYLAND_DISPLAY": "wayland-0"},
        which=lambda name: "/usr/bin/kdialog" if name == "kdialog" else None,
        runner=timed_out,
    )
    with pytest.raises(ProjectRegistryRefusal) as refused:
        timed.pick("parent")
    assert refused.value.code == "project_picker_unavailable"


def test_invalid_purpose_and_relative_picker_output_are_refused() -> None:
    picker = ProjectFolderPicker(
        platform="darwin",
        env={},
        which=lambda _name: "/usr/bin/osascript",
        runner=lambda command, **_kwargs: _completed(command, 0, "relative/path"),
    )
    with pytest.raises(ProjectRegistryRefusal) as purpose:
        picker.pick("upload")
    assert purpose.value.code == "project_invalid"
    with pytest.raises(ProjectRegistryRefusal) as relative:
        picker.pick("existing")
    assert relative.value.code == "project_picker_unavailable"


def test_home_expansion_is_current_user_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert expand_user_home("~/My App", home=home) == home / "My App"
    assert inferred_project_name(home / "My App") == "My App"
    with pytest.raises(ProjectRegistryRefusal):
        expand_user_home("~root/secret", home=home)
    with pytest.raises(ProjectRegistryRefusal):
        expand_user_home("~/../etc", home=home)
