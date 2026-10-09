# Agent Commons operating protocol

Status: MVP-0 shared-workspace contract plus the optional experimental local
delegation extension specified by ADR 0004.

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

A delegated independent reviewer may record verification only for the exact
target and revision of its bound review. The domain lifecycle revalidates this
constraint during writes and replay; a role label or MCP argument cannot widen
the verification target.

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

Task completion and submission retain the compatibility field `artifact_refs`
and also record `artifact_bindings` with each artifact's current effective
revision. Legacy events without bindings remain readable. For bound events, an
artifact revision or correction, invalidation of its effective event, or loss of
its content-addressed manifest makes the task artifact dependency stale. Any
review and acceptance derived from that task revision then becomes stale too.

If the review completion bound by `task.accepted` is corrected or invalidated,
that acceptance event is no longer effective and the task projects back to
`review`. The immutable event remains in the ledger, and a new acceptance can
bind the current effective review revision.

Replay reports machine-readable projection issues with stable codes, severity,
related event IDs, and repairability. `doctor` and the write guard fail closed
on issue severity, never on wording found in human-readable warnings. A
maintenance write is allowed through a damaged projection only when replaying
that exact correction or invalidation strictly reduces the structured error set
without introducing or worsening another error.

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

## 9. Delegate only exact, bounded work

A delegation records one purpose against one typed target and exact immutable
target revision. It selects a closed local profile; it never contains an
executable, shell command, environment override, secret, nonce, or arbitrary
provider prompt. `implementation` uses a builder profile, while
`independent_review` and `verification` use an independent-reviewer profile.

Every launch binds a newly registered child session distinct from the requester.
The effective operational state root is passed as fixed child-MCP argv and is
included in the launch-plan fingerprint; the child session is re-opened through
that same root before provider startup. An external `--state-root` therefore
cannot silently fall back to repository-local operational state.
The delegation carries hard depth, wall-time, attempt, concurrency, and budget
limits. The current supported writer accepts only `max_depth: 0`: every new
delegation is one leaf worker, and worker profiles receive no delegation-create
or broker-run tools. The ledger continues to replay older parent/child records,
but new recursive Codex/Claude chains are rejected until parent-sliced budgets,
fanout accounting, and worker authority are implemented together.
Implementation startup also carries ephemeral broker-selected repository, resolved
state root and existing child session facts. When the configured MCP's paired CLI
is available, commons-start reads use its trusted absolute sibling entrypoint with
explicit `--repo`, `--state-root`, `--session-id` and `--read-only` flags. Pairing
requires the same resolved installation bin directory and Python shebang; another
CLI earlier on PATH is never substituted. Login-shell exports cannot override the
explicit arguments. Custom wrappers or missing/untrusted siblings have no proven
CLI pairing: the instruction says so and requires an honest scoped needs-operator
outcome when mandatory startup cannot be completed. No CLI installation, session
creation, nonce disclosure or canonical CLI write is authorized. Canonical writes
and outcomes remain worker-scoped MCP operations. Reviewer native-tool restrictions
and instructions are unchanged. The final child-bound instruction is checked for
size and included in the existing immutable launch/skill/context fingerprint.


Those request limits never define their own global authority. Every broker using
one state root shares operator-owned global, per-provider, per-profile,
aggregate parent budget, and bounded queue limits. Admission applies the most
restrictive operator/profile/parent/delegation value and rejects a full or
expired queue with backpressure before allocating an attempt.

The current experimental broker enforces two units. `provider_units` is a
coarse launch budget: one unit equals one provider-process attempt, and
`max_attempts` must not exceed its limit. It is the preferred example for a
local provider CLI already authenticated through an operator-selected
subscription; Commons neither selects that account nor changes its billing
mode. `micro_usd` is explicit opt-in to a profile's provider-native monetary
cap. Its limit must reflect current provider/model pricing and reserve enough
room to record the canonical terminal outcome; the obsolete `$0.50` example is
not a safe default. `tokens` and mismatched unit/profile combinations fail
before reservation/spawn; canonical schema acceptance never implies executable
budget enforcement. Current Codex profiles require `provider_units` in addition
to trusted-workspace opt-in. Run broker preflight after provider/runtime
upgrades; it may start provider `--help` and MCP preflight processes but consumes
no attempt and starts no model work.

Every review that reads a shared checkout requires a quiescent subject whose
bytes match the exact registered artifacts/evidence bound to the reviewed
revision. Stop all writers for the duration of the review; if that cannot be
guaranteed, use an operator-provisioned quiescent worktree or immutable snapshot.
A read-only provider profile does not make a changing checkout revision-bound.

The canonical lifecycle records requested, active, input-needed, and terminal
outcomes through `CommonsManager` with exact expected revisions and stable
idempotency keys. The optional local broker owns only process execution,
pre-start cancellation, crash recovery, and metadata-only telemetry under
ignored operational state. MCP and CLI adapters call the same manager and
broker; they do not create another write path.

`input_needed` is a canonical state, not proof of a resumable provider channel.
The current headless MVP cannot resume or reattach an exited provider session;
such an attempt is classified `needs_operator`. Record only a bounded sanitized
requirement, never the answer or a secret, and create new work only after the
old attempt is terminal and no child can remain live.

The current runtime may cancel only requested work that has not launched. It
rejects active cancellation because no control path yet proves termination of
the provider process group before the canonical transition. Stop the provider
under operator control and reconcile; never mark active work cancelled first.
The original requester uses `delegation cancel`. If that requester is absent,
expired, or closed, an operator-authorized session declaring
`delegation:recover` may use `delegation recover` against the exact current
`requested` revision. This records the distinct `delegation.recovered` event,
projects to `cancelled`, and cannot apply after canonical provider start.
Capability metadata is a local coordination gate, not authentication or
external user authority. `session end` rejects a requester that still owns
requested, active, or input-needed delegations so ordinary shutdown cannot
silently manufacture orphaned work.

The broker launches only operator-configured allowlisted profiles. It grants no
new filesystem, Git, deployment, publication, network, communication, or truth
authority. A successful child process or `delegation.succeeded` event is not
task completion, independent approval, acceptance, or truth promotion. Inspect
the exact result references and apply the normal review and acceptance rules.
The current transport is trusted-local stdio under one operating-system user;
it has no remote authentication boundary and must not be exposed as a remote
service.

The safe default automated path is an independent reviewer whose MCP scope is
immutable: its own delegation, target review/outcome, and bounded repository
list/read/literal-search. It receives no native filesystem, shell, edit, web,
subagent, runtime, or delegation-creation tools. Writable builders and current
Codex CLI runners are trusted-workspace-only; require explicit operator profile
opt-in and external OS isolation for untrusted content. A provider permission
prompt or worktree alone is not host isolation.

Automatic operational retry is permitted only while the canonical delegation
is still pre-start and the journal proves no child may be running. An ambiguous
post-start crash becomes `needs_operator`; terminal work requires a new
delegation. Prompts, responses, reasoning, file contents, commands, environment,
raw output, and transcripts are excluded from durable telemetry. See
[ADR 0004](adr/0004-optional-local-delegation-runtime.md) for the complete trust,
crash, cancellation, observability, compatibility, and rollback contract.

## 10. Separate a standing role from a situational run

A **role** is persistent staff: a name, an operator-allowlisted profile, standing
permissions, a place in the index other work can reach, and a history that
outlives any single task. A **delegation** is one bounded run with a fresh child
session and a terminal outcome. A run creates no role record, and "give me an
independent review with a clean context" is fully covered by a delegation to an
independent-reviewer profile. The separation is load-bearing, not clerical: a
fresh context is the mechanism that makes review independent, and a run modelled
as an instance of a role invites inheriting that role's context while still
looking independent by `session_id`.

A role selects a profile; it never describes one. Role settings may only narrow
what the profile already grants, so no role setting can widen a child's
authority. Choices come from an operator-owned catalogue held outside the
delegated workspace; the model a role uses is a property of the profile it
selects.

Independence is decided over principals — session plus the role that session was
running as — so one standing role cannot approve work it authored in an earlier
run. Reviewing the same subject again at a later revision remains allowed; only
authoring and then judging is refused.

Three permissions, each `deny`, `ask`, or `auto`: create roles, retire roles,
and open temporary links. Effective authority is the narrowest value across the
role and every creator above it, derived on each call, so a level lowered
anywhere up the line binds work that is already running. A grant above `deny`
requires a turnover budget counting creations and retirements together. An
automatically created role holds a strictly narrower creation grant than its
creator. A role created by another role is marked as such and records the reason
and the proposer, whether it was recorded automatically or confirmed by a
person.

Removal is `retire`, never delete: the ledger is immutable. No role — however it
was created, and whoever asks — may be retired while it owes a live delegation or
an unfinished review. A role never retires a human-created role. Prefer
declaring a task lifetime at creation to granting a standing right to retire.

A temporary link between two roles records the action it permits, not an
open/closed flag. Today the only action is a bounded question with a deadline,
carried over the existing communication channel. The canonical record is the
grant; the exchange is operational.

Work reaches the system through a person. The standing conversation between that
person and the roles at the top of the chain of command is an `engagement`
thread: where the task is first stated, where progress is discussed, and where
feedback returns. Several roles at the top share one thread with several
recipients rather than a thread each, because merging separate threads in a view
would assert an ordering no record holds. A chat addressed to one role is the
situational channel for stepping in on that role alone.

Recipients are fixed when a thread opens. A role created afterwards is reported
as unaddressed rather than folded in, since rewriting canonical recipients from
a projection is exactly what a view must never do.

A delegated worker may reply only in a thread that addresses it or the role it
runs as. Its reply is how feedback returns; it is not a way into every
conversation in the workspace.

Role settings, messages to a role, and retirement may be recorded from the local
UI whenever it is not `--read-only` — `agent-commons ui` opens a writing panel
by default, no capability flag required. That surface is an adapter over
`CommonsManager` exactly as the CLI and MCP adapters are; it does not create a
second write path, and the capability-granting half of the catalogue is not
editable from it.

### Agent organization and method versions

[ADR 0027](adr/0027-agent-organization-and-method-edits.md) separates an editable
organizational supervisor from immutable creator provenance. Human operators
create or reconfigure `supervisor_agent_id` with canonical revision checks;
null clears it. The parent must be an active persistent non-template agent and
the graph must remain acyclic. These validations run under the canonical lock
and during replay. Organizational edges grant no authority, inheritance,
recursive execution or retirement cascade. `created_by_agent_id` retains its
existing security meaning. The UI projects organization as `supervised_by`.

Modern method edits save an immutable library role and CAS-reconfigure the
agent's exact `specialization_ref`. The writer composes and retains the full
role/skill bundle from trusted service storage. A real binding change requires
an unbound human operator and no requested/active/input-needed delegation or
unfinished review. Legacy skill arrays remain separate. Identical committed
retries recover the original receipt before checking later lifecycle changes;
new changes with stale revisions refuse. No historical role version or other
agent's binding is updated implicitly.

These fields use `agent.v3` and reader semantics floor 8; v1/v2 keep a null
supervisor by default and their existing semantics. Historical supervisor and
specialization bindings are immutable to event correction. Operator editing
reads full values and the revision through `/api/work/agents/{id}`; a DTO above
512 KiB refuses rather than returning shortened editable arrays. Library
version saving and canonical rebinding are separate retryable operations, not
a cross-store transaction. A saved but unbound version remains available for
retry and does not change a running agent.

## 11. Keep Git operations explicit

Agent Commons does not stage, commit, push, merge, publish, or assign ownership
of repository changes. Those actions require separate user authority. Managed
instruction blocks may be updated idempotently, while all project-authored text
outside those markers remains untouched.

## 12. Use the lightest adequate governance

- `light`: coordination for small/reversible work may stop honestly at
  `completed`; review and accepted project truth are optional.
- `standard`: submits the exact completed revision and uses the existing
  independent-review boundary before `accepted`.
- `governed`: adds reproducible verification, revision-bound evidence,
  explicit decisions/dissent, and operator-controlled acceptance for
  high-impact changes.

Small changes should not require ceremonial records. High-impact, irreversible,
security-sensitive, or externally visible decisions should not bypass governed
promotion. These modes choose where the same lifecycle stops; they do not
weaken integrity rules or create a hidden acceptance bypass. Local
principal/model labels are not an authority mechanism.

## Retained worker results

[ADR 0025](adr/0025-retained-worker-results.md) defines the local durable-result
boundary. `commons_publish_design_image` now retains validated PNG/JPEG bytes;
`commons_publish_static_build` retains a deterministic ZIP of a repository-relative
static dist directory. Both are implementation/verification worker tools and prove
current worker/task/delegation bindings before canonical publication. Publication
neither completes the task nor requests/approves review or accepts work.

Existing artifact manifest metadata adds `retained_content: {revision, size_bytes}`
matching the manifest digest and size. Generated output provenance remains a closed
contract. Static build metadata uses `output_kind: task_static_build` and adds
`build_files` (path, SHA256 revision, size); ZIP includes
`commons-build-manifest.json`. Image bounds remain 10 MiB/16 million pixels. Build
bounds are 128 files, 128 directories, depth 16, 10 MiB per file and 20 MiB for the
whole ZIP. An `index.html` is required. Frontend extension allowlist, ordinary
single-link files, descriptor traversal without symlinks, and exact byte verification
exclude hidden/private entries, external paths, special files and link escapes.

The local `output-content` directory below workspace state retains at most 512
objects/256 MiB. Content addresses are SHA256 names. Publication takes the manager
canonical lock, then the content-store process/thread lock; readers never acquire
a canonical write lock while holding a store lock. Write, fsync and atomic link
precede canonical append. An interrupted pre-append object remains quota charged;
identical retries reuse it and repair an interrupted link to their own verified
partial object. Partial files never become readable objects. Quota
failure refuses publication and preserves earlier outputs; no automatic eviction
exists. Backup/restore must include this store. Missing/corrupt bytes have an honest
unavailable preview. Legacy metadata-only images continue checking original source
bytes, and cannot be reconstructed from hashes. New versions need new operation
keys; reuse conflicts if content or intent changed. An identical retry of the exact
historical five-field image metadata returns its original artifact/event revision
with `content_copied: false`; it neither copies bytes nor upgrades ledger history.
Publishing retained bytes for that image requires a new operation key.

`/api/outputs/builds/{artifact_id}/{artifact_revision}` is an authenticated,
project/scoped, exact-version ZIP attachment with `nosniff` and `no-store`; it never
serves build HTML. Browsing the gallery never downloads a build or launches a
server. Live advertisements retain their separate TTL/reachability contract.

Output DTOs add `retained` (old default false) and `result_review_state` (old default
null). Existing `review_state` describes the current task review. Exact result
standing comes only from an artifact-target review of the displayed artifact event
revision. An approval requires independence and, for a delegated reviewer, a
matching succeeded delegation. It does not propagate to another version and does
not imply task acceptance or Design Package acceptance.

Independent-review worker tools `commons_read_output_image` and
`commons_read_build_file` inspect only frozen in-scope artifact revisions. The image
tool returns actual verified MCP pixels. The build tool verifies the entire retained
bundle and requested entry hashes, returns manifest or UTF-8 text chunks of at most
65,536 characters, and rejects non-text/unknown entries. Whole-entry policy scanning
precedes slicing, so secrets spanning chunk boundaries remain redacted. A session
cache reuses checked text only for the frozen manifest/entry digest (at most 128
entries/20 MiB of text and redaction accounting); each read still verifies the
current frozen binding, CAS object and entry bytes. Cache eviction removes no
retained content. All text entries must be
read without gaps before artifact evidence satisfies `commons_finalize_review`;
manifest-only reads do not. Binary assets have verified hashes but are not rendered
by the build-file tool. Evidence coverage is separate from a substantive review:
criteria/summary must state visual, execution and test limitations honestly. Changed
artifact binding, missing bytes, failed verification and out-of-scope reads refuse.

### Text reports and explicit task handoff

Implementation workers additionally receive `commons_publish_text_result`,
`commons_complete_task_result`, `commons_submit_task_result` and
`commons_finalize_task_result`. Verification and review workers do not inherit
these authoring tools. Every operation derives its task and delegation from the
bound child session, validates the exact expected revision and uses an explicit
idempotency key. Publication retains inline UTF-8 text (at most 64 KiB), a title
(256 bytes), summary (4096 bytes) and at most 32 check descriptions (512 bytes
each). It accepts no arbitrary source path and uses the same immutable content
store and quota as other results.

Before a UI implementation launch binds its delegation revision, a locked,
retry-convergent transition activates a ready or assigned task. It preserves
owner and exact-revision checks. Publication leaves task state unchanged.
Completion binds 1–64 exact artifacts
published by that worker; submission preserves the same evidence set and moves
the completed revision to review. Finalization records the submitted task/event
references in the delegation outcome. None of these operations approves a review
or accepts the task. After success, a fresh MCP process exposes only the exact
terminal receipt retry; a changed key or payload refuses, and no authoring tools
remain available. An omitted CLI `--artifact-ref` preserves existing evidence;
an explicit replacement remains distinct from omission.

Output projections include `kind: text_result` with human title, summary and
checks. Authenticated `/api/outputs/text/{artifact_id}/{artifact_revision}` reads
return verified retained text for the requested task/agent scope, never an
unverified source-path fallback. The task-details endpoint
`/api/outputs/task-results/{task_id}` requires the exact `task_revision` and
returns full description and acceptance criteria with bounded evidence pages
(1–64 entries, default 32), total and next offset. The complete DTO is capped at
512 KiB; oversize content refuses explicitly instead of silently truncating.
Every page rechecks the revision; changed tasks refuse with
`results_task_revision_changed`. Identifiers are provenance, not the primary
human description of a result. Stale bindings remain visibly stale.
