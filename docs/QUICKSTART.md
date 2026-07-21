# Agent Commons quickstart

This guide adds Agent Commons to an existing Git project and walks through one
manual author/reviewer cycle. It needs no broker, MCP server, API key, or paid
provider call.

## 1. Install and initialize

From the Agent Commons source checkout:

```bash
uv tool install .
agent-commons --version
```

Then move to the project the agents will share:

```bash
cd /path/to/your-project
agent-commons init --integration codex --integration claude
agent-commons --read-only --json support
```

`init` publishes the canonical onboarding contract, workspace configuration,
small managed blocks in `AGENTS.md`/`CLAUDE.md`, and matching workflow skills.
It never commits or stages files. If a late publication fails, it rolls back
only bytes it published; a structured partial report names anything that could
not be restored safely.

## 2. Start the author window

```bash
agent-commons --json session start \
  --stable-instance-id quickstart-codex-author-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role implementation-author
```

Keep the returned `nonce` private. Export only the returned session ID:

```bash
export AGENT_COMMONS_SESSION_ID='session.returned-by-session-start'
agent-commons doctor
agent-commons orient
agent-commons inbox
```

Open Codex in the same project and use this prompt:

```text
Use commons-start and commons-coordinate. Follow .agent-commons/ONBOARDING.md,
use the already selected session, and inspect doctor/orient/inbox. Create, take,
and narrowly claim a small task with explicit acceptance criteria. Implement it,
run focused checks, register exact evidence, complete and submit the task, then
request independent review of that exact submitted revision. Do not accept,
commit, push, deploy, or overwrite unrelated work.
```

Codex should leave the task in `review` and report its task/review IDs and exact
revisions.

## 3. Start a distinct reviewer window

In a second terminal, do not reuse the author session:

```bash
agent-commons --json session start \
  --stable-instance-id quickstart-claude-reviewer-01 \
  --principal local-operator \
  --client claude \
  --software claude-code \
  --role independent-reviewer
export AGENT_COMMONS_SESSION_ID='the-second-session-id'
claude
```

Ask Claude Code:

```text
Use commons-start and commons-review. Follow .agent-commons/ONBOARDING.md and
use the already selected reviewer session. Inspect the requested review at its
exact current revision. Do not edit source. Verify reproducible facts separately
from design judgment, then complete the review as approved or changes_requested
with severity, scope, evidence, and uncertainty. Do not accept, commit, push,
deploy, or start another agent.
```

Stop all writers while the reviewer inspects a shared checkout. If that cannot
be guaranteed, use an operator-provisioned quiescent worktree or immutable
snapshot matching the registered subject bytes.

## 4. Iterate and accept

If the verdict is `changes_requested`, return to the author session, reopen the
task, address findings, submit a new exact revision, and request a fresh review.
The old review remains audit history but is stale.

After a distinct reviewer approves the current revision, an authorized
non-author session may accept it. Acceptance remains a governance decision; the
library does not infer it from process exit or model agreement.

```bash
agent-commons doctor
agent-commons orient
agent-commons task list --state review
git status --short
```

## Optional automation

The broker can launch the reviewer as a separate headless process instead of
requiring the second interactive window. First read the
[Snake tutorial](tutorials/CODEX_CLAUDE_SNAKE.md), run `broker preflight`, and
keep the same exact-revision, distinct-session, quiescence, and acceptance
rules.
