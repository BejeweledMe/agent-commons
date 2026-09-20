# Contributing

Agent Commons is built around one invariant: canonical files are authoritative;
indexes, briefs, and client integrations are derived. Changes that weaken this
boundary need an explicit ADR. Every rule below was paid for by a real incident
in this repository; treat them as part of the definition of done.

## Development setup

```bash
make sync
make check
uv run --locked python -m build
```

`make sync` (`uv sync --locked --extra test`) creates the locked uv-managed
environment. Do not install packages into it with pip; it carries no pip. The
full `test` extra includes MCP because the repository suite crosses real MCP
stdio. CI runs the full suite from clean environments on macOS/Linux with
Python 3.11–3.14, then installs and smoke-tests the built wheel. Tests must not
require network access or provider credentials.

## The green contract

- `make check` is the one definition of "the tree is green": the Node version
  check, `ruff check .`, `ruff format --check .`, both frontend `node --test`
  harnesses and the full pytest suite, all through `uv run --locked`. CI calls
  exactly this target. Never substitute a subset — a narrower local ritual once
  kept CI red for four days while every laptop believed the branch was clean.
- A push is finished when CI is green, not when the push command returns.
  Watch it: `gh run list --limit 1`, then `gh run watch <id> --exit-status`.

## Versions live in files, not in memory

- Tool versions come from `uv.lock`, `.python-version` and `.node-version`,
  never from whatever is on PATH or whatever is newest. Changing a version is a
  deliberate, visible commit that survives `make check`.
- `ruff` is pinned and `[tool.ruff] required-version` makes a stray binary
  refuse to run rather than format differently.
- Node runs the UI behaviour harnesses. In CI its absence is a test failure
  (`tests/test_ci_environment.py`), not a skip.

## Frontend build and the asset integrator

- The product UI is `frontend/work` (React, Vite, TypeScript); the design
  gallery is `frontend/gallery`. Both build into
  `src/agent_commons/ui/static/{work,gallery}/` with `npm ci && npm run build`
  on the Node from `.node-version`. The hashed bundles are committed and shipped
  as wheel package data so that installing the product never requires npm.
- Exactly one hashed bundle per application is tracked at any time. One asset
  integrator rebuilds the bundle once per wave and holds the workspace claim
  `path:src/agent_commons/ui/static/work` while doing so; everyone else changes
  only sources and tests.
- Frontend checks: `node --test frontend/work/tests/*.test.mjs`,
  `npx tsc --noEmit -p frontend/work`, and the Gallery equivalents.

## Commits and history

- Classic commit messages: an imperative summary line, a body that explains
  why, no `Co-Authored-By` and no AI-attribution trailers.
- One concern per commit; run `make check` before each one.
- Every merged pull request adds an entry under `## Unreleased` in
  [CHANGELOG.md](CHANGELOG.md), in product language. Test counts never go
  there; they live in evidence manifests.

## Documentation rules

- Each fact about the product has one owning document; others link to it. The
  owners are listed in [docs/README.md](docs/README.md). Positioning and the
  glossary belong to [docs/PRODUCT.md](docs/PRODUCT.md); contracts to
  `docs/ARCHITECTURE.md`, `docs/PROTOCOL.md`, `docs/THREAT_MODEL.md` and
  `docs/FRONTEND_CONTRACT.md`; the current backlog to `docs/plans/`.
- A plan carries a seven-line status header (`Status`, `Owner`, `Opened` /
  `Last verified`, `Contract` / `Decision`, `Source boundary`, `Supersedes` /
  `Superseded by`, `Archive trigger`). The header is the only place a plan
  states its status; body checkboxes are progress, not truth.
- A completed or superseded plan moves to `docs/archive/plans/` in the same
  commit that makes it so, byte-for-byte plus the stamp, with inbound links
  repointed. Archive is a location, not a label.
- Language: contracts and ADRs in English; plans, audits and handoffs in
  Russian; user guides in both. Canonical values are never translated.
- `tests/contract/test_documentation_map.py` checks that every Markdown link
  in `docs/` and at the repository root resolves, that the ADR index covers
  every ADR, and that archived plans carry their stamp.

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
