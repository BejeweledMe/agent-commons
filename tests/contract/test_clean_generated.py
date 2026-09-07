"""The cleanup command must prove its narrow scope before deleting anything."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "clean_generated.py"
spec = importlib.util.spec_from_file_location("clean_generated", SCRIPT)
assert spec is not None and spec.loader is not None
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    return tmp_path


def put(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("keep these bytes", encoding="utf-8")
    return path


def test_preview_preserves_bytes_and_apply_removes_only_allowlisted_roots(repo: Path, capsys):
    generated = [put(repo, f"{name}/nested/file") for name in cleanup.TARGETS]
    retained = [
        put(repo, f"{name}/file")
        for name in (
            ".agent-commons/events",
            "artifacts",
            ".venv",
            "node_modules",
            "frontend/work/build",
            "src/agent_commons/ui/static",
        )
    ]
    cleanup.clean(repo)
    assert all(path.read_text() == "keep these bytes" for path in generated + retained)
    assert "Would remove build/" in capsys.readouterr().out
    cleanup.clean(repo, apply=True)
    assert all(not path.exists() for path in generated)
    assert all(path.read_text() == "keep these bytes" for path in retained)
    cleanup.clean(repo, apply=True)
    assert "No disposable" in capsys.readouterr().out


def test_tracked_content_refuses_entire_plan_even_with_alternate_index(repo: Path, monkeypatch):
    first = put(repo, "build/generated")
    tracked = put(repo, "dist/nested/tracked")
    subprocess.run(["git", "-C", str(repo), "add", "dist"], check=True)
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / "unused-index"))
    with pytest.raises(ValueError, match="tracked content"):
        cleanup.clean(repo, apply=True)
    assert first.exists() and tracked.exists()


def test_mixed_case_tracked_root_is_refused_on_every_filesystem(repo: Path):
    tracked = put(repo, "Build/nested/tracked")
    subprocess.run(["git", "-C", str(repo), "add", "Build"], check=True)
    candidate = put(repo, "build/generated")
    with pytest.raises(ValueError, match="tracked content in build"):
        cleanup.clean(repo, apply=True)
    assert tracked.read_text() == "keep these bytes"
    assert candidate.read_text() == "keep these bytes"


@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_deletion_root_refuses_entire_plan(repo: Path, tmp_path: Path, dangling: bool):
    first = put(repo, "build/generated")
    destination = tmp_path / "outside"
    preserved = None if dangling else put(destination, "important")
    (repo / "dist").symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink root"):
        cleanup.clean(repo, apply=True)
    assert first.exists() and (repo / "dist").is_symlink()
    if preserved is not None:
        assert preserved.read_text() == "keep these bytes"


def test_nested_symlinks_are_removed_without_following_the_destination(repo: Path):
    preserved = put(repo, "artifacts/important")
    (repo / "build").mkdir()
    (repo / "build/link").symlink_to(preserved.parent, target_is_directory=True)
    cleanup.clean(repo, apply=True)
    assert preserved.read_text() == "keep these bytes"
    assert not (repo / "build").exists()


def test_non_directory_and_non_root_paths_are_refused(repo: Path):
    file = put(repo, "build")
    with pytest.raises(ValueError, match="non-directory"):
        cleanup.clean(repo, apply=True)
    assert file.exists()
    nested = repo / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="repository root"):
        cleanup.clean(nested, apply=True)


def test_unavailable_git_fails_closed(repo: Path, monkeypatch):
    file = put(repo, "build/generated")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ValueError, match="Cannot verify"):
        cleanup.clean(repo, apply=True)
    assert file.exists()
