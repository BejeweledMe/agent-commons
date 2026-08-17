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

## Engineering standards

Every rule below was paid for by a real incident in this repository. They are
not preferences; treat them as part of the definition of done.

### The green contract

- `make check` is the one definition of "the tree is green": `ruff check .`,
  `ruff format --check .`, and the full pytest suite, all through
  `uv run --locked`. CI calls exactly this target. Never substitute a subset —
  a narrower local ritual (`ruff check src/ tests/`) once kept CI red for four
  days while every laptop believed the branch was clean.
- `make sync` (`uv sync --locked --extra test`) builds the environment. Do not
  `pip install` into the venv; it is uv-managed and carries no pip.
- A push is finished when CI is green, not when the push command returns.
  Watch it: `gh run list --limit 1`, then `gh run watch <id> --exit-status`.

### Versions live in files, not in memory

- Tool versions come from `uv.lock`, `.python-version`, and `.node-version` —
  never from whatever is on PATH or whatever is newest. Changing a version is
  a deliberate, visible commit that updates the file and survives `make check`.
- `ruff` is pinned (`>=0.16,<0.17`) and `[tool.ruff] required-version` makes a
  stray binary refuse to run rather than format differently. When a floating
  pin and a formatter major met in August 2026, CI and the laptops disagreed
  about which files even existed.
- Node runs the UI behaviour harnesses. In CI its absence is a test failure
  (`tests/test_ci_environment.py`), not a skip: a skipped harness is lost
  coverage that a green matrix would hide.

### Commits and history

- Classic commit messages: a lower-case-free imperative summary line, a body
  that explains why, no `Co-Authored-By` and no AI-attribution trailers.
- One concern per commit; run `make check` before each one.

### The UI asset

- `src/agent_commons/ui/static/index.html` is a single ~7000-line file edited
  by ONE agent at a time. Take the workspace claim
  (`path:src/agent_commons/ui/static/index.html`) before touching it.
- Its design laws — CSP-safe DOM, the two-language string table, the
  vocabulary glossary, canonical values never translated — are written in
  `docs/FRONTEND_CONTRACT.md` and enforced by the test suite. Read the
  contract before editing the asset; the tests will otherwise teach it to you
  one failure at a time.

### Tool caveats that have burned sessions before

- Some environments wrap `grep` with ignore-file behaviour that silently skips
  `index.html`. Use `command grep` and run a positive control (search for a
  string you know is present) before trusting any "no matches" answer.
- Scripts fed to node must travel over stdin, not as an argv element: Linux
  enforces a 128 KiB per-argument ceiling and the embedded STRINGS table is
  already past it.
- Tests must not hardcode pids or assume one is free; GitHub's macOS runners
  keep a live daemon at pid 123. Spawn and reap a child when a dead pid is
  needed.

### The workspace CLI

- Inside this repository use the in-repo CLI — `uv run agent-commons …` or
  `.venv/bin/python -m agent_commons …` — so the code that reads the ledger is
  the code of this checkout. A globally installed `agent-commons` from another
  checkout can be older and misread a newer ledger as broken.
