# ADR 0011: Hierarchical delegation closure, risk review and immediate-parent escalation

## Status

**Proposed — H0 semantic contract.** This document translates the accepted product
policy into an implementation proposal. It requires product-owner acceptance and
an independent exact-revision review before it authorizes any W3 write path.

It does not change existing persisted formats, runtime behaviour, task acceptance,
or delegation lifecycle behaviour.

## Context

Two product decisions are already accepted:

- `decision.3JJTDQ7J5RN8846K8FGG9QKAM1`: a parent locally accepts ordinary
  delegated technical work. Independent review is required only when policy,
  risk, competence, or authority require it.
- D2, the accepted escalation and human-access policy: a child raises unresolved
  matters to its immediate parent only. Product positioning, user experience,
  security, strategy, and other C-level matters may reach the human owner only
  through that chain; the human may nevertheless pull-read or intervene anywhere.

The existing strict project-truth boundary remains deliberately narrower:
`task.accepted` requires a current approved independent review. A successful
provider delegation is a result report, not a local acceptance, review approval,
or task acceptance.

The code already retains immutable delegation parent and lineage information, but
threads and handoffs can currently be addressed more broadly. A new model must
preserve replay of old history, make the two acceptance notions impossible to
confuse, and fail closed when a required reviewer cannot be qualified or routed.

## Scope and non-goals

V1 applies only to an `implementation` delegation that has a validated terminal
result bound to a task revision.

It introduces a proposal for:

- an additive, revisioned local-closure record;
- policy-derived independent-review requirement and pairing;
- typed immediate-parent escalation; and
- derived read models that expose an honest hold instead of inferred closure.

It does **not**:

- change `task.accepted`, `review.*`, or existing `delegation.*` semantics;
- equate `delegation.succeeded` with an acceptance outcome;
- backfill or invent local closure for historical delegations;
- auto-retry, reassign, reopen, schedule, or dispatch work;
- add authority grants, a human bypass route for a child, a new CLI command, or
  a mutable product surface; or
- make a role title, model name, or provider profile evidence of reviewer
  competence.

## Proposed vocabulary and records

The preferred design is a new additive `delegation_closure` semantic entity. It
must not be encoded as extra `Delegation` states: execution state and local
governance have different owners, timelines, failure modes, and replay meaning.

### Immutable bindings

`AuthoritativeParentBinding` freezes the parent that is allowed to make the
local decision:

```text
delegation_id
delegation_effective_revision
parent_session_id
lineage
acting_principal_id | null
acting_role_id | null
```

Its source is the existing immutable delegation record. A later change of role
or session identity never silently changes this binding.

`DelegationResultBinding` freezes exactly what is being evaluated:

```text
delegation_id
terminal_delegation_revision
terminal_result_ref(s)
target_task_id
target_task_effective_revision
```

It may be opened only for a current validated terminal result. A retry creates a
new terminal revision and therefore a new possible closure; it does not mutate an
old one.

`ReviewRequirement` is a versioned policy result, not an untyped extension:

```text
policy_version
scope
reason_codes
subject_ref
subject_effective_revision
requires_independent_review
required_competence_domains
required_authority
```

The value must be explicit even when independent review is not required. Unknown,
conflicting, or unrouteable policy is a typed hold, never an implicit exception.

### Proposed event family

After the owner accepts the semantic contract, introduce a separately versioned
schema family and record `workspace.semantics_required` before its first use.
The proposed event names are:

```text
delegation_closure.opened
delegation_closure.held
delegation_closure.accepted
delegation_closure.returned
delegation_closure.resumed       # only from held
```

`opened` contains immutable parent and result bindings plus the exact
`ReviewRequirement`. `held` contains a typed hold reason and, where present, the
review request binding. Terminal events retain exact task, result, review, and
parent references. Existing `task.v1`, `review.v1`, and `delegation.v1` records
are neither reinterpreted nor rewritten.

## Closure state machine

```text
no closure --> OPEN
OPEN -------> ACCEPTED | RETURNED | HELD
HELD -------> OPEN | ACCEPTED | RETURNED
```

`OPEN` and `HELD` are the only live states. The invariant is one live closure for
the pair `(delegation_id, terminal_delegation_revision)`. `ACCEPTED` and
`RETURNED` are terminal.

For a policy-required review, the normal path is:

```text
OPEN -> independent review requested -> HELD(awaiting_review)
     -> parent ACCEPTED | RETURNED
```

`review.changes_requested` requires `RETURNED`; stale, invalidated, missing, or
unqualified review evidence never permits local acceptance. A `RETURNED` closure
does not automatically launch work or mutate the task. The parent uses an
existing explicit workflow to request follow-up work.

Every write uses the current closure revision plus a stable idempotency key. An
open requires the exact terminal delegation revision. A duplicate request with
the same key returns only the original identical result; a stale compare-and-swap
fails without partial mutation. Recovery after a crash may add only the exact
missing event edge and leaves a visible `OPEN` state until that is proven.

## Review independence and qualification

The authoritative parent may locally accept ordinary work, but is never the
independent reviewer for the same work. The existing principal-based
independence predicate remains necessary but insufficient: a role label or model
cannot establish technical competence or product authority.

### Required owner choice before W3

V1 needs an operator-owned, versioned reviewer roster mapping a principal or
role to competence domains and authority. Builders cannot edit it. The policy
must select a qualified reviewer from this roster and bind the roster/policy
version to `ReviewRequirement`.

Recommended fail-closed rule:

```text
no roster | ambiguous roster | missing domain | missing authority
  -> HELD(qualification_unknown | reviewer_unrouteable)
```

This does not reopen the accepted D1/D2 policy; it supplies the missing evidence
needed to enforce it.

## Immediate-parent escalation and no bypass

`EscalationPacket` is a typed record, not free-form routing metadata:

```text
reason_code
decision_needed
options
recommendation
subject_ref(s) and effective revision(s)
authoritative_parent_binding
immediate_recipient
```

A delegated child may address this packet only to the immediate parent in its
binding. The W3 write paths for existing `thread.opened` and `handoff.created`
must reject a delegated child's address to an operator, `*`, or any higher-level
ancestor. A child whose immediate parent is the human owner may address that
owner. Human pull/read and explicit human intervention remain allowed, but any
override is typed, audited, and never grants a lower child new routing authority.

## Replay, old data, and rollback

Old delegations receive no invented closure. Derived views show them as
`legacy_not_evaluated`; they never contribute to local-closure success metrics.
Any historical repair is a separately authorized append-only batch with its own
report, not part of W3.

Invalidation or staleness makes a closure non-current in the derived projection;
it does not rewrite history. A new result creates a new closure candidate. The
feature flag disables new closure writes while preserving honest replay of events
already recorded. Rollback never changes the meaning of `task.accepted`.

## Required fixture and evaluation matrix

Before implementation, golden old-ledger fixtures and deterministic tests must
cover at least:

- ordinary technical work locally accepted while `task.accepted` remains absent;
- product, UX, security, strategy, competence, and authority triggers;
- missing, ambiguous, stale, invalidated, self, or otherwise unqualified review;
- one required review request or a typed hold for every in-scope requirement;
- duplicate, reordered, and concurrent open/transition requests;
- retry, parent restart, parent identity replacement, and wrong-parent attempts;
- direct child-to-operator thread/handoff rejection and valid root-to-human
  escalation;
- audited human intervention;
- result/evidence invalidation, correction tolerance, old-ledger replay, and
  feature-flag rollback.

Hard assertions are zero self-review, zero false strict acceptance, zero invalid
local closure, and 100% required-review pairing-or-hold. Tests use fixed clocks,
exact revisions, and sanitized fixtures; they do not store prompts, transcripts,
provider output, credentials, or copied live workspaces.

## Implementation boundary after approval

Only after this proposal is accepted and independently reviewed may W3 add narrow
typed modules such as:

```text
domain/delegation_acceptance.py
domain/review_pairing.py
domain/escalation.py
```

Service adapters remain narrow. The work must not grow `CommonsManager`, root
`cli.py`, `mcp/server.py::build_server`, `UIContext`, or legacy static UI. The
implementation is an additive schema/migration project with golden replay,
idempotency/CAS, security review, a feature flag, and an explicit rollback owner.

## Decision and review checklist

The owner and independent reviewer must affirm all of the following before W3:

1. additive `delegation_closure` is preferred over overloading `Delegation`;
2. the event vocabulary, record order, and compatibility strategy are accepted;
3. the operator-owned reviewer roster source and its owner are accepted;
4. fail-closed qualification, policy, and routing holds are accepted;
5. existing thread/handoff no-bypass enforcement is included in scope; and
6. the fixture matrix, flag, rollback, and historical-repair boundary are funded.
