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

In an existing Git project, choose one writable state root shared by all of its
worktrees, initialize, and inspect the result:

```bash
cd /path/to/your-project
export AGENT_COMMONS_STATE_ROOT=/absolute/operator-owned/path/agent-commons-state
agent-commons init --integration codex --integration claude
agent-commons --read-only --json support
```

Expected support output includes `"canonical_workspace_available":true`, the
package/Python/platform versions, and whether the state root was explicit. `init`
does not stage or commit anything.

Start the author window, keep its rotating `nonce` private, and export only the
returned session ID:

```bash
agent-commons --json session start \
  --stable-instance-id author-window-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role implementation-author
export AGENT_COMMONS_SESSION_ID='session.returned-by-session-start'
agent-commons doctor
agent-commons orient
agent-commons inbox
```

Ask the author to use `commons-start` and `commons-coordinate`, create/take a
small task, claim only its scope, implement and verify it, register exact
evidence, and submit the current revision for independent review. Then open a
second terminal with a different session and ask the reviewer to use
`commons-start` and `commons-review` without editing the source. A healthy cycle
ends with a current `approved` or `changes_requested` judgment tied to the exact
submitted revision—not with a provider process exit.

The complete copy/paste flow, expected states, and session cleanup are in the
[Quickstart](https://github.com/BejeweledMe/agent-commons/blob/main/docs/QUICKSTART.md).
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
fallback. Share only the secret-free `support`/`doctor` output; prompts,
responses, tool payloads, environment, and raw stdout/stderr are not retained.

## Experimental local broker (manual opt-in)

The broker is disabled by default and remains experimental. Enable it only by
running `broker run` or starting the MCP server with `--enable-runtime`. The
manual core workflow above remains the supported fallback.

Install the optional surface, authenticate provider CLIs yourself, and use one
operator-owned config file outside the delegated workspace:

```bash
python -m pip install 'agent-commons[mcp,observability]'
agent-commons broker profiles --profile-config /absolute/path/runtime.yaml
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/runtime.yaml
```

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

Preflight checks static flags, source/catalog compatibility, and starts no model
work. It is not proof that a provider will call the right tools. A deterministic
real-stdio behavioral canary separately verifies child-session binding, a
bounded MCP write, terminal result references, and canonical finalization.

Broker telemetry distinguishes `process_finished` from
`canonical_finalization_started/completed/failed`, joins canonical state/reason,
and reports `process_canonical_mismatch` plus content-free terminal-tool
counters. Attempts with ambiguous identity fail closed to `needs_operator` and
are never blindly relaunched.

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
