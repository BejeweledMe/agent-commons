<!-- agent-commons:managed:start -->
## Agent Commons

This project uses the shared Agent Commons workspace. Before starting or
resuming work, read `.agent-commons/ONBOARDING.md` and follow its session,
orientation, coordination, review, truth-promotion, and handoff contract.

Use the installed `commons-start` skill at the beginning of each window. The
paired `commons-coordinate`, `commons-share`, `commons-review`, `commons-record`,
and `commons-handoff` skills provide the supported workflows.

That onboarding document is the single canonical guidance contract for every
agent client. This managed block adds no client-specific workflow policy.
<!-- agent-commons:managed:end -->

## Where the rules live

- **Engineering standards** — `make check` as the one green contract, versions
  pinned in files, commit style, the frontend rebuild path, the single asset
  integrator per wave, and the documentation rules — are written once in
  [CONTRIBUTING.md](CONTRIBUTING.md).
- **Frontend laws** for the Work and Gallery applications are in
  [docs/FRONTEND_CONTRACT.md](docs/FRONTEND_CONTRACT.md).
- **What the product is and what its words mean** is in
  [docs/PRODUCT.md](docs/PRODUCT.md); the current plans are in
  [docs/plans/](docs/plans/); the documentation map with the owner of every
  topic is [docs/README.md](docs/README.md).

This file deliberately does not duplicate them.

## Caveats that have burned agent sessions before

- Inside this repository use the in-repo CLI — `uv run agent-commons …` or
  `.venv/bin/python -m agent_commons …` — so the code that reads the ledger is
  the code of this checkout. A globally installed `agent-commons` from another
  checkout can be older and misread a newer ledger as broken.
- One writable worker per checkout. Take the workspace claim for the paths you
  edit; while another session's review runs, keep the checkout quiet: no file
  edits and no ledger writes until the review finalizes.
- Some environments wrap `grep` with ignore-file behaviour that silently skips
  large or generated files. Use `command grep` and run a positive control
  (search for a string you know is present) before trusting any "no matches".
- Scripts fed to node must travel over stdin, not as an argv element: Linux
  enforces a 128 KiB per-argument ceiling. A stdin program finds its first user
  argument at `process.argv[2]`.
- Tests must never assume a hardcoded pid is dead or free: GitHub's macOS
  runners keep a live daemon at pid 123. Where deadness matters, spawn and reap
  a child and use its pid.
- After any change under `src/agent_commons/**`, reinstall the uv tool the
  workers' MCP comes from (`rm -rf build` first; setuptools copies a stale
  `build/lib` tree into the wheel) and re-canary the profile that runs next.
