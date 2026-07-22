# ADR 0001: File ledger with a SQLite projection

- Status: accepted
- Date: 2026-07-14

## Context

Several agent processes need one durable source of project state, readable in a
checkout and recoverable without operating a service. Interactive orientation
also needs indexed queries; repeatedly scanning every record does not scale.

A queue or in-memory database would improve transport, but neither is a durable,
reviewable project history by itself. Requiring a broker would also make local
installation, worktrees, recovery, and archival more fragile.

## Decision

Canonical events and manifests are immutable, schema-validated files. A local
SQLite database in WAL mode is a disposable query projection. Sessions, claims,
idempotency receipts, and locks live in the Git common directory so linked
worktrees coordinate with each other without committing operational leases.

All business writes use one supported service layer. SQLite can always be deleted
and rebuilt from canonical files; it must never become the only copy of meaning.
Normal reads currently replay the ledger rather than querying SQLite, so
canonical writes do not synchronously maintain an index that cannot shorten
their user journey. `doctor` verifies/synchronizes it and `index rebuild`
reconstructs it explicitly. A future indexed read path must prove equivalent
projection semantics before synchronous maintenance is reconsidered.

## Consequences

- The project remains inspectable, diffable, portable, and recoverable offline.
- Replay work is exposed through deterministic counters and a 10,000-event /
  1,000-correction benchmark; correction lookup is indexed by target rather
  than scanning every correction for every event.
- Atomic publication, idempotency, strict schemas, and interprocess locking are
  mandatory because a broker is not serializing writes for us.
- Same-filesystem operation is the MVP boundary. Remote multi-host use needs a
  real service and a new trust model.
- A queue may later distribute notifications, but it will not replace canonical
  history.
