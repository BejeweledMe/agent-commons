# ADR 0003: Ledger-derived, checkout-aware receipt recovery

- Status: Accepted
- Date: 2026-07-15
- Decision: `decision.04NE4TJR0WZ5D9E3EKH4YH1VGJ`
- Resolves design for: `finding.6P1B8EGC4GWJM3EMQYDGCVB821`

## Context

Canonical events live below `.agent-commons/events/` and are designed to travel
through Git. Idempotency receipts are operational files below the Git common
directory. The original implementation nevertheless required a permanent
one-to-one correspondence between every event visible in the current checkout
and every receipt shared by all linked worktrees.

That invariant confuses durable truth with a crash-recovery aid:

- a fresh clone has canonical events but no operational receipts, so it cannot
  make a canonical write;
- linked worktrees and branch switches see receipts for events that are absent
  from their branch-local ledger and report false orphans;
- an abandoned in-flight receipt followed by a Git merge of its exact event is
  permanently contradictory;
- two interrupted reservations cannot be repaired one at a time because each
  unrelated orphan blocks the identical retry of the other.

Receipts are needed while a canonical append is in flight. After the event is
durable, every receipt field can be derived from that event:

| Receipt field | Canonical source |
| --- | --- |
| `namespace` | `event.idempotency_namespace` |
| `key_digest` | SHA-256 of `namespace + "\0" + event.idempotency_key` |
| `semantic_sha256` | canonical SHA-256 of `semantic_event_body(event)` |
| `event_id` | `event.event_id` |
| `recorded_at` | `event.recorded_at` |

The ledger must therefore be authoritative after publication without weakening
crash-safe retry or making ordinary tampering look like recovery.

## Decision

Adopt ledger-derived, checkout-aware receipt recovery. This is an operational
storage migration; canonical event and manifest schemas do not change.

### 1. Separate shared state from checkout-scoped recovery state

Sessions, claims, the canonical write lock, and the disposable SQLite index
remain below the Git common directory and remain shared by linked worktrees.
Receipt recovery state moves to versioned scopes:

```text
.git/agent-commons-state/
├── sessions/
├── claims/
├── canonical-write.lock
├── index.sqlite3
└── idempotency-v2/
    ├── migration.json
    ├── abandonments/                 # append-only, workspace-wide tombstones
    ├── reconciliations/              # append-only tombstone audit markers
    └── scopes/<scope-id>/
        ├── scope.json
        ├── ledger-anchor.json
        └── receipts/<prefix>/<digest>.json
```

A normal Git scope is the hash of all of:

- the workspace ID;
- the normalized absolute per-worktree Git directory (`git rev-parse
  --git-dir`), not the shared common directory;
- the symbolic `HEAD` ref, such as `refs/heads/main`.

Detached HEAD uses the exact commit ID instead of a symbolic ref. Outside Git,
the normalized workspace root plus the literal ref `non-git` identifies the
scope. `scope.json` stores the unhashed descriptor and must match the directory
digest before the scope is trusted.

This makes a branch switch select another receipt scope while preserving the
same scope across ordinary commits on one branch. Two linked worktrees share
coordination but do not treat each other's branch-local reservations as local
orphans.

### 2. Treat a receipt as authoritative only while its append is in flight

The append sequence remains:

1. hold the shared canonical-write lock;
2. validate the current canonical ledger and its completeness anchor;
3. reserve an immutable scoped receipt;
4. atomically publish the canonical event;
5. reconcile the scoped receipts and atomically advance the anchor;
6. update the rebuildable index.

The following classifications are exhaustive inside the selected scope:

- **event and exact receipt**: healthy;
- **event and no receipt**: recoverable; derive and write the receipt;
- **event and conflicting receipt**: integrity failure;
- **receipt and no event**: an in-flight orphan;
- **exact abandonment and no event**: terminal retired identity;
- **exact abandonment and exact event**: reconcilable Git arrival;
- **abandonment and non-matching event/receipt**: integrity failure.

An ordinary new write is blocked by any local in-flight orphan. An identical
retry whose namespace, key, semantic hash, and reserved event ID match one local
orphan may finish even when other local orphans exist. Repeating this operation
repairs multiple interrupted writes deterministically, one at a time.

Receipts absent from the selected scope are irrelevant to this classification.
They remain available for their own worktree/branch and are not deleted.

### 3. Protect ledger completeness with a per-scope anchor

Each scope has one atomically replaceable operational anchor containing the
workspace ID, scope ID, format version, and a sorted map from canonical relative
event path to its full-file SHA-256. The anchor is a tamper-evident observation,
not project truth and not a substitute for Git.

Reconciliation validates every canonical event before consulting the anchor:

- an absent anchor may be bootstrapped from a fully valid ledger;
- every anchored path must still exist with the same bytes;
- valid additional events are allowed and advance the anchor after receipts are
  reconciled;
- a missing or changed anchored event fails closed;
- an explicit operator recovery is required after an intentional branch rewind
  or history replacement under the same symbolic ref.

Bootstrap is safe for a fresh clone because Git is the source of the initial
canonical files and there is no earlier local observation to preserve. Once a
scope has an anchor, silently rebuilding it from a subset is forbidden.

The first implementation exposes `agent-commons receipt status` for a read-only
classification and `agent-commons receipt reconcile` for the locked,
deterministic repair/bootstrap. Ordinary writes may repair missing receipts from
canonical events only after the same anchor checks pass; they must not silently
accept anchor deletion, conflict, or shrinkage.

### 4. Reconcile tombstones only against the exact canonical event

Abandonment remains explicit, capability-gated, and append-only. It records the
namespace, key digest, semantic hash, and reserved event ID. The plaintext key
need not be duplicated because it is verified by recomputing its digest from
the event.

If Git later introduces an event, the tombstone is superseded for the current
scope only when all of these values match exactly:

- namespace;
- digest of namespace and canonical event key;
- semantic SHA-256;
- event ID.

Reconciliation writes an immutable audit marker containing the tombstone hash,
scope ID, event ID, event file hash, actor, reason, and timestamp. It never
deletes or edits the tombstone. A partial match remains terminal and fails
closed. Reusing the abandoned identity for different content is always denied.

### 5. Make migration explicit and reversible

On upgrade, `receipt reconcile` runs under the canonical-write lock:

1. validate the entire current ledger and legacy receipt/tombstone documents;
2. copy every exact event/legacy-receipt match into the current v2 scope;
3. derive any other current-ledger receipts;
4. stop if a legacy orphan exists and report its digest;
5. require the operator either to adopt that orphan into the current scope and
   perform its identical retry, or to abandon it with the existing capability;
6. write the scope anchor, then the common v2 migration marker atomically.

Legacy files are retained as audit and rollback material but ignored by v2 only
after the migration marker exists. No migration guesses which branch owns an
orphan.

Rollback requires all Agent Commons writers to stop. The operator runs the
documented rollback preparation for one checkout, which validates that checkout
and recreates its complete legacy event/receipt correspondence. A v1 binary may
then be used for that one checkout. Linked-worktree operation must remain
disabled after downgrade because v1 cannot isolate receipt scopes. Canonical
events require no down-migration.

## Recovery behavior

| Scenario | Expected result |
| --- | --- |
| Fresh clone with events and no state | `receipt reconcile` validates ledger, derives receipts, creates anchor; writes resume |
| Second linked worktree on another branch | New scope bootstraps independently; foreign receipts do not block it |
| Switch branches in one worktree | Symbolic ref selects that branch's scope; first visit bootstraps, return reuses its anchor |
| Git adds events by pull/merge | Valid additions derive receipts and advance anchor |
| Git removes/changes an anchored event on same ref | Fail closed; require explicit operator recovery |
| Crash after reservation, before event | Local orphan blocks new writes; identical retry finishes it |
| Several such crashes | Any exact orphan retry may finish despite the others; repeat until clear |
| Abandon, then exact event arrives through Git | Preserve tombstone, add exact reconciliation marker, derive receipt |
| Abandon, then different event uses the identity | Fail closed permanently |

## Security consequences

The design restores portability without declaring operational state
authoritative. The anchor preserves detection of missing or changed events after
their first local observation, while Git remains responsible for transport and
its own object integrity. A process able to rewrite both the workspace and the
Git common directory can still tamper with anchors and receipts; this is already
outside MVP-0's local trust boundary.

Branch identity is coordination metadata. Renaming a branch creates a new scope
and a valid-ledger bootstrap; old scope state remains auditable. Scope garbage
collection is deferred and must never delete tombstones or reconciliation
markers implicitly.

## Alternatives considered

- **Limit MVP-0 to one checkout.** Simpler, but contradicts the documented
  linked-worktree topology and leaves Git portability incomplete.
- **Commit receipts.** Rejected because Agent Commons must not stage or commit
  for the user, receipts contain operational data, and Git conflicts would move
  crash coordination into canonical history.
- **Defer to a remote service.** Rejected as outside MVP-0 and unnecessary for a
  same-host deterministic recovery problem.

## Implementation and acceptance gates

Implementation is not accepted until focused tests reproduce and then fix all
of these cases: fresh clone, linked worktrees, branch switch and return, Git
addition, anchored deletion/modification, one and multiple orphans, exact and
conflicting tombstone arrival, legacy migration with and without orphan, crash
at every publication boundary, and rollback preparation. Independent review
must bind the exact implementation revision, and reproducible verification must
remain separate from that expert judgment.
