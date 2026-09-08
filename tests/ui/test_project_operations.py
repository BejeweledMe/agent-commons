from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_commons.ui.project_operations import ProjectOperations
from agent_commons.ui.project_registry import ProjectRegistry, ProjectRegistryRefusal


def test_new_project_requires_a_confirmed_inspection_and_can_retry_after_initialization(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    target = tmp_path / "new-project"

    preview = operations.inspect("new", str(target), "New project")
    result = operations.create(preview["inspection_id"], "new-project")
    retry = operations.create(preview["inspection_id"], "new-project")

    assert retry == result
    assert result["project"]["available"] is True
    assert (target / ".agent-commons" / "workspace.yaml").is_file()


def test_existing_ordinary_folder_connects_with_explicit_initialization(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    kept = ordinary / "notes.txt"
    kept.write_text("keep\n", encoding="utf-8")

    preview = operations.inspect("existing", str(ordinary), "Ordinary")
    result = operations.create(preview["inspection_id"], "ordinary-folder")

    assert preview["initialization_required"] is True
    assert result["project"]["available"] is True
    assert kept.read_text(encoding="utf-8") == "keep\n"
    assert (ordinary / ".git").is_dir()
    assert (ordinary / ".agent-commons" / "workspace.yaml").is_file()


def test_existing_path_and_registry_overlap_are_refused_without_writing(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    missing = tmp_path / "missing"

    with pytest.raises(ProjectRegistryRefusal) as not_repo:
        operations.inspect("existing", str(missing), "Missing")
    assert not_repo.value.code == "project_not_repository"
    with pytest.raises(ProjectRegistryRefusal) as overlap:
        operations.inspect("new", str(registry.root / "outside"), "Overlap")
    assert overlap.value.code == "project_path_refused"
    assert not missing.exists()


def test_existing_symlink_is_still_refused(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    with pytest.raises(ProjectRegistryRefusal) as refused:
        operations.inspect("existing", str(link), "Link")
    assert refused.value.code == "project_path_unsafe"


def test_inspection_revalidates_a_symlink_swap(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    target = tmp_path / "target"
    preview = operations.inspect("new", str(target), "Target")
    target.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(ProjectRegistryRefusal) as refused:
        operations.create(preview["inspection_id"], "symlink-swap")
    assert refused.value.code == "project_path_changed"
    assert not (tmp_path / "elsewhere").exists()


def test_request_key_is_reserved_before_a_second_inspection_can_write(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    first = operations.inspect("new", str(tmp_path / "first"), "First")
    second = operations.inspect("new", str(tmp_path / "second"), "Second")

    operations.create(first["inspection_id"], "same-key")
    with pytest.raises(ProjectRegistryRefusal) as mismatch:
        operations.create(second["inspection_id"], "same-key")

    assert mismatch.value.code == "project_idempotency_mismatch"
    assert not (tmp_path / "second").exists()


def test_registered_checkout_cannot_contain_or_be_contained_by_another_project(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    protected = tmp_path / "protected"
    protected.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=protected, check=True)
    registry.register(
        checkout=protected,
        workspace_id="workspace.protected",
        state_binding=None,
        name="Protected",
        idempotency_key="protected",
    )
    operations = ProjectOperations(registry)

    for target in (protected / "child", tmp_path):
        with pytest.raises(ProjectRegistryRefusal) as refused:
            operations.inspect("new", str(target), "Overlapping")
        assert refused.value.code == "project_path_refused"


def test_unsubmitted_inspection_expires(tmp_path: Path) -> None:
    clock = [0.0]
    operations = ProjectOperations(ProjectRegistry(tmp_path / "registry"), clock=lambda: clock[0])
    preview = operations.inspect("new", str(tmp_path / "late"), "Late")
    clock[0] = 121.0

    with pytest.raises(ProjectRegistryRefusal) as expired:
        operations.create(preview["inspection_id"], "late")
    assert expired.value.code == "project_inspection_expired"


def test_new_project_ignores_an_inherited_exact_state_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inherited = tmp_path / "unrelated-state"
    monkeypatch.setenv("AGENT_COMMONS_STATE_ROOT", str(inherited))
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    preview = operations.inspect("new", str(tmp_path / "isolated"), "Isolated")

    result = operations.create(preview["inspection_id"], "isolated")
    binding = registry.project_binding(result["project"]["id"])
    assert binding.state_binding is not None
    assert str(inherited) not in binding.state_binding


def test_durable_reservation_recovers_after_workspace_init_and_host_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    first_host = ProjectOperations(registry)
    preview = first_host.inspect("new", str(tmp_path / "crash"), "Crash recovery")
    original_register = registry.register

    def interrupted(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("simulated response loss before registry publication")

    monkeypatch.setattr(registry, "register", interrupted)
    with pytest.raises(RuntimeError, match="response loss"):
        first_host.create(preview["inspection_id"], "restart-key")
    monkeypatch.setattr(registry, "register", original_register)

    second_host = ProjectOperations(registry)
    recovered = second_host.create(preview["inspection_id"], "restart-key")
    assert recovered["project"]["name"] == "Crash recovery"


def test_existing_subdirectory_and_parent_inode_swap_are_refused(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=repo, check=True)

    with pytest.raises(ProjectRegistryRefusal) as nested:
        operations.inspect("existing", str(repo / "nested"), "Nested")
    assert nested.value.code == "project_not_repository"

    parent = tmp_path / "parent"
    parent.mkdir()
    preview = operations.inspect("new", str(parent / "new"), "New")
    parent.rename(tmp_path / "moved")
    parent.mkdir()
    with pytest.raises(ProjectRegistryRefusal) as swapped:
        operations.create(preview["inspection_id"], "parent-swap")
    assert swapped.value.code == "project_path_changed"


def test_git_environment_cannot_redirect_existing_repository_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=repo, check=True)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=foreign, check=True)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))

    preview = ProjectOperations(ProjectRegistry(tmp_path / "registry")).inspect(
        "existing", str(repo), "Repo"
    )
    assert preview["mode"] == "existing"


def test_invalid_idempotency_key_is_refused_before_project_initialization(tmp_path: Path) -> None:
    operations = ProjectOperations(ProjectRegistry(tmp_path / "registry"))
    target = tmp_path / "target"
    preview = operations.inspect("new", str(target), "Target")

    with pytest.raises(ProjectRegistryRefusal) as refused:
        operations.create(preview["inspection_id"], "x" * 161)
    assert refused.value.code == "project_invalid"
    assert not target.exists()


def test_new_inspection_never_adopts_a_target_that_appeared_before_first_create(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    target = tmp_path / "appeared"
    preview = operations.inspect("new", str(target), "Appeared")
    target.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=target, check=True)

    with pytest.raises(ProjectRegistryRefusal) as refused:
        operations.create(preview["inspection_id"], "appeared")
    assert refused.value.code == "project_path_changed"
    assert registry.list_projects()["projects"] == []


def test_inspect_infers_name_and_expands_only_the_current_user_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry, home=home)

    preview = operations.inspect("new", "~/My App")
    result = operations.create(preview["inspection_id"], "home-expanded")

    assert preview["name"] == "My App"
    assert result["project"]["name"] == "My App"
    assert (home / "My App" / ".agent-commons" / "workspace.yaml").is_file()
    with pytest.raises(ProjectRegistryRefusal) as foreign:
        operations.inspect("new", "~other/secret", "Secret")
    assert foreign.value.code == "project_path_unsafe"
    with pytest.raises(ProjectRegistryRefusal) as escaped:
        operations.inspect("new", "~/../outside", "Outside")
    assert escaped.value.code == "project_path_unsafe"


def test_invalid_name_refuses_before_a_reservation_or_checkout_side_effect(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    target = tmp_path / "target"

    with pytest.raises(ProjectRegistryRefusal) as refused:
        operations.inspect("new", str(target), "\x01")
    assert refused.value.code == "project_invalid"
    assert not registry.path.exists()
    assert not target.exists()


def test_retry_refuses_an_initialized_workspace_after_crash_before_milestone_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    first = ProjectOperations(registry)
    preview = first.inspect("new", str(tmp_path / "crash-after-init"), "Crash")

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        "agent_commons.ui.project_operations.CommonsManager.initialize", interrupted
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        first.create(preview["inspection_id"], "crash-after-init")
    monkeypatch.undo()

    # The original request owns this inode only through the Git milestone.  A
    # valid workspace appearing before its retry is indistinguishable from an
    # unrelated initialization and must not be adopted.
    target = tmp_path / "crash-after-init"
    from agent_commons.services.manager import CommonsManager

    CommonsManager.initialize(target, integrations=(), workspace_name="Unrelated replacement")

    with pytest.raises(ProjectRegistryRefusal) as ambiguous:
        ProjectOperations(registry).create(preview["inspection_id"], "crash-after-init")
    assert ambiguous.value.code == "project_initialization_ambiguous"
    assert registry.list_projects()["projects"] == []


def test_existing_inspection_can_connect_an_initialized_target_after_new_create_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ProjectRegistry(tmp_path / "registry")
    first = ProjectOperations(registry)
    target = tmp_path / "recover-by-connect"
    preview = first.inspect("new", str(target), "Original")

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        "agent_commons.ui.project_operations.CommonsManager.initialize", interrupted
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        first.create(preview["inspection_id"], "original-key")
    monkeypatch.undo()

    from agent_commons.services.manager import CommonsManager

    CommonsManager.initialize(target, integrations=(), workspace_name="Connected workspace")
    reconnect_host = ProjectOperations(registry)
    reconnect = reconnect_host.inspect("existing", str(target), "Connected")
    result = reconnect_host.create(reconnect["inspection_id"], "connect-key")

    assert result["project"]["name"] == "Connected"
    assert result["project"]["available"] is True
