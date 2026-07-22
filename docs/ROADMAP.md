# Roadmap

Agent Commons deliberately starts as a reliable shared-filesystem protocol. The
first release proves the collaboration model before introducing a server or a
public extension ABI.

## MVP-0 — local shared workspace

- immutable, schema-validated events and artifact manifests;
- explicit sessions, tasks, hierarchical claims, discussions, reviews,
  verifications, findings, decisions, and handoffs;
- security scanning before durable writes;
- rebuildable SQLite/WAL query projection and Markdown views;
- safe project initialization for Codex and Claude Code;
- one CLI business layer and paired project-local skills;
- doctor, recovery, idempotency, concurrency, and end-to-end tests.

## MVP-1 — daily ergonomics

- per-session inbox cursors and acknowledgement state;
- duplicate-work suggestions and stale-work cleanup;
- richer dependency and decision graph views;
- machine-enforced configuration presets for the documented light, standard,
  and governed operating modes;
- import/export of summarized records between workspaces;
- protocol conformance fixtures for additional agent clients.

## MVP-2 — optional local delegation runtime

MVP-2 removes the need to prompt every agent window manually while preserving the
file-ledger core as a complete standalone mode. Its governing contract is
[ADR 0004](adr/0004-optional-local-delegation-runtime.md).

- a canonical, exact-revision-bound `delegation` aggregate with bounded lineage,
  time, attempts, concurrency, and provider budget;
- a long-running same-host broker for notification, live status, conservative
  crash recovery, cancellation, and lower-latency queries;
- one shared MCP adapter over `CommonsManager` and the broker, never a parallel
  business-logic or generic-shell write path;
- authenticated local client grants and operator-allowlisted Codex and Claude
  provider profiles;
- one writable worker per checkout scope, with explicit claims and
  operator-provisioned worktrees when isolation is required;
- metadata-only local observability, with optional OpenTelemetry export and no
  prompts, reasoning, transcripts, secrets, or raw process output;
- compatibility-preserving canonical migrations and versioned, disposable
  operational state.

Delivery is staged and feature-gated:

1. delegation schema, replay, manager, CLI, projection, and fake-runner contract;
2. authenticated bounded MCP tools with CLI/manager parity;
3. one quiescent-checkout, read-only Codex-to-Claude review and reverse-direction
   verification slices;
4. writable delegation with checkout leases, claims, crash injection,
   cancellation, and budget enforcement;
5. local status/watch UX, optional OpenTelemetry export, and an optional AHP
   adapter after its value and compatibility are independently validated.

The long-running broker stage must also define an authenticated operator
adjudication path for `active` or `input_needed` work whose requester becomes
unavailable. It may classify that work only after provider termination and
attempt identity are proven; the requested-only `delegation.recovered` path
must never be widened to cover a possibly live process.

Every stage must be usable or removable independently. Deterministic CI uses fake
runners; real-provider tests are explicit and opt-in so credentials and network
access are not build prerequisites.

## Later — remote teams

Remote and multi-host operation requires a different trust and coordination
boundary: authenticated principals, authorization, server-side transactions,
distributed leases, retention policy, encryption, and administrative audit.
Those capabilities will not be simulated by treating local session metadata as
security credentials.

## Extension policy

The internal schema registry is extensible, but a stable public plugin ABI is
deferred until at least two substantial external domain packs validate the
boundary. Domain-specific workflows must remain optional and must not change the
meaning of the universal core entities.
