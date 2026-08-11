"""Rebuildable SQLite/WAL projection for generic events and manifests."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict
from agent_commons.core.refs import iter_typed_refs
from agent_commons.errors import IntegrityError
from agent_commons.index.search_text import searchable_text, subject_of
from agent_commons.storage import EventRecord, EventStore, ManifestRecord, ManifestStore

_SCHEMA_VERSION = 2
#: Older projections are dropped and rebuilt rather than migrated: this database
#: is disposable and the ledger is the truth, so a migration path would be code
#: that has to stay correct for no benefit.
_REBUILDABLE_FROM = frozenset({1})


@dataclass(frozen=True)
class IndexSyncResult:
    scanned: int
    indexed: int
    removed: int
    unchanged: int


@dataclass(frozen=True)
class ProjectionReadResult:
    events: tuple[Mapping[str, Any], ...]
    manifest_ids: tuple[str, ...]
    source_count: int
    verified_head_sha256: str


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
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.database_path, timeout=30)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout = 30000")
            self.connection.execute("PRAGMA foreign_keys = ON")
            journal_mode = str(
                self.connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            ).lower()
            if journal_mode != "wal":
                raise IntegrityError(f"SQLite projection requires WAL mode, got {journal_mode!r}")
            self._initialize()
        except Exception as exc:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
            if isinstance(exc, IntegrityError):
                raise
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise IntegrityError(f"SQLite projection could not be opened: {exc}") from exc
            raise

    def __enter__(self) -> SQLiteIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, _SCHEMA_VERSION} and version not in _REBUILDABLE_FROM:
            raise IntegrityError(
                f"unsupported SQLite projection version {version}; rebuild with compatible tooling"
            )
        if version in _REBUILDABLE_FROM:
            self._drop_everything()
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

            CREATE TABLE IF NOT EXISTS projection_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Full-text search over allowlisted canonical fields only; see
            -- index/search_text.py for what is and is not in the document.
            CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
                event_id UNINDEXED,
                subject_kind UNINDEXED,
                subject_id UNINDEXED,
                recorded_at UNINDEXED,
                body,
                tokenize = 'unicode61'
            );
            """
        )
        expected_workspace_id = self.paths.workspace_id
        if expected_workspace_id is not None:
            row = self.connection.execute(
                "SELECT value FROM projection_metadata WHERE key = 'workspace_id'"
            ).fetchone()
            if row is not None and str(row["value"]) != expected_workspace_id:
                raise IntegrityError("SQLite projection belongs to a different workspace")
            projected = {
                str(item["workspace_id"])
                for item in self.connection.execute("SELECT DISTINCT workspace_id FROM events")
            }
            if projected and projected != {expected_workspace_id}:
                raise IntegrityError("SQLite projection contains a different workspace")
            if row is None:
                self.connection.execute(
                    "INSERT INTO projection_metadata(key, value) VALUES ('workspace_id', ?)",
                    (expected_workspace_id,),
                )
        self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self.connection.commit()

    def _drop_everything(self) -> None:
        self.connection.executescript(
            """
            DROP TABLE IF EXISTS event_search;
            DROP TABLE IF EXISTS explicit_refs;
            DROP TABLE IF EXISTS relations;
            DROP TABLE IF EXISTS event_subjects;
            DROP TABLE IF EXISTS manifests;
            DROP TABLE IF EXISTS events;
            DROP TABLE IF EXISTS projection_metadata;
            DROP TABLE IF EXISTS source_files;
            """
        )
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
                self.connection.execute("DELETE FROM event_subjects")
                self.connection.execute("DELETE FROM relations")
                self.connection.execute("DELETE FROM events")
                self.connection.execute("DELETE FROM manifests")
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
            if existing["file_kind"] == "event":
                # An FTS5 virtual table is not reached by the foreign-key
                # cascade that removes the ordinary rows, so it is cleared here
                # or it keeps answering for events that no longer exist.
                self.connection.execute(
                    "DELETE FROM event_search WHERE event_id = ?", (existing["identity"],)
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
        subject_kind, subject_id = subject_of(event)
        self.connection.execute(
            "INSERT INTO event_search(event_id, subject_kind, subject_id, recorded_at, body)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                record.event_id,
                subject_kind,
                subject_id,
                event["recorded_at"],
                searchable_text(event),
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

    def search_events(
        self,
        query: str,
        *,
        limit: int = 25,
        subject_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Rank canonical events against a free-text query."""

        return search_rows(self.connection, query, limit=limit, subject_kind=subject_kind)

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

    def read_projection(self, *, workspace_id: str) -> ProjectionReadResult:
        """Verify and load a complete disposable projection for domain replay.

        This verifies database structure, one-to-one source coverage, document
        hashes, identities, and workspace ownership. It never reads canonical
        file contents; ``sync`` remains responsible for validating changed files.
        """

        quick_check = self.connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]).lower() != "ok":
            raise IntegrityError("SQLite projection failed quick_check")
        orphan = self.connection.execute(
            """
            SELECT 1 FROM events AS child
            LEFT JOIN source_files AS source ON source.path = child.source_path
            WHERE source.path IS NULL
            UNION ALL
            SELECT 1 FROM manifests AS child
            LEFT JOIN source_files AS source ON source.path = child.source_path
            WHERE source.path IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan is not None:
            raise IntegrityError("SQLite projection contains an orphaned document")

        event_rows = list(
            self.connection.execute(
                """
                SELECT source.identity, source.sha256, child.event_id,
                       child.workspace_id, child.document_json
                FROM source_files AS source
                LEFT JOIN events AS child ON child.source_path = source.path
                WHERE source.file_kind = 'event'
                ORDER BY child.recorded_at, child.event_id
                """
            )
        )
        manifest_rows = list(
            self.connection.execute(
                """
                SELECT source.identity, source.sha256, child.manifest_id,
                       child.document_json
                FROM source_files AS source
                LEFT JOIN manifests AS child ON child.source_path = source.path
                WHERE source.file_kind = 'manifest'
                ORDER BY child.kind, child.manifest_id
                """
            )
        )
        events: list[Mapping[str, Any]] = []
        manifest_ids: list[str] = []
        head_rows: list[dict[str, str]] = []
        for row in event_rows:
            if row["event_id"] is None or str(row["identity"]) != str(row["event_id"]):
                raise IntegrityError("SQLite projection event source coverage is incomplete")
            if str(row["workspace_id"]) != workspace_id:
                raise IntegrityError("SQLite projection contains a different workspace")
            document_text = str(row["document_json"])
            if hashlib.sha256(document_text.encode("utf-8")).hexdigest() != str(row["sha256"]):
                raise IntegrityError("SQLite projection event document hash mismatch")
            document = _json_object(document_text)
            if (
                document.get("event_id") != row["event_id"]
                or document.get("workspace_id") != workspace_id
            ):
                raise IntegrityError("SQLite projection event identity mismatch")
            events.append(document)
            head_rows.append(
                {"kind": "event", "id": str(row["identity"]), "sha256": str(row["sha256"])}
            )
        for row in manifest_rows:
            if row["manifest_id"] is None or str(row["identity"]) != str(row["manifest_id"]):
                raise IntegrityError("SQLite projection manifest source coverage is incomplete")
            document_text = str(row["document_json"])
            if hashlib.sha256(document_text.encode("utf-8")).hexdigest() != str(row["sha256"]):
                raise IntegrityError("SQLite projection manifest document hash mismatch")
            _json_object(document_text)
            manifest_ids.append(str(row["manifest_id"]))
            head_rows.append(
                {
                    "kind": "manifest",
                    "id": str(row["identity"]),
                    "sha256": str(row["sha256"]),
                }
            )
        source_count = int(
            self.connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0]
        )
        if source_count != len(events) + len(manifest_ids):
            raise IntegrityError("SQLite projection source coverage is inconsistent")
        verified_head = hashlib.sha256(canonical_json_bytes(head_rows)).hexdigest()
        return ProjectionReadResult(
            events=tuple(events),
            manifest_ids=tuple(manifest_ids),
            source_count=source_count,
            verified_head_sha256=verified_head,
        )

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


_QUERY_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _fts_phrase(text: str) -> str:
    return '"' + text.replace('"', " ") + '"'


def search_rows(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 25,
    subject_kind: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Rank canonical events against a free-text query.

    The projection is disposable and non-authoritative, so a result is a pointer
    back to the ledger, never an answer on its own.

    Operators type questions, not query grammar, so this widens in steps and
    reports which step answered: every term, then any term.  Silently switching
    to any-term would make a precise search look like it matched precisely when
    it did not.
    """

    text = query.strip()
    if not text:
        return [], "empty"
    bounded = max(1, min(int(limit), 100))
    statement = (
        "SELECT s.event_id, s.subject_kind, s.subject_id, s.recorded_at,"
        "       e.event_type, snippet(event_search, 4, '', '', ' … ', 12) AS excerpt"
        "  FROM event_search s JOIN events e ON e.event_id = s.event_id"
        " WHERE event_search MATCH ?"
        + ("   AND s.subject_kind = ?" if subject_kind else "")
        + " ORDER BY bm25(event_search), s.recorded_at DESC LIMIT ?"
    )

    def run(expression: str) -> list[Any]:
        arguments: list[Any] = [expression]
        if subject_kind:
            arguments.append(subject_kind)
        arguments.append(bounded)
        return connection.execute(statement, arguments).fetchall()

    tokens = _QUERY_TOKEN.findall(text)
    try:
        rows = run(text)
        mode = "all_terms"
    except sqlite3.OperationalError:
        # Not a query the engine can parse; treat what was typed as a phrase.
        rows = run(_fts_phrase(text)) if tokens else []
        mode = "phrase"
    if not rows and len(tokens) > 1:
        try:
            rows = run(" OR ".join(_fts_phrase(token) for token in tokens))
            mode = "any_term"
        except sqlite3.OperationalError:  # pragma: no cover - tokens are already safe
            rows = []
    return [
        {
            "event_id": str(row["event_id"]),
            "event_type": str(row["event_type"]),
            "subject": {"kind": str(row["subject_kind"]), "id": str(row["subject_id"])},
            "recorded_at": str(row["recorded_at"]),
            "excerpt": str(row["excerpt"]),
        }
        for row in rows
    ], mode


def search_existing_projection(
    database_path: str | Path,
    query: str,
    *,
    limit: int = 25,
    subject_kind: str | None = None,
) -> list[dict[str, Any]] | None:
    """Search a projection that already exists, creating and changing nothing.

    Opening ``SQLiteIndex`` makes directories, a database file, tables, and a
    WAL, which a read-only caller must not do -- this project has already
    shipped a read-only command that created state.  ``None`` means there is no
    projection to answer from, which the caller reports rather than papering
    over with an empty result.
    """

    path = Path(database_path)
    if not path.exists():
        return None
    connection: sqlite3.Connection | None = None
    try:
        # `immutable=1`, not merely `mode=ro`: a plain read-only open still
        # materialises the `-wal` and `-shm` sidecars, so "creating and changing
        # nothing" left two files behind (L1, 2026-08-10 review).  immutable
        # promises the file will not change under us and suppresses the sidecars;
        # a genuinely mid-write projection surfaces as an error, which this
        # already reports as "cannot answer".
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return search_rows(connection, query, limit=limit, subject_kind=subject_kind)
    except sqlite3.Error:
        # An absent WAL sidecar, a projection older than full-text search, or a
        # concurrent writer.  All of them mean "cannot answer", not "no results".
        return None
    finally:
        if connection is not None:
            connection.close()


def _json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_object(value: str) -> Mapping[str, Any]:
    parsed = loads_json_strict(value)
    if not isinstance(parsed, dict):  # tables only store validated documents
        raise IntegrityError("SQLite projection document is not an object")
    return parsed
