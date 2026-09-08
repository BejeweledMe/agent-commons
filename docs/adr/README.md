# ADR and canonical decision index

This directory explains architecture choices. It is not a second decision
registry. Product decisions are canonical events in the Agent Commons ledger;
read their current state with the local checkout:

```bash
uv run agent-commons decision list
```

An ADR may describe an accepted architecture boundary, a proposal that has not
been authorized, a partial supersession, or an explicit withdrawal. The status
below is a navigation aid and is enforced against the files by a repository
contract test. The status written in each ADR remains the detailed record.

## Current index

| ADR | Status | Boundary |
| --- | --- | --- |
| [0001](0001-file-ledger-with-sqlite-projection.md) | Accepted | Immutable file ledger; SQLite is a derived projection. |
| [0002](0002-explicit-truth-promotion.md) | Accepted | Discussion and model agreement do not become project truth without explicit promotion. |
| [0003](0003-ledger-derived-checkout-aware-receipt-recovery.md) | Accepted | Checkout-aware recovery keeps canonical history authoritative over operational receipts. |
| [0004](0004-optional-local-delegation-runtime.md) | Accepted, implementation narrowed | Optional allowlisted local broker; current creation remains leaf-only. |
| [0005](0005-state-root-isolation.md) | Accepted | Workspace ownership and operational state-root isolation. |
| [0006](0006-task-scoped-communication-and-runtime-control.md) | Accepted for communication core | Bounded private parent/child operational communication. |
| [0007](0007-multi-level-delegation-and-raised-concurrency.md) | Proposed | Preconditions for deeper delegation and higher concurrency; not shipped authority. |
| [0008](0008-run-observability-store-withdrawn.md) | Accepted withdrawal | The unused private RunEventStore was removed and must not return as a second ledger. |
| [0009](0009-agents-as-first-class-roles.md) | Accepted, partially superseded | First-class roles; Q2 and part of Q4 are superseded by ADR 0010. |
| [0010](0010-panel-access-by-typed-refusal-not-flags.md) | Accepted | Panel access uses typed refusals rather than capability flags. |
| [0011](0011-hierarchical-delegation-closure.md) | Proposed | H0 semantic contract; no write path is authorized by this ADR. |
| [0012](0012-context-packs.md) | Accepted for C1 semantic slice | Canonical revisioned Context Packs and their bounded compiler contract. |
| [0013](0013-design-packages.md) | Accepted for G1 semantic slice | Canonical revisioned Design Packages with exact screen provenance; Gallery API and UI remain subsequent work. |
| [0014](0014-task-first-workspace-ux.md) | Proposed | Task-first Work workspace, domain-specific UI state, explicit execution and bounded exact-source discovery; branch implementation authorized, verification pending. |
| [0015](0015-browser-native-visual-refinement.md) | Proposed | Council-guided browser refinement: native status selection, persistent recovery focus, inspector disclosures and readable exact Context sources; bounded branch implementation authorized. |
| [0016](0016-service-library-and-live-workspace.md) | Proposed | Service-owned professional skills and specializations, editable library, five workflow blueprints, live task graph and browser Gallery; implementation authorized. |
| [0017](0017-project-scoped-service-host.md) | Proposed | One authenticated local host, immutable project contexts, safe registry/create/connect and project-scoped Work/Gallery; branch implementation authorized. |
| [0018](0018-role-connections-and-execution-modes.md) | Proposed | Future role connection, API/local, SGR and conversation contracts; no runtime capability or live qualification claimed. |
| [0019](0019-project-native-collaboration-and-outputs.md) | Accepted direction, implementation open | Compact project shell, grouped reusable library, producer-scoped outputs, private message attachments and truthful human/agent delivery; current program is on the self-improvement branch. |

## How the two records fit together

- ADRs explain stable architecture, migration and rollback consequences.
- Canonical decisions record the owner-authorized product choice, alternatives,
  rationale, evidence, supersession and current state.
- Source, schemas and tests prove what the current revision actually does.
- Plans, reviews and PRDs remain intent or evidence even when they cite an
  accepted decision.

Important current product decisions include strict reviewed acceptance
(`decision.2FFQCGQKQ21VS1MQHNFCQEZWKJ`), the UI as primary surface
(`decision.61BDS4NC4GVK9K50R09XK98A07`), CLI compatibility migration
(`decision.65J1MEQNYC1GNHJYK9KDBDK49S`), incremental frontend migration
(`decision.7ZTNWNZN480TKQFG2WNDNA5ZQF`), canonical Context and Design Packages
(`decision.2ASFCETB9SMAXTVQ5PXRFJYRXW`) and the unchanged experimental broker
evidence gate (`decision.558YVVEX7D1BTEBERNBPT14XY2`). Use the ledger command,
not this shortlist, to determine current decision state.
