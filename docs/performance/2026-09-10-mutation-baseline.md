# Mutation latency baseline

Date: 10 September 2026. Measurement only; no production optimization.
Synthetic ledgers in temporary directories. The operator workspace and
this checkout's state root were not opened.

## Hardware and runtime

- Python: `3.14.5` (CPython)
- Platform: `macOS-26.6.2-arm64-arm-64bit-Mach-O`
- Machine: `arm64`
- CPU: `Apple M2 Max`
- CPU count: `12`

Cold = first mutation on a fresh `CommonsManager` in an already-imported
process. Warm = the second mutation on that same manager. OS page cache
is therefore warm after the first sample; a brand-new process would pay
import cost on top of these numbers.

## Fixture sizes

| Profile | Events | Manifests | Tasks | Reviews | Decisions | Delegations | Artifacts | Build |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | 420 | 8 | 300 | 0 | 8 | 4 | 8 | 2.527 s |
| large | 3206 | 350 | 390 | 318 | 80 | 40 | 350 | 17.20 s |

Fixture construction is excluded from every mutation sample.

## Phase tables

### small fixture, cold samples

| Mutation | min | median | p95 | lock_wait | lock_hold | canonical_reads_validation | projection_replay | receipt_preparation | append | reconcile | residual | reads | bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `task_create` | 4.267 s | 4.281 s | 4.293 s | 0.2 ms | 26.9 ms | 2.639 s | 66.7 ms | 526.2 ms | 13.0 ms | 930.1 ms | 79.2 ms | 1701 | 1858473 |
| `task_take` | 4.325 s | 4.339 s | 4.352 s | 0.2 ms | 27.4 ms | 2.676 s | 67.1 ms | 535.6 ms | 13.2 ms | 938.4 ms | 81.8 ms | 1717 | 1875385 |
| `task_complete` | 5.126 s | 5.136 s | 5.145 s | 0.2 ms | 27.9 ms | 3.372 s | 137.9 ms | 544.3 ms | 13.1 ms | 953.2 ms | 86.9 ms | 2170 | 2365746 |
| `task_submit` | 5.160 s | 5.187 s | 5.212 s | 0.2 ms | 28.5 ms | 3.402 s | 137.0 ms | 548.7 ms | 13.2 ms | 966.8 ms | 90.6 ms | 2190 | 2386342 |
| `decision_propose` | 4.427 s | 4.451 s | 4.473 s | 0.2 ms | 28.6 ms | 2.743 s | 71.6 ms | 545.8 ms | 13.4 ms | 964.0 ms | 84.5 ms | 1765 | 1924766 |
| `panel_http_task_create` | 4.593 s | 4.666 s | 4.732 s | 0.2 ms | 30.4 ms | 2.812 s | 71.1 ms | 570.7 ms | 14.1 ms | 996.4 ms | 171.1 ms | 1781 | 1941903 |

### small fixture, warm samples

| Mutation | min | median | p95 | lock_wait | lock_hold | canonical_reads_validation | projection_replay | receipt_preparation | append | reconcile | residual | reads | bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `task_create` | 4.288 s | 4.322 s | 4.352 s | 0.2 ms | 27.5 ms | 2.671 s | 66.4 ms | 529.9 ms | 13.4 ms | 928.7 ms | 84.1 ms | 1705 | 1862781 |
| `task_take` | 4.335 s | 4.346 s | 4.355 s | 0.2 ms | 27.4 ms | 2.670 s | 69.1 ms | 541.5 ms | 12.8 ms | 942.6 ms | 82.7 ms | 1721 | 1879437 |
| `task_complete` | 5.137 s | 5.155 s | 5.171 s | 0.2 ms | 27.7 ms | 3.376 s | 136.0 ms | 540.1 ms | 13.1 ms | 973.0 ms | 89.0 ms | 2175 | 2370901 |
| `task_submit` | 5.229 s | 5.235 s | 5.241 s | 0.2 ms | 28.1 ms | 3.439 s | 139.7 ms | 551.1 ms | 13.9 ms | 977.3 ms | 85.7 ms | 2195 | 2391477 |
| `decision_propose` | 4.432 s | 4.518 s | 4.596 s | 0.2 ms | 29.3 ms | 2.782 s | 69.4 ms | 566.1 ms | 13.4 ms | 978.1 ms | 79.4 ms | 1769 | 1929014 |
| `panel_http_task_create` | 4.608 s | 4.663 s | 4.712 s | 0.2 ms | 30.0 ms | 2.814 s | 70.6 ms | 568.4 ms | 13.7 ms | 995.4 ms | 170.1 ms | 1785 | 1946267 |

### large fixture, cold samples

| Mutation | min | median | p95 | lock_wait | lock_hold | canonical_reads_validation | projection_replay | receipt_preparation | append | reconcile | residual | reads | bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `task_create` | 33.26 s | 33.31 s | 33.36 s | 0.3 ms | 206.7 ms | 21.53 s | 501.4 ms | 3.962 s | 62.9 ms | 6.964 s | 86.4 ms | 13529 | 14737249 |
| `task_take` | 33.20 s | 33.22 s | 33.24 s | 0.2 ms | 203.1 ms | 21.48 s | 506.9 ms | 3.923 s | 62.9 ms | 6.964 s | 84.3 ms | 13545 | 14754161 |
| `task_complete` | 39.33 s | 39.35 s | 39.37 s | 0.2 ms | 207.1 ms | 27.02 s | 1.010 s | 3.925 s | 63.8 ms | 6.966 s | 153.2 ms | 17126 | 18524066 |
| `task_submit` | 39.37 s | 39.41 s | 39.45 s | 0.3 ms | 205.2 ms | 27.04 s | 1.012 s | 3.929 s | 64.0 ms | 6.998 s | 157.9 ms | 17146 | 18544662 |
| `decision_propose` | 33.43 s | 33.46 s | 33.48 s | 0.3 ms | 205.2 ms | 21.59 s | 506.3 ms | 3.942 s | 64.1 ms | 7.056 s | 89.0 ms | 13593 | 14803542 |
| `panel_http_task_create` | 33.40 s | 33.43 s | 33.45 s | 0.2 ms | 204.4 ms | 21.55 s | 506.2 ms | 3.938 s | 63.4 ms | 6.991 s | 173.0 ms | 13609 | 14820679 |

### large fixture, warm samples

| Mutation | min | median | p95 | lock_wait | lock_hold | canonical_reads_validation | projection_replay | receipt_preparation | append | reconcile | residual | reads | bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `task_create` | 33.17 s | 33.25 s | 33.32 s | 0.2 ms | 202.4 ms | 21.44 s | 505.9 ms | 3.918 s | 62.4 ms | 7.038 s | 86.3 ms | 13533 | 14741557 |
| `task_take` | 33.17 s | 33.26 s | 33.35 s | 0.2 ms | 203.9 ms | 21.44 s | 500.9 ms | 3.917 s | 63.9 ms | 7.044 s | 87.8 ms | 13549 | 14758213 |
| `task_complete` | 39.38 s | 39.39 s | 39.41 s | 0.2 ms | 208.1 ms | 27.02 s | 1.012 s | 3.950 s | 62.8 ms | 6.984 s | 154.8 ms | 17131 | 18529221 |
| `task_submit` | 39.46 s | 39.50 s | 39.54 s | 0.3 ms | 206.6 ms | 27.08 s | 1.018 s | 3.964 s | 63.6 ms | 7.003 s | 162.0 ms | 17151 | 18549797 |
| `decision_propose` | 33.23 s | 33.32 s | 33.40 s | 0.2 ms | 205.0 ms | 21.56 s | 505.9 ms | 3.926 s | 64.4 ms | 6.968 s | 87.5 ms | 13597 | 14807790 |
| `panel_http_task_create` | 33.37 s | 33.46 s | 33.55 s | 0.2 ms | 205.9 ms | 21.53 s | 511.9 ms | 3.949 s | 63.0 ms | 7.032 s | 172.5 ms | 13613 | 14825043 |

## Where the time goes

On the large fixture, warm `task_create` median is 33.25 s. The largest exclusive phase is `canonical_reads_validation` at about 64% of that median. Canonical writes re-read every event file under the write lock (`iter_events` via `_records_and_snapshot`), replay the whole ledger, then `EventStore.append` looks up a missing idempotency receipt and scans the ledger again through `_find_existing`. Receipt preparation and the post-append reconcile sit on the same lock.

The panel HTTP path is the same `record_event` plus FastAPI TestClient overhead; it is not a browser paint measurement.

## Smallest optimization proposed

Do not implement this here. The next write-path change should be the
smallest one that the phases actually name:

1. **Stop the second full ledger scan inside `append`.** When `idempotency.lookup` misses, `_find_existing` walks every canonical event. That work is already paid by `_records_and_snapshot` a few lines earlier. Passing the in-memory records, or treating a lookup miss as authoritative after a just-reconciled ledger, removes one full `13533-call` read pass from every mutation.
2. **Do not re-validate immutable event bytes under the write lock.** `EventStore.read_path` re-runs schema and envelope validation on every read. Append-time validation already accepted those files. A content-hash / mtime cache scoped to one lock hold would drop the `canonical_reads_validation` share without changing replay.
3. **Keep receipt status checks off the exclusive lock when the anchor already matches.** `prepare_for_write` plus post-append `reconcile` re-derive completeness on every write. If the phases show those two as the bulk of lock hold, compute status once and refresh the anchor incrementally for the single new event.

Suggested regression bound around the first change: warm `task_create` median on the large fixture must stay at or below the measured median, and the median `read_calls` must fall by approximately half (one `iter_events` pass instead of two). Keep the small 300-task test as a shape/sum guard, not an SLO.

No p50/p95 product target is set here. Decision `ux/latency-thresholds` waits on this evidence.

