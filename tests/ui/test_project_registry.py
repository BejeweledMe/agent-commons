from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_commons.ui.project_registry import (
    PROJECT_REGISTRY_SCHEMA,
    ProjectRegistry,
    ProjectRegistryRefusal,
    _canonical,
    _revision,
)


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=path, check=True)
    return path


def test_registry_isolates_two_repositories_and_never_leaks_checkouts(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    first = registry.register(
        checkout=_repo(tmp_path / "one"),
        workspace_id="workspace.one",
        state_binding=None,
        name="One",
        idempotency_key="one",
    )
    second = registry.register(
        checkout=_repo(tmp_path / "two"),
        workspace_id="workspace.two",
        state_binding=None,
        name="Two",
        idempotency_key="two",
    )

    listed = registry.list_projects()

    assert listed["schema"] == "agent_commons.project_list.v1"
    assert {item["id"] for item in listed["projects"]} == {
        first["project"]["id"],
        second["project"]["id"],
    }
    assert "checkout" not in str(listed)
    assert "state_binding" not in str(listed)


def test_duplicate_workspace_requires_the_exact_registered_checkout(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    repo = _repo(tmp_path / "one")
    created = registry.register(
        checkout=repo,
        workspace_id="workspace.same",
        state_binding=None,
        name="One",
        idempotency_key="create",
    )

    retried = registry.register(
        checkout=repo,
        workspace_id="workspace.same",
        state_binding=None,
        name="One",
        idempotency_key="retry",
    )
    assert retried["project"] == created["project"]

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.register(
            checkout=_repo(tmp_path / "other"),
            workspace_id="workspace.same",
            state_binding=None,
            name="Other",
            idempotency_key="other",
        )
    assert refused.value.code == "project_workspace_registered"


def test_linked_worktree_cannot_register_the_same_workspace_from_another_checkout(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    repo = _repo(tmp_path / "primary")
    subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "Initial",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["/usr/bin/git", "worktree", "add", "--quiet", str(linked)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    registry.register(
        checkout=repo,
        workspace_id="workspace.linked",
        state_binding=None,
        name="Primary",
        idempotency_key="primary",
    )

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.register(
            checkout=linked,
            workspace_id="workspace.linked",
            state_binding=None,
            name="Linked",
            idempotency_key="linked",
        )
    assert refused.value.code == "project_workspace_registered"


def test_update_cas_and_idempotency_survive_a_lost_response(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    created = registry.register(
        checkout=_repo(tmp_path / "repo"),
        workspace_id="workspace.cas",
        state_binding=None,
        name="Before",
        idempotency_key="create",
    )
    project = created["project"]

    first = registry.update(
        project["id"], project["revision"], name="After", idempotency_key="rename"
    )
    recovered = registry.update(
        project["id"], project["revision"], name="After", idempotency_key="rename"
    )
    assert recovered == first

    with pytest.raises(ProjectRegistryRefusal) as stale:
        registry.update(
            project["id"], project["revision"], archived=True, idempotency_key="archive"
        )
    assert stale.value.code == "project_stale"
    with pytest.raises(ProjectRegistryRefusal) as mismatch:
        registry.update(
            project["id"], first["project"]["revision"], archived=True, idempotency_key="rename"
        )
    assert mismatch.value.code == "project_idempotency_mismatch"


def test_registry_refuses_tampered_records_and_replaced_state_binding(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    state = tmp_path / "state"
    state.mkdir()
    created = registry.register(
        checkout=_repo(tmp_path / "repo"),
        workspace_id="workspace.binding",
        state_binding=state,
        name="Bound",
        idempotency_key="create",
    )
    moved = tmp_path / "old-state"
    state.rename(moved)
    state.symlink_to(moved)
    entry = registry.project_binding(created["project"]["id"])
    assert entry.public()["state"] == "identity_conflict"

    registry.path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ProjectRegistryRefusal) as malformed:
        registry.list_projects()
    assert malformed.value.code == "project_registry_unavailable"


def test_ordinary_receipt_key_cannot_be_reused_as_an_unrelated_create(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    registry.register(
        checkout=_repo(tmp_path / "one"),
        workspace_id="workspace.one",
        state_binding=None,
        name="One",
        idempotency_key="same-key",
    )

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.reserve_create(
            "same-key",
            {
                "inspection_id": "inspection." + "0" * 32,
                "mode": "new",
                "checkout": str(tmp_path / "later"),
                "name": "Later",
                "state_binding": None,
                "initialization_required": True,
                "anchor_identity": "1:1",
                "git_identity": None,
            },
        )
    assert refused.value.code == "project_idempotency_mismatch"


def test_receipt_limit_refuses_before_publishing_an_unreadable_registry(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    for index in range(64 * 4):
        assert (
            registry.reserve_create(
                f"key-{index}",
                {
                    "inspection_id": "inspection." + f"{index:032x}",
                    "mode": "new",
                    "checkout": str(tmp_path / f"target-{index}"),
                    "name": "Target",
                    "state_binding": None,
                    "initialization_required": True,
                    "anchor_identity": "1:1",
                    "git_identity": None,
                },
            )
            is None
        )

    with pytest.raises(ProjectRegistryRefusal) as full:
        registry.reserve_create(
            "key-overflow",
            {
                "inspection_id": "inspection." + "f" * 32,
                "mode": "new",
                "checkout": str(tmp_path / "overflow"),
                "name": "Overflow",
                "state_binding": None,
                "initialization_required": True,
                "anchor_identity": "1:1",
                "git_identity": None,
            },
        )
    assert full.value.code == "project_registry_full"
    assert len(registry.list_projects()["projects"]) == 0


def test_same_checkout_cannot_silently_reuse_a_different_exact_state_binding(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    first_state = tmp_path / "first-state"
    second_state = tmp_path / "second-state"
    first_state.mkdir()
    second_state.mkdir()
    repo = _repo(tmp_path / "repo")
    registry.register(
        checkout=repo,
        workspace_id="workspace.state",
        state_binding=first_state,
        name="State",
        idempotency_key="first",
    )

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.register(
            checkout=repo,
            workspace_id="workspace.state",
            state_binding=second_state,
            name="State",
            idempotency_key="second",
        )
    assert refused.value.code == "project_state_binding_conflict"


def test_correctly_checksumming_malformed_receipt_is_refused_on_load(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    registry._ensure_root()
    body = {
        "schema": PROJECT_REGISTRY_SCHEMA,
        "projects": [],
        "receipts": {"malformed": {"unexpected": "anything"}},
    }
    body["revision"] = _revision(body)
    registry.path.write_bytes(_canonical(body))

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.list_projects()
    assert refused.value.code == "project_registry_unavailable"


def test_receipt_retry_refuses_replaced_state_inode(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    state = tmp_path / "state"
    state.mkdir()
    repo = _repo(tmp_path / "repo")
    registry.register(
        checkout=repo,
        workspace_id="workspace.inode",
        state_binding=state,
        name="State",
        idempotency_key="same",
    )
    state.rename(tmp_path / "old-state")
    state.mkdir()

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.register(
            checkout=repo,
            workspace_id="workspace.inode",
            state_binding=state,
            name="State",
            idempotency_key="same",
        )
    assert refused.value.code == "project_state_binding_conflict"


def test_fresh_key_refuses_replaced_state_inode(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    state = tmp_path / "state"
    state.mkdir()
    repo = _repo(tmp_path / "repo")
    registry.register(
        checkout=repo,
        workspace_id="workspace.fresh-inode",
        state_binding=state,
        name="State",
        idempotency_key="first",
    )
    state.rename(tmp_path / "old-state")
    state.mkdir()

    with pytest.raises(ProjectRegistryRefusal) as refused:
        registry.register(
            checkout=repo,
            workspace_id="workspace.fresh-inode",
            state_binding=state,
            name="State",
            idempotency_key="fresh",
        )
    assert refused.value.code == "project_state_binding_conflict"


def test_checkout_symlink_after_rename_does_not_brick_lexical_receipt_loading(
    tmp_path: Path,
) -> None:
    registry = ProjectRegistry(tmp_path / "private")
    repo = _repo(tmp_path / "repo")
    registry.register(
        checkout=repo,
        workspace_id="workspace.rename",
        state_binding=None,
        name="Repo",
        idempotency_key="create",
    )
    moved = tmp_path / "moved"
    repo.rename(moved)
    repo.symlink_to(moved)

    listed = registry.list_projects()
    assert listed["projects"][0]["state"] == "missing"
    assert listed["projects"][0]["available"] is False
