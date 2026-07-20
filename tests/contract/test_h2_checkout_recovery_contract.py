"""Positive recovery contracts for ADR 0003 checkout-scoped receipts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_commons.core.canonical import canonical_json_file_bytes
from agent_commons.errors import IntegrityError
from agent_commons.services import CommonsManager


def _manager(
    repo: Path,
    *,
    state_root: Path | None = None,
    suffix: str,
    capabilities: tuple[str, ...] = (),
) -> CommonsManager:
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id=f"h2-contract-{suffix}-12345678",
        principal="local-operator",
        client="codex",
        software="contract-test",
        role="recovery-tester",
        capabilities=capabilities,
    )
    manager.session_id = session["session_id"]
    return manager


def _initialize(repo: Path) -> None:
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="h2-contract")


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent Commons Tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
    )


def _create_objective(manager: CommonsManager, *, key: str, title: str) -> dict:
    return manager.create_objective(
        title=title,
        description="Executable H2 recovery contract",
        acceptance_criteria=("canonical writes remain portable",),
        idempotency_key=key,
    )


def test_fresh_clone_rebuilds_receipts_from_canonical_ledger(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _initialize(source)
    source_manager = _manager(
        source,
        state_root=tmp_path / "source-state",
        suffix="fresh-source",
    )
    _create_objective(source_manager, key="portable-event", title="Portable event")

    clone = tmp_path / "clone"
    clone.mkdir()
    shutil.copytree(source / ".agent-commons", clone / ".agent-commons")
    clone_manager = _manager(
        clone,
        state_root=tmp_path / "clone-state",
        suffix="fresh-clone",
    )

    report = clone_manager.doctor()
    assert report["ok"] is False
    assert "receipt reconcile" in report["issues"][0]

    recovered = clone_manager.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert recovered["derived_receipts"] == 1
    assert clone_manager.doctor()["ok"] is True
    created = _create_objective(clone_manager, key="first-clone-write", title="Clone write")
    assert created["event_type"] == "objective.created"


def test_linked_worktree_bootstraps_without_foreign_branch_orphan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    _git(repo, "init", "-q", "-b", "main")
    _commit_all(repo, "base workspace")

    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "linked-branch", str(linked))

    main_manager = _manager(repo, suffix="main-worktree")
    _create_objective(main_manager, key="main-only-event", title="Main-only event")
    linked_manager = _manager(linked, suffix="linked-worktree")

    report = linked_manager.doctor()
    assert report["ok"] is False
    assert "scope is new" in report["issues"][0]

    recovered = linked_manager.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert recovered["orphan_receipts"] == []
    created = _create_objective(linked_manager, key="linked-write", title="Linked write")
    assert created["event_type"] == "objective.created"
    assert (
        main_manager.events.idempotency.scope["scope_id"]
        != (linked_manager.events.idempotency.scope["scope_id"])
    )


def test_branch_switch_and_return_select_independent_receipt_scopes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    _git(repo, "init", "-q", "-b", "main")
    _commit_all(repo, "base workspace")
    _git(repo, "switch", "-q", "-c", "feature")

    feature_manager = _manager(repo, suffix="feature-ref")
    _create_objective(feature_manager, key="feature-only-event", title="Feature event")
    _commit_all(repo, "feature event")
    _git(repo, "switch", "-q", "main")

    main_manager = _manager(repo, suffix="main-ref")
    report = main_manager.doctor()
    assert report["ok"] is False
    assert "scope is new" in report["issues"][0]
    recovered = main_manager.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert (
        _create_objective(main_manager, key="main-after-switch", title="Main event")["event_type"]
        == "objective.created"
    )
    _commit_all(repo, "main event")

    _git(repo, "switch", "-q", "feature")
    returned = _manager(repo, suffix="feature-return")
    assert returned.doctor()["ok"] is True


def test_multiple_orphans_can_be_retried_one_at_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    manager = _manager(repo, state_root=tmp_path / "state", suffix="multi-orphan")

    real_guard = manager._guard_integrity
    real_reserve = manager.events.idempotency.reserve

    def reserve_then_crash(**kwargs: object) -> object:
        real_reserve(**kwargs)  # type: ignore[arg-type]
        raise RuntimeError("crash after reservation")

    monkeypatch.setattr(manager, "_guard_integrity", lambda **_: manager.snapshot())
    monkeypatch.setattr(manager.events.idempotency, "reserve", reserve_then_crash)
    for key in ("orphan-one", "orphan-two"):
        with pytest.raises(RuntimeError, match="crash after reservation"):
            _create_objective(manager, key=key, title=key)

    monkeypatch.setattr(manager, "_guard_integrity", real_guard)
    monkeypatch.setattr(manager.events.idempotency, "reserve", real_reserve)

    first = _create_objective(manager, key="orphan-one", title="orphan-one")
    assert first["repaired"] is True
    interim = manager.receipt_status()
    assert len(interim["orphan_receipts"]) == 1
    second = _create_objective(manager, key="orphan-two", title="orphan-two")
    assert second["repaired"] is True
    assert manager.doctor()["ok"] is True


def test_exact_git_arrival_after_abandonment_is_audited_and_reconciled(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    state_root = tmp_path / "state"
    writer = _manager(repo, state_root=state_root, suffix="tombstone-writer")
    result = _create_objective(writer, key="arrives-after-abandon", title="Git arrival")
    record = writer.events.get(result["event_id"])
    event_bytes = record.path.read_bytes()
    event_path = record.path
    receipt = writer.events.idempotency.lookup(
        namespace=writer._namespace(writer._active_session()),
        key="arrives-after-abandon",
    )
    assert receipt is not None

    event_path.unlink()
    maintainer = _manager(
        repo,
        state_root=state_root,
        suffix="tombstone-maintainer",
        capabilities=("receipt:abandon",),
    )
    maintainer.abandon_idempotency_receipt(
        receipt.key_digest,
        reason="simulate abandonment before another branch is merged",
    )
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_bytes(event_bytes)

    report = maintainer.doctor()
    assert report["ok"] is False
    assert "require reconciliation" in " ".join(report["issues"])

    recovered = maintainer.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert recovered["reconciled_tombstones_count"] == 1
    assert len(recovered["reconciled_tombstones"]) == 1
    assert maintainer.doctor()["ok"] is True


def test_anchor_rejects_removal_after_first_local_observation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    manager = _manager(repo, state_root=tmp_path / "state", suffix="anchor-delete")
    created = _create_objective(manager, key="anchored-event", title="Anchored")
    record = manager.events.get(created["event_id"])
    record.path.unlink()

    report = manager.doctor()
    assert report["ok"] is False
    assert "anchored canonical event is missing" in report["issues"][0]
    with pytest.raises(IntegrityError, match="anchored canonical event is missing"):
        manager.reconcile_idempotency_receipts()


def test_legacy_exact_receipts_migrate_without_branch_guessing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    state_root = tmp_path / "state"
    writer = _manager(repo, state_root=state_root, suffix="legacy-exact")
    _create_objective(writer, key="legacy-exact-event", title="Legacy exact")
    receipt = writer.events.idempotency.lookup(
        namespace=writer._namespace(writer._active_session()),
        key="legacy-exact-event",
    )
    assert receipt is not None
    writer.events.idempotency.prepare_legacy_receipt(receipt)
    shutil.rmtree(writer.paths.idempotency_v2)

    upgraded = CommonsManager(repo, state_root=state_root, session_id=writer.session_id)
    status = upgraded.receipt_status()
    assert status["legacy_receipt_count"] == 1
    assert status["legacy_orphan_receipts"] == []
    recovered = upgraded.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert recovered["imported_receipts"] == 1
    assert upgraded.doctor()["ok"] is True


def test_legacy_orphan_requires_explicit_adoption_then_exact_retry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    state_root = tmp_path / "state"
    writer = _manager(repo, state_root=state_root, suffix="legacy-orphan")
    created = _create_objective(writer, key="legacy-orphan-event", title="Legacy orphan")
    receipt = writer.events.idempotency.lookup(
        namespace=writer._namespace(writer._active_session()),
        key="legacy-orphan-event",
    )
    assert receipt is not None
    writer.events.idempotency.prepare_legacy_receipt(receipt)
    writer.events.get(created["event_id"]).path.unlink()
    shutil.rmtree(writer.paths.idempotency_v2)

    upgraded = CommonsManager(repo, state_root=state_root, session_id=writer.session_id)
    with pytest.raises(IntegrityError, match="adopt-legacy-orphan.*or abandonment"):
        upgraded.reconcile_idempotency_receipts()
    adopted = upgraded.reconcile_idempotency_receipts(adopt_legacy_orphans=(receipt.key_digest,))
    assert adopted["adopted_legacy_orphans"] == [receipt.key_digest]
    assert adopted["orphan_receipts"] == [receipt.key_digest]

    repaired = _create_objective(upgraded, key="legacy-orphan-event", title="Legacy orphan")
    assert repaired["repaired"] is True
    assert upgraded.doctor()["ok"] is True


def test_prepare_rollback_rebuilds_complete_legacy_receipt_set(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    manager = _manager(repo, state_root=tmp_path / "state", suffix="rollback")
    _create_objective(manager, key="rollback-one", title="Rollback one")
    _create_objective(manager, key="rollback-two", title="Rollback two")

    prepared = manager.reconcile_idempotency_receipts(prepare_rollback=True)
    assert prepared["ok"] is True
    assert prepared["legacy_receipts_prepared"] == 2
    assert len(list(manager.events.idempotency.iter_legacy_reservations())) == 2


def test_prepare_rollback_refuses_unfinished_in_flight_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    manager = _manager(repo, state_root=tmp_path / "state", suffix="rollback-orphan")
    _create_objective(manager, key="rollback-bootstrap", title="Bootstrap")
    manager.events.idempotency.reserve(
        namespace=manager._namespace(manager._active_session()),
        key="unfinished-before-rollback",
        semantic_sha256="a" * 64,
    )

    with pytest.raises(IntegrityError, match="retried or abandoned before rollback"):
        manager.reconcile_idempotency_receipts(prepare_rollback=True)


def test_crash_after_event_publication_is_recovered_from_valid_git_addition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _initialize(repo)
    manager = _manager(repo, state_root=tmp_path / "state", suffix="post-publish-crash")
    _create_objective(manager, key="bootstrap", title="Bootstrap")
    real_reconcile = manager.receipt_recovery.reconcile

    def crash_after_publish(*args: object, **kwargs: object) -> object:
        raise RuntimeError("crash before anchor advance")

    monkeypatch.setattr(manager.receipt_recovery, "reconcile", crash_after_publish)
    with pytest.raises(RuntimeError, match="anchor advance"):
        _create_objective(manager, key="published-before-crash", title="Published")
    monkeypatch.setattr(manager.receipt_recovery, "reconcile", real_reconcile)

    status = manager.receipt_status()
    assert len(status["unobserved_events"]) == 1
    recovered = manager.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    retry = _create_objective(manager, key="published-before-crash", title="Published")
    assert retry["created"] is False


def test_migration_retry_resumes_when_marker_write_was_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _initialize(source)
    source_manager = _manager(source, state_root=tmp_path / "source-state", suffix="marker-source")
    _create_objective(source_manager, key="marker-event", title="Marker event")
    clone = tmp_path / "clone"
    clone.mkdir()
    shutil.copytree(source / ".agent-commons", clone / ".agent-commons")
    clone_manager = _manager(clone, state_root=tmp_path / "clone-state", suffix="marker-clone")
    real_mark = clone_manager.events.idempotency.mark_migrated

    def fail_marker(**_: object) -> object:
        raise RuntimeError("migration marker interrupted")

    monkeypatch.setattr(clone_manager.events.idempotency, "mark_migrated", fail_marker)
    with pytest.raises(RuntimeError, match="marker interrupted"):
        clone_manager.reconcile_idempotency_receipts()
    monkeypatch.setattr(clone_manager.events.idempotency, "mark_migrated", real_mark)

    recovered = clone_manager.reconcile_idempotency_receipts()
    assert recovered["ok"] is True
    assert clone_manager.doctor()["ok"] is True


def test_conflicting_tombstone_arrival_fails_before_anchor_bootstrap(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _initialize(source)
    source_manager = _manager(
        source, state_root=tmp_path / "source-state", suffix="conflict-source"
    )
    created = _create_objective(source_manager, key="conflicted-arrival", title="Original")
    source_receipt = source_manager.events.idempotency.lookup(
        namespace=source_manager._namespace(source_manager._active_session()),
        key="conflicted-arrival",
    )
    assert source_receipt is not None

    clone = tmp_path / "clone"
    clone.mkdir()
    shutil.copytree(source / ".agent-commons", clone / ".agent-commons")
    clone_manager = _manager(clone, state_root=tmp_path / "clone-state", suffix="conflict-clone")
    scoped = clone_manager.events.idempotency.import_reservation(source_receipt)
    clone_manager.events.idempotency.abandon(
        scoped,
        reason="simulate a tombstone received before conflicting Git content",
        actor_session_id=clone_manager.session_id or "",
        actor_principal_id="local-operator",
    )
    record = clone_manager.events.get(created["event_id"])
    changed = dict(record.event)
    changed["payload"] = {**dict(record.event["payload"]), "title": "Conflicting"}
    record.path.write_bytes(canonical_json_file_bytes(changed))

    with pytest.raises(IntegrityError, match="abandonment conflicts"):
        clone_manager.reconcile_idempotency_receipts()
    assert not clone_manager.events.idempotency.anchor_path.exists()
