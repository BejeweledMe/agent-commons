"""UI cache reads are scoped, owned, content-verified and never writer guards."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from agent_commons.core.canonical import canonical_json_file_bytes, loads_json_strict
from agent_commons.errors import CommonsError, IntegrityError
from agent_commons.services.conversations import Conversations
from agent_commons.services.manager import CommonsManager
from agent_commons.ui import snapshot_cache
from agent_commons.ui.context import UIContext
from tests.services.test_conversations import manager as manager_fixture


@pytest.fixture
def manager(tmp_path):
    return manager_fixture.__wrapped__(tmp_path)


@pytest.fixture
def context(manager):
    return UIContext(
        manager.repo_root, state_root=manager.paths.state_root, writer_session_id=manager.session_id
    )


def create_task(manager, title="First"):
    return manager.create_task(
        title=title,
        description="A synthetic cache fixture",
        acceptance_criteria=("Observed correctly",),
    )


def counter(monkeypatch):
    original = CommonsManager.snapshot
    counts = []

    def counted(self):
        counts.append(self.read_only)
        return original(self)

    monkeypatch.setattr(CommonsManager, "snapshot", counted)
    return counts


def test_concurrent_read_managers_share_one_load_and_owned_snapshots(context, manager, monkeypatch):
    task = create_task(manager)
    original = CommonsManager.snapshot
    counts = []
    barrier = threading.Barrier(4)

    def slow(self):
        counts.append(True)
        time.sleep(0.02)
        return original(self)

    monkeypatch.setattr(CommonsManager, "snapshot", slow)

    def read(_):
        barrier.wait(timeout=3)
        return context.manager().snapshot()

    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(read, range(4)))
    assert len(counts) == 1
    identifier = task["entity_ref"]["id"]
    values[0].tasks.clear()
    assert values[1].tasks[identifier]["title"] == "First"
    assert context.manager().snapshot().tasks[identifier]["title"] == "First"
    assert len(counts) == 1


def test_writer_is_uncached_and_new_event_invalidates_reads(context, monkeypatch):
    counts = counter(monkeypatch)
    reader = context.manager()
    assert reader.read_only
    reader.snapshot()
    reader.snapshot()
    assert counts == [True]
    writer = context.writer()
    assert type(writer) is CommonsManager and not writer.read_only
    task = create_task(writer)
    assert task["entity_ref"]["id"] in reader.snapshot().tasks
    assert counts.count(True) == 2
    with pytest.raises(CommonsError):
        create_task(reader)


def test_same_size_preserved_mtime_edit_never_hits_old_snapshot(context, manager, monkeypatch):
    task = create_task(manager)
    counts = counter(monkeypatch)
    context.manager().snapshot()
    event = manager.events.get(task["revision"])
    path = event.path
    before = path.stat()
    value = loads_json_strict(path.read_bytes())
    value["payload"]["title"] = "Other"
    data = canonical_json_file_bytes(value)
    assert len(data) == before.st_size
    path.write_bytes(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    result = context.manager().snapshot()
    assert result.tasks[task["entity_ref"]["id"]]["title"] == "Other"
    assert counts.count(True) == 2


def test_corrupt_bytes_never_fall_back_to_cached_snapshot(context, manager):
    task = create_task(manager)
    context.manager().snapshot()
    path = manager.events.get(task["revision"]).path
    path.write_bytes(b"not canonical JSON")
    for _ in range(2):
        with pytest.raises(CommonsError):
            context.manager().snapshot()


def test_deleted_event_invalidates_snapshot(context, manager):
    task = create_task(manager)
    context.manager().snapshot()
    manager.events.get(task["revision"]).path.unlink()
    assert task["entity_ref"]["id"] not in context.manager().snapshot().tasks


def test_changed_manifest_is_validated_instead_of_serving_old_cache(context, manager):
    context.manager().snapshot()
    folder = manager.paths.manifests / "artifact" / "aa"
    folder.mkdir(parents=True)
    (folder / "bad.json").write_bytes(b"{}\n")
    with pytest.raises(CommonsError):
        context.manager().snapshot()


def test_symlinked_event_directory_is_never_followed(context, manager, tmp_path):
    create_task(manager)
    reader = context.manager()
    reader.snapshot()
    manager.paths.events.rename(tmp_path / "original-events")
    outside = tmp_path / "outside"
    outside.mkdir()
    manager.paths.events.symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntegrityError):
        reader.snapshot()
    assert list(outside.iterdir()) == []


def test_event_file_symlink_cannot_reuse_known_bytes(context, manager, tmp_path):
    task = create_task(manager)
    context.manager().snapshot()
    path = manager.events.get(task["revision"]).path
    outside = tmp_path / "copy.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(IntegrityError):
        context.manager().snapshot()


def test_canonical_change_during_load_is_not_published(context, manager, monkeypatch):
    original = CommonsManager.snapshot
    changed = False
    created = None

    def interleaved(self):
        nonlocal changed, created
        result = original(self)
        if self.read_only and not changed:
            changed = True
            created = create_task(manager)
        return result

    monkeypatch.setattr(CommonsManager, "snapshot", interleaved)
    with pytest.raises(IntegrityError):
        context.manager().snapshot()
    assert created["entity_ref"]["id"] in context.manager().snapshot().tasks


def test_workspaces_and_contexts_never_share_cached_snapshot(manager, tmp_path, monkeypatch):
    first = UIContext(manager.repo_root, state_root=manager.paths.state_root)
    second = UIContext(manager.repo_root, state_root=manager.paths.state_root)
    counts = counter(monkeypatch)
    first.manager().snapshot()
    second.manager().snapshot()
    assert counts == [True, True]
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = manager_fixture.__wrapped__(other_root)
    task = create_task(other)
    first.repo = other.repo_root
    first._state_root = other.paths.state_root
    assert task["entity_ref"]["id"] in first.manager().snapshot().tasks
    assert task["entity_ref"]["id"] not in second.manager().snapshot().tasks


def test_workspace_identity_change_cannot_reuse_previous_projection(context, manager):
    import yaml

    create_task(manager)
    context.manager().snapshot()
    path = manager.paths.commons_root / "workspace.yaml"
    value = yaml.safe_load(path.read_text())
    value["workspace_id"] = "workspace.different"
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(CommonsError):
        context.manager().snapshot()


def test_operational_changes_do_not_force_canonical_replay(context, manager, monkeypatch):
    counts = counter(monkeypatch)
    context.manager().snapshot()
    runtime = manager.paths.state_root / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "synthetic-update.json").write_bytes(b'{"state":"running"}')
    context.manager().snapshot()
    assert counts == [True]


def test_explicit_fresh_read_bypasses_cache(context, monkeypatch):
    counts = counter(monkeypatch)
    reader = context.manager()
    reader.snapshot()
    reader._read_snapshot(fresh=True)
    reader.snapshot()
    assert counts == [True, True]


def test_cache_budget_falls_back_to_authoritative_uncached_reads(context, monkeypatch):
    counts = counter(monkeypatch)
    monkeypatch.setattr(snapshot_cache, "_MAX_BYTES", 1)
    context.manager().snapshot()
    context.manager().snapshot()
    assert counts == [True, True]


def test_graph_and_conversation_reads_share_cache_without_canonical_writes(
    context, manager, monkeypatch
):
    Conversations(manager).ensure(scope={"kind": "project"}, idempotency_key="open")
    expected = deepcopy(manager.snapshot().threads)
    counts = counter(monkeypatch)
    context.rebuild_graph()
    conversations = Conversations(context.manager()).list({"kind": "project"})
    context.manager().snapshot()
    assert len(conversations["conversations"]) == len(expected)
    assert counts == [True]


def test_graph_refresh_detects_equal_size_preserved_mtime_tamper(context, manager, monkeypatch):
    task = create_task(manager)
    counts = counter(monkeypatch)
    context.rebuild_graph()
    assert not context.refresh_if_changed()
    path = manager.events.get(task["revision"]).path
    before = path.stat()
    value = loads_json_strict(path.read_bytes())
    value["payload"]["title"] = "Other"
    data = canonical_json_file_bytes(value)
    assert len(data) == before.st_size
    path.write_bytes(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert context.refresh_if_changed()
    assert counts == [True, True]
    assert context.manager().snapshot().tasks[task["entity_ref"]["id"]]["title"] == "Other"


def test_graph_refresh_detects_workspace_configuration_change(context, manager):
    context.rebuild_graph()
    before = context.fingerprint()
    path = manager.paths.commons_root / "workspace.yaml"
    info = path.stat()
    path.write_bytes(path.read_bytes() + b"\n")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert context.fingerprint() != before
    assert context.refresh_if_changed()


def test_existing_manifest_bytes_are_checked_even_with_same_identity_and_mtime(context, manager):
    source = manager.repo_root / "synthetic-evidence.txt"
    source.write_text("synthetic evidence")
    registered = manager.register_artifact(source, media_type="text/plain")
    record = manager.manifests.get(registered["manifest_id"])
    context.manager().snapshot()
    info = record.path.stat()
    raw = record.path.read_bytes()
    record.path.write_bytes(b"!" + raw[1:])
    os.utime(record.path, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises(CommonsError):
        context.manager().snapshot()


def test_validated_configuration_semantics_are_part_of_cache_scope(context, monkeypatch):
    counts = counter(monkeypatch)
    reader = context.manager()
    reader.snapshot()
    reader.workspace_config = {**reader.workspace_config, "name": "Changed in memory"}
    reader.snapshot()
    assert counts == [True, True]
