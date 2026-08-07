from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import canonical_json_file_bytes
from agent_commons.core.ids import stable_id
from agent_commons.errors import IntegrityError
from agent_commons.index.sqlite import SQLiteIndex
from agent_commons.storage.events import EventStore
from agent_commons.storage.manifests import ManifestStore

from .helpers import event_document, make_kernel, manifest_document, workspace_id


def test_rebuild_and_incremental_sync_follow_authoritative_files(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())
    manifest = manifests.put(manifest_document(related_ref={"kind": "note", "id": "note.1"}))

    with SQLiteIndex(paths, events, manifests) as index:
        rebuilt = index.rebuild()
        assert (rebuilt.scanned, rebuilt.indexed) == (2, 2)
        assert index.event_count() == 1
        assert index.manifest_count() == 1
        assert index.get_event(event.event_id) == event.event
        assert index.get_manifest(manifest.manifest_id) == manifest.manifest
        assert index.list_events(workspace_id=workspace_id()) == [event.event]
        assert index.references_to("note", "note.1") == [
            ("event", event.event_id),
            ("manifest", manifest.manifest_id),
        ]

        second = events.append(event_document("second", key="note-2"))
        synced = index.sync()
        assert synced.indexed == 1
        assert synced.unchanged == 2
        assert index.event_count() == 2

        second.path.unlink()
        removed = index.sync()
        assert removed.removed == 1
        assert index.event_count() == 1
        assert index.references_to("note", "note.1") == [
            ("event", event.event_id),
            ("manifest", manifest.manifest_id),
        ]


def test_projection_is_wal_and_rebuild_repairs_local_corruption(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())

    with SQLiteIndex(paths, events, manifests) as index:
        assert index.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        index.rebuild()
        index.connection.execute("DELETE FROM events")
        index.connection.commit()
        assert index.event_count() == 0
        with pytest.raises(IntegrityError, match="source coverage"):
            index.read_projection(workspace_id=workspace_id())
        index.rebuild()
        assert index.get_event(event.event_id) == event.event


def test_projection_read_verifies_document_hashes(tmp_path: Path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())

    with SQLiteIndex(paths, events, manifests) as index:
        index.rebuild()
        projected = index.read_projection(workspace_id=workspace_id())
        assert projected.events == (event.event,)
        assert projected.source_count == 1
        assert len(projected.verified_head_sha256) == 64
        index.connection.execute(
            "UPDATE events SET document_json = ? WHERE event_id = ?",
            ('{"tampered":true}', event.event_id),
        )
        index.connection.commit()
        with pytest.raises(IntegrityError, match="document hash mismatch"):
            index.read_projection(workspace_id=workspace_id())


def test_warm_thousand_event_projection_reads_no_canonical_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event_root = paths.events / "2026" / "01" / "01"
    event_root.mkdir(parents=True)
    for index_value in range(1_000):
        document = event_document(str(index_value), key=f"note-{index_value}")
        event_id = stable_id("evt", f"scale-{index_value}")
        document["event_id"] = event_id
        document["recorded_at"] = "2026-01-01T00:00:00Z"
        schemas.validate_event(document)
        (event_root / f"{event_id}.json").write_bytes(canonical_json_file_bytes(document))

    with SQLiteIndex(paths, events, manifests) as index:
        index.rebuild()

        def unexpected_read(_: object) -> object:
            raise AssertionError("warm projection must not read canonical file contents")

        monkeypatch.setattr(events, "read_path", unexpected_read)
        synced = index.sync()
        projected = index.read_projection(workspace_id=workspace_id())

    assert synced.scanned == 1_000
    assert synced.indexed == 0
    assert synced.unchanged == 1_000
    assert len(projected.events) == 1_000


def test_failed_verified_sync_rolls_back_previous_projection(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())

    with SQLiteIndex(paths, events, manifests) as index:
        index.rebuild()
        event.path.write_text("{}\n", encoding="utf-8")
        with pytest.raises(IntegrityError, match="filename does not match"):
            index.sync(verify_unchanged=True)
        assert index.get_event(event.event_id) == event.event


def test_projection_rejects_unknown_database_version(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    with pytest.raises(IntegrityError, match="unsupported SQLite"):
        SQLiteIndex(
            paths,
            EventStore(paths, schemas),
            ManifestStore(paths, schemas),
            database_path=database,
        )


def test_projection_is_bound_to_one_workspace_id(tmp_path: Path) -> None:
    paths, schemas = make_kernel(tmp_path)
    bound = CommonsPaths.for_workspace(
        paths.repo_root,
        commons_root=paths.commons_root,
        state_root=paths.state_root,
        workspace_id=workspace_id(),
    )
    events = EventStore(bound, schemas)
    manifests = ManifestStore(bound, schemas)
    events.append(event_document())
    with SQLiteIndex(bound, events, manifests) as index:
        index.rebuild()

    foreign = CommonsPaths.for_workspace(
        paths.repo_root,
        commons_root=paths.commons_root,
        state_root=paths.state_root,
        workspace_id="workspace.00000000000000000000000002",
    )
    with pytest.raises(IntegrityError, match="belongs to a different workspace"):
        SQLiteIndex(foreign, EventStore(foreign, schemas), ManifestStore(foreign, schemas))
