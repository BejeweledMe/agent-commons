# Changelog

All notable changes are documented here. This project follows Semantic
Versioning once a stable release line is declared.

## Unreleased

- Added workspace-namespaced state bases and fail-closed exact-root ownership
  checks before operational registries open, with source-aware/path-opt-in
  support diagnostics and non-destructive legacy compatibility. Legacy receipt
  migration proves ownership only after no-follow path, strict JSON,
  canonical-byte, packaged-schema, and typed workspace-ID validation.
- Added compact bounded `orient`/`inbox` defaults backed by a verified
  incremental SQLite read path, with canonical `--fresh` and expanded
  `--verbose` modes.
- Added `session current`, one-time zsh/bash/fish session exports, and
  field-specific typed-reference diagnostics without session auto-selection or
  nonce repetition.
- Added authenticated task-scoped request/progress/blocker/reply/ack operations.
  Full message content remains private operational state; canonical
  `input_needed`/`resumed` events use fixed maintainer-defined text.
- Added the first parent-to-child control slice: bounded guidance and safe
  checkpoint operations with exactly-once child acknowledgement, exact
  delegation binding, and a disable-able MCP surface.
- Added a privacy-safe 25-case orchestration eval catalog: executable P0 cases,
  explicit planned cases, and unsupported non-passing cases share one bounded
  result schema without prompts, reasoning, or raw provider output.
- Added an MCP-specific unavailable-executable preflight diagnostic, safe
  installed-source fingerprints in support/preflight output, and a cache-safe
  uv source reinstall path.
- Documented a package-scoped uv refresh for same-version source reinstalls,
  avoiding noisy cold dependency-index scans while replacing stale local
  wheels.
- Added an explicit isolated real-Claude compatibility canary that reports
  provider/model/catalog metadata and fails unless the terminal MCP result and
  canonical delegation agree.
- Added capability-gated recovery for requested delegations whose requester is
  unavailable, effective session-expiry reporting, requester shutdown guards,
  pre-admission child cleanup, and visible foreign-owner reconcile diagnostics.
- Made independent-review terminal calls explicit and stopped diagnostics from
  claiming no tool was called when terminal-tool audit is unavailable.
- Hardened immutable-ledger integrity, correction replay, evidence bindings,
  worker-scoped reads, and shared state-root propagation.
- Added canonical-finalization telemetry, terminal-tool audit counters,
  actionable diagnostics, operator-owned broker caps, aggregate budgets,
  bounded admission queues, and backpressure.
- Deferred the optional SQLite projection out of the canonical write path and
  added deterministic replay work counters and a scale benchmark.
- Declared macOS/Linux support, Apache-2.0 licensing, CI, package metadata, and
  experimental broker release criteria.

## 0.1.0

- Initial experimental file-ledger and local coordination release.
