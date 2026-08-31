# ADR 0012: Canonical revisioned Context Packs

- Status: accepted for the C1 semantic slice
- Date: 2026-08-30

## Context

An accumulated-context run needs a reproducible baseline without inheriting a
provider transcript, hidden reasoning, credentials, mutable workspace state, or
authority from another run.  The existing `accumulated` role flag is therefore
not enough: it records intent but does not identify the information a launch
would receive.

The immutable event ledger remains the only project-truth store.  Context Pack
lookups, revision history, compiled text, and fingerprints are projections of
that ledger; they are not a private run ledger and cannot complete a
delegation.

## Decision

Context Packs are canonical entities with two event types:

- `context_pack.created` records the first published revision;
- `context_pack.revised` records a complete replacement after an exact
  `expected_revision` compare-and-swap.

`publish` is the application-service verb.  The first canonical event is named
`created` so it uses the existing generic creation lifecycle path.  The shared
lifecycle guard derives duplicate protection from the event registry and adds
only the Context Pack rule that every bound source be current, exact, and safe.
A revision is a full snapshot, not a patch.  The projection retains both the
current record and every effective historical revision, so publishing v2
cannot mutate v1.

Each revision contains only:

- one summary;
- ordered facts, each with at least one exact revision-bound canonical source;
- ordered exact revision-bound decision references;
- ordered open questions.

The service accepts only currently effective sources.  Artifact sources marked
`restricted` are refused.  Canonical security scanning runs before the write,
and a refusal never records an event or idempotency reservation.  References do
not copy their target body into the pack.  Transcripts, provider output, tool
arguments, hidden reasoning, credentials, arbitrary paths, executable
configuration, and provider-specific CLI material are outside the schema.

## Bounds

The schema and domain validator enforce the same ceilings:

| Field | Limit |
|---|---:|
| summary | 4,096 UTF-8 characters |
| facts | 64 |
| fact statement | 1,024 UTF-8 characters |
| sources per fact | 8 |
| decision refs | 32 |
| open questions | 32 |
| open question | 1,024 UTF-8 characters |
| canonical semantic payload | 65,536 bytes |
| compiled baseline | 65,536 bytes |

Empty facts and decisions are allowed, but a fact without a source is not.
Unknown fields fail closed.  Inputs are deeply copied into frozen domain
records before they cross the feature boundary.  That ownership copy accepts
mapping/list subclasses only through bounded iteration, converts them to plain
builtins, caps container size/depth/count, and replaces hostile container
exceptions with fixed messages that do not echo their values.

## Revisions, corrections, and stale input

The public pack revision is the effective event revision.  A correction to a
pack event therefore creates a different effective revision and can make a
successor CAS stale; it never silently changes the bytes identified by an
already compiled fingerprint.  An invalidated event disappears from the
effective revision history on replay.

A correction may fix non-provenance wording such as the summary or an open
question.  It may not change `facts` (including their source refs) or
`decision_refs`, because correction does not rewrite the immutable event
relations extracted from those fields.  New or changed provenance must be a
full `context_pack.revised` event.  Both the write path and replay enforce this
rule, so an imported hostile correction makes doctor fail closed.

The narrow service returns typed refusal codes for `missing`, `stale`,
`unsafe`, `oversized`, and `invalid`.  A requested historical revision that is
still effective remains compilable after a newer normal revision is published.
A missing historical revision is not rebound to the latest one.

## Deterministic compiler

Compiler version `context-pack-compiler.v1` renders UTF-8 with fixed headings,
fixed LF line endings, 1-based ordered facts/questions, and canonical JSON for
revision-bound references.  The compiled fingerprint is
`sha256(compiler_version + NUL + compiled_bytes)`.  Relevant content or a
compiler-version change therefore changes the fingerprint.  The compiler
returns text, size, exact source references, and a frozen `ContextPackBinding`;
only the fingerprint/size/binding may later enter launch or telemetry records.
The compiled body itself must not be persisted in canonical events, attempts,
telemetry, or UI DTOs.

C1 does not bind a pack to a launch.  C2 must bind an exact pack revision and
the resulting fingerprint into one immutable validated launch plan.  Fresh
remains the default.  Fan-out, UI selection, provider resume, and checkpoint
recovery are not part of this ADR.

## Migration, older readers, and rollback

No existing event or manifest changes shape.  A ledger with no Context Pack
events replays to an empty pack collection and preserves its previous public
snapshot bytes; a sanitized objective/task lifecycle fixture pins those bytes
and the old semantics floor.  Before either `context_pack.created` or
`context_pack.revised`, the service raises `workspace.semantics_required` to
version 3.  This includes revising an imported unstamped pack.  A v2 reader sees
that floor and fails closed instead of silently ignoring an unknown event
family.  New readers rebuild the derived pack projection directly from
canonical events.

Rollback is write-disable, not history deletion: stop new publish/revise/bind
operations, keep replay support for existing events, and make accumulated
launches return a typed unavailable refusal while fresh launches continue.
`CommonsManager(context_pack_writes_enabled=False)` is the explicit operator
gate: publish/revise refuse before reserving an idempotency key, while
read/compile and unrelated fresh-mode work remain available.  Removing the
schema or projection after events exist is not a supported rollback.  No
migration or backfill is required for old ledgers.

## Consequences

- Two roles can later share one immutable canonical baseline without sharing a
  transcript, session, authority, or mutable state.
- Exact source provenance and bounded compilation are reviewable and
  reproducible.
- A pack cannot smuggle provider controls or claim resume semantics.
- The extra canonical event family requires every future reader to retain its
  schema and replay support even when publishing is disabled.
