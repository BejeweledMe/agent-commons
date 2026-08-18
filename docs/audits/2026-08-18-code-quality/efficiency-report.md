# Efficiency and memory audit

**Audited revision:** `4e0ec8f` (`Add code quality audit materials`)
**Scope:** every file below `src/agent_commons/`, with detailed tracing of replay,
SQLite indexing, canonical storage, runtime attempt storage and subprocess output,
and UI context refresh paths. This is a read-only audit of the frozen revision;
no source code was changed.

## Where volume grows

The durable growth axis is the canonical event and manifest set. Event replay and
`SQLiteIndex.read_projection()` deliberately materialize that set because the
projected snapshot is a full in-memory view. The SQLite index makes unchanged-file
sync incremental, but a verified projection read still reconstructs every JSON
document. Runtime requests and their queue grow with launched attempts, while UI
views derive their data from a cached `CommonsManager` snapshot and invalidate
only when the ledger fingerprint changes.

## Findings

### E1 — major — replay retains and reprocesses the whole ledger, up to three times

- **Where:** `src/agent_commons/domain/projection.py:844-1069` and
  `src/agent_commons/domain/projection.py:1072-1157`.
- **What:** `project_events()` first materializes every event, and histories with
  an acceptance referenced as an expected revision run a probe plus the normal
  replay; a stale acceptance then invokes a third complete replay. Each replay
  sorts copied events and builds further lists and sets (`raw`, `relations`,
  `corrections`, and `effective`).
- **Cost:** memory is linear in the complete ledger with several simultaneous
  representations, while CPU becomes two or three `O(n log n)` replays rather
  than one. This is on the canonical read/write path, so it increases UI/CLI
  latency and peak RSS as history accumulates.
- **Noticeable at:** tens of thousands of events, or earlier when event payloads
  carry large bounded diagnostics/manifests. At 100,000 events a three-pass
  replay copies and sorts the full history three times; a 20 MiB source ledger
  can transiently require multiples of that size in Python objects.
- **Fix:** preserve the fixed-point semantics but split planning from applying:
  sort/materialize once, pre-index corrections and acceptance dependencies in a
  single scan, then apply the selected effective event stream once. If a second
  validation pass remains necessary, reuse the sorted normalized representation
  rather than rebuilding `raw`, relations, corrections, and revision groups.
  Add a benchmark with representative correction and stale-acceptance chains
  that asserts both pass count and peak allocation.
- **Size:** L.
- **What could break:** lifecycle ordering, correction conflict reporting,
  acceptance staleness and its successor exemption, replay metrics, and every
  manager/CLI/MCP/UI caller that depends on projection results. This is a
  behavior-preserving structural change and must remain separate from semantic
  changes.

### E2 — major — verified SQLite reads deserialize every indexed document before replay

- **Where:** `src/agent_commons/index/sqlite.py:506-603`.
- **What:** `read_projection()` loads all event and manifest rows into Python
  lists, verifies each JSON string/hash, deserializes each event, and returns a
  tuple that `project_events()` materializes again.
- **Cost:** a verified projection duplicates the ledger in SQLite result rows,
  decoded JSON objects, `events`/`manifest_ids` lists, returned tuples, and then
  replay’s list/copies. The duplicate data is intentional for integrity, but
  the extra container and decoding stages set the interactive memory floor to
  the entire ledger rather than the active working set.
- **Noticeable at:** around 50,000–100,000 events or several hundred MiB of
  canonical JSON; this is most visible in long-lived workspaces opened by the
  UI and CLI, not a one-off fresh workspace.
- **Fix:** retain full verification but expose a verified, ordered iterator or
  an immutable normalized event sequence that replay can consume without its
  second `list(events)` materialization. Benchmark the verified UI refresh path
  against a synthetic large ledger and set a peak-RSS budget. Do not skip hash
  or workspace checks as an optimization.
- **Size:** L.
- **What could break:** integrity fail-closed behavior, deterministic ordering,
  projection cache invalidation, and callers expecting tuples in
  `ProjectionReadResult`. Preserve the disk format and validate equivalence
  against current replay fixtures.

## Checked and healthy

- `SQLiteIndex.sync()` (`index/sqlite.py:235-300`) compares path, size, and
  nanosecond mtime, parses only changed canonical files on the interactive path,
  and writes all index changes in one transaction. It does not issue a query per
  event while reconciling unchanged data.
- Search is bounded: `search_rows()` clamps result limits to 1–100 and delegates
  ranking/filtering to FTS5/SQLite rather than loading the event corpus into
  Python (`index/sqlite.py:626-688`).
- Canonical stores stream iteration instead of preloading their own inventories:
  `EventStore.iter_events()` and `ManifestStore.iter_manifests()` yield one
  validated record at a time (`storage/events.py:220-224`,
  `storage/manifests.py:76-80`). Full materialization happens at the explicit
  projection boundary, where a complete snapshot is required.
- Provider stderr is bounded while it is read, not after unbounded accumulation:
  `PROVIDER_STDERR_TAIL_BYTES` is 4 KiB in
  `runtime/subprocess_runner.py:43`, and the runner’s bounded-tail path keeps
  diagnostics suitable for persistence without retaining provider output.
- UI reads are cached behind a ledger fingerprint and refresh only after that
  fingerprint changes (`ui/context.py:68-110`, `225-323`); repeated rendering
  does not recompute a projection for every individual panel accessor.
- The audit found no justified micro-optimization in one-shot CLI setup,
  canonical writes, or SQLite query construction. Their costs are dominated by
  required validation/durability work and should not be traded for weaker
  integrity guarantees.

## Suggested order

1. Add reproducible large-ledger replay/peak-memory benchmarks covering the
   two- and three-pass fixed-point paths.
2. Remove duplicate materialization between verified SQLite reads and replay
   while preserving every integrity check.
3. Consolidate fixed-point planning so the selected event stream is replayed
   once from the normalized, ordered data.
