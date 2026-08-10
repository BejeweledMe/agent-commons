# ADR 0008: The run-observability store is withdrawn

Status: accepted. Code removed; this record replaces it.

## What existed

A disposable SQLite/WAL projection for high-frequency run events — node status,
tool calls, token usage — separate from the immutable ledger and from the
metadata-only telemetry in `runtime/telemetry.py`. Roughly 2 850 lines: the
store, a pure fold reducer, three-tier retention, snapshot replay, JSONL export,
and their tests. Removed in full, along with `agent-commons run list/export`,
`CommonsPaths.orchestrator_db`, and `RunRetentionLimits`.

## Why it was removed

- **No producer, and none was close.** The broker never wrote to it, the UI
  never read from it. Every claim it made — disposable, retained, replayable,
  exportable — was unverifiable on any real path.
- **Its producer was blocked by other work.** `SubprocessRunner` buffers
  provider output and hands it over only after exit, so nothing can emit an
  event while a run is in progress. The streaming seam has to exist first.
- **The event vocabulary did not match the available data.** Eighteen event
  kinds, including `llm.turn`, `tool.started/finished` and `span.*`. Child
  agents are opaque subprocesses: the broker observes launch, exit, and byte
  counts. Even with a producer, four or five kinds were reachable.
- **Dead infrastructure is not neutral.** It sat in the import graph, in CI, and
  in every runtime refactor, and it made the branch read as though observability
  had shipped. Two independent reviews reached the same conclusion separately.

## What it got right, and worth keeping

- Retention candidates were restricted **structurally** to terminal states, so
  no branch could reach an active or `needs_operator` run. That is the right way
  to express the invariant — a filter in the query, not a conditional in a loop.
- Retention loops must be driven by a measure that responds to deletion. Driving
  the size cap off SQLite page count silently deleted every run, because pages
  do not return after a delete.
- A digest must materialise its terminal snapshot **before** dropping the
  stream, or it loses everything after the last periodic snapshot.
- Folded state has to be bounded. Unbounded guardrail/milestone history was
  copied into every snapshot, so a digested run grew larger than the stream it
  replaced.
- A pure reducer with no clock, environment, or storage is worth rebuilding: it
  made `fold(events) == snapshot(k) + fold(tail)` testable at every cut.
- Readers need their own connections. Sharing the write connection corrupted
  cursor state and exposed rows from a transaction that later rolled back.

## What to do when it returns

1. Build the streaming seam in `SubprocessRunner` first — a per-line callback
   keeping the existing bounded-output and process-group termination contract.
2. Derive the event vocabulary from what the broker can actually observe, not
   from the orchestrator we would like to have. Add a kind when a producer for
   it exists.
3. Land producer and consumer in one change. A durable mechanism with no caller
   in `src/` should not merge again.
4. Keep the store out of the request/attempt identity: it is operational state
   and nothing canonical may depend on it.

The removed implementation is in git history at `4525131`.
