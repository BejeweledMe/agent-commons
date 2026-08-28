# Documentation map

This page is the routing guide for Agent Commons documentation. It is not a
second product specification. When prose conflicts with executable behaviour,
the current source code, tests, schemas, and canonical project state win.

## Read first

| Need | Primary source | Status rule |
| --- | --- | --- |
| Product purpose and boundaries | [Vision](VISION.md) | Product intent, not proof that a feature is shipped. |
| What the current checkout actually does | Source code and tests at the current Git revision | This is the release truth; verify with `make check`. |
| Human-readable product snapshot | [Current product and architecture](current-product-and-architecture.md) | Historical baseline at `f998e33`; it is not a live release dashboard. |
| Protocol and promotion-to-truth rules | [Protocol](PROTOCOL.md), then accepted [ADRs](adr/) | Proposals and withdrawn ADRs do not authorize implementation. |
| System boundaries and threat model | [Architecture](ARCHITECTURE.md), [Threat model](THREAT_MODEL.md), [Frontend contract](FRONTEND_CONTRACT.md) | Code and tests decide shipped behaviour; these documents explain invariants and boundaries. |
| User-visible workflows | [English guide](user/en/README.md) or [Russian guide](user/ru/README.md) | Describe available UI behaviour only; link future work as future. |
| Roadmap and approved direction | [Roadmap](ROADMAP.md), [implementation plan](architecture-improvement-implementation-plan.md) | Intent and sequencing, never evidence of delivery. |

## Canonical data versus derived material

The repository follows this order of authority:

1. Immutable project events and manifests under `.agent-commons/` are the
   canonical project history. They are append-only and must not be edited by
   hand.
2. Source code, schemas, and tests define the behaviour of the current Git
   revision. A Markdown statement cannot make an unimplemented capability
   real.
3. SQLite projections, Markdown views, UI bundles, `build/`, `dist/`, and local
   caches are derived or generated. They may be rebuilt and are not a second
   source of truth.
4. Accepted decisions in the Agent Commons ledger constrain future work.
   Reviews, audits, proposals, plans, and PRDs remain analysis or intent until
   an explicit owner-authorized decision promotes them.

## Document collections

- [ADRs](adr/) — decisions, including explicit withdrawals and superseded
  alternatives; read the status at the top of each file.
- [Reviews](reviews/) — revision-bound judgments and corrected assessments.
- [Audits](audits/) — historical observations, characterization, and evidence.
- [Proposals](proposals/) — product and technical options that still need the
  relevant owner gates.
- [Evals](evals/) — reproducible evaluation contracts and measurements.
- [Tutorials](tutorials/) — guided exercises, not implementation contracts.

The three architecture-review files currently at the repository root are
tracked user-provided working materials and provenance evidence:

- [`agent_commons_product_architecture_review.md`](../agent_commons_product_architecture_review.md)
- [`claude_architecture_improvement_review.md`](../claude_architecture_improvement_review.md)
- [`codex_architecture_improvement_review.md`](../codex_architecture_improvement_review.md)

They are intentionally not canonical product truth and are not promoted into
the decision record merely by being tracked. Their exact revision,
authorship, and status are recorded in the [review provenance register](reviews/README.md)
and must be resolved before any recommendation becomes an accepted decision.

## Maintenance rules

When behaviour changes, update code and tests first, then update the relevant
current-status or user documentation with the exact Git boundary. Keep older
reviews and withdrawn proposals as historical evidence; mark them rather than
silently rewriting their conclusions. A claim that a feature is shipped should
have an implementation, tests, and a reproducible verification path.
