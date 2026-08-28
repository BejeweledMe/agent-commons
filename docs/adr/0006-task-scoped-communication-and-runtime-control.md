# ADR 0006: Task-scoped operational communication and runtime control

Status: accepted for the communication-core batch.

## Context

A delegated worker sometimes needs to ask its parent a bounded question, report
bounded progress, or flag that it is blocked, without turning Agent Commons into
an agent-to-agent chat protocol. [ADR 0004](0004-optional-local-delegation-runtime.md)
already forbids persisting prompts, reasoning, or interactive input in canonical
history: "sensitive interactive input stays in the local runtime channel." This
ADR defines that channel's data model before any code exists.

The feature must stay additive to the existing file-ledger core, must not create
a second source of project truth, and must fail closed rather than silently
dropping or duplicating an operational exchange across a crash or restart.

At the time this ADR was written, it defined the channel before implementation.
The bounded communication slice is now implemented; see the delivered entry in
the [roadmap](../ROADMAP.md). The implementation remains subject to the same
canonical/operational boundary and does not make this ADR a second status board.

## Canonical/operational boundary

Question, answer, progress, and blocker content described here never becomes a
canonical event. The service may move the existing canonical delegation between
`active` and `input_needed`, but those transitions use maintainer-defined fixed
status text; neither the caller's question nor its answer nor a caller-supplied
summary can enter the ledger. The operational communication store is private,
non-authoritative, rebuildable state that a session in a workspace already
holding runtime access may use directly; it is not a second project-truth store.

A durable requirement, blocker, decision, or finding that other sessions must
see later still belongs in the normal typed Commons thread/finding/decision
workflow. This channel is for bounded, ephemeral, in-flight coordination between
exactly the parent and child of one delegation attempt while that attempt is
still active.

## Participant graph

Every operation is created inside a fixed **scope**:

- `workspace_fingerprint` — the SHA-256 checkout fingerprint already used by
  [`checkout_fingerprint`](0004-optional-local-delegation-runtime.md);
- `delegation_id`, `task_id`, `target_revision`, `attempt_id` — the exact
  canonical work the operation is bound to;
- `sender_session_id` — the session that opened the operation;
- `allowed_recipient_session_ids` — a non-empty, explicit set of sessions
  permitted to see and act on it.

The participant graph is exactly two-sided: the sender and its declared
recipients. A session absent from both sets is **foreign** and every operation
fails closed for it — it cannot open, check, reply to, acknowledge, or cancel an
operation it is not named in, and a lookup never distinguishes "does not exist"
from "exists but you cannot see it" in its error text. Sender and recipient sets
are disjoint; a session cannot be its own counterparty.

An operation additionally records the exact `attempt_id`/`target_revision` it
was opened under. A caller may assert the scope it believes it is operating in;
a mismatch is **stale** and fails closed rather than silently rebinding the
operation to newer canonical state.

## Operation kinds and lifecycle

Three kinds share one record shape:

- `request` — the sender asks a bounded question and expects a `reply`;
- `progress` — the sender reports bounded status; no answer is expected;
- `blocker` — the sender reports it cannot proceed; no answer is expected.
- `guidance` — the parent sends one bounded tactical instruction to the child;
  the child acknowledges receipt;
- `checkpoint` — the parent asks the child to acknowledge a named safe boundary.

```text
request:            open ── reply ──> replied ── ack ──> acked
progress / blocker / guidance / checkpoint: open ── ack ──> acked

open | replied ── cancel_request ──> cancel_requested ── cancel_confirm ──> cancelled

open | replied | cancel_requested ── deadline elapsed ──> expired
any non-terminal ── restart reconciliation past deadline ──> expired
```

`acked`, `cancelled`, and `expired` are terminal. A `request` may only move to
`replied` and then `acked`; notifications move directly from `open` to `acked`
because there is no answer to await. `reply` is authorized only for an allowed
recipient; `ack` of a `request`'s reply is authorized only for the original
sender (it is acknowledging receipt of an answer to its own question), while
`ack` of a notification is authorized only for an allowed recipient. Guidance
and checkpoint are parent-to-child notifications; the child cannot manufacture
a parent control by selecting a recipient. Each of `reply` and
`ack` is **exactly-once**: the first call fixes the content, a retry with the
same idempotency key and content is a no-op returning the stored record, and a
retry with different content or a second distinct attempt is a fail-closed
`LifecycleConflictError` rather than a silent overwrite or an echoed duplicate.

## Continuation identity and depth

A new operation may declare `continuation_of` an existing operation instead of
starting a fresh exchange, forming a bounded chain (for example, a `blocker`
that continues an earlier `progress` report). The child's `depth` is its
parent's `depth + 1`, and a request whose depth would exceed the configured
`max_chain_depth` fails closed. Every ancestor referenced by `continuation_of`
must already exist inside the exact same workspace, delegation, task, target
revision, attempt, sender, and recipient scope; a chain that
references a foreign scope, a missing ancestor, or revisits an already-visited
ancestor while walking the chain is rejected as cyclic/corrupt rather than
walked indefinitely. Continuation identity therefore lets a bounded
conversation exist without ever becoming unbounded recursion or an implicit
second delegation.

## Message, size, and deadline budgets

Configured `OperationLimits` bound every operation before it is admitted:

- `max_chain_depth` bounds continuation depth;
- `max_metadata_bytes` bounds the canonical-JSON size of the question, reply,
  or progress/blocker payload;
- `min_deadline_seconds`/`max_deadline_seconds` bound how long a `request` may
  stay open awaiting a reply/ack before it lazily expires.

An operation exceeding any budget at creation time is rejected before any
mutation occurs. A limit is deliberately not inferred from message content; it
is store configuration, analogous to `OperatorLimits` in ADR 0004.

## Two-phase cancellation

Cancellation is sender-owned but never unilateral: only the original sender may
move an open or replied operation to `cancel_requested`, and only one of the
fixed allowed recipients may `cancel_confirm` it into the terminal `cancelled`
state. Identical retries of either phase return the already stored state. This
mirrors the attempt store's
`CANCEL_REQUESTED` phase in ADR 0004 so that a party cannot unilaterally erase
an in-flight exchange the other side has not yet observed; an operation stuck in
`cancel_requested` still expires normally if its deadline elapses first.

## Privacy and integrity

The private store never enters canonical history and is scanned with the same
`SecurityPolicy` used elsewhere in the runtime: a question, reply, or
progress/blocker payload containing a classified/secret/PII value is rejected
before it is written, not redacted after the fact. Every public operation ID is
validated as a typed ID before it can participate in path construction. Every
document is stored as canonical JSON with both a public SHA-256 diagnostic
digest and an HMAC-SHA-256 authentication tag over all semantic fields. The
authentication key is generated once under the communication lock, stored only
in private operational state as a non-symlink `0600` file owned by the current
OS principal, and never emitted into canonical history, telemetry, or command
output. Missing, replaced, malformed, weak-permission, or foreign-owned key
material fails closed. Documents and key are written through the existing
atomic-replace primitive with `0o600`/`0o700` permissions and re-validated on
every read. A missing operation
and an operation hidden from a foreign session produce the same error class and
message, and bulk reads return only records visible to the requesting
participant. A foreign, stale, duplicate, expired, cyclic/depth-exceeded,
oversized, secret-bearing, or tampered document fails closed without a partial
mutation and without echoing the rejected content back to the caller.

The HMAC prevents a party that can alter only an operation document from
silently recomputing its integrity proof. It is not an OS sandbox: a process
running as the same principal with read access to the entire operational state
root can also read the key. Preventing that stronger attacker requires the
operator-owned checkout/process isolation described by ADR 0004; this store
does not claim to simulate it.

## Migration and rollback

This is a wholly new, additive, private store below the existing runtime state
root. No existing schema, canonical event, or file changes shape. Disabling or
deleting the communication store, including its integrity key, recovers a workspace where operational
exchanges from before the change never existed; it has no effect on canonical
delegation, task, or review state, and no receipt, replay, or projection code
depends on it. Rollback is: stop opening new operations, let any in-flight
attempt finish or expire on its own deadline, and remove the
`runtime/communication` directory below the operational state root.

## Consequences

- Delegated work gains a bounded, inspectable channel for questions, progress,
  blockers, parent guidance, and safe-boundary checkpoints that never becomes a
  second project-truth store or an unbounded chat surface.
- The exactly-once reply/ack contract and two-phase cancellation trade a small
  amount of caller ceremony for the same crash-safe, non-duplicating semantics
  the attempt store already provides.
- Depth, size, and deadline budgets are operator configuration, not inferred
  from content, so they can be tightened without a schema change.
