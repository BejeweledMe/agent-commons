# ADR 0007: Multi-level delegation and raised concurrency

Status: proposed.

Current implementation note (2026-08-18): supported delegation creation is
leaf-only and requires `max_depth: 0`. This proposal describes prerequisites for
reenabling child delegation; runtime policy primitives alone do not make that
workflow available to workers.

## Context

The visual orchestrator described in
[the PRD](../visual_multi_agent_orchestrator_prd.md) requires an organization
deeper than one delegation level (Product Lead → Tech Lead → Backend) and more
than two agents running at once. Both are currently blocked by defaults, not by
mechanism.

The mechanism already exists and is already operator-owned:

- `RuntimePolicy.remaining_depth` defaults to `1`, but `derive_child()` consumes
  exactly one level per generation and `assert_reduction_of()` enforces that no
  child widens `max_fanout`, `max_attempts`, `max_concurrency`,
  `timeout_seconds`, `max_output_bytes`, or `max_budget_microusd`. A bounded
  parent cannot produce an unbounded child at any depth.
- `OperatorLimits.global_concurrency` defaults to `2`,
  `provider_concurrency` to `{codex: 2, claude: 2}`, and `profile_concurrency`
  to `1` per profile. All three are read from operator configuration through
  `OperatorLimits.from_mapping`, and `profile_concurrency_cap()` already takes
  the minimum across all three tiers.

So raising either limit is a configuration change today. This ADR exists because
raising them is **not** safe today: the amplification and isolation guarantees
that hold at depth 1 / concurrency 2 do not hold above them. This ADR defines
the preconditions, the new guards, and the rollback.

## Decision

Multi-level delegation and raised concurrency become supported configurations
once — and only once — the guards in this ADR exist. Until then the defaults
stay where they are, and operator configuration that exceeds them is rejected at
load time rather than silently honoured.

## What breaks above the current defaults

### 1. Depth bounds vertical growth only

`remaining_depth` and `max_fanout` together bound the *shape* of a delegation
tree but not its *total size*. Two amplification paths escape both:

- **Flat amplification.** A supervisor with `remaining_depth=1` and generous
  `max_fanout` can open an unbounded number of sequential depth-1 delegations
  over successive planning rounds. Every individual delegation is compliant.
- **Horizontal ping-pong.** `A → B` followed by `B → A` on the same work is two
  compliant delegations at the same depth. Nothing detects that the pair is not
  converging.

Neither is hypothetical at depth > 1: a supervisor that decomposes, dispatches,
reads results, and re-plans is exactly the loop in which both appear.

### 2. Concurrency collides with the writable-worker invariant

`ARCHITECTURE.md` holds one writable worker per checkout scope. Raising
`global_concurrency` alone does not create a second safe writable slot — it
creates two processes editing one working tree. The PRD's own example
(Backend and Frontend implementing in parallel) is precisely this case.

Read-only concurrency has no such problem: independent reviewers do not write,
so `codex-independent-reviewer` and `claude-independent-reviewer` may be raised
independently of any worktree work.

### 3. Attempt accounting is per-delegation, not per-subtree

`assert_launch_allowed()` checks `active_fanout`, `attempts_started`, and
`active_concurrency` against the policy of the delegation being launched. There
is no subtree-wide counter, so a deep tree can consume budget geometrically
while every node stays individually within limits.

## New guards

**Amended after implementation.** The first attempt put these on
`RuntimePolicy`, which was wrong twice over: that object is the authority
granted to one delegation, it is stored in the request document, and it is
hashed into the request identity. Operator ceilings there broke rollback — an
older reader rejects the unknown fields — and made an operator editing config
invalidate the retry key of every in-flight request. They live on
`OperatorLimits` and are checked at admission.

| Field | Bounds | Default | Status |
|---|---|---|---|
| `max_delegations_total` | delegations in the whole subtree | `16` | implemented |
| `max_wave_count` | planning rounds a supervisor may run | — | **not implemented** |
| `max_context_tokens` | assembled context handed to one child | — | **not implemented** |

The last two are removed rather than shipped inert. Nothing produces a wave
index or a context estimate, and configuration that reads as enforced while it
cannot fire is worse than configuration that is absent: it is the shape of a
guarantee without the guarantee.

The default for `max_delegations_total` is `16`, not `1`. Spend is already
bounded per tree by `provider_units`, so this is a structural backstop that
begins to matter when an operator raises the other limits. A default of `1`
would not bound amplification; it would forbid delegation trees.

Because the tree is now recorded in the stored correlation, the request and
attempt schemas move to **v4**. v3 and v2 remain readable, but the change is
one-way: an older build refuses a v4 document by envelope. Rolling back to a
release that predates v4 requires clearing `runtime/requests` in the state root.

A fourth guard is not a policy field because it is stateful rather than
inherited — a repeat-pair counter keyed on
`(from_agent, to_agent, task_fingerprint)`, where
`task_fingerprint = sha256(canonical(target_ref, purpose, acceptance_criteria))`.
The fingerprint is deliberately computed over the canonical target and intent,
not the instruction text, so that a model rephrasing the same request does not
reset the counter. Exceeding `max_repeat_per_pair` (default `2`) is a
fail-closed `PolicyViolationError`, not a skipped step: a non-converging pair
means the supervisor is not making progress, and continuing silently burns
budget.

All four guards fail closed and escalate to the operator. None of them retries.

## Preconditions for raising each limit

### Depth > 1

1. `max_delegations_total` is enforced at admission (**done**), and the
   repeat-pair guard exists (**not started**). Note that fanout and concurrency
   are deliberately *not* checked there: they are transient and belong to the
   admission queue, so `assert_launch_allowed()` has no production caller by
   design.
2. Every delegation carries a handoff packet with revision-bound `artifacts`;
   a packet missing them blocks the launch. Depth > 1 means a grandchild acts on
   a parent's conclusions it never observed being formed, so the carry-over must
   be explicit and typed rather than implied by shared context.
3. The subtree budget is charged at the root: `provider_units` and
   `max_budget_microusd` decrement across the whole subtree, not per generation.

### Concurrency > 2, read-only profiles

Raising `profile_concurrency` for `*-independent-reviewer` requires only the
queue accounting that already exists. No worktree, no new invariant. **This is
the cheap half and can ship first.**

### Concurrency > 2, writable profiles

1. One worktree per writable agent, provisioned by the orchestrator with
   explicit operator confirmation. The broker still never creates worktrees —
   the invariant from ADR 0004 stands unchanged.
2. Claims validated across the whole planned wave *before* dispatch:
   `resources_overlap()` conflicts within one wave are a planning error, and
   the conflicting steps are serialized into different waves rather than
   retried at runtime.
3. Merge stays outside the system. The orchestrator does not merge branches and
   does not resolve conflicts; it surfaces the worktrees and stops.

## Configuration surface

No new configuration mechanism. `RuntimePolicy.from_mapping` and
`OperatorLimits.from_mapping` gain the new keys and keep rejecting unknown ones.
Operator configuration that raises a limit whose preconditions are unmet fails
at load with a message naming the missing guard — the failure is at
configuration load, not at launch, so an operator learns about it before a run
starts rather than after a partial fan-out.

The UI never writes these values. It reads them through `GET /api/profiles` and
renders the resulting ceiling — the depth ruler and the capacity widget in the
orchestrator plan are projections of this configuration, not inputs to it.

## Canonical/operational boundary

Unchanged. The new guards are runtime policy, evaluated before launch, and their
violations are operational failures with existing diagnostic codes. No new
canonical event type, no change to any existing event shape. A tripped guard is
recorded the same way an exhausted fanout limit is recorded today.

## Migration and rollback

Additive. Defaults stay at `remaining_depth=1`, `global_concurrency=2`,
`profile_concurrency=1`; a workspace that changes nothing behaves exactly as
before, and the new policy fields default to values that cannot widen anything.

Rollback is configuration-only: lower the limits back to defaults, and in-flight
work finishes or expires on its existing deadlines. Because the guards are
narrowing-only, lowering a limit can never invalidate an already-running
delegation's authority — it only prevents new launches. No stored state changes
shape, so no migration or replay is required in either direction.

## Consequences

- Depth > 1 and higher read-only concurrency become supported, making the PRD's
  organizational structure executable rather than decorative.
- Writable concurrency stays gated behind worktree isolation, so the most
  visually appealing PRD scenario is also the last one delivered. This is
  deliberate: shipping it earlier would violate an invariant the rest of the
  system depends on.
- The subtree-total and repeat-pair guards close amplification paths that depth
  and fanout never covered. They are worth adding even if depth stays at 1,
  because flat amplification is already reachable today.
- Four more ways for a launch to be refused. Each refusal names the guard it
  tripped, so the orchestrator can render it on the exact node rather than as a
  generic failure.
