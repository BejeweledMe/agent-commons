# Documentation map

Use this page to choose a document. Current source and tests define implemented
behaviour; accepted Agent Commons decisions constrain future work. Plans and
reviews do not establish delivery or acceptance.

## Read first

| Need | Primary source |
| --- | --- |
| Use the app | [English guide](user/en/README.md) · [Русское руководство](user/ru/README.md) |
| Current work and remaining gates | [Implementation programme](agent-platform-implementation-program.md) |
| Project workspaces and future connection modes | [Project execution plan](project-workspace-ux-plan.md) · [Verification](audits/2026-09-08-project-workspaces-verification.md) · [Future API/local modes](adr/0018-role-connections-and-execution-modes.md) |
| Product purpose and future direction | [Vision](VISION.md) · [Roadmap](ROADMAP.md) |
| Architecture and invariants | [Architecture](ARCHITECTURE.md) · [Protocol](PROTOCOL.md) · [Threat model](THREAT_MODEL.md) · [Frontend contract](FRONTEND_CONTRACT.md) |
| Install, operate or troubleshoot | [Quickstart](QUICKSTART.md) · [Technical workflows](USER_WORKFLOWS.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Broker operations](BROKER_OPERATIONS.md) |
| Decisions and evidence | [ADR index](adr/README.md) · [Review provenance](reviews/README.md) · [Audits](audits/) · [Evidence](evidence/) |

Read the current decision registry through the command in the ADR index. An ADR,
PRD, review or completed task alone does not promote a proposal to project truth.
Implementation sequencing belongs in the programme; revision-bound results
belong in their verification reports, not in repeated status lists.

## Historical plans

- [UI pivot analysis](pivot-ui-only-plan.md) — historical decision intake.
- [Managed-loop plan](architecture-improvement-implementation-plan.md) — predecessor programme.
- [Product snapshot](current-product-and-architecture.md) — baseline at `f998e33`.
- [Initial council synthesis](archive/COUNCIL_SYNTHESIS.md) and
  [24-August product review](archive/agent_commons_product_architecture_review.md)
  — archived inputs, preserved byte-for-byte.

These explain past reasoning; they are not the current backlog. Use the
[implementation programme](agent-platform-implementation-program.md).

## Current direction summaries

The [visual orchestrator PRD](visual_multi_agent_orchestrator_prd.md) and
[navigation plan](visual_orchestrator_plan.md) summarize direction under
`decision.55YN4B2W60Z8HD03WDD0T1V2A9`. They remain subordinate to accepted
decisions and verified implementation. [Proposals](proposals/), [evals](evals/)
and [tutorials](tutorials/) have separate purposes.

## Retention and maintenance

Keep immutable events, manifests, receipts and registered proof retrievable.
Historical artifact retention does not make an old plan current. The
[provenance register](reviews/README.md) locates retained architecture inputs;
registered root review files remain at their recorded paths.

For new verification, retain a bounded result summary, meaningful delta or exact
Git reference. Keep temporary full logs outside the tracked repository. Do not
routinely copy full-source inventories or screenshots; retain them only when
they prove distinct behaviour or satisfy an explicit verification requirement.
Never prune registered evidence or canonical history as cache cleanup.

SQLite projections, views, local caches and build output are derived. Shipped UI
bundles are generated too, but remain required package assets. Update the
relevant current guide when behaviour changes and run `make check`.

With tests and builds stopped, `make clean-preview` previews cleanup and
`make clean` applies it only to `build/`, `dist/`, `.pytest_cache/` and
`.ruff_cache/`. Tracked files are refused; canonical state, evidence, dependency
environments and shipped UI assets are outside that scope.
