# Agent Commons operating protocol

Status: MVP-0 contract for a project-local, shared-filesystem workspace.

## 1. Keep information layers separate

Agent Commons separates project policy, working communication, immutable
evidence, and effective project truth.

- Objectives, requirements, acceptance criteria, authority, and operating
  constraints form project policy.
- Tasks, proposals, questions, critiques, messages, and handoffs form the
  working space.
- Registered artifact revisions and reproducible observations form evidence.
- Verified findings and accepted decisions form effective truth until they are
  corrected, invalidated, contested, or superseded.

Discussion is durable but provisional. Agreement between agents is never an
implicit promotion rule.

## 2. Start every session with orientation

Every window registers a distinct session with an accurate client identity, a
stable work role, and declared capabilities. Model and capability labels are
coordination metadata; authority must come from the operator/user workflow.
MVP-0 records that metadata but does not authenticate it.

Before taking work, run the integrity check, read a bounded orientation, inspect
the addressed inbox, and review active tasks, claims, discussions, reviews, and
handoffs. Resolve critical integrity failures before ordinary canonical writes;
only an identical idempotent retry or a preflighted correction/invalidation that
strictly improves the reported state may write during recovery.
Receipt integrity is evaluated in the current checkout/branch scope. Missing
post-commit receipts are reconstructed only from fully validated canonical
events and a non-shrinking ledger-completeness anchor. A local in-flight orphan
blocks ordinary new writes, but its exact retry may complete even when several
local orphans exist. When the original request is permanently lost, a session
declaring the explicit `receipt:abandon` capability may write an operational
audit tombstone. A later Git arrival reconciles that tombstone only when its
namespace, key digest, semantic hash, and event ID all match exactly; the audit
record is never deleted. Use `receipt status` and `receipt reconcile` for the
read-only diagnosis and deterministic recovery contract described in
[ADR 0003](adr/0003-ledger-derived-checkout-aware-receipt-recovery.md).
Reconcile never clears a receipt-without-event: finish that in-flight operation
with the same stable idempotency key or abandon it explicitly. Legacy orphan
adoption requires naming its digest; rollback preparation is per-checkout and
requires all linked-worktree writers to stop before a v1 binary is used.

## 3. Coordinate tasks and claims

A task records an outcome, acceptance criteria, dependencies, current state,
and durable assignment. A claim is a temporary lease used to reduce duplicate
or overlapping work.

- Reuse or refine an existing task before creating a similar one.
- Split work along verifiable outcomes, not arbitrary agent boundaries.
- Acquire a claim before overlapping edits or exclusive resource use.
- Renew a claim only while the protected work remains active.
- Release it when the session pauses or ownership ends.
- Break a stale or unsafe claim only with an auditable reason.

A claim is not Git ownership, authentication, or authorization to discard
another participant's changes.

## 4. Discuss without contaminating truth

Use typed threads for proposals, questions, critiques, risks, help, reviews, and
decision requests. Link a thread to its relevant task, artifact, finding, or
decision. Thread relations are navigational entity references; register and use
revision-bound evidence when the exact content revision matters. Reply in the
existing thread and state the desired resolution.

Preserve meaningful objections and uncertainty. If a discussion does not
converge after a small number of substantive rounds, request an authorized
decision instead of generating more commentary.

Do not store private reasoning, routine status chatter, or complete logs. Record
only information another session will need after the current context is gone.

## 5. Separate review, verification, and acceptance

A review is a scoped expert judgment. A verification records a reproducible
fact. Acceptance is a governance transition.

Every review and verification binds the exact subject revision and explicit
criteria. A changed revision makes previous judgments stale. When independent
review is required, the authoring session cannot satisfy it itself. In MVP-0,
every task acceptance binds the current approved independent review revision,
completed by a session outside the task's accumulated work-author set. Taking,
starting, blocking, unblocking, or completing work records that session as a
work author; submitting or accepting alone does not. This is a protocol
invariant, not a configurable switch, and it remains true when another session
submits the work after a handoff.

Every evidence entry is revision-bound. CLI and manager callers may supply a
plain `kind:id` reference only as input; the manager resolves and records
`{ref: {kind, id}, revision}` before publication. An immutable manifest binds to
its manifest ID, while an event binds to its current effective correction head.
Stale reviews, verifications, verified findings, and accepted decisions remain
visible as history but are excluded from effective truth.

If the review completion bound by `task.accepted` is corrected or invalidated,
that acceptance event is no longer effective and the task projects back to
`review`. The immutable event remains in the ledger, and a new acceptance can
bind the current effective review revision.

Task completion means the author believes the work is ready. It does not imply
review approval, requirement satisfaction, or acceptance.

## 6. Promote project truth explicitly

A finding begins as a report and may become verified, contested, or resolved;
an erroneous finding event can be invalidated through the maintenance workflow.
A decision begins as a proposal and may be accepted, rejected, deferred, or
later superseded.

Promotion records:

- an exact subject revision;
- revision-bound canonical evidence when evidence is supplied;
- any review, verification, or evidence gate required by the operator workflow;
- an actor that the operator/client workflow permits to make that choice;
- preserved alternatives and dissent where material.

The service enforces evidence for verified findings. Decision acceptance always
requires a rationale but leaves any additional evidence/review gate to the local
operator workflow in MVP-0; the CLI cannot authenticate or prove that authority.

Conflicting active decisions in one scope fail closed. Agent votes or model
family diversity do not replace evidence or authority.

MVP-0 records the actor but does not authenticate that authority; accepted
records coordinate trusted local participants and never authorize an external
side effect.

## 7. Hand work off explicitly

Before pausing, update task states, record durable findings and decisions, and
create a handoff. A handoff identifies completed and active work, typed artifact
and task references, blockers, risks, open questions, and concrete next actions.
Related references are navigational; register revision-bound evidence when the
recipient must recover an exact artifact revision. Release claims not protecting
live work. A targeted recipient acknowledges the handoff without rewriting it.

## 8. Preserve immutable history

Use the supported writer for every canonical or coordination change. Existing
events, manifests, and receipts are never edited to improve current state.
Thread messages are events rather than a separate writable store. Recording
errors use corrections; changed assertions use invalidation or supersession;
reopened work uses an explicit state transition.

A correction cannot change identity, causal revisions, targets, dependencies,
manifest/content revisions, or evidence references because the immutable event
envelope would retain the old relation graph. Invalidate and record a new event
when one of those structural links was wrong.

Human-readable briefs, boards, indexes, and graphs are rebuildable projections.
They must never become an independent source of truth.

## 9. Keep Git operations explicit

Agent Commons does not stage, commit, push, merge, publish, or assign ownership
of repository changes. Those actions require separate user authority. Managed
instruction blocks may be updated idempotently, while all project-authored text
outside those markers remains untouched.

## 10. Use the lightest adequate governance

- `light`: orientation, task/claim, communication, review, and handoff.
- `standard`: adds durable findings, decisions, and acceptance workflows.
- `governed`: requires independent review, verification, and operator-controlled
  authority for high-impact changes.

Small changes should not require ceremonial records. High-impact, irreversible,
security-sensitive, or externally visible decisions should not bypass governed
promotion. MVP-0 ships the `standard` mechanics and independent task-acceptance
guard. Named policy presets and authenticated operator authority are roadmap
items; local principal/model labels are not an authority mechanism.
