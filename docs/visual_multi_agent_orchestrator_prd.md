# PRD: Agent Commons as a visual heterogeneous-agent workspace

**Document category:** `current-product-direction`

**Status:** current product-direction summary, not a release specification.
Rewritten in place under accepted
`decision.55YN4B2W60Z8HD03WDD0T1V2A9` because the previous PRD contradicted the
UI pivot and the canonical/derived boundary. Shipped behaviour is proved by the
current source, schemas, tests and `make check`; implementation sequencing lives
in the [agent platform programme](agent-platform-implementation-program.md).

## Product promise

Agent Commons is a local workspace in which a person coordinates Codex and
Claude roles over one repository without treating provider output as truth. It
makes work, ownership, dependencies, evidence, review and decisions visible and
recoverable across otherwise isolated agent windows.

The product is not an agent chat transcript, a free-form process launcher, or an
autonomous company. It is a control plane for bounded heterogeneous work with a
human operator and explicit truth promotion.

## Users and jobs

- An operator creates or selects a project, sees what is ready or blocked,
  chooses a role/provider/profile and starts bounded work.
- A coordinator decomposes work, records dependencies and prevents writable
  path collisions with temporary claims.
- A Codex or Claude builder receives one exact task and scoped tools in a fresh
  child session; provider-specific argv and environment never become UI data.
- An independent reviewer inspects the exact registered revision read-only.
- A designer publishes revision-bound visual artifacts and, after the Gallery
  vertical lands, an ordered Design Package with provenance and feedback.

## Product surfaces

### Work

Work is the primary entry surface. It guides provider setup, role selection and
bounded launch while returning actionable typed refusals for missing
configuration, authentication, capability or operator action. The legacy panel
remains a compatibility and recovery surface during screen-by-screen migration
under `decision.7ZTNWNZN480TKQFG2WNDNA5ZQF`.

### Tracker

The tracker assembles existing task, dependency, delegation, attempt, attention
and SSE data into an honest task DAG, readiness state, run timeline and human
attention queue. It may show phases, timestamps, duration, snapshot freshness
and advisory critical path. It must not invent percentage complete, ETA, token
count or cost.

### Context

Fresh is the default: a new child session receives no previous transcript,
hidden reasoning or provider resume state. Accumulated context means an exact,
bounded, revisioned Context Pack containing summary, facts, decision references,
source references and open questions. Two roles may receive the same frozen
baseline without sharing mutable authority or working state. Canonical Context
and Design Packages are authorized by
`decision.2ASFCETB9SMAXTVQ5PXRFJYRXW`.

### Design Gallery

Gallery is a separate React surface and the first accepted screen migration
(`decision.0A252PQN9QH7HZCBF4ZDF8BR8X`). A real Design Package provides ordered
screen bindings to exact artifact revisions, producer/task provenance and safe
PNG/JPEG previews. Feedback uses the existing revision-bound discussion model.
Mock frontend screens must never be presented as project data.

## Trust and data model

```text
immutable project truth     .agent-commons/events + manifests
        ↓ replay
typed domain projections and read DTOs
        ↓
Work / Tracker / Gallery

private operational state   sessions, attempts, receipts and bounded telemetry
provider process            untrusted output; never an acceptance authority
```

The immutable ledger is the sole canonical project record. UI reads typed DTOs
and writes through the existing manager/service boundary. Provider adapters
cannot write canonical events or complete delegations. Process exit is not
canonical success: only the scoped terminal MCP path plus valid lifecycle
transition can produce it. Prompts, transcripts, reasoning, raw tool arguments,
raw stderr and credentials are excluded from canonical and UI persistence.

Task completion, independent review, verification and owner acceptance remain
different states. `task.accepted` requires a current independent approval under
`decision.2FFQCGQKQ21VS1MQHNFCQEZWKJ`.

## Current honest boundary

Coordination core, roles, tasks, dependencies, claims, reviews, evidence,
staleness, lifecycle, the local panel, a bounded Codex/Claude broker and safe
image preview exist. Provider adapters, Context Pack C1 and tracker read models
are active implementation slices in the current checkout.

The complete tracker surface, exact launch binding for accumulated context,
canonical Design Packages, data-backed Gallery and end-to-end heterogeneous
journey are not release-complete until their code, tests, canaries, rollback
evidence and exact independent review land. Resume remains unavailable until
provider session, child session, attempt and checkpoint identities can be
reconciled safely.

## Non-goals

- arbitrary argv, environment, workspace provider discovery or dynamic plugins;
- a second authoritative RunEventStore or provider transcript archive;
- hidden fallback between providers or capabilities;
- unrestricted shared mutable memory between roles;
- SVG/HTML preview, visual source editing or demo data presented as real;
- automatic acceptance, self-review or authority expansion through context.

See the [documentation map](README.md), [ADR/decision index](adr/README.md),
[architecture](ARCHITECTURE.md), [threat model](THREAT_MODEL.md) and
[frontend contract](FRONTEND_CONTRACT.md) for the governing boundaries.
