# User workflows

The examples describe the shared operating flow rather than client-specific
prompt syntax. Each participant begins by reading the generated onboarding
contract, registering a distinct session, and running a bounded orientation.
For a copyable two-terminal walkthrough, see
[Build Snake with Codex and Claude Code](../README.md#worked-example-build-snake-with-codex-and-claude-code).

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

## 2. Delegate one bounded local step

The parent first records or finds the exact task or review request. It creates a
delegation against that target's current immutable revision, chooses a built-in
Codex or Claude profile whose role matches the purpose, and supplies explicit
depth, time, attempt, concurrency, and budget limits. Creation records intent;
the optional broker launch is a separate operation and uses a different stable
idempotency key. The current broker enforces `micro_usd` only for a profile with
a provider-native monetary cap (currently Claude). It also enforces
`provider_units` as one coarse unit per provider-process attempt, with
`max_attempts <= budget.limit`. `tokens` and mismatched unit/profile combinations
fail before reservation/spawn even when the canonical schema can record them.

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

The broker registers a distinct child session, records process identity before
the child receives its instruction, and then binds `delegation.started`. The
child works only on the recorded target. It may return `input_needed` with a
sanitized requirement summary, but secrets and interactive input remain outside
the canonical ledger. The current headless MVP cannot resume or reattach an
exited `input_needed` provider session, so the broker classifies it
`needs_operator` rather than promising continuation. On success it records typed
result references. The parent then inspects those exact results and applies the
ordinary review and acceptance rules; process success is never automatic
acceptance.

If the broker loses certainty after start, it records `needs_operator` and does
not relaunch. A new delegation is safe only after the old attempt is terminal
and no earlier child can remain live. The core `agent-commons` CLI remains a
prerequisite for either launch mode. If only the optional runtime, profile, or
provider integration is unavailable, follow the README's
[manual two-window fallback](../README.md#manual-two-window-fallback) with the
same task, review, revision, and session boundaries.

Active cancellation is not a current runtime capability: only requested,
unlaunched work may be cancelled through core `delegation cancel` or bounded
MCP `commons_cancel_delegation`. Once active, stop the provider under operator
control and reconcile; do not record canonical cancellation first.

Outcome: the safe automated default lets Codex request a bounded Claude review.
Claude-to-Codex implementation remains trusted-workspace-only because current
Codex runners and writable builders lack host OS isolation; require explicit
operator profile opt-in, a `provider_units` budget, plus an externally isolated
worktree for untrusted content, or use the manual flow.

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
