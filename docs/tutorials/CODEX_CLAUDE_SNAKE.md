# Build Snake with Codex and Claude Code

This tutorial shows the concrete author → reviewer → remediation loop. Codex
builds a small browser game; the optional broker launches a separate headless
Claude Code reviewer. It does not control an already-open IDE pane.

## 1. Create the project

```bash
mkdir browser-snake
cd browser-snake
git init
agent-commons init --integration codex --integration claude
```

Install the optional runtime and validate compatibility before creating work:

```bash
cd /path/to/agent-commons
uv tool install --force '.[mcp,observability]'
cd /path/to/browser-snake
claude --version
agent-commons broker profiles \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

Preflight starts no model work and consumes no delegation attempt. Configure the
project MCP as shown in the [README](../../README.md#optional-automatic-codex--claude-handoff).

## 2. Start the Codex author

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

Paste this prompt:

```text
Follow .agent-commons/ONBOARDING.md and use commons-start, commons-coordinate,
commons-record, commons-review, commons-delegate, and commons-handoff as needed.
Use the already selected Codex session. Run doctor, orient, and inbox, then
create, take, and narrowly claim a task to build a small browser Snake game.

Requirements: plain HTML/CSS/JavaScript, arrow-key and WASD controls, score,
restart after game over, and a short README explaining local use. Add focused
automated checks where practical. Do not commit, push, deploy, or overwrite
unrelated work.

When ready, run checks, register exact evidence, complete and submit the task,
and request independent review of that exact submitted revision. Then request
an independent_review delegation targeting the review request's exact revision
with profile claude-independent-reviewer, max_depth 0, wall time 1800 seconds,
one attempt, one concurrent worker, and budget provider_units 1. Use distinct
stable idempotency keys for review creation, delegation creation, and broker
launch. Invoke commons_run_delegation; never pass an executable, env, command,
session nonce, provider credential, or raw prompt to the broker.

After the child exits, inspect canonical review and delegation state. Process
exit is not approval. If ambiguous, leave needs_operator and never blind-retry
after canonical start. Report durable IDs and states only.
```

The `provider_units: 1` example is for a local Claude CLI you already
authenticated through your Claude Code subscription. It permits one provider
process attempt; it is not a dollar cap and Agent Commons does not change the
CLI's account or fall back to API credits. If you explicitly use billable
provider-native monetary mode, choose your own current `micro_usd` ceiling and
leave finalization reserve; `$0.50` is known to be too small for some real
reviews.

The equivalent CLI shape is:

```bash
agent-commons --json delegation create \
  --target-ref "review:$REVIEW_ID" \
  --target-revision "$REVIEW_REVISION" \
  --target-profile claude-independent-reviewer \
  --purpose independent_review \
  --limits-json '{"max_depth":0,"wall_time_seconds":1800,"max_attempts":1,"max_concurrency":1,"budget":{"unit":"provider_units","limit":1}}' \
  --idempotency-key snake-review-delegation-create-v1

agent-commons --json broker run "$DELEGATION_ID" "$DELEGATION_REVISION" \
  --idempotency-key snake-review-delegation-launch-v1 \
  --telemetry local \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

`REVIEW_REVISION` is the delegation target. `DELEGATION_REVISION` is the new
delegation's own requested revision used for launch.

## 3. What the broker does

```text
requested delegation
  -> private attempt reservation
  -> distinct child process + child session
  -> canonical delegation.started
  -> fixed instruction on stdin
  -> exact snapshot read through worker-scoped MCP
  -> approved/changes_requested through bounded review tools
  -> canonical delegation.succeeded with typed review ref
```

Claude receives no native filesystem, edit, Bash, web, subagent, runtime, or
delegation-creation tools. It can inspect bounded text and record its own exact
review outcome. This supports expert source review; it does not pretend Claude
ran pytest unless a separately authorized verification really did so.

All writers must stop changing the reviewed checkout until the review is
terminal. If quiescence is impossible, provide an operator-owned worktree or
immutable snapshot matching the registered artifacts.

## 4. Remediate and review again

If Claude requests changes, tell Codex:

```text
Check Agent Commons inbox and the Snake review discussion. Reopen the task,
address every actionable finding, rerun checks, register a new exact artifact,
resubmit, and request a fresh independent review. Do not accept, commit, push,
or deploy.
```

Run a new review delegation for the new revision. The previous review remains
immutable history but cannot approve changed work. When a distinct reviewer
approves, a non-author operator session may accept the task. Git commit/push is
a separate user decision:

```bash
agent-commons doctor
agent-commons orient
git status --short
```

## Manual fallback

If the core CLI works but broker/MCP/provider integration does not, keep the
same task and review. Open a second terminal, register a distinct Claude reviewer
session, export its ID, and start `claude` interactively. Give it the exact
review ID/revision and ask it to use `commons-start` + `commons-review`, avoid
source edits, separate verification facts from review judgment, and record the
canonical verdict. See the [Quickstart](../QUICKSTART.md) for the copy-paste
manual flow.

If the provider reports `input_needed` and exits, the headless runtime cannot
resume it. It records `needs_operator`; inspect the sanitized requirement and
create replacement work only after the earlier attempt is terminal and no child
can remain live.
