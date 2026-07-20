# Council synthesis — round 1

Three independent passes reviewed the existing collaboration prototype from
architecture, product, and security/test perspectives.

## Agreed direction

- Build a model-agnostic manager-space, not a renamed ML experiment ledger.
- Preserve the existing donor system unchanged.
- Never copy its live events, manifests, blobs, discussions, claims, caches, or
  migration data into this repository.
- Reuse immutable storage, canonical serialization, typed IDs, atomic publication,
  idempotency, claims, invalidation, and security concepts.
- Rewrite configuration, identity, domain lifecycles, query/orientation, doctor,
  views, CLI, and ingest around universal collaboration semantics.
- Add a rebuildable SQLite projection from the first universal release; the
  file-scan prototype is already strained at roughly five hundred events.
- Keep the everyday workflow light so small projects do not need governed
  ceremony for every edit; named policy presets can follow after the protocol is
  proven.
- Preserve disagreement and require explicit evidence/review/authority before
  promoting a statement into effective project truth.

## Observed failure modes that shape the new design

- Intentional draft references appeared as global ledger corruption.
- A staging artifact admitted directly identifying IDs despite secret scanning.
- Agent role names and detected software diverged.
- Exact-string path claims allowed ancestor/descendant edit overlap.
- Agents continued writing after a failing integrity check.
- Duplicate proposals and stale open work accumulated without ergonomic cleanup.
- Full scans exceeded the documented orientation and doctor latency targets.

These are treated as design inputs, not copied defects.

## MVP acceptance gate

1. Initialize in an arbitrary empty Git repository with no ML terminology.
2. Join two sessions from different model families and observe one current state.
3. Prevent conflicting and ancestor/descendant path claims.
4. Keep discussion outside effective truth without explicit promotion.
5. Mark review stale after an artifact revision changes.
6. Fail closed on conflicting active decisions in one scope.
7. Deliver and acknowledge a targeted handoff.
8. Retry writes after crashes without duplicates.
9. Reject secrets, classified PII, path traversal, and actor spoofing.
10. Rebuild indexes and views entirely from canonical files.
11. Preserve existing `AGENTS.md` and `CLAUDE.md` outside managed blocks.
12. Pass one provider-crossing end-to-end protocol scenario, and document how
    the same primitives apply to web-app, design-prototype, and chatbot-service
    work without adding domain-specific schemas.
