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
- policy presets for light, standard, and governed projects;
- import/export of summarized records between workspaces;
- protocol conformance fixtures for additional agent clients.

## MVP-2 — optional local service

- a long-running local daemon for notifications and lower-latency queries;
- an MCP adapter over the same service and canonical schemas;
- authenticated local client registration;
- observability and workspace health metrics;
- compatibility-preserving schema migrations.

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
