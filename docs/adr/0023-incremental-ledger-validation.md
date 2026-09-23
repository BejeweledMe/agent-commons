# ADR 0023: Incremental ledger validation on the canonical write path

Date: 2026-09-23.
Status: **draft proposal; not authorized and not in force**.
Design input for work package WP-20 of the
[consolidation and wave 4 plan](../plans/2026-09-18-consolidation-and-wave-4-plan.md),
under owner decision D-16 (`perf/write-path-target`,
`decision.46WMB9XKCDJ87HD58PGQZHXWVG`): warm `task_create` ≤ 1 s on a
3 206-event ledger is an **engineering target, not a product SLO**.

## Context

One canonical append re-derives, under the exclusive write lock, facts the
process already holds. Measured on this checkout on 23 September 2026 with
`uv run --locked python benchmarks/benchmark_work_mutation.py --profile small
--repeats 2` (420 events, 8 manifests), warm `task_create` median 3.282 s:

| phase | median | share |
| --- | ---: | ---: |
| `canonical_reads_validation` | 2.018 s | 61.5 % |
| `reconcile` | 0.707 s | 21.5 % |
| `receipt_preparation` | 0.410 s | 12.5 % |
| `projection_replay` | 0.052 s | 1.6 % |
| `append` | 0.010 s | 0.3 % |

1 705 `read_path` calls for 428 canonical files — **3.98 full validating passes
per write**. The four are `_records_and_snapshot` (manager.py:363),
`_event_for_idempotency_identity` (manager.py:711), `EventStore._find_existing`
(events.py:261) and `reconcile(list(self.events.iter_events()))`
(manager.py:1155). `ReceiptRecovery._event_info` adds a fifth read of every
event's bytes that `read_path` does not count.

The 33.3 s / 13 529-read figure at 3 206 events is **read**, from
`docs/performance/2026-09-10-mutation-baseline.md`, not re-measured here.

A read-only micro-measurement over this repository's own 4 178 event files
(warm page cache, best of three; my validator omitted
`SecurityPolicy.assert_safe`, so the last row is a **lower bound**):

| work per event file | total | per event |
| --- | ---: | ---: |
| `lstat` only | 8.4 ms | 2 µs |
| read bytes + sha256 | 65.7 ms | 16 µs |
| read + strict JSON parse | 151 ms | 36 µs |
| read + parse + canonical re-serialize | 246 ms | 59 µs |
| full `EventStore.read_path` | 2 271 ms | 544 µs |
| `project_events` fixed point (whole set) | 1 444 ms | 346 µs |
| bulk load + schema-validate 4 178 v2 receipts | 429 ms | 103 µs |

**Byte-identity verification is 34× cheaper than revalidation** (and ~70×
against the benchmark's 1.18 ms/read, which includes `assert_safe`).

A 22 September Terra run correctly refused the plan's original sketch. A
"verified prefix hash, then validate only the suffix" cache is unsafe:
`project_events` is a multi-pass fixed point over the whole history
(projection.py:1187), so no suffix fold exists, and `ReceiptRecovery` derives
anchor, receipt, abandonment and reconciliation completeness from the entire
event set. **The prefix cannot be skipped. It can be verified differently.**

## Invariants that must hold

1. **Append-only.** No canonical file is rewritten, moved or deleted. No
   canonical schema, event type or payload schema changes. The cache is
   operational state, disposable and rebuildable, never a second ledger
   (ADR 0001, ADR 0008).
2. **`doctor` detects every corruption it detects today.** `doctor()`
   (manager.py:1338) keeps calling `_records_and_snapshot()` and
   `index.sync(verify_unchanged=True)`, which fully re-read and revalidate every
   canonical file. The fast path is a *separate, named* method; `doctor` must not
   consume it.
3. **Golden replay identical.** For the same event set, `project_events` returns
   the same `ProjectSnapshot`: same issues, severities, effective revisions and
   `replay_metrics`. No caching of the snapshot across an append.
4. **Canonical write-lock semantics unchanged.** Reentrancy depth, cross-process
   `flock`, cascade atomicity (manager.py:664) stay as they are. Freshness is
   re-verified *after* the lock is acquired, every time; a view is never trusted
   across a lock release.
5. **Verification coverage is unchanged, not relaxed.** Any file whose bytes are
   not proven identical to bytes this process already validated is read and
   validated in full.
6. **No authority.** Nothing here grants a caller a lifecycle transition,
   review, acceptance or receipt it does not already earn.

The safety argument is one sentence: **a byte-identical file cannot fail a check
it already passed**, because every `read_path` check — symlink ban, path layout,
canonical-JSON equality, filename/`event_id` agreement, schema, envelope parse,
`assert_safe`, `recorded_at`/path agreement — is a pure function of the file's
bytes and its relative path.

## Options

### A. Lock-scoped verified ledger view, in process only

A `VerifiedLedgerView` built and owned by `CommonsManager`: for every canonical
event and manifest, the relative path, `st_size`, `st_mtime_ns`, the sha256 of
the *file* bytes, the validated document, plus a `(namespace, key) → event_id`
index. Refresh, always inside the lock: glob the event and manifest trees;
for each path `lstat` (regular file, not a symlink) and sha256 its bytes; any
new, missing, or non-matching path is read through `read_path` in full; any
structural surprise discards the whole view and rebuilds it from scratch.

**Modules:** `services/manager.py` — new `_verified_view()`, used by
`_records_and_snapshot`'s write-path callers, `_guard_integrity`,
`_event_for_idempotency_identity`; pass `[*view.records, record]` to
`receipt_recovery.reconcile` instead of a fresh `iter_events()`.
`storage/events.py` — `append()` accepts an optional pre-verified identity index
so `_find_existing` stops scanning. `storage/receipt_recovery.py` —
`_event_info` accepts the file digest the view already holds instead of
re-reading bytes. `domain/**` untouched. `cli/**` untouched.

**Cache location:** none on disk. Process memory, one manager instance.

**Invalidation:** any path-set change, any `lstat`/digest mismatch, lock
re-acquisition, `fork`/pid change, workspace-id mismatch, or an exception during
refresh → full rebuild. The view is never serialized, so it cannot outlive the
process that validated it.

**Threats:** *poisoning* — impossible; every cached digest was produced by a
full validating read in this process, and nothing on disk is trusted. *Tampering
between writes* — the digest comparison names the exact file, same class of
detection as the ledger anchor (THREAT_MODEL, "Concurrent corruption and
duplicate writes"). *A writer ignoring the lock* — detected at the next lock
acquisition, as today. *mtime forgery* — irrelevant: mtime is a hint, the sha256
decides.

**Expected win (rough, from the measured rates):** four validating passes →
one hashing pass. At 3 206 events, `canonical_reads_validation` ≈ 21.5 s →
about 0.06 s of hashing plus the new event's own validation. The remaining
dominant phase becomes `projection_replay` (≈ 0.5 s read / ≈ 1.1 s at my
measured 346 µs per event), which stays because the fixed point cannot be
folded incrementally.

### B. Receipt bulk load only

One pass over `receipts/`, `abandonments/` and `reconciliations/` into three
digest-keyed dicts per lock hold, reused by `status()`,
`_preflight_event_identities` and `_derive_receipts_and_tombstones` instead of
`get_by_digest`/`get_abandonment`/`get_reconciliation` per event (three random
reads each, from several call sites); compute `status()` once per write and
update the ledger anchor for the single new event.

**Modules:** `storage/receipt_recovery.py`, `storage/idempotency.py` (add bulk
iterators; keep every existing per-digest accessor for `doctor` and the CLI).
**Cache location:** none on disk; per-lock-hold dicts.
**Invalidation:** per lock hold, discarded on release; a file count or path-set
change forces a re-scan.
**Threats:** none new — the same documents are compared with the same
predicates, once instead of many times. Risk is a *missed* comparison, caught by
the parity test below.
**Expected win:** `receipt_preparation` + `reconcile` ≈ 10.9 s at 3 206 events
→ about 0.33 s (measured 103 µs/receipt), and near zero once the dicts live in
the option-A view across writes in one process.

### C. Durable verified-prefix document under the state root

A/B plus `<state_root>/write-path-cache/verified-prefix.json`: schema
`commons.write_path_cache.v1`, ordered `(path, size, mtime_ns, sha256)` rows,
`event_count`, `prefix_sha256`, `code_version` (writer version + projection
schema version), `workspace_id`, `scope_id`; 0600, atomic replace, alongside
`index.sqlite3` and `idempotency-v2/` (ADR 0005).

**Invalidation:** any of `workspace_id`, `scope_id`, `code_version`,
`event_count`, `prefix_sha256`, or a single row mismatch → discard and
re-validate everything.
**Threats:** this is the only option with a real poisoning surface. A local
process that can write the state root can pre-record a digest for bytes that
were never validated; the digest would then match. Detection: `code_version`
pinning and the row digests catch drift and truncation, **not** a coherent
forgery — which is exactly the residual already stated in THREAT_MODEL ("a
process able to rewrite both the canonical workspace and Git-common operational
anchors can defeat local tamper detection"). Mitigation, if it is ever built:
re-validate a rotating quota of files per write, and never trust the document
when `doctor` runs.
**Expected win over A/B:** only the *cold* first write of a process (≈ 2 s at
3 206 events) — nothing for the warm target D-16 names.

## Recommendation

**A + B, staged, two Terra runs. C is deferred and unauthorized.** A durable
cache buys cold writes only and is the sole option that introduces a poisoning
surface; do not pay that for a target measured warm.

**Run 1 — collapse the passes (option A).** Add `_verified_view()` and route
`_guard_integrity`, `_event_for_idempotency_identity`, `EventStore.append`'s
`_find_existing` and the post-append `reconcile` through it. Tests: read-path
count per warm `task_create` drops from ~4 N to ~N (assert on
`median_read_calls`); mutate one event's bytes between two writes → full
revalidation and the same `IntegrityError` as today; delete one event file →
same anchor issue; add a file out of band → validated on the next write;
`doctor` output byte-identical before and after on a fixture with a seeded
corruption of each kind; `project_events` output equality on the wave-3
fixtures.

**Run 2 — receipts (option B).** Bulk load, one `status()` per write,
incremental anchor. Tests: a parity test asserting bulk and per-digest paths
return the same `status()` dict on fixtures with missing, orphan, conflicting,
tombstoned and reconciled receipts; legacy-v1 migration and
`prepare_rollback` unchanged; `receipt reconcile` CLI output unchanged.

**Gate for both runs:** `make check` green; the small profile as a shape and
phase-sum control (not an SLO); Grok re-runs the large profile and reports warm
`task_create` median against 1 s, with the phase table, before acceptance.

## Non-goals

Faster reads or SQLite index changes. A product latency SLO. Any canonical
schema, event type or payload change. Persisting a `ProjectSnapshot` (replay is
1.6 % of the measured median; serializing it would cost more than it saves).
Incremental or bounded fixed-point replay — worth revisiting only when replay
alone approaches 1 s, around 6 000 events at my measured rate. Weakening
`doctor`, the anchor, or receipt recovery. Concurrency beyond the existing lock.
Option C.
