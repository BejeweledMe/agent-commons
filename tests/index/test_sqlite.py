from __future__ import annotations

import sqlite3

import pytest

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
        index.rebuild()
        assert index.get_event(event.event_id) == event.event


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
