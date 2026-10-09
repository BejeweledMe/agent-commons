from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_commons.services.manager import CommonsManager
from agent_commons.ui import project_registry as registry_module
from agent_commons.ui.project_registry import ProjectRegistry, ProjectRegistryRefusal
from tests.ui.test_project_registry import _recoverable, _repo


def _changed_device(monkeypatch: pytest.MonkeyPatch, state: Path) -> str:
    original = registry_module._directory_identity
    device, inode = original(state).split(":")
    changed = f"{int(device) + 1}:{inode}"

    def identity(path: Path) -> str | None:
        current = original(path)
        return changed if path == state and current is not None else current

    monkeypatch.setattr(registry_module, "_directory_identity", identity)
    return changed


def _tree(path: Path) -> dict[str, object]:
    return {
        str(item.relative_to(path)): (item.read_bytes(), item.stat().st_mtime_ns)
        if item.is_file()
        else None
        for item in path.rglob("*")
    }


def _verify(registry, project, state, **changes):
    return registry.recover_state(
        project["id"],
        changes.pop("expected_revision", project["revision"]),
        **{
            "state_root": state,
            "idempotency_key": "verify-state",
            "verify_existing": True,
            **changes,
        },
    )


def test_explicit_revalidation_preserves_data_receipts_and_unrelated_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, project, old, _ = _recoverable(tmp_path)
    other = registry.register(
        checkout=_repo(tmp_path / "unrelated"),
        workspace_id="workspace.unrelated",
        state_binding=None,
        name="Unrelated",
        idempotency_key="other",
    )["project"]
    changed = _changed_device(monkeypatch, old.paths.state_root)
    before = registry._load()
    project_tree, state_tree = _tree(old.repo_root), _tree(old.paths.state_root)
    assert registry.get(project["id"]).public()["state"] == "identity_conflict"
    assert registry._load() == before
    with pytest.raises(ProjectRegistryRefusal) as default:
        _verify(registry, project, old.paths.state_root, verify_existing=False)
    assert default.value.code == "project_state_not_missing"
    assert registry._load() == before
    result = _verify(registry, project, old.paths.state_root)
    assert result["project"]["state"] == "ready"
    assert result["project"]["id"] == project["id"]
    assert result["project"]["workspace_id"] == project["workspace_id"]
    assert result["project"]["revision"] != project["revision"]
    assert registry.get(project["id"]).state_identity == changed
    assert registry.get(other["id"]).public() == other
    assert all(
        registry._load()["receipts"][key] == value for key, value in before["receipts"].items()
    )
    registry_bytes = registry.path.read_bytes()
    assert _verify(registry, project, old.paths.state_root) == result
    assert registry.path.read_bytes() == registry_bytes
    assert (
        registry.register_existing(old.repo_root, old.paths.state_root)["project"]
        == result["project"]
    )
    assert _tree(old.repo_root) == project_tree
    assert _tree(old.paths.state_root) == state_tree


@pytest.mark.parametrize(
    "failure",
    [
        "different-path",
        "missing",
        "empty",
        "owner",
        "workspace",
        "git",
        "checkout-link",
        "state-link",
        "ancestor-link",
        "non-normal-path",
        "relative-path",
        "stale",
        "manifest",
    ],
)
def test_revalidation_refuses_unverified_bindings_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    registry, project, old, replacement = _recoverable(tmp_path)
    state = old.paths.state_root
    _changed_device(monkeypatch, state)
    changes = {}
    if failure == "different-path":
        state = replacement.paths.state_root
    elif failure == "missing":
        state.rename(tmp_path / "retained-state")
    elif failure == "empty":
        state.rename(tmp_path / "retained-state")
        state.mkdir()
    elif failure in {"owner", "workspace"}:
        foreign_repo = _repo(tmp_path / "foreign")
        CommonsManager.initialize(foreign_repo, integrations=())
        foreign = CommonsManager(foreign_repo, state_root=tmp_path / "foreign-state")
        old.paths.owner_marker.write_bytes(foreign.paths.owner_marker.read_bytes())
        if failure == "workspace":
            (old.paths.commons_root / "workspace.yaml").write_bytes(
                (foreign.paths.commons_root / "workspace.yaml").read_bytes()
            )
    elif failure == "git":
        monkeypatch.setattr(registry_module, "_git_identity", lambda _: str(tmp_path / "other-git"))
    elif failure == "checkout-link":
        moved = tmp_path / "moved-repo"
        old.repo_root.rename(moved)
        old.repo_root.symlink_to(moved, target_is_directory=True)
    elif failure == "state-link":
        moved = tmp_path / "moved-state"
        state.rename(moved)
        state.symlink_to(moved, target_is_directory=True)
    elif failure == "ancestor-link":
        moved = tmp_path.parent / (tmp_path.name + "-moved")
        tmp_path.rename(moved)
        tmp_path.symlink_to(moved, target_is_directory=True)
    elif failure == "non-normal-path":
        state = state / ".." / state.name
    elif failure == "relative-path":
        monkeypatch.chdir(tmp_path)
        state = Path(state.name)
    elif failure == "stale":
        changes["expected_revision"] = "sha256:stale"
    elif failure == "manifest":
        corrupt = old.paths.manifests / "00" / "00" / "bad.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("not a valid manifest")
    before = registry.path.read_bytes()
    with pytest.raises(ProjectRegistryRefusal):
        _verify(registry, project, state, **changes)
    assert registry.path.read_bytes() == before
    if failure == "missing":
        assert not state.exists()


@pytest.mark.parametrize("change", ["mode", "key-revision", "path"])
def test_revalidation_receipt_rejects_changed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    registry, project, old, replacement = _recoverable(tmp_path)
    _changed_device(monkeypatch, old.paths.state_root)
    result = _verify(registry, project, old.paths.state_root)
    arguments = {
        "mode": {"verify_existing": False},
        "key-revision": {"expected_revision": result["project"]["revision"]},
        "path": {"state_root": replacement.paths.state_root},
    }[change]
    before = registry.path.read_bytes()
    with pytest.raises(ProjectRegistryRefusal) as refused:
        _verify(registry, project, old.paths.state_root, **arguments)
    assert refused.value.code == "project_idempotency_mismatch"
    assert registry.path.read_bytes() == before


def test_revalidation_rechecks_replay_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, project, old, _ = _recoverable(tmp_path)
    _changed_device(monkeypatch, old.paths.state_root)
    _verify(registry, project, old.paths.state_root)
    _changed_device(monkeypatch, old.paths.state_root)
    before = registry.path.read_bytes()
    with pytest.raises(ProjectRegistryRefusal) as refused:
        _verify(registry, project, old.paths.state_root)
    assert refused.value.code == "project_state_binding_conflict"
    assert registry.path.read_bytes() == before


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_revalidation_cas_and_identical_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_key: bool
) -> None:
    registry, project, old, _ = _recoverable(tmp_path)
    _changed_device(monkeypatch, old.paths.state_root)

    def recover(index):
        try:
            return _verify(
                registry,
                project,
                old.paths.state_root,
                idempotency_key="same" if same_key else f"verify-{index}",
            )
        except ProjectRegistryRefusal as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert len([result for result in results if isinstance(result, dict)]) == 1
        assert "project_stale" in results


def test_revalidation_replays_after_atomic_save_loses_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, project, old, _ = _recoverable(tmp_path)
    _changed_device(monkeypatch, old.paths.state_root)
    original = registry._save

    def crash_after_save(document):
        original(document)
        raise OSError("synthetic lost response")

    monkeypatch.setattr(registry, "_save", crash_after_save)
    with pytest.raises(OSError, match="synthetic lost response"):
        _verify(registry, project, old.paths.state_root)
    before = registry.path.read_bytes()
    monkeypatch.setattr(registry, "_save", original)
    result = _verify(registry, project, old.paths.state_root)
    assert result["project"]["state"] == "ready"
    assert registry.path.read_bytes() == before


def test_revalidation_rejects_directory_change_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, project, old, _ = _recoverable(tmp_path)
    _changed_device(monkeypatch, old.paths.state_root)
    original = registry_module.open_project_manager

    def change_after_open(*args, **kwargs):
        manager = original(*args, **kwargs)
        _changed_device(monkeypatch, old.paths.state_root)
        return manager

    monkeypatch.setattr(registry_module, "open_project_manager", change_after_open)
    before = registry.path.read_bytes()
    with pytest.raises(ProjectRegistryRefusal) as refused:
        _verify(registry, project, old.paths.state_root)
    assert refused.value.code == "project_recovery_unverified"
    assert registry.path.read_bytes() == before


def test_revalidation_does_not_create_unknown_registry_or_state(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "missing-registry")
    state = tmp_path / "missing-state"
    with pytest.raises(ProjectRegistryRefusal):
        _verify(registry, {"id": "project." + "0" * 32, "revision": "sha256:unknown"}, state)
    assert not registry.root.exists()
    assert not state.exists()
