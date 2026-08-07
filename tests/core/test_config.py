from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_commons.config import CommonsPaths, _publish_owner_marker
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


def _initialized_layout(repo: Path) -> None:
    (repo / ".agent-commons" / "events").mkdir(parents=True)
    (repo / ".agent-commons" / "manifests").mkdir()


def test_default_and_explicit_base_are_namespaced_by_workspace_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    workspace_id = "workspace.00000000000000000000000001"

    default = CommonsPaths.for_workspace(repo, workspace_id=workspace_id)
    explicit = CommonsPaths.for_workspace(
        repo,
        state_base=tmp_path / "operator-state",
        workspace_id=workspace_id,
    )

    assert default.state_mode == "base"
    assert default.state_root == repo / ".git" / "agent-commons-state" / "workspaces" / workspace_id
    assert explicit.state_root == tmp_path / "operator-state" / "workspaces" / workspace_id


def test_exact_state_root_remains_exact_and_root_env_precedes_base_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace_id = "workspace.00000000000000000000000001"
    exact = tmp_path / "exact"
    monkeypatch.setenv("AGENT_COMMONS_STATE_ROOT", str(exact))
    monkeypatch.setenv("AGENT_COMMONS_STATE_BASE", str(tmp_path / "ignored-base"))

    paths = CommonsPaths.for_workspace(repo, workspace_id=workspace_id)

    assert paths.state_mode == "exact"
    assert paths.state_source == "env:AGENT_COMMONS_STATE_ROOT"
    assert paths.state_root == exact


def test_two_workspaces_under_one_base_have_independent_owned_roots(tmp_path: Path) -> None:
    base = tmp_path / "state"
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "second"
    for repo in (first_repo, second_repo):
        repo.mkdir()
        _initialized_layout(repo)
    first_id = "workspace.00000000000000000000000001"
    second_id = "workspace.00000000000000000000000002"
    first = CommonsPaths.for_workspace(first_repo, state_base=base, workspace_id=first_id)
    second = CommonsPaths.for_workspace(second_repo, state_base=base, workspace_id=second_id)

    first.ensure_layout()
    second.ensure_layout()

    assert first.state_root != second.state_root
    assert first.ownership_report()["match"] is True
    assert second.ownership_report()["match"] is True


def test_exact_owner_mismatch_fails_without_mutating_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    state = tmp_path / "state"
    first = CommonsPaths.for_workspace(
        repo,
        state_root=state,
        workspace_id="workspace.00000000000000000000000001",
    )
    first.ensure_layout()
    before = {
        path.relative_to(state): path.read_bytes() for path in state.rglob("*") if path.is_file()
    }
    second = CommonsPaths.for_workspace(
        repo,
        state_root=state,
        workspace_id="workspace.00000000000000000000000002",
    )

    with pytest.raises(ConfigurationError, match="belongs to a different") as captured:
        second.ensure_layout()

    assert captured.value.code == "state_owner_mismatch"
    assert captured.value.details == {
        "expected_workspace_id": "workspace.00000000000000000000000002",
        "owner_workspace_id": "workspace.00000000000000000000000001",
        "mode": "exact",
        "source": "argument:state-root",
        "resolved_repo_root": str(repo),
        "resolved_state_root": str(state),
    }
    after = {
        path.relative_to(state): path.read_bytes() for path in state.rglob("*") if path.is_file()
    }
    assert after == before


def test_matching_legacy_exact_root_is_adopted_without_moving_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    state = tmp_path / "legacy"
    migration = state / "idempotency-v2" / "migration.json"
    migration.parent.mkdir(parents=True)
    workspace_id = "workspace.00000000000000000000000001"
    migration.write_text(
        json.dumps(
            {
                "schema": "commons.idempotency_migration.v2",
                "workspace_id": workspace_id,
                "format": 2,
                "migrated_at": "2026-01-01T00:00:00Z",
                "migrated_by_session_id": "session.00000000000000000000000000000000",
                "legacy_receipt_count": 0,
                "legacy_abandonment_count": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    paths = CommonsPaths.for_workspace(repo, state_root=state, workspace_id=workspace_id)

    paths.ensure_layout()

    assert migration.is_file()
    assert paths.owner_marker.is_file()
    assert not (state / "workspaces").exists()


@pytest.mark.parametrize(
    "migration_bytes",
    (
        b'{"workspace_id":"workspace.00000000000000000000000001"}\n',
        (
            json.dumps(
                {
                    "schema": "commons.idempotency_migration.v2",
                    "workspace_id": "workspace.00000000000000000000000001",
                    "format": 2,
                    "migrated_at": "2026-01-01T00:00:00Z",
                    "migrated_by_session_id": "session.00000000000000000000000000000000",
                    "legacy_receipt_count": 0,
                    "legacy_abandonment_count": 0,
                },
                indent=2,
            )
            + "\n"
        ).encode(),
        (
            b'{"format":2,"legacy_abandonment_count":0,"legacy_receipt_count":0,'
            b'"migrated_at":"2026-01-01T00:00:00Z",'
            b'"migrated_by_session_id":"session.00000000000000000000000000000000",'
            b'"schema":"commons.idempotency_migration.v2",'
            b'"workspace_id":"workspace.00000000000000000000000001",'
            b'"workspace_id":"workspace.00000000000000000000000001"}\n'
        ),
        (
            b'{"format":2,"legacy_abandonment_count":0,"legacy_receipt_count":0,'
            b'"migrated_at":"2026-01-01T00:00:00Z",'
            b'"migrated_by_session_id":"session.00000000000000000000000000000000",'
            b'"schema":"commons.idempotency_migration.v2",'
            b'"workspace_id":"not-a-typed-workspace"}\n'
        ),
    ),
    ids=("underspecified", "noncanonical", "duplicate-key", "untyped-workspace"),
)
def test_invalid_legacy_migration_never_proves_ownership(
    tmp_path: Path, migration_bytes: bytes
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    state = tmp_path / "legacy"
    migration = state / "idempotency-v2" / "migration.json"
    migration.parent.mkdir(parents=True)
    migration.write_bytes(migration_bytes)
    foreign_session = state / "sessions" / "session.foreign.json"
    foreign_session.parent.mkdir()
    foreign_session.write_text("{}\n", encoding="utf-8")
    paths = CommonsPaths.for_workspace(
        repo,
        state_root=state,
        workspace_id="workspace.00000000000000000000000001",
    )

    assert paths.ownership_report()["status"] == "ambiguous-legacy"
    with pytest.raises(ConfigurationError) as captured:
        paths.ensure_layout()

    assert captured.value.code == "state_owner_unproven"
    assert not paths.owner_marker.exists()


def test_symlinked_legacy_migration_directory_never_proves_ownership(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    state = tmp_path / "legacy"
    state.mkdir()
    outside = tmp_path / "outside"
    migration = outside / "migration.json"
    migration.parent.mkdir()
    migration.write_text(
        json.dumps(
            {
                "schema": "commons.idempotency_migration.v2",
                "workspace_id": "workspace.00000000000000000000000001",
                "format": 2,
                "migrated_at": "2026-01-01T00:00:00Z",
                "migrated_by_session_id": "session.00000000000000000000000000000000",
                "legacy_receipt_count": 0,
                "legacy_abandonment_count": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (state / "idempotency-v2").symlink_to(outside, target_is_directory=True)
    paths = CommonsPaths.for_workspace(
        repo,
        state_root=state,
        workspace_id="workspace.00000000000000000000000001",
    )

    assert paths.ownership_report()["status"] == "ambiguous-legacy"
    with pytest.raises(ConfigurationError) as captured:
        paths.ensure_layout()

    assert captured.value.code == "state_owner_unproven"
    assert not paths.owner_marker.exists()


def test_owner_marker_collision_never_follows_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    root.mkdir()
    marker = root / "workspace-owner.json"
    outside = tmp_path / "outside.json"
    outside.write_text("protected\n", encoding="utf-8")
    real_link = os.link

    def collide(_source: str | Path, _destination: str | Path) -> None:
        marker.symlink_to(outside)
        raise FileExistsError

    monkeypatch.setattr("agent_commons.config.os.link", collide)
    with pytest.raises(ConfigurationError) as captured:
        _publish_owner_marker(
            marker,
            {
                "schema": "agent_commons.state_owner.v1",
                "workspace_id": "workspace.00000000000000000000000001",
            },
        )
    monkeypatch.setattr("agent_commons.config.os.link", real_link)

    assert captured.value.code == "state_owner_race"
    assert outside.read_text(encoding="utf-8") == "protected\n"


def test_owner_marker_rejects_canonical_documents_with_extra_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    workspace_id = "workspace.00000000000000000000000001"
    paths = CommonsPaths.for_workspace(
        repo,
        state_root=tmp_path / "state",
        workspace_id=workspace_id,
    )
    paths.ensure_layout()
    paths.owner_marker.write_text(
        json.dumps(
            {
                "extra": "not part of the operational schema",
                "schema": "agent_commons.state_owner.v1",
                "workspace_id": workspace_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    assert paths.ownership_report()["status"] == "invalid-marker"
    with pytest.raises(ConfigurationError) as captured:
        paths.validate_state_ownership()
    assert captured.value.code == "state_owner_unproven"


def test_base_with_unproven_legacy_material_fails_before_namespacing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialized_layout(repo)
    base = tmp_path / "legacy-state"
    foreign_session = base / "sessions" / "session.foreign.json"
    foreign_session.parent.mkdir(parents=True)
    foreign_session.write_text("{}\n", encoding="utf-8")
    workspace_id = "workspace.00000000000000000000000001"

    paths = CommonsPaths.for_workspace(repo, state_base=base, workspace_id=workspace_id)

    assert paths.state_mode == "legacy-exact"
    assert paths.state_root == base
    with pytest.raises(ConfigurationError) as captured:
        paths.ensure_layout()
    assert captured.value.code == "state_owner_unproven"
    assert captured.value.details["status"] == "ambiguous-legacy"
    assert foreign_session.read_text(encoding="utf-8") == "{}\n"
    assert not (base / "workspaces").exists()
