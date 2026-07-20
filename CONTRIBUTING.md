# Contributing

Agent Commons is built around one invariant: canonical files are authoritative;
indexes, briefs, and client integrations are derived. Changes that weaken this
boundary need an explicit ADR.

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

The package supports Python 3.11 and later. Tests must not require network access.

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

Do not stage, commit, publish, or modify another project as a side effect of a
test or initialization command.
