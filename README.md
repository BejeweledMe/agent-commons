# Agent Commons

![Agent Commons hero](assets/agent-commons-hero.png)

Agent Commons is a project-local coordination layer for coding agents. Codex,
Claude Code, and future clients share one durable view of tasks, claims,
discussions, artifacts, exact-revision reviews, decisions, and handoffs without
sharing chat transcripts or inventing a second source of truth beside Git.

The CLI and `CommonsManager` are the business boundary. Immutable files are
authoritative; SQLite, Markdown views, runtime status, and telemetry are
rebuildable or disposable projections. An optional experimental broker can
launch one already-recorded, bounded Codex or Claude delegation through a fixed
profile. It is not a general shell, remote agent host, or autonomous swarm.

## Five-minute start

Install the CLI, initialize an existing Git project, and confirm the environment:

```bash
uv tool install .
cd /path/to/your-project
agent-commons init --integration codex --integration claude
agent-commons --version
agent-commons --read-only --json support
```

Start one explicit writer session. Keep the returned rotating `nonce` private;
export only the `session_id`:

```bash
agent-commons --json session start \
  --stable-instance-id codex-window-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role implementation-author
export AGENT_COMMONS_SESSION_ID='session.returned-by-session-start'

agent-commons doctor
agent-commons orient
agent-commons inbox
```

Open Codex in that project and ask it to use `commons-start` before creating or
taking work. Open Claude Code with a different Agent Commons session and ask it
to review the exact submitted revision. The complete copy-paste walkthrough is
[Build Snake with Codex and Claude Code](docs/tutorials/CODEX_CLAUDE_SNAKE.md).
For every command and expected output in the initial setup, use the
[Quickstart](docs/QUICKSTART.md).

The core loop is deliberately small:

```text
orient -> create/take + claim -> implement -> register exact evidence
       -> submit -> independent review -> remediate/re-review -> accept
```

A successful test run is author evidence, not independent verification. A
provider process exit is not approval. A changed target revision makes the old
review stale, and the work author cannot independently accept their own work.

## Optional automatic Codex ↔ Claude handoff

Install the optional MCP/observability extras from the Agent Commons source
checkout, return to your project, and authenticate the provider CLIs yourself.
Agent Commons never supplies API credentials or changes how a provider CLI is
billed:

```bash
cd /path/to/agent-commons
uv tool install --force '.[mcp,observability]'
cd /path/to/your-project
codex --version
claude --version
agent-commons broker profiles \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

`broker preflight` is credential-free and non-consuming: it runs provider
`--help`, validates the allowlisted flags, starts the generated MCP command in
preflight mode, and does not create a delegation attempt or start model work.
Run it after provider or Agent Commons upgrades.

For a local Claude CLI already authenticated through a Claude Code subscription,
the tutorial uses this launch budget:

```json
{"unit":"provider_units","limit":1}
```

One `provider_units` unit means one provider-process attempt. It is not a dollar
or token cap, and Agent Commons does not silently fall back to API/usage credits.
Use `micro_usd` only when you explicitly choose a provider-native monetary cap
for a billable provider mode. Do not reuse the old `$0.50` tutorial value: field
testing showed that it can terminate after analysis but before the canonical
review outcome is recorded. Reserve enough room for finalization and choose the
monetary limit from the current provider/model pricing you actually use.

The safe automated default is `claude-independent-reviewer`: `dontAsk`, fixed
allowed MCP tools, no native filesystem/edit/Bash/web/subagent tools, and a
quiescent exact subject. Writable builders and current Codex runners are
trusted-workspace-only and need explicit operator opt-in plus external OS
isolation for untrusted content.

To let both interactive clients see the same manager-space, configure the same
project-scoped stdio server. Claude Code can create its project entry directly:

```bash
claude mcp add --scope project agent-commons -- \
  agent-commons-mcp --repo . --enable-runtime --telemetry local \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

The equivalent Codex project configuration is:

```toml
[mcp_servers.agent_commons]
command = "agent-commons-mcp"
args = ["--repo", ".", "--enable-runtime", "--telemetry", "local", "--profile-config", "/absolute/path/to/agent-commons-profiles.yaml"]
env_vars = ["AGENT_COMMONS_SESSION_ID"]
```

Start a distinct Agent Commons session for each client and restart the client so
its MCP child inherits that session ID. Never put the rotating nonce into MCP
configuration. The broker creates another distinct child session for every
launched provider worker.

Machine-specific provider, MCP, and Git executable paths belong in a strict
operator-owned profile file outside the delegated workspace:

```yaml
profiles:
  claude-independent-reviewer:
    executable: /absolute/path/to/claude
    mcp_executable: /absolute/path/to/agent-commons-mcp
    git_executable: /absolute/path/to/git
    permission_mode: dontAsk
```

The file must be owned by the operator or root and not group/world writable.
Delegations may select only one of four closed profile IDs; they cannot inject
argv, environment variables, commands, credentials, or prompts.

## Diagnose without exposing provider output

Raw prompts, responses, reasoning, environment, paths, tool payloads, and
stdout/stderr are never persisted. Failed attempts retain a closed diagnostic
code and a maintainer-authored hint:

```bash
agent-commons --read-only --json support
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
agent-commons --read-only broker attempts --diagnostic
agent-commons delegation list
agent-commons doctor
```

See [Troubleshooting](docs/TROUBLESHOOTING.md) for the safe diagnostic codes,
recovery order, read-only/state-root inspection, and the manual two-window
fallback. `--state-root /operator/owned/path` makes operational state explicit;
global `--read-only` guarantees inspection does not create cache, index,
session, claim, or receipt state.

## Receipt recovery

After an upgrade, fresh clone, first visit to a branch, or Git merge, inspect and
reconcile the current checkout with an active session:

```bash
agent-commons receipt status
agent-commons receipt reconcile
agent-commons doctor
```

An in-flight receipt without an event still requires the identical idempotent
retry or an explicit audited abandon operation. Reconcile never silently deletes
it. The crash, non-shrinking anchor, tombstone, migration, worktree, and rollback
contract is in [ADR 0003](docs/adr/0003-ledger-derived-checkout-aware-receipt-recovery.md).

## Documentation map

- [Quickstart](docs/QUICKSTART.md) — install, initialize, first task, and first review.
- [Snake tutorial](docs/tutorials/CODEX_CLAUDE_SNAKE.md) — concrete Codex author + Claude reviewer flow.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — support bundle, diagnostics, preflight, and recovery.
- [Product vision](docs/VISION.md) and [roadmap](docs/ROADMAP.md) — why this exists and what remains.
- [Protocol](docs/PROTOCOL.md) and [user workflows](docs/USER_WORKFLOWS.md) — durable operating rules.
- [Architecture](docs/ARCHITECTURE.md) and [threat model](docs/THREAT_MODEL.md) — boundaries and risks.
- [ADR 0004](docs/adr/0004-optional-local-delegation-runtime.md) — broker, MCP, privacy, and observability.
- [Contributing](CONTRIBUTING.md) — development invariants and checks.

## Current boundary

Agent Commons currently coordinates processes sharing one local project
filesystem. The broker is optional and experimental: no remote authentication,
cross-machine synchronization, daemon, generic command tool, live IDE-pane
takeover, or provider-session reattachment. Ambiguous restarts fail closed to
`needs_operator`; only requested, unlaunched work can be cancelled canonically.

Agent Commons never authorizes staging, committing, pushing, merging, deploying,
publishing, messaging people, or other external/destructive actions. Disable the
runtime by removing `--enable-runtime`; the canonical CLI workflow remains
available.
