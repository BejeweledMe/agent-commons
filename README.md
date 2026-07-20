# Agent Commons

Agent Commons is a project-local shared manager-space for heterogeneous coding
agents. Codex, Claude Code, and future integrations use one durable workspace to
understand the project, divide work, exchange ideas, request independent review,
preserve disagreement, hand work off, and promote verified conclusions into the
effective project truth.

It is not an autonomous agent launcher, a group-chat transcript, or a replacement
for Git. The CLI is the single business layer; immutable files are authoritative,
while indexes and Markdown views are rebuildable projections.

The motivation, intended collaboration model, and definition of success are
described in the [product vision](docs/VISION.md).

## Status

This repository is a clean universal implementation derived from lessons learned
in a production multi-agent collaboration prototype. It contains only generic
software-collaboration semantics and no copied runtime ledger data.

The local shared-workspace MVP is implemented. Its product contract is
documented in [Architecture](docs/ARCHITECTURE.md) and
[Council synthesis](docs/COUNCIL_SYNTHESIS.md). The staged product boundary is
in the [Roadmap](docs/ROADMAP.md).

Key architectural choices are recorded as ADRs, beginning with the
[file-ledger/SQLite boundary](docs/adr/0001-file-ledger-with-sqlite-projection.md)
and [explicit truth promotion](docs/adr/0002-explicit-truth-promotion.md). Receipt
portability and linked-worktree recovery are specified by the
[checkout-aware recovery ADR](docs/adr/0003-ledger-derived-checkout-aware-receipt-recovery.md).
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

`session start` returns a `session_id` and a rotating private `nonce`. Keep the
nonce for heartbeat/end operations and select the session in later commands:

```bash
export AGENT_COMMONS_SESSION_ID='session.returned-by-session-start'
agent-commons doctor
agent-commons orient
agent-commons inbox
```

The generated `.agent-commons/ONBOARDING.md` is the single entrypoint to give a
new agent window. `AGENTS.md` and `CLAUDE.md` receive small managed blocks, and
the same six workflow skills are installed below `.agents/skills/` and
`.claude/skills/`. Existing project-authored instructions and locally modified
skills fail closed unless replacement is explicitly requested.

Use `--idempotency-key` with a stable operation-specific value for canonical
writes that may be retried after a timeout or process failure. Use each command's
`--help` for its exact arguments; the CLI intentionally has no hidden write path.

## Worked example: build Snake with Codex and Claude Code

This example starts from a separate empty project after Agent Commons itself has
been installed. It uses Codex as the builder and Claude Code as an independent
reviewer. Agent Commons coordinates their durable work; it does not launch
either agent.

### 1. Create and initialize the project

```bash
mkdir browser-snake
cd browser-snake
git init
agent-commons init --integration codex --integration claude
```

Open two terminals in `browser-snake`. The generated
`.agent-commons/ONBOARDING.md` is the canonical operating contract for both
windows.

### 2. Ask Codex to build the game

Run `codex` in the first terminal and paste this prompt:

```text
Read .agent-commons/ONBOARDING.md and follow the Agent Commons workflow.
Register a distinct Codex session as the implementation author, run doctor and
orient, then create, take, and claim a task to build a small browser Snake game.

Requirements: plain HTML/CSS/JavaScript, arrow-key and WASD controls, score,
restart after game over, and a short README explaining how to run it locally.
Add focused automated checks where practical. Do not commit, push, deploy, or
overwrite unrelated work.

When it is ready, run the checks, register exact evidence/artifact revisions,
complete and submit the task, request an independent review, and open a targeted
review discussion for the Claude reviewer. Preserve the session nonce privately
and report only durable task/review/thread IDs and the result to me.
```

Codex should leave the task in `review`, not claim that its own successful tests
are independent approval.

### 3. Ask Claude Code to review it

Run `claude` in the second terminal and paste this prompt:

```text
Read .agent-commons/ONBOARDING.md and follow the Agent Commons workflow.
Register a new Claude Code session with a stable instance ID and the role
independent-reviewer. This session must be different from every work-author
session. Run doctor, orient, and inbox, then find the submitted browser Snake
task and its review discussion.

Do not edit the implementation. Review the exact submitted revision for the
stated acceptance criteria, gameplay correctness, keyboard behavior, browser
safety, and maintainability. Independently reproduce the runnable checks and
record those deterministic results as verification; record design/code judgment
separately as the review. Complete the review with approved or
changes_requested, cite exact evidence, reply in the thread, and report the IDs
to me. Never reveal the session nonce and do not commit, push, or deploy.
```

If Claude cannot see the request yet, wait for Codex to finish and then tell
Claude: `Check Agent Commons inbox again and process the Snake review request.`
There is deliberately no background notification service in MVP-0.

### 4. Iterate on review findings

If Claude returns `changes_requested`, tell Codex:

```text
Check Agent Commons inbox and the Snake review discussion. Reopen the task,
address every actionable finding, rerun the checks, register a new exact
artifact revision, resubmit it, and request a fresh independent review. Do not
accept the task yourself and do not commit or deploy.
```

Then ask Claude to review the new revision. The earlier review remains in
history but becomes stale; it cannot approve changed work.

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

MVP-0 coordinates processes that share one local project filesystem. It does
not launch agents, authenticate remote users, synchronize independent machines,
or authorize Git, deployment, publishing, messaging, or destructive actions.
Redis, Kafka, and a daemon are deliberately unnecessary at this stage: immutable
files are authoritative, while SQLite and Markdown are rebuildable views.
