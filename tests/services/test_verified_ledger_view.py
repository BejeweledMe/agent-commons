from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.core.canonical import canonical_json_file_bytes
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.services import CommonsManager


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="verified-view")
    manager = CommonsManager(repo, state_root=state)
    session = manager.start_session(
        stable_instance_id="verified-view-test-agent-12345678",
        principal="operator",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager.session_id = session["session_id"]
    manager.create_task(
        title="seed",
        description="seed ledger record",
        acceptance_criteria=["seed"],
        idempotency_key="seed",
    )
    return manager


def test_verified_view_reuses_unchanged_records_by_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    calls = 0
    original = manager.events.read_path

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(manager.events, "read_path", counted)
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
        assert calls == 1
        manager._records_and_snapshot()
        assert calls == 1


def test_verified_view_fully_revalidates_changed_file(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
        event = next(manager.paths.events.glob("*/*/*/evt.*.json"))
        event.write_bytes(b"{}\n")
        with pytest.raises(IntegrityError):
            manager._records_and_snapshot()


def test_verified_view_revalidates_after_lock_reacquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    calls = 0
    original = manager.events.read_path

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(manager.events, "read_path", counted)
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
    assert calls == 2


def test_write_builds_one_receipt_view_for_preflight_and_one_for_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    calls = 0
    original = manager.events.idempotency.load_recovery_view

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(manager.events.idempotency, "load_recovery_view", counted)
    manager.create_task(
        title="second",
        description="exercises receipt bulk loading",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )

    # ``prepare_for_write`` shares its first view across status and recovery;
    # the post-append reconcile then deliberately starts a fresh view.
    assert calls == 2


def test_verified_view_validates_only_own_new_file_within_one_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    calls = 0
    original = manager.events.read_path

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    with manager._canonical_write_lock():
        manager._records_and_snapshot()
        monkeypatch.setattr(manager.events, "read_path", counted)
        # Our own append is fully validated before being accepted into the
        # current view. The freshness refresh still hashes the seed but does
        # not repeat its schema and policy validation.
        manager.create_task(
            title="second",
            description="a second canonical event created inside the same hold",
            acceptance_criteria=["second"],
            idempotency_key="second",
        )
        event_paths = list(manager.paths.events.glob("*/*/*/evt.*.json"))
        assert len(event_paths) == 2
        assert calls == 1

        # The view now covers both files by identity: a later snapshot in the
        # same hold reuses them without going back through read_path.
        calls = 0
        records, _ = manager._records_and_snapshot()
        assert len(records) == 2
        assert calls == 0


def test_verified_view_snapshot_silently_drops_a_deleted_event_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The view does not itself detect a missing chain link.

    ``_records_and_snapshot`` has no notion of a fixed event count: deleting a
    canonical file just makes the projection forget that event ever existed,
    the same as it would for a workspace that legitimately never recorded it.
    Confirmed here by running the actual code path rather than assuming a
    raise; the full rebuild this triggers (every *remaining* path revalidated)
    is asserted instead. Downstream integrity checks such as ``doctor`` -- not
    exercised by this test -- are what catch the resulting orphaned receipt.
    """

    manager = _manager(tmp_path)
    manager.create_task(
        title="second",
        description="a second canonical event, so one deletion still leaves one behind",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
        event_paths = sorted(manager.paths.events.glob("*/*/*/evt.*.json"))
        assert len(event_paths) == 2
        event_paths[0].unlink()

        calls = 0
        original = manager.events.read_path

        def counted(path: Path):
            nonlocal calls
            calls += 1
            return original(path)

        monkeypatch.setattr(manager.events, "read_path", counted)
        records, _ = manager._records_and_snapshot()
        remaining = list(manager.paths.events.glob("*/*/*/evt.*.json"))
        assert len(remaining) == 1
        assert len(records) == 1
        # The path set changed, so the full (now smaller) collection is
        # revalidated rather than raising: no exception was observed here.
        assert calls == len(remaining) == 1


def test_verified_view_supports_a_nested_write_inside_a_trusted_before_append_hook(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    thread = manager.open_thread(
        thread_type="question",
        subject="Question",
        desired_outcome="Answer",
        to=("operator",),
        idempotency_key="hook-thread",
    )

    def hook() -> None:
        # A trusted service hook performing its own canonical write, reentrant
        # on the same lock hold as the outer reply, exactly as the message
        # attachment binding hook does in Conversations.send.
        manager.create_task(
            title="nested",
            description="created from inside a before_append hook",
            acceptance_criteria=["nested"],
            idempotency_key="nested",
        )

    result = manager.reply_thread(
        thread["entity_ref"]["id"],
        thread["revision"],
        body="hello from the hook test",
        idempotency_key="hook-reply",
        _before_append=hook,
    )
    assert result["event_type"] == "thread.replied"

    snapshot = manager.snapshot()
    assert thread["entity_ref"]["id"] in snapshot.threads
    messages = snapshot.threads[thread["entity_ref"]["id"]]["messages"]
    assert any(message["body"] == "hello from the hook test" for message in messages)
    task_titles = {task["title"] for task in snapshot.tasks.values()}
    assert {"seed", "nested"} <= task_titles

    doctor = manager.doctor()
    assert doctor["ok"] is True
    assert doctor["issues"] == []


def test_doctor_is_ok_before_and_after_a_view_backed_write_and_never_sets_the_view(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    assert manager._verified_ledger_view is None

    before = manager.doctor()
    assert manager._verified_ledger_view is None

    manager.create_task(
        title="second",
        description="a second canonical event written through the verified view",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )
    assert manager._verified_ledger_view is None

    after = manager.doctor()
    assert manager._verified_ledger_view is None

    for result in (before, after):
        assert result["ok"] is True
        assert result["issues"] == []
        assert isinstance(result["warnings"], list)
        assert result["index"] is not None
        assert result["index"]["scanned"] == result["index"]["indexed"]
    assert after["event_count"] == before["event_count"] + 1
    assert after["index"]["scanned"] == before["index"]["scanned"] + 1


def test_verified_view_raises_when_a_file_keeps_changing_across_all_three_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    event = next(manager.paths.events.glob("*/*/*/evt.*.json"))
    original = manager.events.read_path
    mutation_count = 0

    def racing(path: Path):
        # Read the current (still canonical) bytes first, exactly like a real
        # reader would, then mutate the file under it -- simulating a writer
        # racing the verification read on every attempt, not only the first.
        record = original(path)
        if path == event:
            nonlocal mutation_count
            mutation_count += 1
            value = json.loads(path.read_bytes())
            value["tags"] = sorted(set(value.get("tags", [])) | {f"race-{mutation_count}"})
            path.write_bytes(canonical_json_file_bytes(value))
        return record

    monkeypatch.setattr(manager.events, "read_path", racing)
    with manager._canonical_write_lock():
        with pytest.raises(IntegrityError, match="changed while being verified"):
            manager._records_and_snapshot()
    # Three attempts, each one observing a mutation after its own read.
    assert mutation_count == 3


def test_verified_view_revalidates_a_changed_parent_path_even_with_identical_bytes(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    with manager._canonical_write_lock():
        records, _ = manager._records_and_snapshot()
        event = records[0].path
        original_day = event.parent
        outside = tmp_path / "moved-event-day"
        original_day.rename(outside)
        original_day.symlink_to(outside, target_is_directory=True)
        # The final file is regular and its bytes are unchanged, but read_path
        # must still enforce the resolved canonical storage boundary.
        with pytest.raises(IntegrityError, match="outside canonical storage"):
            manager._records_and_snapshot()


def test_own_append_still_revalidates_an_unexpected_extra_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    original_append = manager.events.append_event
    original_read = manager.events.read_path
    reads: list[Path] = []

    def counted(path: Path):
        reads.append(path)
        return original_read(path)

    def extra_append(**kwargs):
        record = original_append(**kwargs)
        extra = dict(kwargs)
        extra["idempotency_key"] = "unexpected-extra"
        extra["payload"] = {**kwargs["payload"], "title": "unexpected"}
        original_append(**extra)
        return record

    monkeypatch.setattr(manager.events, "read_path", counted)
    monkeypatch.setattr(manager.events, "append_event", extra_append)
    manager.create_task(
        title="second",
        description="second",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )
    seed = next(
        record.path
        for record in manager.events.iter_events()
        if record.event["payload"].get("title") == "seed"
    )
    # Initial read, structural rebuild, and our explicit iter_events read.
    assert reads.count(seed) == 3


@pytest.mark.parametrize("change", ["same-size-corruption", "delete", "symlink"])
def test_own_append_does_not_hide_a_changed_prior_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    manager = _manager(tmp_path)
    seed = next(manager.paths.events.glob("*/*/*/evt.*.json"))
    original_append = manager.events.append_event

    def mutate_prior(**kwargs):
        record = original_append(**kwargs)
        if change == "same-size-corruption":
            before = seed.stat()
            raw = seed.read_bytes()
            seed.write_bytes(
                raw.replace(b'"schema":"commons.event.v1"', b'"schema":"commons.event.v0"')
            )
            assert seed.stat().st_size == before.st_size
            os.utime(seed, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "delete":
            seed.unlink()
        else:
            outside = tmp_path / "seed-copy.json"
            outside.write_bytes(seed.read_bytes())
            seed.unlink()
            seed.symlink_to(outside)
        return record

    monkeypatch.setattr(manager.events, "append_event", mutate_prior)
    expected_error = ValidationError if change == "same-size-corruption" else IntegrityError
    with pytest.raises(expected_error):
        manager.create_task(
            title="second",
            description="second",
            acceptance_criteria=["second"],
            idempotency_key="second",
        )
    assert manager._verified_ledger_view is None


@pytest.mark.parametrize("condition", ["retry", "repair", "maintenance", "different-content"])
def test_own_append_falls_back_for_ambiguous_or_recovery_records(
    tmp_path: Path, condition: str
) -> None:
    manager = _manager(tmp_path)
    with manager._canonical_write_lock():
        manager._records_and_snapshot()
        view = manager._verified_ledger_view
        assert view is not None
        original = next(manager.events.iter_events())
        view.events.clear()
        record = replace(original, created=True)
        if condition == "retry":
            record = replace(record, created=False)
        elif condition == "repair":
            record = replace(record, repaired=True)
        elif condition == "maintenance":
            record = replace(record, event={**record.event, "event_type": "event.corrected"})
        else:
            record = replace(record, sha256="0" * 64)
        view.accept_own_append(record)
        assert view.events == {}
        records, _ = view.refresh()
        assert records == [original]


def test_crash_while_accepting_own_append_is_recovered_by_identical_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_commons.services.manager import _VerifiedLedgerView

    manager = _manager(tmp_path)
    kwargs = dict(
        title="second",
        description="second",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )

    def crash(self, record):
        assert record.created
        raise RuntimeError("crash after append")

    with monkeypatch.context() as patch:
        patch.setattr(_VerifiedLedgerView, "accept_own_append", crash)
        with pytest.raises(RuntimeError, match="crash after append"):
            manager.create_task(**kwargs)
    assert manager._verified_ledger_view is None
    after_crash = list(manager.events.iter_events())
    assert len(after_crash) == 2
    result = manager.create_task(**kwargs)
    assert result["created"] is False
    assert len(list(manager.events.iter_events())) == 2
    assert manager.doctor()["ok"]


def test_same_manager_threads_cannot_bypass_lifecycle_lock(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from agent_commons.domain.task_edits import TaskEditRefusal

    manager = _manager(tmp_path)
    first = next(iter(manager.snapshot().tasks.values()))
    second = manager.create_task(
        title="second",
        description="second",
        acceptance_criteria=["second"],
        idempotency_key="second",
    )
    barrier = Barrier(2)

    def depend(source_id: str, revision: str, dependency: str) -> str:
        barrier.wait(timeout=5)
        try:
            manager.edit_task(
                source_id,
                revision,
                changes={"dependencies": [dependency]},
                idempotency_key="depend-" + source_id,
            )
            return "saved"
        except TaskEditRefusal as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(depend, first["task_id"], first["revision"], second["entity_ref"]["id"]),
            pool.submit(depend, second["entity_ref"]["id"], second["revision"], first["task_id"]),
        ]
        assert sorted(f.result(timeout=10) for f in futures) == ["saved", "task_dependency_cycle"]
    assert manager._write_lock_depth == 0
    assert manager._verified_ledger_view is None
    assert manager.doctor()["ok"]


def test_another_thread_waits_for_same_manager_outer_hold(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    manager = _manager(tmp_path)
    started = Event()
    entered = Event()

    def acquire():
        started.set()
        with manager._canonical_write_lock():
            entered.set()
            assert manager._write_lock_depth == 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager._canonical_write_lock():
            manager._records_and_snapshot()
            view = manager._verified_ledger_view
            future = pool.submit(acquire)
            assert started.wait(timeout=5)
            assert not entered.wait(timeout=0.1)
            assert manager._verified_ledger_view is view
            assert manager._write_lock_depth == 1
        future.result(timeout=5)
    assert entered.is_set()
    assert manager._verified_ledger_view is None


@pytest.mark.parametrize("read", ["snapshot", "identity", "manifests"])
def test_other_thread_read_does_not_refresh_a_writers_view(tmp_path: Path, read: str) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    manager = _manager(tmp_path)
    started = Event()
    completed = Event()
    records = list(manager.events.iter_events())
    snapshot = manager.snapshot()

    def observe():
        started.set()
        if read == "snapshot":
            manager.snapshot()
        elif read == "identity":
            seed = records[0].event
            manager._event_for_idempotency_identity(
                seed["idempotency_namespace"], seed["idempotency_key"]
            )
        else:
            manager._manifest_reference_issues(records, snapshot)
        completed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager._canonical_write_lock():
            manager._records_and_snapshot()
            future = pool.submit(observe)
            assert started.wait(timeout=5)
            assert not completed.wait(timeout=0.1)
        future.result(timeout=5)
    assert completed.is_set()


def test_rebuild_uses_only_verifications_before_and_after_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_commons.services.manager import _VerifiedLedgerView

    manager = _manager(tmp_path)
    identities: list[Path] = []
    original = _VerifiedLedgerView._identity

    def counted(path: Path):
        identities.append(path)
        return original(path)

    monkeypatch.setattr(_VerifiedLedgerView, "_identity", staticmethod(counted))
    with manager._canonical_write_lock():
        records, _ = manager._records_and_snapshot()
        assert identities == [records[0].path, records[0].path]
        identities.clear()
        manager._records_and_snapshot()
        # Cache reuse still checks every file's bytes, not only stat metadata.
        assert identities == [records[0].path]
