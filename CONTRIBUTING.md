# Contributing

Agent Commons is built around one invariant: canonical files are authoritative;
indexes, briefs, and client integrations are derived. Changes that weaken this
boundary need an explicit ADR.

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
python -m build
```

The full `test` extra includes MCP because the repository suite crosses real
MCP stdio. Use `test-core` only for a deliberately core-only environment and
`test-mcp` for the optional surface. CI runs the full suite from clean
environments on macOS/Linux with Python 3.11, 3.12, 3.13, and 3.14, then installs and
smoke-tests the built wheel. Tests must not require network access or provider
credentials.

## Change boundaries

- `core`, `storage`, and `index` remain domain-neutral.
- Universal collaboration lifecycle belongs in `domain` and `services`.
- Canonical writes pass schema, semantic, lifecycle, security, idempotency, and
  concurrency checks through the service layer.
- SQLite and Markdown views must be fully rebuildable.
- Provider integrations share one protocol contract; provider-specific glue must
  not fork business semantics.
- Existing project instructions outside managed markers are operator-owned.
- Tests must use synthetic data and must not import external runtime ledgers.

## Required verification

Behavior changes need focused tests plus the complete suite. Storage or lifecycle
changes also need crash/retry and concurrent-writer coverage. Integration changes
must verify both Codex and Claude Code installations and wheel packaging.
Runtime changes must keep the deterministic behavioral canary green and add
content-free telemetry assertions when they cross a lifecycle boundary.

Do not stage, commit, publish, or modify another project as a side effect of a
test or initialization command.
