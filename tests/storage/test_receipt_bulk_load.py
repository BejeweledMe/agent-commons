"""Parity and behaviour tests for the receipt bulk load path (ADR 0023, option B).

``IdempotencyStore.load_recovery_view`` reads ``receipts/``, ``abandonments/``
and ``reconciliations/`` once per lock hold instead of the per-digest
accessors (``get_by_digest``/``get_abandonment``/``get_reconciliation``) that
``ReceiptRecovery.status``/``reconcile`` used to call per event. These tests
prove the bulk path returns exactly what the per-digest path would have, on a
fixture that exercises every receipt state: normal, missing, orphan,
conflicting, abandoned-without-reconciliation and reconciled. They also prove
the view is never retained across calls, and that the legacy-v1 migration and
a real write's recovery path are unaffected.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent_commons.core.canonical import (
    canonical_json_file_bytes,
    canonical_sha256,
    load_json_strict,
    sha256_bytes,
)
from agent_commons.errors import IdempotencyConflictError
from agent_commons.services import CommonsManager
from agent_commons.storage.events import semantic_event_body
from agent_commons.storage.idempotency import ReceiptRecoveryView


def _initialize(repo: Path) -> None:
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="bulk-load")


def _manager(repo: Path, *, state_root: Path, suffix: str) -> CommonsManager:
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id=f"bulk-load-{suffix}-12345678",
        principal="operator",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager.session_id = session["session_id"]
    return manager


def _open_workspace(tmp_path: Path, *, suffix: str) -> tuple[CommonsManager, Path, Path]:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    _initialize(repo)
    manager = _manager(repo, state_root=state_root, suffix=suffix)
    return manager, repo, state_root


def _create(manager: CommonsManager, *, key: str, title: str) -> dict:
    return manager.create_objective(
        title=title,
        description="Receipt bulk load fixture",
        acceptance_criteria=("fixture",),
        idempotency_key=key,
    )


def _replace_field(path: Path, field: str, value: object) -> None:
    body = load_json_strict(path)
    body[field] = value
    path.write_bytes(canonical_json_file_bytes(body))


def _seed_mixed_states(manager: CommonsManager) -> dict[str, str]:
    """Plant one digest for every receipt state a recovery view must handle.

    Returns a mapping of state name to key_digest for: a normal reserved
    receipt backed by a matching canonical event, a receipt file deleted for
    an existing event (missing), a receipt rewritten to disagree with its
    event (conflicting), a reservation with no canonical event (orphan), and
    an orphan reservation that was explicitly abandoned and never reconciled
    (tombstoned).
    """
    store = manager.events.idempotency
    namespace = manager._namespace(manager._active_session())

    # Every canonical write is created first, before any receipt is deleted or
    # corrupted below: prepare_for_write opportunistically repairs any missing
    # receipt it finds on the *next* write, so corrupting one before the last
    # write would silently heal it again.
    _create(manager, key="normal-event", title="Normal")
    normal_digest = store.key_digest(namespace, "normal-event")
    _create(manager, key="missing-event", title="Missing")
    missing_digest = store.key_digest(namespace, "missing-event")
    _create(manager, key="conflict-event", title="Conflict")
    conflict_digest = store.key_digest(namespace, "conflict-event")

    missing_receipt = store.lookup(namespace=namespace, key="missing-event")
    assert missing_receipt is not None
    missing_receipt.path.unlink()

    conflict_receipt = store.lookup(namespace=namespace, key="conflict-event")
    assert conflict_receipt is not None
    _replace_field(conflict_receipt.path, "semantic_sha256", "f" * 64)

    orphan_reservation = store.reserve(
        namespace=namespace, key="orphan-key", semantic_sha256="a" * 64
    )
    orphan_digest = orphan_reservation.key_digest

    tombstoned_reservation = store.reserve(
        namespace=namespace, key="tombstoned-key", semantic_sha256="b" * 64
    )
    store.abandon(
        tombstoned_reservation,
        reason="lost original operation",
        actor_session_id=manager.session_id or "",
        actor_principal_id="operator",
    )
    tombstoned_digest = tombstoned_reservation.key_digest

    return {
        "normal": normal_digest,
        "missing": missing_digest,
        "conflicting": conflict_digest,
        "orphan": orphan_digest,
        "tombstoned": tombstoned_digest,
    }


def _seed_reconciled_tombstone(manager: CommonsManager, *, key: str) -> str:
    """Plant one digest whose abandonment tombstone is reconciled against a real event.

    Mirrors the "exact Git arrival after abandonment" sequence from
    tests/contract/test_h2_checkout_recovery_contract.py::
    test_exact_git_arrival_after_abandonment_is_audited_and_reconciled, but
    calls ``reconcile_abandonment`` directly (rather than the full
    ``ReceiptRecovery.reconcile``) so it does not repair any other digest
    already planted on the same manager.
    """
    store = manager.events.idempotency
    namespace = manager._namespace(manager._active_session())
    created = _create(manager, key=key, title=f"Reconciled {key}")
    record = manager.events.get(created["event_id"])
    event_bytes = record.path.read_bytes()
    receipt = store.lookup(namespace=namespace, key=key)
    assert receipt is not None

    record.path.unlink()
    abandonment = store.abandon(
        receipt,
        reason="simulate a lost event before it returns bit-identical",
        actor_session_id=manager.session_id or "",
        actor_principal_id="operator",
    )
    record.path.parent.mkdir(parents=True, exist_ok=True)
    record.path.write_bytes(event_bytes)

    semantic_sha256 = canonical_sha256(semantic_event_body(record.event))
    event_sha256 = sha256_bytes(record.path.read_bytes())
    store.reconcile_abandonment(
        abandonment,
        namespace=receipt.namespace,
        key=key,
        semantic_sha256=semantic_sha256,
        event_id=receipt.event_id,
        event_sha256=event_sha256,
        actor_session_id=manager.session_id or "",
        actor_principal_id="operator",
        reason="canonical event returned bit-identical",
    )
    return receipt.key_digest


def _reconciliation_path(manager: CommonsManager, digest: str) -> Path:
    store = manager.events.idempotency
    root = store.paths.idempotency_v2 / "reconciliations" / store.scope["scope_id"]
    return root / f"{digest}.json"


def test_load_recovery_view_matches_per_digest_accessors_across_receipt_states(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="parity")
    # The reconciled-tombstone fixture performs its own write first: once
    # _seed_mixed_states plants a conflicting receipt below, prepare_for_write
    # hard-blocks every subsequent write on this manager.
    reconciled_digest = _seed_reconciled_tombstone(manager, key="reconciled-event")
    digests = _seed_mixed_states(manager)
    digests["reconciled"] = reconciled_digest
    store = manager.events.idempotency

    view = store.load_recovery_view()

    union = (
        set(view.reservations)
        | set(view.abandonments)
        | set(view.reconciliations)
        | set(digests.values())
    )
    assert union == set(digests.values())

    for digest in union:
        assert view.reservations.get(digest) == store.get_by_digest(digest)
        assert view.abandonments.get(digest) == store.get_abandonment(digest)
        assert view.reconciliations.get(digest) == store.get_reconciliation(digest)

    assert digests["normal"] in view.reservations
    assert digests["missing"] not in view.reservations
    assert view.reservations[digests["conflicting"]].semantic_sha256 == "f" * 64
    assert digests["orphan"] in view.reservations
    assert digests["tombstoned"] in view.abandonments
    assert digests["tombstoned"] not in view.reconciliations
    assert digests["reconciled"] in view.reconciliations


def test_status_from_bulk_view_matches_status_from_hand_built_per_digest_view(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="status-parity")
    # See test_load_recovery_view_matches_per_digest_accessors_across_receipt_states:
    # the reconciled-tombstone fixture must write before the conflicting receipt
    # is planted, since a conflicting receipt hard-blocks every later write.
    _seed_reconciled_tombstone(manager, key="reconciled-event")
    _seed_mixed_states(manager)
    store = manager.events.idempotency
    records = list(manager.events.iter_events())

    bulk_status = manager.receipt_recovery.status(records)

    all_digests: set[str] = set()
    for reservation in store.iter_reservations():
        all_digests.add(reservation.key_digest)
    for abandonment in store.iter_abandonments():
        all_digests.add(str(abandonment["key_digest"]))
    reconciliation_root = store.paths.idempotency_v2 / "reconciliations" / store.scope["scope_id"]
    if reconciliation_root.exists():
        for path in reconciliation_root.glob("*.json"):
            all_digests.add(path.stem)

    hand_built = ReceiptRecoveryView(
        reservations={
            digest: reservation
            for digest in all_digests
            if (reservation := store.get_by_digest(digest)) is not None
        },
        abandonments={
            digest: abandonment
            for digest in all_digests
            if (abandonment := store.get_abandonment(digest)) is not None
        },
        reconciliations={
            digest: reconciliation
            for digest in all_digests
            if (reconciliation := store.get_reconciliation(digest)) is not None
        },
    )

    hand_built_status = manager.receipt_recovery.status(records, receipt_view=hand_built)

    assert bulk_status == hand_built_status


def test_corrupted_reconciliation_tombstone_hash_mismatch_raises_from_both_paths(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="corrupt-hash")
    digest = _seed_reconciled_tombstone(manager, key="reconciled-event")
    path = _reconciliation_path(manager, digest)
    assert path.exists()
    _replace_field(path, "tombstone_sha256", "0" * 64)

    store = manager.events.idempotency
    with pytest.raises(IdempotencyConflictError, match="tombstone hash"):
        store.get_reconciliation(digest)
    with pytest.raises(IdempotencyConflictError, match="tombstone hash"):
        store.load_recovery_view()


def test_corrupted_reconciliation_foreign_scope_id_raises_from_both_paths(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="corrupt-scope")
    digest = _seed_reconciled_tombstone(manager, key="reconciled-event")
    path = _reconciliation_path(manager, digest)
    assert path.exists()
    _replace_field(path, "scope_id", "f" * 64)

    store = manager.events.idempotency
    with pytest.raises(IdempotencyConflictError, match="conflicts with its recovery scope"):
        store.get_reconciliation(digest)
    with pytest.raises(IdempotencyConflictError, match="conflicts with its recovery scope"):
        store.load_recovery_view()


def test_corrupted_reconciliation_foreign_workspace_id_raises_from_both_paths(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="corrupt-workspace")
    digest = _seed_reconciled_tombstone(manager, key="reconciled-event")
    path = _reconciliation_path(manager, digest)
    assert path.exists()
    _replace_field(path, "workspace_id", "different-workspace")

    store = manager.events.idempotency
    with pytest.raises(IdempotencyConflictError, match="conflicts with its recovery scope"):
        store.get_reconciliation(digest)
    with pytest.raises(IdempotencyConflictError, match="conflicts with its recovery scope"):
        store.load_recovery_view()


def test_load_recovery_view_returns_a_fresh_object_and_the_store_retains_none(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="not-retained")
    _seed_mixed_states(manager)
    store = manager.events.idempotency

    first = store.load_recovery_view()
    second = store.load_recovery_view()
    assert first is not second
    assert first == second

    assert not any("view" in name.lower() for name in vars(store))


def test_receipt_recovery_keeps_no_view_attribute_after_prepare_for_write_or_reconcile(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="rr-no-view")
    # create_objective's write goes through ReceiptRecovery.prepare_for_write.
    _create(manager, key="seed", title="Seed")

    receipt_recovery = manager.receipt_recovery
    assert not any("view" in name.lower() for name in vars(receipt_recovery))

    records = list(manager.events.iter_events())
    result = receipt_recovery.reconcile(records, actor=manager._actor())
    assert result["ok"] is True
    assert not any("view" in name.lower() for name in vars(receipt_recovery))


def test_prepare_for_write_end_to_end_rederives_a_deleted_receipt_through_a_real_write(
    tmp_path: Path,
) -> None:
    manager, _repo, _state_root = _open_workspace(tmp_path, suffix="rederive")
    _create(manager, key="first-write", title="First write")
    assert manager.doctor()["ok"] is True
    status = manager.receipt_status()
    assert status["missing_receipts"] == []
    assert status["orphan_receipts"] == []
    assert status["conflicting_receipts"] == []

    namespace = manager._namespace(manager._active_session())
    receipt = manager.events.idempotency.lookup(namespace=namespace, key="first-write")
    assert receipt is not None
    receipt.path.unlink()
    assert manager.receipt_status()["missing_receipts"] == [receipt.key_digest]

    # An unrelated write must still repair the deleted receipt it finds along
    # the way -- the same guarantee prepare_for_write gave before the bulk view.
    _create(manager, key="second-write", title="Second write")

    status_after = manager.receipt_status()
    assert status_after["missing_receipts"] == []
    assert status_after["orphan_receipts"] == []
    assert status_after["conflicting_receipts"] == []
    assert manager.doctor()["ok"] is True


def test_legacy_receipt_migration_is_unaffected_by_the_bulk_view(tmp_path: Path) -> None:
    """Replicates the inline legacy-migration setup from
    tests/contract/test_h2_checkout_recovery_contract.py::
    test_legacy_exact_receipts_migrate_without_branch_guessing (no separate
    fixture helper exists there to import) against the bulk-view reconcile
    path, and checks the bulk-loaded status still counts the imported receipt.
    """
    manager, repo, state_root = _open_workspace(tmp_path, suffix="legacy")
    _create(manager, key="legacy-event", title="Legacy")
    store = manager.events.idempotency
    namespace = manager._namespace(manager._active_session())
    receipt = store.lookup(namespace=namespace, key="legacy-event")
    assert receipt is not None
    store.prepare_legacy_receipt(receipt)
    shutil.rmtree(manager.paths.idempotency_v2)

    upgraded = CommonsManager(repo, state_root=state_root, session_id=manager.session_id)
    status = upgraded.receipt_status()
    assert status["legacy_receipt_count"] == 1

    records = list(upgraded.events.iter_events())
    recovered = upgraded.receipt_recovery.reconcile(records, actor=upgraded._actor())
    assert recovered["ok"] is True
    assert recovered["imported_receipts"] == 1
    assert recovered["receipt_count"] == 1
    assert upgraded.doctor()["ok"] is True


@pytest.mark.parametrize("conflict", [False, True])
def test_legacy_abandonment_migration_preserves_identity_checks_and_reconciliation(
    tmp_path: Path, conflict: bool
) -> None:
    from agent_commons.errors import IntegrityError

    manager, repo, state_root = _open_workspace(tmp_path, suffix="legacy-abandonment")
    created = _create(manager, key="returned-event", title="Returned event")
    store = manager.events.idempotency
    receipt = store.lookup(
        namespace=manager._namespace(manager._active_session()), key="returned-event"
    )
    assert receipt is not None
    record = manager.events.get(created["event_id"])
    raw = record.path.read_bytes()
    record.path.unlink()
    abandonment = dict(
        store.abandon(
            receipt,
            reason="event lost before exact return",
            actor_session_id=manager.session_id or "",
            actor_principal_id="operator",
        )
    )
    record.path.write_bytes(raw)
    if conflict:
        abandonment["semantic_sha256"] = "f" * 64
    store.prepare_legacy_receipt(receipt)
    store.prepare_legacy_abandonment(abandonment)
    shutil.rmtree(manager.paths.idempotency_v2)
    upgraded = CommonsManager(repo, state_root=state_root, session_id=manager.session_id)
    records = list(upgraded.events.iter_events())
    recovery = upgraded.receipt_recovery
    upgraded_store = upgraded.events.idempotency

    if conflict:
        with pytest.raises(IntegrityError, match="abandonment conflicts"):
            recovery.reconcile(records, actor=upgraded._actor())
        assert upgraded_store.get_migration() is None
        assert upgraded_store.get_reconciliation(receipt.key_digest) is None
    else:
        result = recovery.reconcile(records, actor=upgraded._actor())
        assert result["ok"] is True
        assert result["reconciled_tombstones_count"] == 1
        fresh = upgraded.receipt_status()
        assert fresh["ok"] is True
        assert fresh["tombstone_matches"] == []
        assert upgraded_store.get_reconciliation(receipt.key_digest) is not None
        retried = recovery.reconcile(records, actor=upgraded._actor())
        assert retried["ok"] is True
        assert retried["reconciled_tombstones_count"] == 0
