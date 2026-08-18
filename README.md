# Agent Commons

![Agent Commons hero](https://raw.githubusercontent.com/BejeweledMe/agent-commons/main/assets/agent-commons-hero.png)

Agent Commons is a project-local coordination layer for coding agents. It gives
Codex, Claude Code, and future clients one durable view of tasks, claims,
discussion, exact artifacts, reviews, evidence, decisions, and handoffs without
sharing chat transcripts or replacing Git.

Use it when work moves between agent windows or model families and you need to
know exactly what was done, reviewed, superseded, or accepted. It is not an
issue tracker, generic shell, remote agent host, permission broker, or autonomous
swarm. The immutable file ledger is authoritative; SQLite, Markdown views,
runtime journals, and telemetry are rebuildable or operational projections.

## First value: one manual author/reviewer cycle

The supported first path needs no MCP server, provider API key, or paid model
launch. Clone and install from source:

```bash
git clone https://github.com/BejeweledMe/agent-commons.git
cd agent-commons
python3 -m venv .venv
.venv/bin/python -m pip install .
export PATH="$PWD/.venv/bin:$PATH"
agent-commons --version
```

In an existing Git project, prefer one operator-owned state **base**. Agent
Commons derives an isolated `<base>/workspaces/<workspace_id>` root while linked
worktrees of that workspace continue to share it:

```bash
cd /path/to/your-project
unset AGENT_COMMONS_STATE_ROOT
export AGENT_COMMONS_STATE_BASE=/absolute/operator-owned/path/agent-commons-state
agent-commons init --integration codex --integration claude
agent-commons --read-only --json support --show-paths
```

Expected support output includes `"canonical_workspace_available":true`, the
package/Python/platform versions, `agent_commons_source_sha256`, the selected
configuration source, workspace ID, and ownership match. `AGENT_COMMONS_STATE_ROOT`
remains a backward-compatible **exact workspace root**; reusing it for another
workspace fails before sessions, claims, receipts, runtime, or SQLite are
opened. No state is moved or deleted automatically. The source fingerprint
distinguishes different checkouts that intentionally share one unreleased
package version. `init` does not stage or commit anything.

Reserve the base for its generated `workspaces/` namespace. Keep operator files,
including runtime profile YAML, in a separate operator-owned directory outside
both the base and the delegated workspace; unrelated material in the base is
diagnosed as ambiguous legacy state.

Start the author window, keep its rotating `nonce` private, and export only the
returned session ID:

```bash
eval "$(agent-commons session start \
  --stable-instance-id author-window-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role implementation-author \
  --shell-export zsh)"
agent-commons session current
agent-commons doctor
agent-commons orient
agent-commons inbox
```

The one-time shell export contains the rotating nonce and must remain private;
`session current`, later status, and errors never repeat it and never
auto-select another active session. Compact `orient` and `inbox` are the
default. Use `--verbose` for expanded entities and `--fresh` when a full
canonical replay is required instead of the verified disposable SQLite read
path.

Ask the author to use `commons-start` and `commons-coordinate`, create/take a
small task, claim only its scope, implement and verify it, register exact
evidence, and submit the current revision for independent review. Then open a
second terminal with a different session and ask the reviewer to use
`commons-start` and `commons-review` without editing the source. A healthy cycle
ends with a current `approved` or `changes_requested` judgment tied to the exact
submitted revision—not with a provider process exit.

The complete copy/paste flow, expected states, and session cleanup are in the
[Quickstart](https://github.com/BejeweledMe/agent-commons/blob/main/docs/QUICKSTART.md).
To try the optional runtime from a clean customer repository, follow
[First delegation: one coordinator, one worker](https://github.com/BejeweledMe/agent-commons/blob/main/docs/tutorials/FIRST_DELEGATION.md).
It checks the writable-profile trust boundary before recording work and keeps a
manual two-window path beside every launch blocker.
The larger worked example is
[Build Snake with Codex and Claude Code](https://github.com/BejeweledMe/agent-commons/blob/main/docs/tutorials/CODEX_CLAUDE_SNAKE.md).

## Core concepts

```text
orient → create/take + claim → implement → bind exact evidence
       → complete → submit → independent review → remediate/re-review → accept
```

- Sessions identify one active client window; role/model labels coordinate work
  but do not prove authority.
- Claims are expiring overlap protection, not Git ownership.
- Canonical writes are immutable, idempotent, and guarded by exact revision CAS.
- Changed or invalidated evidence makes dependent judgments stale.
- A completed task is an author report. `accepted` is a distinct governance
  transition that always requires a current independent approved review.
- Provider/model agreement never promotes a finding, decision, or task by
  itself.

## Explicit governance modes

The modes choose how far a team runs the same strict lifecycle; none weakens
integrity rules or creates a hidden acceptance bypass.

- `light`: coordinate a small/reversible change and end honestly at
  `completed`. Review and accepted project truth are optional.
- `standard`: submit the completed revision, obtain an independent current
  review, and use the normal `accepted` boundary.
- `governed`: standard plus reproducible verification, revision-bound evidence,
  explicit decisions/dissent, and operator-controlled acceptance for high-risk
  or externally visible work.

## Diagnose and recover safely

```bash
agent-commons --read-only --json support
agent-commons --read-only doctor
agent-commons orient
agent-commons receipt status
```

Machine-readable failures include `safe_next_actions`. Doctor validates the
ledger and synchronizes the disposable SQLite projection; normal canonical
writes deliberately do not pay that index cost. An in-flight receipt without an
event requires the identical idempotent retry or an explicit audited abandon—it
is never silently deleted.

See
[Troubleshooting](https://github.com/BejeweledMe/agent-commons/blob/main/docs/TROUBLESHOOTING.md)
for diagnostic codes, read-only recovery, state-root checks, and the manual
fallback. Share only the secret-free `support`/`doctor` output. Prompts,
responses, tool payloads, environment, stdout, and successful-run stderr are
not retained. An unsuccessful local provider attempt keeps a private sanitized
stderr tail of at most 4 KiB: absolute paths and complete secret/PII-bearing
lines are redacted, and truncation/redaction are marked explicitly. Review even
that diagnostic before sharing it outside the machine.

## Experimental local broker (manual opt-in)

The broker is disabled by default and remains experimental. Enable it only by
running `broker run` or starting the MCP server with `--enable-runtime`. The
manual core workflow above remains the supported fallback.

Install the optional surface from an exact source checkout, authenticate
provider CLIs yourself, and use one operator-owned config file outside the
delegated workspace:

```bash
cd /path/to/agent-commons
uv tool install -q --force --reinstall-package agent-commons --python 3.13 '.[mcp]'
export PATH="$(uv tool dir --bin):$PATH"
agent-commons --read-only --json support
agent-commons broker profiles --profile-config /absolute/path/runtime.yaml
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/runtime.yaml
```

`--reinstall-package agent-commons` rebuilds the same-version local source and
implies a refresh of that package only, preserving the dependency cache instead
of forcing a noisy cold resolution of every third-party package. The explicit
PATH command replaces the installer warning with a deterministic setup step.
Add the `observability` extra only when using OpenTelemetry export.

```yaml
profiles:
  claude-independent-reviewer:
    executable: /absolute/path/to/claude
    mcp_executable: /absolute/path/to/agent-commons-mcp
    git_executable: /absolute/path/to/git
    permission_mode: dontAsk
limits:
  global_concurrency: 2
  queue_capacity: 8
  queue_wait_seconds: 30
  parent_provider_units: 4
  parent_budget_microusd: 10000000
  provider_concurrency:
    claude: 2
  profile_concurrency:
    claude-independent-reviewer: 1
```

Admission uses the most restrictive applicable operator, provider, profile,
parent, and delegation limit. `provider_units` counts provider-process attempts
across a parent/provider scope; `micro_usd` is an explicit provider-native cap
and is partitioned across retries. The bounded shared queue reports wait/depth
metadata and rejects excess work with backpressure.

### See the whole loop without a provider (demo mode)

To watch a role take a task from Hire to a finished result in a scratch
workspace — without a subscription and without launching any billable process —
add `demo: true` to the top level of the same operator config:

```yaml
demo: true
profiles:
  claude-builder:
    executable: /absolute/path/to/claude   # never invoked in demo mode
    mcp_executable: /absolute/path/to/agent-commons-mcp
    git_executable: /absolute/path/to/git
    permission_mode: acceptEdits
    trusted_workspace: true
```

Then launch the panel with the same gate the real broker uses:

```bash
agent-commons ui --state-root /absolute/path/to/state \
  --enable-launch --profile-config /absolute/path/to/demo-runtime.yaml
```

Demo mode stands in for the provider CLI at the exact runner seam — it is still
the one launch path, not a second one. A demo implementation run records an
honest `delegation.succeeded` whose summary says plainly that no provider ran;
it never fabricates a review or a verification, so those purposes fall to
`needs_operator` instead. Remove `demo: true` to run the real provider.

Preflight checks static flags, source/catalog compatibility, and starts no model
work. A missing MCP helper is reported as
`mcp_executable_unavailable`, before the provider starts. Static success is not
proof that a provider will call the right tools. The explicit paid-provider
compatibility canary described in
[broker operations](https://github.com/BejeweledMe/agent-commons/blob/main/docs/BROKER_OPERATIONS.md)
separately verifies child-session binding, a bounded MCP write, terminal result
references, and canonical finalization.

Broker telemetry distinguishes `process_finished` from
`canonical_finalization_started/completed/failed`, joins canonical state/reason,
and reports `process_canonical_mismatch` plus content-free terminal-tool
counters. Attempts with ambiguous identity fail closed to `needs_operator` and
are never blindly relaunched.

Independent reviewers are instructed to record both the exact review verdict
and the delegation result; prose or process exit alone is invalid. If an
unavailable requester leaves canonical `requested` work behind, an explicitly
operator-authorized `delegation:recover` session can record the distinct
recovery transition. Active work is never recovered this way. Session listing
uses effective expiry, and normal session shutdown refuses outstanding
non-terminal delegations.

The broker stays manual/experimental until macOS and Linux canaries pass, each
supported provider completes 100 consecutive hermetic contract runs, and 20
real local launches finish with zero process/canonical mismatches. Initial SLOs
and the evidence policy are documented in
[Broker operations](https://github.com/BejeweledMe/agent-commons/blob/main/docs/BROKER_OPERATIONS.md).

## Platform and project status

Agent Commons supports CPython 3.11–3.14 on macOS and Linux. Windows is not
supported because durable coordination uses POSIX `fcntl.flock`; unsupported
hosts fail before state mutation with an actionable message.

The project is alpha software under Apache-2.0. See the
[changelog](https://github.com/BejeweledMe/agent-commons/blob/main/CHANGELOG.md),
[support policy](https://github.com/BejeweledMe/agent-commons/blob/main/SUPPORT.md),
and
[contribution guide](https://github.com/BejeweledMe/agent-commons/blob/main/CONTRIBUTING.md).
Agent Commons never authorizes staging, committing, pushing, merging,
deploying, publishing, contacting people, or destructive external actions.

## Documentation

- [Protocol](https://github.com/BejeweledMe/agent-commons/blob/main/docs/PROTOCOL.md)
  and [workflows](https://github.com/BejeweledMe/agent-commons/blob/main/docs/USER_WORKFLOWS.md)
- [Architecture](https://github.com/BejeweledMe/agent-commons/blob/main/docs/ARCHITECTURE.md)
  and [threat model](https://github.com/BejeweledMe/agent-commons/blob/main/docs/THREAT_MODEL.md)
- [Vision](https://github.com/BejeweledMe/agent-commons/blob/main/docs/VISION.md)
  and [roadmap](https://github.com/BejeweledMe/agent-commons/blob/main/docs/ROADMAP.md)
- [Runtime ADR](https://github.com/BejeweledMe/agent-commons/blob/main/docs/adr/0004-optional-local-delegation-runtime.md)
