# Agent Commons

![Agent Commons hero](assets/agent-commons-hero.png)

Agent Commons is a project-local shared manager-space for heterogeneous coding
agents. Codex, Claude Code, and future integrations use one durable workspace to
understand the project, divide work, exchange ideas, request independent review,
preserve disagreement, hand work off, and promote verified conclusions into the
effective project truth.

The shared workspace is not a group-chat transcript or a replacement for Git.
The CLI and manager remain the single business layer; immutable files are
authoritative, while indexes and Markdown views are rebuildable projections. An
optional local broker can execute one already-recorded bounded delegation through
a fixed Codex or Claude profile. It is not a general shell or autonomous swarm.

The motivation, intended collaboration model, and definition of success are
described in the [product vision](docs/VISION.md).

## Status

This repository is a clean universal implementation derived from lessons learned
in a production multi-agent collaboration prototype. It contains only generic
software-collaboration semantics and no copied runtime ledger data.

The local shared-workspace MVP is implemented. Canonical delegation, an optional
stdio MCP adapter, and the local allowlisted broker are an experimental next
slice and are disabled unless installed/configured. The product contract is
documented in [Architecture](docs/ARCHITECTURE.md) and
[Council synthesis](docs/COUNCIL_SYNTHESIS.md). The staged product boundary is
in the [Roadmap](docs/ROADMAP.md).

Key architectural choices are recorded as ADRs, beginning with the
[file-ledger/SQLite boundary](docs/adr/0001-file-ledger-with-sqlite-projection.md)
and [explicit truth promotion](docs/adr/0002-explicit-truth-promotion.md). Receipt
portability and linked-worktree recovery are specified by the
[checkout-aware recovery ADR](docs/adr/0003-ledger-derived-checkout-aware-receipt-recovery.md).
The broker, MCP, crash, privacy, observability, and rollback contract is in
[ADR 0004](docs/adr/0004-optional-local-delegation-runtime.md).
Development invariants and checks are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Install and initialize

```bash
uv tool install .
cd /path/to/project
agent-commons init --integration codex --integration claude
agent-commons --json session start \
  --stable-instance-id codex-window-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role backend-builder
```

For the optional shared MCP and metadata-only OpenTelemetry adapter, install the
extras and separately install/authenticate the provider CLIs you intend to run:

```bash
uv tool install --force '.[mcp,observability]'
codex --version
claude --version
agent-commons broker profiles
```

The core `agent-commons` CLI is required for both automatic and manual Commons
workflows. If that command is unavailable, install and initialize it before
continuing. If the core CLI works but `broker`, a matching profile, or a provider
CLI is unavailable, use the [manual two-window fallback](#manual-two-window-fallback);
do not invent a command, environment, or prompt payload for the runtime.

The defaults execute the `codex`, `claude`, and `agent-commons-mcp` basenames
from the broker's safe `PATH`. A machine-specific strict YAML profile file may
replace those executable paths; it must be outside the delegated workspace,
owned by the operator or root, and not group/world writable. Pass it to the
operator-controlled MCP server or `broker` command with `--profile-config`. A
delegation or MCP tool call can select only one of the four closed profile IDs
and can never supply argv or environment overrides.

The safe automated default is a Claude independent reviewer with an immutable
worker-scoped MCP. Writable builders and current Codex CLI runners are
trusted-workspace-only: they require explicit operator profile opt-in and an
externally OS-isolated worktree for untrusted content. A worktree or provider
permission prompt by itself is not host isolation.

For example, a machine where Claude or the MCP executable is not on `PATH` may
use an operator-owned file outside the delegated workspace like this
(independent reviewers must keep the locked `dontAsk` mode):

```yaml
profiles:
  claude-independent-reviewer:
    executable: /absolute/path/to/claude
    mcp_executable: /absolute/path/to/agent-commons-mcp
    permission_mode: dontAsk
    max_budget_microusd: 1000000
```

Inspect it before launch with
`agent-commons broker profiles --profile-config /path/to/profiles.yaml` and pass
the same file when starting the MCP server or running `broker run`. Unknown
fields and symlinked config files fail closed. `micro_usd` requires a profile
with a provider-native monetary cap (currently Claude). `provider_units` is a
coarse launch budget: one unit equals one provider-process attempt and
`max_attempts` must not exceed the limit. `tokens` and mismatched unit/profile
combinations fail before reservation or spawn.

`session start` returns a `session_id` and a rotating private `nonce`. Keep the
nonce for heartbeat/end operations and select the session in later commands:

```bash
export AGENT_COMMONS_SESSION_ID='session.returned-by-session-start'
agent-commons doctor
agent-commons orient
agent-commons inbox
```

Before a broker launch, heartbeat the parent session with a TTL covering the
delegation's `wall_time_seconds` plus the broker's 60-second finalization
margin. The broker fails before reservation if that lifetime is unavailable;
use `agent-commons session heartbeat --help` and keep the rotated nonce private.

The generated `.agent-commons/ONBOARDING.md` is the single entrypoint to give a
new agent window. `AGENTS.md` and `CLAUDE.md` receive small managed blocks, and
the same seven workflow skills, including `commons-delegate`, are installed below `.agents/skills/` and
`.claude/skills/`. Existing project-authored instructions and locally modified
skills fail closed unless replacement is explicitly requested.

Use `--idempotency-key` with a stable operation-specific value for canonical
writes that may be retried after a timeout or process failure. Use each command's
`--help` for its exact arguments; the CLI intentionally has no hidden write path.

### Connect the same MCP to Codex and Claude

Configure one project-scoped stdio server. Claude Code can create its project
entry directly:

```bash
claude mcp add --scope project agent-commons -- \
  agent-commons-mcp --repo . --enable-runtime --telemetry local
```

For a current Codex CLI, either use `codex mcp add` or place the equivalent
project configuration in `.codex/config.toml`:

```toml
[mcp_servers.agent_commons]
command = "agent-commons-mcp"
args = ["--repo", ".", "--enable-runtime", "--telemetry", "local"]
env_vars = ["AGENT_COMMONS_SESSION_ID"]
```

Older Codex builds may expose only `codex mcp` (running Codex itself as a server)
and must be upgraded before using `codex mcp add`; the TOML form remains explicit
and inspectable. Start a distinct Agent Commons session, export its ID, and then
start/restart the client so its MCP subprocess inherits that identity. Never put
the rotating session nonce in MCP configuration. The broker replaces the parent
identity with a newly registered child session for every launched worker.
This first rollout trusts local stdio processes running as the same operating-
system user. It has no remote authentication endpoint: do not expose the MCP or
broker over a network transport.

## Worked example: build Snake with Codex and Claude Code

This example starts from an empty project after the optional setup above. Codex
builds the game and invokes a new headless Claude Code reviewer through the
broker. It does not wake or control an already-open Claude VS Code pane; provider
processes started by the broker are separate bounded sessions.

### 1. Create and initialize the project

```bash
mkdir browser-snake
cd browser-snake
git init
agent-commons init --integration codex --integration claude
```

Add the project MCP configuration from the previous section. The generated
`.agent-commons/ONBOARDING.md` is the canonical operating contract for every
parent and child session.

### 2. Ask Codex to build the game

Register the parent session, export its ID, then run Codex and paste this prompt:

```bash
agent-commons --json session start \
  --stable-instance-id snake-codex-author-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role implementation-author
export AGENT_COMMONS_SESSION_ID='session.from-the-command-above'
codex
```

```text
Use $commons-start, $commons-coordinate, $commons-record, $commons-review,
$commons-delegate, and $commons-handoff as applicable. Follow
.agent-commons/ONBOARDING.md. Use the already selected Codex session; do not
register or borrow another one. Run doctor, orient, and inbox, then create, take,
and narrowly claim a task to build a small browser Snake game.

Requirements: plain HTML/CSS/JavaScript, arrow-key and WASD controls, score,
restart after game over, and a short README explaining how to run it locally.
Add focused automated checks where practical. Do not commit, push, deploy, or
overwrite unrelated work.

When ready, run the checks, register exact artifacts/evidence, complete and
submit the task, and request an independent review on that exact submitted task
revision. Then request an `independent_review` delegation targeting the review
request's exact revision with profile `claude-independent-reviewer`, max_depth 0,
wall time 1800 seconds, one attempt, one concurrent worker, and a 500000
micro-USD budget. Use different stable idempotency keys for review creation,
delegation creation, and broker launch. Invoke `commons_run_delegation`; do not
ask me to relay a prompt to Claude and do not pass a command, env, executable, or
raw prompt to the broker.

After the child exits, inspect the canonical review and delegation result. A
process exit is not approval. If the result is ambiguous, leave it
`needs_operator`; never blind-retry after canonical start. Report durable IDs
and current states only.
```

Codex should leave the task in `review`. Its own successful tests are author
evidence, not independent verification. Once it has registered the exact
subject artifacts and requested review, all writers must stop changing that
shared checkout until the review is terminal. If quiescence cannot be
guaranteed, give the reviewer an operator-provisioned worktree or immutable
snapshot whose bytes match the registered subject instead.

The equivalent bounded CLI shape is explicit. `REVIEW_REVISION` is the target
revision used when creating the delegation; `DELEGATION_REVISION` is the new
delegation's own requested revision used when launching it:

```bash
agent-commons --json delegation create \
  --target-ref "review:$REVIEW_ID" \
  --target-revision "$REVIEW_REVISION" \
  --target-profile claude-independent-reviewer \
  --purpose independent_review \
  --limits-json '{"max_depth":0,"wall_time_seconds":1800,"max_attempts":1,"max_concurrency":1,"budget":{"unit":"micro_usd","limit":500000}}' \
  --idempotency-key snake-review-delegation-create-v1

agent-commons --json broker run "$DELEGATION_ID" "$DELEGATION_REVISION" \
  --idempotency-key snake-review-delegation-launch-v1 \
  --telemetry local
```

### 3. The broker starts Claude and joins the result

`commons_run_delegation` performs this bounded sequence:

```text
requested delegation
  -> private launch reservation
  -> identifiable child process and distinct child session
  -> canonical delegation.started
  -> fixed instruction delivered on stdin
  -> Claude reads the exact target through its read-only profile
  -> Claude records approved/changes_requested through bounded MCP tools
  -> canonical delegation.succeeded with the review ref
```

The Claude reviewer runs in `dontAsk` with an immutable worker-scoped MCP:
bounded repository list/read/literal-search plus only its own delegation,
review, and outcome tools. It receives no native filesystem, edit, Bash, web,
subagent, runtime, or delegation-creation tools. This supports an expert source
review and canonical verdict. It does not pretend to have independently run
pytest; reproducible verification still requires a separately authorized
bounded check or manual reviewer session with real evidence.

If the child reports `input_needed` and then exits, the current headless MVP
cannot resume or reattach that provider session. The broker records
`needs_operator`; inspect the sanitized requirement and create new work only
after the earlier attempt is terminal and no child can remain live.

### 4. Iterate on review findings

If Claude returns `changes_requested`, tell Codex:

```text
Check Agent Commons inbox and the Snake review discussion. Reopen the task,
address every actionable finding, rerun the checks, register a new exact
artifact revision, resubmit it, and request a fresh independent review. Do not
accept the task yourself and do not commit or deploy.
```

Then request and run a new Claude review delegation. The earlier review remains
in history but becomes stale; it cannot approve changed work.

When Claude records `approved`, a non-author session can accept the task with
that current review bound to the acceptance event. You can then inspect the
result and decide separately whether to commit it:

```bash
agent-commons doctor
agent-commons orient
git status --short
```

That is the core loop: define and claim work, submit an exact revision, verify
facts independently, review judgment independently, remediate, re-review, and
accept. See [User workflows](docs/USER_WORKFLOWS.md) and the
[operating protocol](docs/PROTOCOL.md) for the general form.

### Reverse direction: Claude invokes Codex

This path is not the safe default. Current Codex CLI runners and writable
builders do not provide host OS isolation or an enforceable monetary budget.
They require explicit trusted-workspace profile opt-in; Codex additionally uses
an enforced `provider_units` budget, where each provider-process attempt consumes
one unit. For untrusted repository content, also run it inside an externally
OS-isolated worktree/container/VM; otherwise use the manual flow.

Within that explicit trust boundary, start an ordinary interactive Claude
parent with its own exported Commons session and the same project MCP. Ask it to
create an implementation task, request an `implementation` delegation for the
exact task revision using `codex-builder`, and invoke
`commons_run_delegation`. The parent must stop writing transferred paths first
and inspect Codex's result before normal review. Canonical depth, attempts,
concurrency, wall time, one-writer, and target-revision guards still apply, but
they are not an OS sandbox and grant no permission to commit or push.

## Manual two-window fallback

Use this path only when the core CLI is installed and the project is initialized
but the optional broker, MCP adapter, matching profile, or provider integration
is unavailable. If `agent-commons` itself is missing, return to
[Install and initialize](#install-and-initialize); without the core CLI there is
no shared ledger, exact-revision review, or Agent Commons handoff.

First let the author complete and submit the task, register exact artifacts,
and request review. Record the returned `REVIEW_ID` and current
`REVIEW_REVISION`. Stop every writer in the shared checkout for the whole
review, or provision a separate quiescent worktree/immutable snapshot whose
bytes match those artifacts.

Open a second terminal, register a distinct reviewer session, export only its
session ID, and start Claude Code normally:

```bash
agent-commons --json session start \
  --stable-instance-id snake-claude-reviewer-01 \
  --principal local-operator \
  --client claude \
  --software claude-code \
  --role independent-reviewer
export AGENT_COMMONS_SESSION_ID='session.from-the-command-above'
claude
```

Paste this concrete prompt after substituting the two placeholders:

```text
Use $commons-start and $commons-review. Follow .agent-commons/ONBOARDING.md and
use the already selected Claude session. Run doctor, orient, and inbox. Review
the requested review REVIEW_ID at its exact current revision REVIEW_REVISION.

Do not edit source. Confirm that the checkout is quiescent and that the files
match the registered subject artifacts; if either cannot be established, stop
and request an operator-provisioned worktree or immutable snapshot. Inspect the
actual source and evidence, then complete that review as approved or
changes_requested with concrete severity, scope, evidence, and uncertainty.
Record a verification only for deterministic commands you personally ran; do
not infer one from the author's report. Do not accept the task, commit, push,
deploy, or start another agent.
```

Back in the author window, check `agent-commons inbox`, remediate any findings,
and request a fresh review for every changed revision. For Claude-to-Codex,
reverse the client/role names and give the Codex window the exact implementation
task ID and revision; keep the same distinct sessions, claims, quiescence, and
review boundaries. The manual window inherits that client's own permission and
host-isolation policy; the Commons broker is not enforcing it. Only process
launch is manual—the durable protocol is unchanged.

## Observability without transcripts

There are three deliberately separate views:

- the canonical ledger records delegation intent, exact target, child session,
  state, sanitized outcome, and typed result references;
- `agent-commons broker attempts` reports private operational metadata such as
  profile/provider, attempt number, PID, timestamps, exit classification, byte
  counts, truncation, and correlation IDs;
- `--telemetry local` writes the same metadata-only milestones below ignored
  operational state, while `--telemetry otel` emits optional short-lived spans
  through the configured OpenTelemetry SDK/exporter.

Telemetry is lossy and never changes canonical behavior. Prompts, responses,
reasoning, file contents, tool arguments/results, shell commands, environment
variables, credentials, and raw stdout/stderr are excluded. High-cardinality IDs
are trace attributes, not metric labels. Diagnose with:

```bash
agent-commons delegation list
agent-commons broker attempts
agent-commons broker reconcile
agent-commons doctor
```

## Receipt recovery

An interrupted reservation is local to its checkout/branch scope and blocks new
canonical writes there until an identical retry finishes. If the retry is
permanently impossible, a session explicitly opened with the `receipt:abandon`
capability may create an audit tombstone. `receipt reconcile` never deletes an
in-flight orphan; it only rebuilds post-commit state from canonical events.

After an upgrade, fresh clone, first visit to a branch, or Git merge, inspect and
reconcile the current checkout with an active session:

```bash
agent-commons receipt status
agent-commons receipt reconcile
agent-commons doctor
```

Legacy orphans stop migration instead of being assigned to a branch by guess.
After inspecting the reported digest, explicitly retry or abandon it, or adopt
it into the current scope with `receipt reconcile --adopt-legacy-orphan DIGEST`.
Before intentionally downgrading one checkout to v1, stop other writers and run
`receipt reconcile --prepare-rollback`. The complete crash, anchor, tombstone,
migration, and rollback contract is in
[ADR 0003](docs/adr/0003-ledger-derived-checkout-aware-receipt-recovery.md).

## Current boundary

MVP-0 coordinates processes that share one local project filesystem. The
experimental broker can launch allowlisted local Codex/Claude headless workers,
but there is no remote authentication, cross-machine synchronization, daemon,
generic command tool, live session takeover, or guarantee that an existing IDE
pane can be addressed. Input resume and provider-session reattachment remain
fail-closed; ambiguous restarts become `needs_operator`.

Only requested, unlaunched work can be cancelled through the current broker or
MCP. Active cancellation is not implemented: stop the provider under operator
control and reconcile instead of recording canonical cancellation first.

Agent Commons never authorizes Git, deployment, publishing, messaging, or other
external/destructive actions. Disable runtime by removing `--enable-runtime`
from MCP configuration; canonical records remain readable and ordinary CLI
workflows continue. Before deleting operational runtime state, terminate or
classify every attempt and run reconciliation. Redis and Kafka remain
unnecessary: immutable files are authoritative, while SQLite, Markdown,
runtime status, and telemetry are rebuildable or disposable views.
