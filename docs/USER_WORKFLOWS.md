# User workflows

The examples describe the shared operating flow rather than client-specific
prompt syntax. Each participant begins by reading the generated onboarding
contract, registering a distinct session, and running a bounded orientation.
For a copyable two-terminal walkthrough, see
[Build Snake with Codex and Claude Code](tutorials/CODEX_CLAUDE_SNAKE.md).

## 1. Build a web application

The user records the product objective, non-negotiable constraints, and release
acceptance criteria. An architecture session proposes the service boundary and
opens a decision request. Other sessions critique the proposal before an
authorized decision is recorded.

The work is decomposed into API, interface, persistence, and integration-test
tasks with explicit dependencies. Implementers take different tasks and claim
their component or path scopes. A design-oriented participant registers an
interface artifact and asks for usability review without blocking unrelated API
work.

When implementation is ready, authors submit exact revisions for review.
Reviewers record judgments and reproducible checks separately. A changed
revision makes earlier approval stale. Accepted tasks and concrete release work
appear in the next orientation; reported risks and rejected approaches remain
available through the bounded list commands and generated views. Pausing
sessions leave targeted handoffs and release inactive claims.

Evidence flags in the CLI still take concise `kind:id` values. Agent Commons
binds each one to the current effective revision before writing, so a later
artifact revision cannot silently leave an earlier finding or decision in the
effective-truth view.

Outcome: after orientation and the relevant bounded views, a new window sees
current architecture, completed and blocked work, pending reviews, and rejected
approaches without replaying earlier chats.

## 1a. Staff the work with standing roles

A role is persistent staff; a delegation is one bounded run. Create the role
once with `agent create`, then point each run at it with `delegation create
--on-behalf-of`. The run stays terminal and unnamed; the role accumulates the
history, keeps its place in the index, and can be reached from other work.

```bash
agent-commons agent create \
  --name "Senior Node.js backend" --profile claude-builder \
  --rationale "the payments surface needs a standing owner between tasks"
```

Grants default to `deny`. Prefer a declared lifetime to a standing right:
`--retire-with-task <task-id>` retires the role automatically when its task is
accepted or cancelled, which covers "made for this task, gone when it lands"
without granting anything. Granting `--create-roles` or `--retire-roles` above
`deny` requires `--turnover-budget`, which counts creations and retirements
together below that role.

`agent show` reports the role's *effective* authority — the narrowest value
across the role and every creator above it — plus what currently blocks its
retirement. `agent retire --cascade` takes a role and everything it created out
of service in one command, and refuses as a whole if any of them still owes a
live delegation or an unfinished review. Nothing is ever deleted.

`agent-commons ui --enable-writes` opens the same operations in a panel built
around one home per kind of thing: a library sidebar (the board, runs, a
two-minute Overview, Skills and Tools over the operator catalogue, the agent
catalogue of role templates), the board in the centre, and the conversation —
main chat plus the attention queue — docked right. Clicking a node opens its
drawer, where Record, Settings, Links, Run and Message are tabs of the selected
thing; the ring still marks anything waiting on a human. Hiring sits behind the
board's + button (a new agent from scratch, or two clicks from a template), and
dragging one of a role card's four ports onto another role opens a recorded
link — closed later with a reason, never deleted. Anyone holding the printed
token writes as the session the server was started with.

## 2. Delegate one bounded local step

The parent first records or finds the exact task or review request. It creates a
delegation against that target's current immutable revision, chooses a built-in
Codex or Claude profile whose role matches the purpose, and supplies explicit
depth, time, attempt, concurrency, and budget limits. Creation records intent;
the optional broker launch is a separate operation and uses a different stable
idempotency key. The current broker enforces `provider_units` as one coarse unit
per provider-process attempt, with `max_attempts <= budget.limit`. Use it for a
subscription-authenticated local provider CLI when the operator wants to limit
launches, not claim a dollar cap; Commons never switches credentials or billing
mode. `micro_usd` is explicit provider-native monetary-cap opt-in and must
reflect current pricing plus canonical-finalization reserve; `$0.50` is not a
safe default. `tokens` and mismatched unit/profile combinations fail before
reservation/spawn even when the canonical schema can record them. Run broker
preflight after upgrades; it consumes no attempt and starts no model work.

Before any shared-checkout review, every writer stops and the operator confirms
that the bytes match the exact registered artifacts/evidence bound to the
subject revision; otherwise the reviewer gets a quiescent worktree or immutable
snapshot. Before delegating writable work, the parent also obtains the relevant
claims and stops writing
the transferred paths. The first rollout defaults to depth one and one writable
worker in a checkout. An independent reviewer receives a read-only provider
profile and an immutable worker-scoped MCP limited to its own delegation,
review/outcome, and bounded repository list/read/literal-search. It receives no
native filesystem, edit, shell, web, subagent, runtime, or delegation-creation
tools.

The broker registers a distinct child session and starts an inert local exec
gate. It records that stable PID and binds `delegation.started` before releasing
the gate; the gate then replaces itself with the fixed provider process and only
that provider receives the instruction. This prevents a slow ledger write from
consuming the provider's own stdin/startup timeout while preserving the same PID
and process group for cancellation and recovery. The child works only on the
recorded task revision. While its provider process is still live, it can open a
bounded request, progress report, or blocker in the authenticated operational
channel. The fixed parent/child graph, deadline, depth, size, idempotency, and
HMAC checks prevent generic chat or rebinding. A blocking request moves the
canonical delegation to `input_needed`; a parent reply resumes that exact
delegation, and the child polls and acknowledges the operational answer.
Question, context, answer, and caller-provided summaries never enter canonical
history: the two lifecycle events use fixed maintainer-defined status text. The
current broker still cannot reattach an already exited provider process, so an
exit without a terminal tool remains `needs_operator` rather than a promised
continuation. On success it records typed result references. The parent then
inspects those exact results and applies the ordinary review and acceptance
rules; process success is never automatic acceptance.

The parent can also use the control slice to send one bounded tactical guidance
item or request acknowledgement at a named safe checkpoint. These are private
`guidance`/`checkpoint` operations, not peer chat or hard cancellation; the
exact child acknowledges each once through `commons_ack_control`. Disable the
slice with `--disable-controls` without changing canonical history. Track
acknowledgement latency and stale/foreign-operation rejection alongside the
existing progress and blocker signals.

If the broker loses certainty after start, it records `needs_operator` and does
not relaunch. A new delegation is safe only after the old attempt is terminal
and no earlier child can remain live. The core `agent-commons` CLI remains a
prerequisite for either launch mode. If only the optional runtime, profile, or
provider integration is unavailable, follow the Quickstart's
[manual two-window flow](QUICKSTART.md#3-start-a-distinct-reviewer-window) with the
same task, review, revision, and session boundaries.

Active cancellation is not a current runtime capability: only requested,
unlaunched work may be cancelled through core `delegation cancel` or bounded
MCP `commons_cancel_delegation`. Once active, stop the provider under operator
control and reconcile; do not record canonical cancellation first.
If the original requester is unavailable, a separately authorized root session
declaring `delegation:recover` may recover only the exact canonical `requested`
revision through `delegation recover` or `commons_recover_delegation`. The
distinct event projects to `cancelled`; it never grants worker scope or active
process cancellation. A requester cannot end its session while it still owns
requested, active, or input-needed delegations.

Outcome: the safe automated default lets Codex request a bounded Claude review.
Claude-to-Codex implementation remains trusted-workspace-only because current
Codex runners and writable builders lack host OS isolation; require explicit
operator profile opt-in, a `provider_units` budget, plus an externally isolated
worktree for untrusted content, or use the manual flow.

## 2a. Migrate or roll back operational-state selection

For a shell that previously exported one global exact root, do not move or
delete that directory. Start from a clean shell, inspect the project, and then
select a base:

```bash
unset AGENT_COMMONS_STATE_ROOT
export AGENT_COMMONS_STATE_BASE=/absolute/operator-owned/agent-commons-state
agent-commons --read-only --json support --show-paths
agent-commons --read-only doctor
```

The effective location is namespaced by the canonical workspace ID. Existing
exact roots remain exact: a writable open adds an ownership marker only when
existing receipt metadata already proves the same workspace. Ambiguous legacy
material fails as `state_owner_unproven`; another workspace fails as
`state_owner_mismatch`. Resolve either by selecting a new empty exact root or a
base after operator review—never by deleting or rewriting the old state.

Rollback changes configuration, not data: unset `AGENT_COMMONS_STATE_BASE` and
select a previously proven exact root if one exists. For read-path diagnosis,
`orient --fresh --verbose` and `inbox --fresh --verbose` bypass the disposable
SQLite fast path without changing canonical history. Disabling the optional
runtime removes communication tools; let in-flight operations finish or expire
before removing any private operational communication directory.

## 3. Prototype a product design

A designer session creates a task with measurable usability and accessibility
criteria, then registers several immutable design revisions. It opens one
proposal thread linked to the variants rather than separate unconnected chats.

An accessibility reviewer identifies contrast and navigation risks. A product
reviewer challenges the information hierarchy and supplies a competing
proposal. The designer responds with a new revision; old review results remain
visible but stale for that revision.

The authorized decision records the selected variant, evidence, alternatives,
and reasons the other variants were rejected. Those negative conclusions remain
available to later orientation, preventing another session from unknowingly
recreating a discarded direction.

Outcome: critique and dissent are preserved, while only the accepted decision
and verified findings enter effective project truth.

## 4. Create a chatbot service

The project starts with separate conversation, external-tool, user-interface,
privacy, and security workstreams. One session defines the service contracts,
another implements the tool boundary, and another creates adversarial and
integration checks. Each participant claims only the relevant task and path
scope.

A security review reports an instruction-injection weakness and returns
`changes_requested`. The implementation task cannot be accepted under the
MVP protocol invariant. After a new artifact revision is registered, the reviewer
repeats the scoped verification and records new evidence.

The final decision documents the accepted safeguards and residual limitations.
A handoff to an operations session includes typed artifact references,
revision-bound evidence where exact content matters, open monitoring work, and
prohibited deployment assumptions. It does not grant permission to deploy.

Outcome: implementation, adversarial review, governance, and operational
handoff remain connected without treating discussion or model agreement as
proof.
