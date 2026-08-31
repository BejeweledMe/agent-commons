# ADR 0013: Canonical revisioned Design Packages

- Status: accepted for the G1 semantic slice
- Date: 2026-08-30

## Context

The Gallery shell and verified PNG/JPEG preview reader already exist, but there
is no canonical object that says which screens belong together, in which
order, or which exact artifact and task revisions produced them.  Inferring a
gallery from current files or artifact metadata would make mutable filesystem
state authoritative and would lose provenance when either an artifact or task
changes.

The immutable event ledger remains the only project-truth store.  Design
Package lists and revision history are derived projections.  Package events do
not copy image bytes or filesystem paths and do not create a second feedback
store.

## Decision

Design Packages are canonical entities with two event types:

- `design_package.created` publishes the first complete package revision;
- `design_package.revised` replaces it after an exact `expected_revision`
  compare-and-swap.

Each revision records a title and an ordered, non-empty list of `ScreenBinding`
objects.  A screen binding contains:

- a stable `screen.<ULID>` identifier and a contiguous 1-based ordinal;
- a bounded title;
- the exact artifact entity revision and its `sha256:` content revision;
- the exact producing task revision;
- the verified media type and classification;
- `safe_preview_eligible: true`, which is accepted only after the existing
  verified preview reader has proved current PNG/JPEG bytes below all
  descriptor, no-follow, hash, byte, pixel, MIME and classification limits.

The event actor is the producer for that package revision.  Publication is
allowed only when the actor authored both the exact artifact revision and the
producing task, and the task binds that exact artifact revision.  This makes
producer/task provenance an enforced property rather than display text.
`public` and `internal` are the only V1 classifications.  `restricted`, stale,
missing, replaced, traversing, symlinked, non-image or otherwise unverifiable
sources receive a typed refusal before any event or idempotency reservation.

The ordered screen array is canonical.  Ordinals must be exactly `1..N` in
array order, screen identifiers must be unique, and a
revision is a complete snapshot rather than a patch.  Projection retains every
effective historical revision so a later package revision cannot mutate an
already selected one.

Corrections may repair non-provenance wording in the package title.  They may
not change screens because immutable event relations continue to identify the
original artifact and task provenance.  Screen or provenance changes require
`design_package.revised`.

## Bounds and security

| Field | Limit |
|---|---:|
| package title | 256 UTF-8 characters |
| screens | 64 |
| screen title | 256 UTF-8 characters |
| semantic payload | 65,536 bytes |
| owned container depth | 8 |

Inputs cross a bounded ownership-copy boundary before validation.  Hostile
mapping/list subclasses cannot force unbounded iteration, expose values through
exception text, or mutate a frozen record after publication.  Unknown fields,
duplicate screen identifiers, non-contiguous order and mismatched artifact/task/manifest
facts fail closed.  Canonical payloads and public refusals contain no raw path,
image bytes, manifest body or provider data.

## Migration, older readers, and rollback

No existing event or manifest changes shape.  A ledger with no Design Package
events preserves its prior public snapshot bytes; a representative pre-G1
fixture pins that behavior.  Before the first package create or revise write,
the writer records `workspace.semantics_required` version 4.  A version-3
reader therefore refuses a ledger containing this new canonical family instead
of silently ignoring it.

Rollback is write-disable, never history deletion.  With
`design_package_writes_enabled=False`, both the narrow service and the universal
`record_event` entry point refuse create/revise before idempotency reservation.
Existing package events remain replayable and readable by the new binary.  An
older binary is safe only before the version-4 semantics stamp.  No operational
run state needs draining and SQLite may be rebuilt because it remains derived.
The last safe checkout is the revision immediately before this ADR and the G1
implementation commit; restore rehearsal is old-ledger replay plus write-disable
tests and exact independent review.

## Consequences

- G2 can expose real ordered screens with exact provenance instead of demo data.
- Gallery preview reuses the existing hardened artifact reader and cannot widen
  it to arbitrary paths, SVG or HTML.
- Feedback remains an existing revision-bound `review_discussion` workflow and
  is deliberately outside G1.
- Package publication is stricter than artifact registration: an image may be
  valid evidence but still be ineligible for Gallery display.
