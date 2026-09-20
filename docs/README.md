# Documentation map

One need, one owning document. Current source and tests define implemented
behaviour; accepted Agent Commons decisions constrain future work. Plans,
proposals and reviews do not establish delivery or acceptance.

## Need → owning document

| Need | Owning document |
| --- | --- |
| Use the app | [English guide](user/en/README.md) · [Русское руководство](user/ru/README.md) |
| Product, positioning, glossary | [Product](PRODUCT.md) |
| Architecture and invariants | [Architecture](ARCHITECTURE.md) · [Protocol](PROTOCOL.md) · [Threat model](THREAT_MODEL.md) · [Frontend contract](FRONTEND_CONTRACT.md) |
| Install, operate, troubleshoot | [Quickstart](QUICKSTART.md) · [Technical workflows](USER_WORKFLOWS.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Broker operations](BROKER_OPERATIONS.md) |
| Decisions | [ADR index](adr/README.md) plus the live registry: `uv run agent-commons decision list` |
| Active plan | [Consolidation and wave 4](plans/2026-09-18-consolidation-and-wave-4-plan.md) · [UX closure technical plan](plans/2026-09-09-ux-closure-tech-plan.md) |
| Evidence and verification | [Evidence](evidence/) · [Audits](audits/) · [Review provenance](reviews/README.md) |
| Superseded and completed material | [Archive](archive/) |

Both entries under "Active plan" are active: the consolidation plan owns
repository and documentation work plus wave 4, the UX closure technical plan
owns the remaining UX work packages. Nothing else in this tree is a backlog.

Read the current decision registry through the command above. An ADR, proposal,
review or completed task alone does not promote anything to project truth.
Revision-bound results belong in their verification reports, not in repeated
status lists.

**Archive is a location, not a label.** Anything under `archive/` is historical
by the fact of where it lives: a document there is never current, never a
backlog, and never evidence of shipped behaviour. Every archived plan carries a
status stamp with its opening date, contract, successor and source boundary.

## Historical plans

All historical plans live in [`docs/archive/plans/`](archive/plans/). They
explain past reasoning at their own source boundaries; none of them is the
current backlog.

- [Vision](archive/plans/VISION.md) — superseded by the product document.
- [Visual orchestrator PRD](archive/plans/visual_multi_agent_orchestrator_prd.md)
  and [navigation plan](archive/plans/visual_orchestrator_plan.md) — superseded
  by the product document.
- [Product snapshot](archive/plans/current-product-and-architecture.md) —
  baseline at `f998e33`; superseded by the product document.
- [Agent platform programme](archive/plans/agent-platform-implementation-program.md)
  and [provider adapter plan](archive/plans/provider-adapter-architecture-plan.md)
  — superseded by the active consolidation plan.
- [UX improvement plan](archive/plans/2026-09-09-ux-improvement-plan.md) —
  superseded by the active consolidation plan.
- [UI pivot analysis](archive/plans/pivot-ui-only-plan.md) — historical decision
  intake at `1c4e570`.
- [Managed-loop plan](archive/plans/architecture-improvement-implementation-plan.md)
  and [agent team plan](archive/plans/architecture-improvement-agent-team-plan.md)
  — predecessor programme.
- [Task-first workspace plan](archive/plans/task-first-workspace-implementation-plan.md),
  [project-native collaboration plan](archive/plans/project-native-collaboration-implementation-plan.md),
  [project workspace plan](archive/plans/project-workspace-ux-plan.md),
  [context pack and gallery plan](archive/plans/context-pack-gallery-implementation-plan.md),
  [feedback remediation plan](archive/plans/feedback-remediation-plan-2026-08-28.md),
  [CLI migration inventory](archive/plans/cli-migration-inventory.md) —
  completed increments.
- [Starter packs proposal](archive/plans/starter-packs-integration.md) and
  [C1 UI surface contract](archive/plans/c1-ui-surface-contract.md) —
  superseded proposals.

Archived reviews and low-inbound audit reports live in
[`docs/archive/reviews/`](archive/reviews/), including the two registered
architecture reviews indexed by the [provenance register](reviews/).
Earlier archived inputs — [council synthesis](archive/COUNCIL_SYNTHESIS.md), the
[24-August product review](archive/agent_commons_product_architecture_review.md)
and the two original orchestrator documents — remain byte-for-byte.

## Current direction summaries

Product purpose, positioning, the role → task → run → review → acceptance chain
and the shared glossary live in one place: [`PRODUCT.md`](PRODUCT.md). Earlier
direction summaries were merged into it and archived; do not reintroduce a
second product description. [Proposals](proposals/), [evals](evals/),
[tutorials](tutorials/) and [performance notes](performance/) have separate,
narrower purposes.

## Retention and maintenance

The repository keeps current files plus canonical history. Immutable events,
manifests, receipts and registered proof stay retrievable; retaining a
historical artifact does not make an old plan current.

Evidence retention follows decision D-13: keep the latest manifest version per
work package and the wave integration manifests; older manifest versions are
retrievable only through the tag `archive/2026-09-pre-consolidation`; canonical
ledger history is never rewritten. The policy and the per-directory rules live
in [`docs/evidence/README.md`](evidence/README.md).

For new verification, retain a bounded result summary, meaningful delta or exact
Git reference. Keep temporary full logs outside the tracked repository. Do not
routinely copy full-source inventories or screenshots; retain them only when
they prove distinct behaviour or satisfy an explicit verification requirement.

SQLite projections, views, local caches and build output are derived. Shipped UI
bundles are generated too, but remain required package assets. Update the
relevant current guide when behaviour changes and run `make check`.

With tests and builds stopped, `make clean-preview` previews cleanup and
`make clean` applies it only to `build/`, `dist/`, `.pytest_cache/` and
`.ruff_cache/`. Tracked files are refused; canonical state, evidence, dependency
environments and shipped UI assets are outside that scope.
