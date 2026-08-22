from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.operator_files import (
    STATE_REFUSAL_CODE,
    assert_outside_state_storage,
    assert_outside_workspace,
    replace_operator_file,
)


def test_workspace_check_refuses_a_path_inside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        assert_outside_workspace(workspace / "runtime.yaml", workspace, label="runtime config")


def test_workspace_check_refuses_the_workspace_itself(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ConfigurationError, match="outside the delegated workspace"):
        assert_outside_workspace(workspace, workspace, label="runtime config")


def test_workspace_check_fails_closed_when_the_workspace_cannot_resolve(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="cannot be resolved safely"):
        assert_outside_workspace(
            tmp_path / "runtime.yaml", tmp_path / "no-such-workspace", label="runtime config"
        )


def test_workspace_check_passes_outside_and_without_an_anchor(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert_outside_workspace(tmp_path / "runtime.yaml", workspace, label="runtime config")
    assert_outside_workspace(workspace / "runtime.yaml", None, label="runtime config")


def _state_refusal(target: Path, **kwargs: object) -> ConfigurationError:
    with pytest.raises(ConfigurationError) as captured:
        assert_outside_state_storage(target, label="runtime config", **kwargs)  # type: ignore[arg-type]
    return captured.value


def test_state_check_refuses_the_state_base_itself(tmp_path: Path) -> None:
    base = tmp_path / "state-base"

    error = _state_refusal(base, state_root=base / "workspaces" / "ws.1", state_base=base)

    assert getattr(error, "code", None) == STATE_REFUSAL_CODE
    assert error.details["root_kind"] == "state_base"  # type: ignore[attr-defined]
    assert error.details["refused_root"] == str(base)  # type: ignore[attr-defined]


def test_state_check_refuses_a_path_inside_the_state_base(tmp_path: Path) -> None:
    base = tmp_path / "state-base"

    error = _state_refusal(
        base / "runtime.yaml", state_root=base / "workspaces" / "ws.1", state_base=base
    )

    assert getattr(error, "code", None) == STATE_REFUSAL_CODE
    assert error.details["root_kind"] == "state_base"  # type: ignore[attr-defined]


def test_state_check_refuses_a_path_inside_the_state_root(tmp_path: Path) -> None:
    root = tmp_path / "exact-state-root"

    error = _state_refusal(root / "nested" / "runtime.yaml", state_root=root)

    assert getattr(error, "code", None) == STATE_REFUSAL_CODE
    assert error.details["root_kind"] == "state_root"  # type: ignore[attr-defined]
    assert error.details["refused_root"] == str(root)  # type: ignore[attr-defined]


def test_state_check_refuses_roots_that_do_not_exist_yet(tmp_path: Path) -> None:
    base = tmp_path / "never-created"
    assert not base.exists()

    error = _state_refusal(base / "runtime.yaml", state_root=base / "ws", state_base=base)

    assert getattr(error, "code", None) == STATE_REFUSAL_CODE


def test_state_check_passes_a_sibling_of_the_state_storage(tmp_path: Path) -> None:
    base = tmp_path / "state-base"
    base.mkdir()

    assert_outside_state_storage(
        tmp_path / "config" / "runtime.yaml",
        state_root=base / "workspaces" / "ws.1",
        state_base=base,
        label="runtime config",
    )
    assert_outside_state_storage(
        tmp_path / "state-base-lookalike" / "runtime.yaml",
        state_root=base / "workspaces" / "ws.1",
        state_base=base,
        label="runtime config",
    )


def test_replace_publishes_private_bytes_and_overwrites_atomically(tmp_path: Path) -> None:
    target = tmp_path / "runtime.yaml"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o644)

    replace_operator_file(target, b"profiles: {}\n", label="runtime config")

    assert target.read_bytes() == b"profiles: {}\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["runtime.yaml"]


def test_replace_refuses_a_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    real.write_text("kept\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)

    with pytest.raises(ConfigurationError, match="must not be a symlink"):
        replace_operator_file(link, b"payload", label="runtime config")

    assert real.read_text(encoding="utf-8") == "kept\n"


def test_replace_refuses_a_missing_parent_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="directory does not exist"):
        replace_operator_file(
            tmp_path / "absent" / "runtime.yaml", b"payload", label="runtime config"
        )
