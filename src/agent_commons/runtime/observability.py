"""Disposable run-observability store (stream B).

This store is an operational projection, never a source of truth.  It holds the
high-frequency stream a canvas needs -- node status, tool calls, token usage --
while canonical milestones stay in the immutable ledger and are referenced here
only by ``event_id``.  Deleting the database loses observability, never truth.

The canonical metadata-only telemetry in :mod:`agent_commons.runtime.telemetry`
is a separate stream and is not touched by this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.platform_support import unlock
from agent_commons.runtime.policy import RunRetentionLimits
from agent_commons.runtime.run_state import (
    DIGEST_DROP_KINDS,
    RUN_LEVEL_NODE_ID,
    RunEventKind,
    RunState,
    fold_events,
    initial_state,
    state_from_dict,
)
from agent_commons.security.policy import SecurityPolicy


class StoreNotFound(IntegrityError):
    """A read-only caller asked for a store that has never been created."""


_SCHEMA_VERSION = 1
_EXPORT_SCHEMA = "agent_commons.run_export.v1"
_SNAPSHOT_INTERVAL = 1000
_LOW_WATER_RATIO = 0.9
_VACUUM_PAGES = 20_000

#: Runs in these states are candidates for retention.  Every other state --
#: including ``needs_operator`` -- is structurally excluded from every sweep.
_PRUNABLE_STATES = ("completed", "failed")

_RUN_STATES = frozenset({"created", "running", "stopping", "completed", "failed", "needs_operator"})


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RunEventEnvelope:
    """One observability event as supplied by a producer.

    ``body`` carries kind-specific metadata only.  Prompts, transcripts, and raw
    tool arguments never belong here; producers pass ``args_sha256`` instead.
    """

    run_id: str
    node_id: str
    kind: RunEventKind
    body: Mapping[str, Any] | None = None
    ts: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    org_edge_id: str | None = None
    delegation_id: str | None = None
    attempt_id: str | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"body": dict(self.body or {})}
        for name in (
            "trace_id",
            "span_id",
            "parent_span_id",
            "org_edge_id",
            "delegation_id",
            "attempt_id",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


@dataclass(frozen=True, slots=True)
class StoredRunEvent:
    run_id: str
    seq: int
    ts: str
    node_id: str
    kind: str
    payload: Mapping[str, Any]

    @property
    def body(self) -> Mapping[str, Any]:
        body = self.payload.get("body")
        return body if isinstance(body, Mapping) else {}

    @property
    def span_id(self) -> str | None:
        value = self.payload.get("span_id")
        return value if isinstance(value, str) else None

    @property
    def parent_span_id(self) -> str | None:
        value = self.payload.get("parent_span_id")
        return value if isinstance(value, str) else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "node_id": self.node_id,
            "kind": self.kind,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class RunRow:
    run_id: str
    workspace_id: str
    org_ref: str
    org_revision: str
    root_target: str
    canonical_event_id: str | None
    state: str
    retention_tier: str
    created_at: str
    finished_at: str | None
    head_seq: int
    event_count: int
    approx_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "org_ref": self.org_ref,
            "org_revision": self.org_revision,
            "root_target": self.root_target,
            "canonical_event_id": self.canonical_event_id,
            "state": self.state,
            "retention_tier": self.retention_tier,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "head_seq": self.head_seq,
            "event_count": self.event_count,
            "approx_bytes": self.approx_bytes,
        }


@dataclass(frozen=True, slots=True)
class SpanRow:
    run_id: str
    span_id: str
    parent_span_id: str | None
    node_id: str
    kind: str
    started_seq: int
    ended_seq: int | None
    attrs: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "node_id": self.node_id,
            "kind": self.kind,
            "started_seq": self.started_seq,
            "ended_seq": self.ended_seq,
            "attrs": dict(self.attrs),
        }


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    run_id: str
    upto_seq: int
    created_at: str
    state: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LayoutEntry:
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    meta: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "meta": dict(self.meta) if self.meta else None,
        }


@dataclass(frozen=True, slots=True)
class RetentionSweepResult:
    reason: str
    digested: tuple[str, ...] = ()
    purged: tuple[str, ...] = ()
    bytes_before: int = 0
    bytes_after: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.digested or self.purged)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "digested": list(self.digested),
            "purged": list(self.purged),
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
        }


@dataclass(frozen=True, slots=True)
class ExportResult:
    run_id: str
    path: str
    events: int
    spans: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": self.path,
            "events": self.events,
            "spans": self.spans,
            "sha256": self.sha256,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    workspace_id       TEXT NOT NULL,
    org_ref            TEXT NOT NULL,
    org_revision       TEXT NOT NULL,
    root_target        TEXT NOT NULL,
    canonical_event_id TEXT,
    state TEXT NOT NULL CHECK(state IN
        ('created','running','stopping','completed','failed','needs_operator')),
    retention_tier TEXT NOT NULL DEFAULT 'full'
        CHECK(retention_tier IN ('full','digest')),
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    head_seq     INTEGER NOT NULL DEFAULT 0,
    event_count  INTEGER NOT NULL DEFAULT 0,
    approx_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_state_finished ON runs(state, finished_at);

CREATE TABLE IF NOT EXISTS run_events (
    run_id  TEXT    NOT NULL,
    seq     INTEGER NOT NULL,
    ts      TEXT    NOT NULL,
    node_id TEXT    NOT NULL,
    kind    TEXT    NOT NULL,
    payload TEXT    NOT NULL,
    PRIMARY KEY (run_id, seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS run_events_node ON run_events(run_id, node_id, seq);
CREATE INDEX IF NOT EXISTS run_events_kind ON run_events(run_id, kind, seq);

CREATE TABLE IF NOT EXISTS spans (
    run_id         TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    parent_span_id TEXT,
    node_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    started_seq    INTEGER NOT NULL,
    ended_seq      INTEGER,
    attrs          TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, span_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS spans_node ON spans(run_id, node_id, started_seq);
CREATE INDEX IF NOT EXISTS spans_open ON spans(run_id, started_seq)
    WHERE ended_seq IS NULL;

CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id     TEXT    NOT NULL,
    upto_seq   INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    state_json TEXT    NOT NULL,
    PRIMARY KEY (run_id, upto_seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS canvas_layout (
    org_ref TEXT NOT NULL,
    node_id TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    meta TEXT,
    PRIMARY KEY (org_ref, node_id)
) WITHOUT ROWID;
"""


class RunEventStore:
    """Disposable run-observability projection.  Never a source of truth.

    A single process holds the writer role through an exclusive lock file, and a
    single in-process lock serialises sequence assignment so ``seq`` stays dense
    and monotonic per run.
    """

    def __init__(
        self,
        paths: CommonsPaths,
        *,
        database_path: str | Path | None = None,
        retention: RunRetentionLimits | None = None,
        writer: bool = True,
        clock: Any = time.time,
        snapshot_interval: int = _SNAPSHOT_INTERVAL,
        security_policy: SecurityPolicy | None = None,
    ) -> None:
        self.paths = paths
        self.retention = retention or RunRetentionLimits()
        self.database_path = Path(database_path or paths.orchestrator_db)
        self.snapshot_interval = max(1, int(snapshot_interval))
        self._clock = clock
        self._writer = writer
        self._security = security_policy or SecurityPolicy()
        self._guard = threading.RLock()
        self._lock_descriptor = -1
        self._closed = False
        self._readers = threading.local()
        self._reader_pool: list[sqlite3.Connection] = []
        try:
            if not writer and not self.database_path.exists():
                # A read-only caller must not bring the store into existence.
                # `run list` on a workspace that never ran anything answered
                # "nothing" and left a database behind to prove it.
                raise StoreNotFound("no run observability store exists for this workspace yet")
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            if writer:
                self._acquire_writer_lock()
            fresh = not self.database_path.exists()
            # The write connection is only ever touched under ``_guard``.  Readers
            # get their own connections: sharing one across threads corrupts
            # cursor state and exposes rows from a transaction that later rolls
            # back.
            self.connection = sqlite3.connect(
                self.database_path, timeout=30, check_same_thread=False
            )
            self.connection.row_factory = sqlite3.Row
            self._configure(fresh=fresh)
            self._initialize()
            if writer:
                self.sweep(reason="startup")
        except Exception as exc:
            self._release_writer_lock()
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()
            if isinstance(exc, (IntegrityError, ValidationError)):
                raise
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise IntegrityError(
                    f"run observability store could not be opened: {exc}; the store is "
                    "disposable, remove orchestrator.sqlite3 to rebuild"
                ) from exc
            raise

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> RunEventStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
            for reader in self._reader_pool:
                try:
                    reader.close()
                except sqlite3.Error:  # pragma: no cover - best effort teardown
                    pass
            self._reader_pool.clear()
            try:
                self.connection.close()
            finally:
                self._release_writer_lock()

    def _assert_open(self) -> None:
        if self._closed:
            raise IntegrityError("run observability store is closed")

    @property
    def _read(self) -> sqlite3.Connection:
        """A per-thread read connection.

        Reads never share the write connection: a concurrent writer would
        otherwise reset cursor state under the reader and expose uncommitted
        rows that a rollback then removes.
        """

        self._assert_open()
        connection = getattr(self._readers, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA query_only = ON")
            self._readers.connection = connection
            with self._guard:
                self._reader_pool.append(connection)
        return connection

    def _lock_path(self) -> Path:
        return self.database_path.with_name(self.database_path.name + ".lock")

    def _acquire_writer_lock(self) -> None:
        path = self._lock_path()
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError) as exc:
            os.close(descriptor)
            raise IntegrityError(
                "another process already holds the run observability writer lock"
            ) from exc
        self._lock_descriptor = descriptor

    def _release_writer_lock(self) -> None:
        descriptor = getattr(self, "_lock_descriptor", -1)
        if descriptor < 0:
            return
        try:
            unlock(descriptor)
        except Exception:  # pragma: no cover - best effort on teardown
            pass
        finally:
            os.close(descriptor)
            self._lock_descriptor = -1

    def _configure(self, *, fresh: bool) -> None:
        connection = self.connection
        connection.execute("PRAGMA busy_timeout = 30000")
        if fresh:
            # auto_vacuum only takes effect before the first table is created.
            connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise IntegrityError(f"run observability store requires WAL mode, got {journal_mode!r}")
        connection.execute("PRAGMA synchronous = NORMAL")

    def _initialize(self) -> None:
        connection = self.connection
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, _SCHEMA_VERSION}:
            raise IntegrityError(
                f"unsupported run observability store version {version}; the store is "
                "disposable, remove orchestrator.sqlite3 to rebuild"
            )
        connection.executescript(_SCHEMA)
        if version == 0 and self._writer:
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        expected = self.paths.workspace_id
        row = connection.execute(
            "SELECT value FROM store_meta WHERE key = 'workspace_id'"
        ).fetchone()
        if row is None:
            if expected is not None and self._writer:
                connection.execute(
                    "INSERT INTO store_meta(key, value) VALUES ('workspace_id', ?)",
                    (expected,),
                )
                connection.commit()
        elif expected is not None and row["value"] != expected:
            raise IntegrityError(
                "run observability store belongs to a different workspace; the store is "
                "disposable, remove orchestrator.sqlite3 to rebuild"
            )
        connection.commit()

    def _require_writer(self) -> None:
        if not self._writer:
            raise IntegrityError("run observability store is open read-only")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._guard:
            self._require_writer()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    # -- runs --------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        org_ref: str,
        org_revision: str,
        root_target: str,
        canonical_event_id: str | None = None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO runs(
                    run_id, workspace_id, org_ref, org_revision, root_target,
                    canonical_event_id, state, retention_tier, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'created', 'full', ?)
                """,
                (
                    run_id,
                    workspace_id,
                    org_ref,
                    org_revision,
                    root_target,
                    canonical_event_id,
                    _iso(self._clock()),
                ),
            )

    def set_run_state(self, run_id: str, state: str, *, reason: str | None = None) -> None:
        if state not in _RUN_STATES:
            raise ValidationError(f"unsupported run state {state!r}")
        terminal = state in _PRUNABLE_STATES
        finished_at = _iso(self._clock()) if terminal else None
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValidationError(f"unknown run {run_id!r}")
            connection.execute(
                "UPDATE runs SET state = ?, finished_at = COALESCE(?, finished_at) "
                "WHERE run_id = ?",
                (state, finished_at, run_id),
            )
            body: dict[str, Any] = {"state": state}
            if reason is not None:
                body["reason"] = reason
            self._append_locked(
                connection,
                [
                    RunEventEnvelope(
                        run_id=run_id,
                        node_id=RUN_LEVEL_NODE_ID,
                        kind=RunEventKind.RUN_STATE,
                        body=body,
                    )
                ],
            )
        if terminal:
            self.sweep(reason="run_finished")

    def get_run(self, run_id: str) -> RunRow | None:
        row = self._read.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row is not None else None

    def list_runs(self, *, states: Sequence[str] | None = None, limit: int = 100) -> list[RunRow]:
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = self._read.execute(
                f"SELECT * FROM runs WHERE state IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT ?",
                (*states, int(limit)),
            ).fetchall()
        else:
            rows = self._read.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._run_row(row) for row in rows]

    @staticmethod
    def _run_row(row: sqlite3.Row) -> RunRow:
        return RunRow(
            run_id=row["run_id"],
            workspace_id=row["workspace_id"],
            org_ref=row["org_ref"],
            org_revision=row["org_revision"],
            root_target=row["root_target"],
            canonical_event_id=row["canonical_event_id"],
            state=row["state"],
            retention_tier=row["retention_tier"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            head_seq=int(row["head_seq"]),
            event_count=int(row["event_count"]),
            approx_bytes=int(row["approx_bytes"]),
        )

    # -- events ------------------------------------------------------------

    def append(self, envelope: RunEventEnvelope) -> int:
        """Append one event and return its assigned sequence number."""

        return self.append_many([envelope])[0]

    def append_many(self, envelopes: Sequence[RunEventEnvelope]) -> list[int]:
        if not envelopes:
            return []
        with self._transaction() as connection:
            return self._append_locked(connection, envelopes)

    def _append_locked(
        self, connection: sqlite3.Connection, envelopes: Sequence[RunEventEnvelope]
    ) -> list[int]:
        assigned: list[int] = []
        by_run: dict[str, list[RunEventEnvelope]] = {}
        for envelope in envelopes:
            by_run.setdefault(envelope.run_id, []).append(envelope)
        for run_id, batch in by_run.items():
            row = connection.execute(
                "SELECT head_seq, event_count, approx_bytes FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValidationError(f"unknown run {run_id!r}")
            seq = int(row["head_seq"])
            added_bytes = 0
            rows: list[tuple[Any, ...]] = []
            for envelope in batch:
                seq += 1
                payload = envelope.payload()
                rendered = canonical_json_bytes(payload).decode("utf-8")
                # Second line of defence: producers must already exclude content.
                self._security.assert_safe(rendered)
                ts = envelope.ts or _iso(self._clock())
                rows.append((run_id, seq, ts, envelope.node_id, str(envelope.kind), rendered))
                added_bytes += len(rendered)
                assigned.append(seq)
            connection.executemany(
                "INSERT INTO run_events(run_id, seq, ts, node_id, kind, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._project_spans(connection, run_id, batch, rows)
            connection.execute(
                "UPDATE runs SET head_seq = ?, event_count = event_count + ?, "
                "approx_bytes = approx_bytes + ? WHERE run_id = ?",
                (seq, len(rows), added_bytes, run_id),
            )
            self._maybe_snapshot(connection, run_id, seq)
        return assigned

    def _project_spans(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        batch: Sequence[RunEventEnvelope],
        rows: Sequence[tuple[Any, ...]],
    ) -> None:
        for envelope, row in zip(batch, rows, strict=True):
            seq = int(row[1])
            if envelope.kind is RunEventKind.SPAN_START and envelope.span_id:
                body = dict(envelope.body or {})
                connection.execute(
                    "INSERT OR REPLACE INTO spans(run_id, span_id, parent_span_id, node_id, "
                    "kind, started_seq, ended_seq, attrs) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        run_id,
                        envelope.span_id,
                        envelope.parent_span_id,
                        envelope.node_id,
                        str(body.get("kind", "")),
                        seq,
                        canonical_json_bytes(body.get("attrs") or {}).decode("utf-8"),
                    ),
                )
            elif envelope.kind is RunEventKind.SPAN_END and envelope.span_id:
                connection.execute(
                    "UPDATE spans SET ended_seq = ? WHERE run_id = ? AND span_id = ?",
                    (seq, run_id, envelope.span_id),
                )

    def _maybe_snapshot(self, connection: sqlite3.Connection, run_id: str, head_seq: int) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(upto_seq), 0) AS latest FROM run_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        latest = int(row["latest"])
        if head_seq - latest < self.snapshot_interval:
            return
        state = self._replay_locked(connection, run_id, upto_seq=head_seq)
        self._write_snapshot(connection, run_id, state)
        # Only the newest snapshot can serve a replay, and an active run is
        # excluded from every retention tier, so stale ones would accumulate
        # for the whole life of the run.
        connection.execute(
            "DELETE FROM run_snapshots WHERE run_id = ? AND upto_seq < ?",
            (run_id, state.upto_seq),
        )

    def _write_snapshot(self, connection: sqlite3.Connection, run_id: str, state: RunState) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO run_snapshots(run_id, upto_seq, created_at, state_json) "
            "VALUES (?, ?, ?, ?)",
            (
                run_id,
                state.upto_seq,
                _iso(self._clock()),
                canonical_json_bytes(state.as_dict()).decode("utf-8"),
            ),
        )

    def read_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        upto_seq: int | None = None,
        kind: str | None = None,
        node_id: str | None = None,
        limit: int = 1000,
    ) -> list[StoredRunEvent]:
        clauses = ["run_id = ?", "seq > ?"]
        params: list[Any] = [run_id, int(after_seq)]
        if upto_seq is not None:
            clauses.append("seq <= ?")
            params.append(int(upto_seq))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        params.append(int(limit))
        rows = self._read.execute(
            f"SELECT * FROM run_events WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ?",
            params,
        ).fetchall()
        return [
            StoredRunEvent(
                run_id=row["run_id"],
                seq=int(row["seq"]),
                ts=row["ts"],
                node_id=row["node_id"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def head_seq(self, run_id: str) -> int:
        row = self._read.execute("SELECT head_seq FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return int(row["head_seq"]) if row is not None else 0

    def can_replay_from(self, run_id: str, seq: int) -> bool:
        run = self.get_run(run_id)
        if run is None or run.retention_tier != "full":
            return False
        return 0 <= int(seq) <= run.head_seq

    # -- snapshots and replay ---------------------------------------------

    def latest_snapshot(self, run_id: str, *, upto_seq: int | None = None) -> SnapshotRow | None:
        if upto_seq is None:
            row = self._read.execute(
                "SELECT * FROM run_snapshots WHERE run_id = ? ORDER BY upto_seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        else:
            row = self._read.execute(
                "SELECT * FROM run_snapshots WHERE run_id = ? AND upto_seq <= ? "
                "ORDER BY upto_seq DESC LIMIT 1",
                (run_id, int(upto_seq)),
            ).fetchone()
        if row is None:
            return None
        return SnapshotRow(
            run_id=row["run_id"],
            upto_seq=int(row["upto_seq"]),
            created_at=row["created_at"],
            state=json.loads(row["state_json"]),
        )

    def replay_state(self, run_id: str) -> RunState:
        # Reads go through the per-thread connection so a replay never blocks
        # the writer and never observes an uncommitted batch.
        return self._replay_locked(self._read, run_id)

    def _replay_locked(
        self, connection: sqlite3.Connection, run_id: str, *, upto_seq: int | None = None
    ) -> RunState:
        if upto_seq is None:
            row = connection.execute(
                "SELECT * FROM run_snapshots WHERE run_id = ? ORDER BY upto_seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM run_snapshots WHERE run_id = ? AND upto_seq <= ? "
                "ORDER BY upto_seq DESC LIMIT 1",
                (run_id, int(upto_seq)),
            ).fetchone()
        if row is None:
            state = initial_state(run_id)
            after = 0
        else:
            state = state_from_dict(json.loads(row["state_json"]))
            after = int(row["upto_seq"])
        params: list[Any] = [run_id, after]
        clause = "run_id = ? AND seq > ?"
        if upto_seq is not None:
            clause += " AND seq <= ?"
            params.append(int(upto_seq))
        rows = connection.execute(
            f"SELECT * FROM run_events WHERE {clause} ORDER BY seq", params
        ).fetchall()
        events = (
            StoredRunEvent(
                run_id=entry["run_id"],
                seq=int(entry["seq"]),
                ts=entry["ts"],
                node_id=entry["node_id"],
                kind=entry["kind"],
                payload=json.loads(entry["payload"]),
            )
            for entry in rows
        )
        return fold_events(state, events)

    # -- spans and layout --------------------------------------------------

    def read_spans(
        self, run_id: str, *, node_id: str | None = None, open_only: bool = False
    ) -> list[SpanRow]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        if open_only:
            clauses.append("ended_seq IS NULL")
        rows = self._read.execute(
            f"SELECT * FROM spans WHERE {' AND '.join(clauses)} ORDER BY started_seq", params
        ).fetchall()
        return [
            SpanRow(
                run_id=row["run_id"],
                span_id=row["span_id"],
                parent_span_id=row["parent_span_id"],
                node_id=row["node_id"],
                kind=row["kind"],
                started_seq=int(row["started_seq"]),
                ended_seq=None if row["ended_seq"] is None else int(row["ended_seq"]),
                attrs=json.loads(row["attrs"]),
            )
            for row in rows
        ]

    def get_layout(self, org_ref: str) -> dict[str, LayoutEntry]:
        rows = self._read.execute(
            "SELECT * FROM canvas_layout WHERE org_ref = ?", (org_ref,)
        ).fetchall()
        return {
            row["node_id"]: LayoutEntry(
                x=row["x"],
                y=row["y"],
                w=row["w"],
                h=row["h"],
                meta=json.loads(row["meta"]) if row["meta"] else None,
            )
            for row in rows
        }

    def put_layout(self, org_ref: str, entries: Mapping[str, LayoutEntry]) -> None:
        with self._transaction() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO canvas_layout(org_ref, node_id, x, y, w, h, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        org_ref,
                        node_id,
                        entry.x,
                        entry.y,
                        entry.w,
                        entry.h,
                        canonical_json_bytes(entry.meta).decode("utf-8") if entry.meta else None,
                    )
                    for node_id, entry in entries.items()
                ],
            )

    # -- retention ---------------------------------------------------------

    def store_bytes(self) -> int:
        """Physical size of the database file, for diagnostics only.

        Retention is *not* driven by this number: SQLite does not shrink
        ``page_count`` promptly after a delete, so a loop that waited for it to
        fall would keep finding victims and eventually delete every run.
        """

        page_count = int(self._read.execute("PRAGMA page_count").fetchone()[0])
        freelist = int(self._read.execute("PRAGMA freelist_count").fetchone()[0])
        page_size = int(self._read.execute("PRAGMA page_size").fetchone()[0])
        return max(0, page_count - freelist) * page_size

    def retained_bytes(self) -> int:
        """Logical payload bytes currently retained across all runs.

        This responds immediately and deterministically to a digest or purge,
        which is exactly what a retention loop needs to make progress.
        """

        row = self._read.execute(
            "SELECT COALESCE(SUM(approx_bytes), 0) AS events FROM runs"
        ).fetchone()
        snapshots = self._read.execute(
            "SELECT COALESCE(SUM(LENGTH(state_json)), 0) AS total FROM run_snapshots"
        ).fetchone()
        return int(row["events"]) + int(snapshots["total"])

    def sweep(self, *, reason: str) -> RetentionSweepResult:
        """Apply the three retention tiers.

        Active and ``needs_operator`` runs are excluded structurally: every
        candidate query filters on terminal states only, so no branch can reach
        a run that still matters for forensics.
        """

        with self._guard:
            self._require_writer()
            before = self.retained_bytes()
            digested: list[str] = []
            purged: list[str] = []

            cutoff = _iso(self._clock() - self.retention.digest_age_days * 86_400)
            placeholders = ",".join("?" for _ in _PRUNABLE_STATES)
            aged = self.connection.execute(
                f"SELECT run_id FROM runs WHERE state IN ({placeholders}) "
                "AND finished_at IS NOT NULL AND finished_at < ?",
                (*_PRUNABLE_STATES, cutoff),
            ).fetchall()
            for row in aged:
                self._purge_run(row["run_id"])
                purged.append(row["run_id"])

            surplus = self.connection.execute(
                f"SELECT run_id FROM runs WHERE state IN ({placeholders}) "
                "AND retention_tier = 'full' ORDER BY finished_at DESC LIMIT -1 OFFSET ?",
                (*_PRUNABLE_STATES, int(self.retention.full_run_limit)),
            ).fetchall()
            for row in surplus:
                self._digest_run(row["run_id"])
                digested.append(row["run_id"])

            low_water = int(self.retention.max_total_bytes * _LOW_WATER_RATIO)
            # The newest finished run is never a size-cap victim: a store that
            # answers "what just happened" with nothing is worse than one that
            # sits slightly over its ceiling.
            newest = self._read.execute(
                f"SELECT run_id FROM runs WHERE state IN ({placeholders}) "
                "ORDER BY finished_at DESC LIMIT 1",
                _PRUNABLE_STATES,
            ).fetchone()
            protected = {newest["run_id"]} if newest is not None else set()
            attempted: set[str] = set()
            while self.retained_bytes() > self.retention.max_total_bytes:
                victim = self._next_size_victim(
                    placeholders, tier="full", skip=protected | attempted
                )
                if victim is not None:
                    self._digest_run(victim)
                    digested.append(victim)
                else:
                    victim = self._next_size_victim(
                        placeholders, tier="digest", skip=protected | attempted
                    )
                    if victim is None:
                        break
                    self._purge_run(victim)
                    purged.append(victim)
                # A victim that did not shrink the store is skipped rather than
                # ending the sweep: stopping at the first one left the ceiling
                # unenforced for every run behind it.
                attempted.add(victim)
                if self.retained_bytes() <= low_water:
                    break

            if digested or purged:
                self._reclaim()
            return RetentionSweepResult(
                reason=reason,
                digested=tuple(digested),
                purged=tuple(purged),
                bytes_before=before,
                bytes_after=self.retained_bytes(),
            )

    def _next_size_victim(self, placeholders: str, *, tier: str, skip: set[str]) -> str | None:
        rows = self._read.execute(
            f"SELECT run_id FROM runs WHERE state IN ({placeholders}) "
            "AND retention_tier = ? ORDER BY finished_at",
            (*_PRUNABLE_STATES, tier),
        ).fetchall()
        for row in rows:
            if row["run_id"] not in skip:
                return str(row["run_id"])
        return None

    def _reclaim(self) -> None:
        try:
            self.connection.execute(f"PRAGMA incremental_vacuum({_VACUUM_PAGES})")
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:  # pragma: no cover - reclamation is best effort
            pass

    def _digest_run(self, run_id: str) -> None:
        with self._transaction() as connection:
            head_seq = int(
                connection.execute(
                    "SELECT head_seq FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()["head_seq"]
            )
            latest = int(
                connection.execute(
                    "SELECT COALESCE(MAX(upto_seq), -1) AS latest FROM run_snapshots "
                    "WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["latest"]
            )
            if latest < head_seq:
                # Materialise the terminal snapshot while the stream is intact;
                # without this the digest would lose everything after the last
                # periodic snapshot.
                state = self._replay_locked(connection, run_id)
                self._write_snapshot(connection, run_id, state)
                latest = state.upto_seq
            connection.execute(
                "DELETE FROM run_snapshots WHERE run_id = ? AND upto_seq < ?",
                (run_id, latest),
            )
            drop = tuple(str(kind) for kind in sorted(DIGEST_DROP_KINDS))
            placeholders = ",".join("?" for _ in drop)
            connection.execute(
                f"DELETE FROM run_events WHERE run_id = ? AND kind IN ({placeholders})",
                (run_id, *drop),
            )
            connection.execute("DELETE FROM spans WHERE run_id = ?", (run_id,))
            remaining = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(payload)), 0) AS total FROM run_events "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()["total"]
            connection.execute(
                "UPDATE runs SET retention_tier = 'digest', approx_bytes = ? WHERE run_id = ?",
                (int(remaining), run_id),
            )

    def _purge_run(self, run_id: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM spans WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM run_snapshots WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    # -- export ------------------------------------------------------------

    def export_run(self, run_id: str, destination: str | Path) -> ExportResult:
        import hashlib

        run = self.get_run(run_id)
        if run is None:
            raise ValidationError(f"unknown run {run_id!r}")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        events = 0
        spans = 0
        temporary = target.with_name(target.name + ".partial")

        def emit(handle: Any, value: Mapping[str, Any]) -> None:
            line = canonical_json_bytes(value) + b"\n"
            digest.update(line)
            handle.write(line)

        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                emit(
                    handle,
                    {
                        "schema": _EXPORT_SCHEMA,
                        "exported_at": _iso(self._clock()),
                        "run": run.as_dict(),
                        "head_seq": run.head_seq,
                        "retention_tier": run.retention_tier,
                        "store_schema_version": _SCHEMA_VERSION,
                    },
                )
                cursor = 0
                while True:
                    batch = self.read_events(run_id, after_seq=cursor, limit=1000)
                    if not batch:
                        break
                    for event in batch:
                        emit(handle, {"record": "event", **event.as_dict()})
                        events += 1
                        cursor = event.seq
                for span in self.read_spans(run_id):
                    emit(handle, {"record": "span", **span.as_dict()})
                    spans += 1
                snapshot = self.latest_snapshot(run_id)
                if snapshot is not None:
                    emit(
                        handle,
                        {
                            "record": "snapshot",
                            "upto_seq": snapshot.upto_seq,
                            "created_at": snapshot.created_at,
                            "state": dict(snapshot.state),
                        },
                    )
                trailer = {
                    "record": "end",
                    "events": events,
                    "spans": spans,
                    "sha256": digest.hexdigest(),
                }
                line = canonical_json_bytes(trailer) + b"\n"
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, target)
        return ExportResult(
            run_id=run_id,
            path=str(target),
            events=events,
            spans=spans,
            sha256=digest.hexdigest(),
        )


def iter_export_records(path: str | Path) -> Iterable[Mapping[str, Any]]:
    """Read an exported run back, verifying the trailer digest."""

    import hashlib

    digest = hashlib.sha256()
    records: list[Mapping[str, Any]] = []
    trailer: Mapping[str, Any] | None = None
    with open(path, "rb") as handle:
        for raw in handle:
            value = json.loads(raw)
            if isinstance(value, Mapping) and value.get("record") == "end":
                trailer = value
                break
            digest.update(raw)
            records.append(value)
    if trailer is None:
        raise ValidationError("run export is truncated: trailer record is missing")
    if trailer.get("sha256") != digest.hexdigest():
        raise ValidationError("run export digest mismatch: the file is truncated or altered")
    return records
