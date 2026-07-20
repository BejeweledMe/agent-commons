"""Rebuildable SQLite/WAL projection for generic events and manifests."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict
from agent_commons.core.refs import iter_typed_refs
from agent_commons.errors import IntegrityError
from agent_commons.storage import EventRecord, EventStore, ManifestRecord, ManifestStore

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IndexSyncResult:
    scanned: int
    indexed: int
    removed: int
    unchanged: int


class SQLiteIndex:
    """A disposable query accelerator; canonical files always win."""

    def __init__(
        self,
        paths: CommonsPaths,
        events: EventStore,
        manifests: ManifestStore,
        *,
        database_path: str | Path | None = None,
    ) -> None:
        self.paths = paths
        self.events = events
        self.manifests = manifests
        self.database_path = Path(database_path or paths.index_db)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        journal_mode = str(
            self.connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        ).lower()
        if journal_mode != "wal":
            self.connection.close()
            raise IntegrityError(f"SQLite projection requires WAL mode, got {journal_mode!r}")
        try:
            self._initialize()
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> SQLiteIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, _SCHEMA_VERSION}:
            raise IntegrityError(
                f"unsupported SQLite projection version {version}; rebuild with compatible tooling"
            )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_files (
                path TEXT PRIMARY KEY,
                file_kind TEXT NOT NULL CHECK(file_kind IN ('event', 'manifest')),
                identity TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                document_json TEXT NOT NULL,
                source_path TEXT NOT NULL UNIQUE
                    REFERENCES source_files(path) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS events_type_time
                ON events(event_type, recorded_at, event_id);
            CREATE INDEX IF NOT EXISTS events_workspace_time
                ON events(workspace_id, recorded_at, event_id);

            CREATE TABLE IF NOT EXISTS manifests (
                manifest_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                schema_name TEXT NOT NULL,
                document_json TEXT NOT NULL,
                source_path TEXT NOT NULL UNIQUE
                    REFERENCES source_files(path) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS manifests_kind ON manifests(kind, manifest_id);

            CREATE TABLE IF NOT EXISTS event_subjects (
                event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                ref_kind TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                PRIMARY KEY(event_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS event_subject_lookup
                ON event_subjects(ref_kind, ref_id, event_id);

            CREATE TABLE IF NOT EXISTS relations (
                event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                subject_kind TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_kind TEXT NOT NULL,
                object_id TEXT NOT NULL,
                PRIMARY KEY(event_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS relation_object_lookup
                ON relations(object_kind, object_id, predicate);

            CREATE TABLE IF NOT EXISTS explicit_refs (
                owner_kind TEXT NOT NULL CHECK(owner_kind IN ('event', 'manifest')),
                owner_id TEXT NOT NULL,
                ref_kind TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                PRIMARY KEY(owner_kind, owner_id, ref_kind, ref_id)
            );
            CREATE INDEX IF NOT EXISTS explicit_ref_lookup
                ON explicit_refs(ref_kind, ref_id, owner_kind, owner_id);
            """
        )
        self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self.connection.commit()

    def rebuild(self) -> IndexSyncResult:
        """Validate every canonical file and replace the projection atomically."""

        return self._sync(force=True, reset=True)

    def sync(self, *, verify_unchanged: bool = False) -> IndexSyncResult:
        """Incrementally reconcile changed, added, and removed canonical files.

        By default, unchanged size/mtime pairs are trusted because this database
        is disposable. ``verify_unchanged`` revalidates every file and is suited
        to integrity checks rather than the interactive fast path.
        """

        return self._sync(force=verify_unchanged, reset=False)

    def _sync(self, *, force: bool, reset: bool) -> IndexSyncResult:
        event_paths = (
            sorted(self.paths.events.glob("*/*/*/evt.*.json")) if self.paths.events.exists() else []
        )
        manifest_paths = (
            sorted(self.paths.manifests.glob("*/*/*.json")) if self.paths.manifests.exists() else []
        )
        discovered = [("event", path) for path in event_paths] + [
            ("manifest", path) for path in manifest_paths
        ]
        known = {
            str(row["path"]): row for row in self.connection.execute("SELECT * FROM source_files")
        }
        current_relative = {
            self.paths.canonical_relative(path): (kind, path) for kind, path in discovered
        }
        removed_paths = sorted(set(known) - set(current_relative))
        indexed = 0
        unchanged = 0

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if reset:
                self.connection.execute("DELETE FROM explicit_refs")
                self.connection.execute("DELETE FROM source_files")
                known = {}
                removed_paths = []
            else:
                for relative in removed_paths:
                    self._delete_source(relative)

            for relative, (kind, path) in sorted(current_relative.items()):
                stat = path.stat()
                previous = known.get(relative)
                if (
                    not force
                    and previous is not None
                    and int(previous["size_bytes"]) == stat.st_size
                    and int(previous["mtime_ns"]) == stat.st_mtime_ns
                ):
                    unchanged += 1
                    continue
                if kind == "event":
                    record = self.events.read_path(path)
                    self._replace_event(relative, stat.st_size, stat.st_mtime_ns, record)
                else:
                    record = self.manifests.read_path(path)
                    self._replace_manifest(relative, stat.st_size, stat.st_mtime_ns, record)
                indexed += 1
            self.connection.commit()
        except Exception as exc:
            self.connection.rollback()
            if isinstance(exc, IntegrityError):
                raise
            raise IntegrityError(f"SQLite projection sync failed: {exc}") from exc

        return IndexSyncResult(
            scanned=len(discovered),
            indexed=indexed,
            removed=len(removed_paths),
            unchanged=unchanged,
        )

    def _replace_source(
        self,
        *,
        relative: str,
        file_kind: str,
        identity: str,
        size_bytes: int,
        mtime_ns: int,
        sha256: str,
    ) -> None:
        self._delete_source(relative)
        self.connection.execute(
            """
            INSERT INTO source_files(path, file_kind, identity, size_bytes, mtime_ns, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (relative, file_kind, identity, size_bytes, mtime_ns, sha256),
        )

    def _delete_source(self, relative: str) -> None:
        existing = self.connection.execute(
            "SELECT file_kind, identity FROM source_files WHERE path = ?", (relative,)
        ).fetchone()
        if existing is not None:
            self.connection.execute(
                "DELETE FROM explicit_refs WHERE owner_kind = ? AND owner_id = ?",
                (existing["file_kind"], existing["identity"]),
            )
        self.connection.execute("DELETE FROM source_files WHERE path = ?", (relative,))

    def _replace_event(
        self, relative: str, size_bytes: int, mtime_ns: int, record: EventRecord
    ) -> None:
        event = record.event
        self._replace_source(
            relative=relative,
            file_kind="event",
            identity=record.event_id,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            sha256=record.sha256,
        )
        self.connection.execute(
            """
            INSERT INTO events(
                event_id, workspace_id, event_type, recorded_at,
                actor_json, payload_json, document_json, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.event_id,
                event["workspace_id"],
                event["event_type"],
                event["recorded_at"],
                _json_text(event["actor"]),
                _json_text(event["payload"]),
                _json_text(event),
                relative,
            ),
        )
        for ordinal, ref in enumerate(event.get("subject_refs", [])):
            self.connection.execute(
                "INSERT INTO event_subjects VALUES (?, ?, ?, ?)",
                (record.event_id, ordinal, ref["kind"], ref["id"]),
            )
        for ordinal, relation in enumerate(event.get("relations", [])):
            subject, object_ref = relation["subject"], relation["object"]
            self.connection.execute(
                "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.event_id,
                    ordinal,
                    subject["kind"],
                    subject["id"],
                    relation["predicate"],
                    object_ref["kind"],
                    object_ref["id"],
                ),
            )
        self._insert_refs("event", record.event_id, event)

    def _replace_manifest(
        self, relative: str, size_bytes: int, mtime_ns: int, record: ManifestRecord
    ) -> None:
        self._replace_source(
            relative=relative,
            file_kind="manifest",
            identity=record.manifest_id,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            sha256=record.sha256,
        )
        self.connection.execute(
            """
            INSERT INTO manifests(manifest_id, kind, schema_name, document_json, source_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.manifest_id,
                record.kind,
                record.manifest["schema"],
                _json_text(record.manifest),
                relative,
            ),
        )
        self._insert_refs("manifest", record.manifest_id, record.manifest)

    def _insert_refs(self, owner_kind: str, owner_id: str, document: Any) -> None:
        refs = sorted({(ref.kind, ref.id) for ref in iter_typed_refs(document)})
        self.connection.executemany(
            "INSERT INTO explicit_refs VALUES (?, ?, ?, ?)",
            ((owner_kind, owner_id, kind, identifier) for kind, identifier in refs),
        )

    def event_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def manifest_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM manifests").fetchone()[0])

    def get_event(self, event_id: str) -> Mapping[str, Any]:
        row = self.connection.execute(
            "SELECT document_json FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"event is absent from projection: {event_id}")
        return _json_object(row["document_json"])

    def list_events(
        self,
        *,
        event_type: str | None = None,
        workspace_id: str | None = None,
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type)
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            parameters.append(workspace_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = "SELECT document_json FROM events" + where + " ORDER BY recorded_at, event_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be positive")
            sql += " LIMIT ?"
            parameters.append(limit)
        return [
            _json_object(row["document_json"]) for row in self.connection.execute(sql, parameters)
        ]

    def get_manifest(self, manifest_id: str) -> Mapping[str, Any]:
        row = self.connection.execute(
            "SELECT document_json FROM manifests WHERE manifest_id = ?", (manifest_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"manifest is absent from projection: {manifest_id}")
        return _json_object(row["document_json"])

    def list_manifests(self, *, kind: str | None = None) -> list[Mapping[str, Any]]:
        if kind is None:
            rows = self.connection.execute(
                "SELECT document_json FROM manifests ORDER BY kind, manifest_id"
            )
        else:
            rows = self.connection.execute(
                "SELECT document_json FROM manifests WHERE kind = ? ORDER BY manifest_id",
                (kind,),
            )
        return [_json_object(row["document_json"]) for row in rows]

    def references_to(self, kind: str, identifier: str) -> list[tuple[str, str]]:
        return [
            (str(row["owner_kind"]), str(row["owner_id"]))
            for row in self.connection.execute(
                """
                SELECT owner_kind, owner_id FROM explicit_refs
                WHERE ref_kind = ? AND ref_id = ?
                ORDER BY owner_kind, owner_id
                """,
                (kind, identifier),
            )
        ]


def _json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_object(value: str) -> Mapping[str, Any]:
    parsed = loads_json_strict(value)
    if not isinstance(parsed, dict):  # tables only store validated documents
        raise IntegrityError("SQLite projection document is not an object")
    return parsed
