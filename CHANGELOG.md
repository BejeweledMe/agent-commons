# Changelog

All notable changes are documented here. This project follows Semantic
Versioning once a stable release line is declared.

## Unreleased

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
