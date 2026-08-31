# Plan: from coordination core to a usable agent platform

**Document category:** `current-navigation`

**Status:** current navigation companion, not the authoritative execution
programme and not proof of delivery. Rewritten under accepted
`decision.55YN4B2W60Z8HD03WDD0T1V2A9`. The executable ordering and acceptance
criteria are in
[`agent-platform-implementation-program.md`](agent-platform-implementation-program.md).

## Decisions that constrain the plan

| Boundary | Canonical decision or ADR |
| --- | --- |
| UI is the primary product surface | `decision.61BDS4NC4GVK9K50R09XK98A07` |
| CLI migrates gradually to compatibility/bootstrap duties | `decision.65J1MEQNYC1GNHJYK9KDBDK49S` |
| Frontend migration is screen-by-screen | `decision.7ZTNWNZN480TKQFG2WNDNA5ZQF` |
| Gallery is the first separate React product screen | `decision.0A252PQN9QH7HZCBF4ZDF8BR8X` |
| Context Pack and Design Package are canonical revisioned entities | `decision.2ASFCETB9SMAXTVQ5PXRFJYRXW` |
| Image preview is limited to authorized current PNG/JPEG artifacts | `decision.50RSN30Q2Q1QW7QYHXX4BZJDHQ` |
| Accepted task truth requires current independent review | `decision.2FFQCGQKQ21VS1MQHNFCQEZWKJ` |
| Broker remains experimental until its evidence gate passes | `decision.558YVVEX7D1BTEBERNBPT14XY2` |
| Private RunEventStore is withdrawn | [ADR 0008](adr/0008-run-observability-store-withdrawn.md) |

Use `uv run agent-commons decision list` to read current decision states. This
table is navigation, not a duplicate registry.

## Architecture path

```text
immutable ledger + manifests
        ↓ replay
typed domain projections and read DTOs
        ↓
Work / Tracker / Gallery

bounded launch request
        ↓
ValidatedLaunchPlan → allowlisted ProviderAdapter
        ↓
Codex or Claude child process
        ↓ scoped terminal MCP result
canonical lifecycle transition
```

The manager, root CLI, server builder and UI context stay composition facades.
The broker owns admission, process group, timeout, cancellation and attempts.
Adapters own provider flags, MCP wiring, sandbox/permission mapping and bounded
result decoding, but never canonical writes or delegation completion.

## Execution graph

The current programme advances by verified vertical slices:

```text
baseline and P0 lifecycle/browser remediation
  → state-root/lock integrity
  → provider adapter + immutable launch plan + canaries + skill projections
  → unified provider availability

in parallel after integrity:
  tracker read models → execution plan → API/SSE → frontend
  Context Pack schema/compiler → exact launch binding → accumulated fan-out
  Design Package domain → Gallery API → board/inspector/feedback

then:
  integrated heterogeneous journey → release evidence and rollback review
```

Dependencies are gates, not labels. A downstream surface may be developed in a
separate claimed path, but it cannot be called shipped until the upstream
canonical and security contract is accepted.

## UI plan

- Keep Work, Tracker and Gallery as separate task-oriented surfaces rather than
  one speculative infinite canvas.
- Show canonical state, derived freshness and private operational state with
  visibly different semantics.
- Preserve EN/RU vocabulary, CSP-safe DOM, keyboard access, typed
  empty/loading/error/refusal states and no-store authenticated reads.
- Keep the old panel available for functionality not yet migrated; remove it
  only after parity is measured and compatibility is deliberately retired.
- Never expose provider argv, executable paths, raw output, MCP JSON or secrets.

## Runtime qualification

Each advertised profile needs three distinct checks:

1. static compatibility preflight without an attempt;
2. provider initialization/authentication probe;
3. scoped behavioral canary that completes through the real terminal MCP path.

A successful process exit without a terminal result is `needs_operator` or a
typed failure, never success. Unsupported budgets, context modes or provider
capabilities refuse before child-session creation and attempt reservation.

## Release and rollback gates

- focused tests give fast feedback, while `make check` remains the only green
  tree definition;
- old-ledger replay, missing/stale projections, crash boundaries,
  idempotency/retry, timeout/cancellation and secret redaction are covered;
- provider canaries pass for every advertised profile;
- `process_canonical_mismatch` remains false;
- exact artifacts and verification are recorded;
- an independent reviewer approves the exact revision;
- rollback is executable and does not delete canonical history.

## Explicitly retired assumptions

The previous plan described an Org DSL canvas, configurable arbitrary agent
tools, full communication transcripts, token/cost telemetry and a private
RunEventStore as if they were committed product contracts. They are not. The
current product builds task-centred visual surfaces over existing canonical and
derived data, keeps provider content untrusted and adds new event families only
through ADR/schema/replay/migration/rollback gates.

See the [PRD summary](visual_multi_agent_orchestrator_prd.md),
[ADR/decision index](adr/README.md), [architecture](ARCHITECTURE.md),
[broker operations](BROKER_OPERATIONS.md) and
[frontend contract](FRONTEND_CONTRACT.md).
