# Product and evals assessment of the corrected architecture reviews

**Status:** recommendation for the next planning synthesis; not an accepted
decision and not evidence that any capability is implemented.

**Scope:** this assessment evaluates only
`codex_architecture_improvement_review.md` and
`claude_architecture_improvement_review.md`.  It deliberately does **not** use
`agent_commons_product_architecture_review.md`.  The approved
Context Pack / Design Gallery programme is mentioned only to preserve its
already-decided scope; its implementation status is not re-assessed here.

**Revision discipline:** the Codex review is bounded at
`4844fdbc95adc12ed9b11937d1eb6415f3fb3ba6`; the Claude review labels its
operational numbers as a 24-Aug-2026 snapshot.  Neither document promotes its
recommendations to project truth.

## 1. Answer first

The strongest product direction is **not more autonomy first**.  It is a
reliable, inspectable path from submitted work to a correct next human action:
every required review is requestable and reachable, every terminal runtime
outcome is classified, and the operator can see the few items that need a safe
decision.  This is a better near-term user promise than a scheduler because it
removes lost work and manual forensic coordination without weakening the
ledger, independent review, or human authority.

The two reviews are directionally aligned on five product principles:

1. Treat the ledger, exact-revision evidence, stale cascade, independent
   review, and `completed != accepted` as the trust product, not incidental
   plumbing.
2. Prefer derived views over a speculative canonical migration where existing
   events can already explain the user-facing state.
3. Make `task next` an explainable, operator-visible pull recommendation before
   considering autonomous push-dispatch.
4. Measure workflow outcomes and harmful failure modes before claiming that
   more agents or roles create value.
5. Keep Context Pack and read-only Gallery as a parallel, bounded product
   track—not as a substitute for proving the general coordination loop.

The Claude review's operational diagnosis is high-value **evidence to
re-measure**, not a current baseline: its numbers are explicitly a 24-Aug
snapshot.  The plan must regenerate the query at the start of Phase 0, pin the
query version and measurement window, and publish both numerator and
denominator.  No current release gate should be justified by a historical
count alone.

## 2. Product verdict by proposal

| Proposal from the corrected reviews | Product / operator value | Assessment | Preconditions and risk |
|---|---|---|---|
| `submit -> current independent review request` coupling | Removes the most damaging invisible failure: work appears ready for review but has no reviewer-facing request. It lowers reviewer search and founder follow-up. | **Highest-value first repair, conditional on fact check.** | The owner must choose whether submit refuses without an eligible route or creates an unassigned attention item. A second canonical write must be atomic or recoverable/idempotent; backfill must not fabricate review evidence. |
| Parent-side finalisation plus environment preflight | Converts an avoidable `needs_operator` escalation into a bounded, explainable result; protects the operator from manually completing provider bookkeeping. | **High value, but security-sensitive.** Prioritise preflight and an eval before a parent writes any artifact/result. | A worker report is untrusted data, never a command or acceptance. Parent-side finalisation needs path/hash attestation, size limits, idempotency, a typed failure path, and no new worker authority. |
| Handoff typing, supersede and ageing attention | Reduces stale "please continue" noise and makes an owner see an ageing handoff. | **Useful hygiene, not the first vertical outcome.** | Never auto-acknowledge or auto-close. Recipient typing and supersede semantics may change persisted behaviour; require a separate owner decision and replay contract. |
| Read-only orient repair / composite start | Cuts time-to-first-use for every new operator/agent window and prevents onboarding ceremony from hiding system state. | **High-leverage DX repair.** | A convenience `start` command must never borrow another session; read-only orient must produce no write, receipt, or hidden session mutation. |
| Derived execution/acceptance views and task-side RunView | Lets the operator distinguish execution, review, acceptance, retries and recovery without claiming that a provider exit is acceptance. | **Good UX/read-model hypothesis; defer canonicalisation.** | Need equivalence tests over historical fixtures and clear labels for unknown states. Calling `Delegation` a `Run` is not itself a product outcome. |
| Operator Attention Queue before full Decision Inbox | Gives the founder one honest answer to "what needs safe action now?" across missing reviews, recovery, stale evidence and handoffs. | **Build only after metric definitions, initially read-only.** | Prevent a noisy second inbox. Every action must use existing revision-bound command paths; sample cards with humans before making the queue the primary surface. |
| Pull `task next` plus SchedulerTrace | Reduces manual routing while preserving an explanation and an operator decision. It also creates labelled data about recommendation usefulness. | **A later outcome experiment, not Phase 0/1.** | Requires a factually correct readiness predicate, authority boundary, task revision binding, and a no-write recommendation path. LLM ranking is advisory only. |
| AcceptancePolicy as an additive task field | Could let genuinely low-risk work end honestly as `NOT_REQUIRED` rather than forcing fake approval or permanent backlog. | **Do not put in the smallest slice.** | It changes persisted Task semantics despite being called “additive”; it needs an owner decision, schema/migration/replay contract, policy owner, and explicit UX for light/standard/governed. |
| Context compiler manifest, token policy, cache and Pack diff | Makes the approved Context Pack promise inspectable: what sources were selected, what was truncated, and whether two child runs share a baseline. | **Plausible extensions to the approved parallel track, not automatically included.** | Treat each as an extension decision. Manifest must remain operational/derived, redact prompt body and secrets, and have retention/size limits. Cache correctness must be keyed by the full deterministic compilation input. |
| Gallery as a board/editor | Lets a product operator inspect visual artifacts without leaving the product; supports feedback to a designer. | **Remain on the approved read-only V1 track.** | It does not validate scheduler or governance value. Preserve the existing image-only, revision-bound, safe-preview scope; do not pull visual editing, hotspots, or arbitrary media forward. |

## 3. User, business and operator hypothesis

### Situation and complication

The intended buyer/operator is a founder or small local team coordinating
otherwise isolated agent windows.  Their costly work is not merely starting an
agent; it is reconstructing what happened, finding a reviewer, distinguishing
provider completion from accepted work, and deciding whether recovery is safe.

The corrected reviews hypothesise that three feedback loops are losing work:
`submit -> review`, `delegation -> canonical finalisation`, and
`handoff -> acknowledgement`.  If a fresh measurement confirms that diagnosis,
adding scheduling first would increase activity while leaving the operator's
closure work unchanged or worse.

### Testable value proposition

> For a bounded work item, Agent Commons should show the current
> revision-bound state, create or expose the next required review/recovery
> action, and preserve independent acceptance—so an operator spends less time
> routing and diagnosing without accepting less trustworthy output.

This is deliberately a **flow-quality** proposition.  Number of roles, runs,
messages, board nodes, generated tasks, or tokens are activity counters, not
success measures.

### Expected value and counter-metrics

| Stakeholder | Expected gain if the hypothesis is true | Counter-metric / downside to watch |
|---|---|---|
| Founder / operator | Less time discovering missing review, stale handoffs, and ambiguous process outcomes; clearer next safe action. | Attention queue becomes a noisy second inbox; manual actions shift rather than disappear. |
| Builder agent | A bounded and visible recovery/finalisation path; fewer repeated attempts with lost context. | Parent report channel could falsely promote unverified output or leak sensitive data. |
| Reviewer | Receives a current, independently routable request rather than scanning a task warehouse. | Auto-routing sends inappropriate volume or routes an author to self-review. |
| Product team | Evidence for whether governed pull execution reduces routing work before investing in autonomy. | Metrics improve by closing/hiding work rather than improving accepted-result quality. |
| Context/Gallery user | Shared research baseline and inspectable visual artifacts continue to progress on the already-approved path. | Core-loop work gets starved, or Gallery scope expands into unsafe editing/media handling. |

The measurement frame should use multiple system-level dimensions: accepted
result quality and safety, flow latency, collaboration/review health, and
operator satisfaction.  It must not rank individual agents from telemetry.

## 4. Smallest measurable Phase 0 and Phase 1

### Phase 0 — evidence, contract and safe test harness

**Goal:** establish whether the loop diagnosis is real today and define the
behaviour that must never regress.  No autonomous dispatch, no new canonical
entity, no silent persisted-schema change, and no expanded worker authority.

1. Run one versioned, read-only measurement query over an explicitly declared
   window.  Store the query definition, not prompts/transcripts, and label the
   resulting dashboard with its window and snapshot revision.
2. Define the three loop contracts in plain language and record owner choices
   in section 7 before code starts: review pairing/routing, parent-finalisation
   trust boundary, and handoff lifecycle.
3. Create the fixture-workspace eval harness in section 5 and implement the
   deterministic cases for the three loops plus read-only orient.  Reproduce
   failures from sanitised canonical records only after fact-check confirms
   them; do not copy private worker output.
4. Add a golden replay comparison for the selected historical fixture.  A
   projection change is allowed only with an intentional, reviewed expected
   diff; normal replay must remain byte/semantically stable according to the
   existing contract.
5. Publish a read-only daily/weekly metric view.  It may expose a missing
   action, but it must not create review, accept work, or close handoffs.

**Phase-0 exit gate:** every metric has a current measurement window,
numerator, denominator, owner, and action; all fixture tests pass; the owner
has chosen the first loop to repair.  A historical 24-Aug number is useful for
comparison, not sufficient to pass the gate.

### Phase 1 — one closure loop: required review becomes reachable

**Goal:** prove one user-visible improvement end-to-end, rather than ship a
wide control plane.  Recommended first vertical is review pairing because it
has a clear user contract and entirely deterministic success condition.

For every *new* task revision that enters a policy requiring independent
review, the system must either:

1. create exactly one current, non-stale review request with an eligible
   independent route; or
2. refuse/hold the transition with a typed, operator-visible reason that no
   eligible route exists.

The workflow must never create approval, fabricate a review verdict, or
silently let the task look reviewable without an actionable request.  Historic
warehouse repair is a separate, operator-confirmed batch: it can create new
requests or mark candidates for manual disposition, but cannot rewrite old
events.

Deliver together:

- read-only Attention item for a missing/unroutable review request;
- idempotency and stale-revision handling for retry/re-submit;
- a reviewer-facing request/queue projection with exact target revision;
- the deterministic review-pairing eval suite; and
- a short operator baseline study: record the time from submit to a valid
  request and the time from request to a human action for a declared sample.

**Phase-1 exit gate:** 100% of *new, in-scope* transitions into required review
are paired or explicitly refused in deterministic and live sampled evidence;
false acceptance and self-review are both zero; all changes remain reversible
by disabling only the coupling while retaining ledger history.  This gate says
nothing about autonomous scheduling.

### What follows, but is not Phase 1

If fact-check confirms the finalisation-gap diagnosis, the next vertical should
be **preflight first**, then a narrowly attested parent finalisation path.  Do
not batch it with review coupling: it has a different trust boundary, attack
surface, and owner.  Handoff supersede, AcceptancePolicy, objective admission,
`task next`, and any push scheduler stay outside this smallest proof.

## 5. Eval design: data, harness, graders, traces and gates

### Eval objective

The eval objective is not "did the agent sound competent?"  It is:

> Does the workflow preserve revision-bound governance while reliably making
> the next necessary review or recovery action visible and reachable?

This is both a **capability** suite (can the new loop work?) and, once stable,
a **regression** suite (did an instruction/profile/command change break it?).

### Dataset and fixture plan

Start with a small, versioned, hermetic fixture workspace: 24 cases, balanced
between valid, edge, and refusal/recovery behaviour.  Seed it from sanitised
local incidents only where the fact-check can bind an incident to a case;
otherwise use explicit synthetic cases.  Each case has a task statement,
initial canonical state, allowed action, expected resulting state, and
case-specific grader.

| Family | Example cases | Primary evidence / grader |
|---|---|---|
| Review coupling (6) | valid submit, duplicate retry, resubmit after revision, stale request, no eligible independent reviewer, self-review attempt | exact request cardinality, target revision, independence predicate, typed refusal |
| Finalisation boundary (6) | valid bounded report, duplicate report, path/hash mismatch, absent file, oversized report, environment preflight mismatch | parent attestation/state check; no canonical terminal success on invalid input |
| Handoff hygiene (4) | canonical recipient, invalid alias, narrowly scoped supersede, ageing item | recipient validation and projection result; no automatic acknowledgement/close |
| Read-only / session DX (4) | no-session orient, active own session, foreign session, composite start retry | no canonical write in read-only mode; no cross-session borrowing |
| Replay, privacy and safety (4) | historical unpaired task, stale evidence, secret-like payload, replay after retry | stable replay, redaction/size rule, no acceptance from process exit |

This is an initial capability set, not a claim of production representativeness.
After every real incident, add a sanitised regression case with a failure tag
and source reference.  Do not mark a planned or unsupported capability as
passing.

### Harness and trace contract

Run every fixture in a fresh temporary workspace with a fixed clock and,
where runtime is exercised, a deterministic fake provider.  Reset filesystem,
ledger, operational state, and cache between trials.  Capture:

- `case_id`, harness/schema version, source and code revision;
- input fixture hash and policy/config version;
- expected and actual canonical event refs plus derived projection before/after;
- tool/command outcome class, idempotency result, and typed refusal class;
- elapsed time and bounded resource counters where available.

Do **not** retain prompt bodies, transcripts, hidden reasoning, raw provider
arguments, secrets, or unbounded worker output.  A real-provider canary, if
later authorised, records the same bounded evidence plus a trace correlation
identifier—not the conversation.

### Grader mix

| Layer | What it grades | Grader | Required bar |
|---|---|---|---|
| L0 — pure domain/projection | cycle/readiness, revision pairing, stale cascade, metric formula | deterministic unit/property assertions | 100% on every run |
| L1 — command/ledger contract | idempotency, atomic/recoverable coupling, replay, no false acceptance | fixture state and event-order assertions | 100% on every run |
| L2 — runtime safety | preflight, invalid terminal report, timeout/crash/duplicate terminal result | deterministic fake-provider state checks | 100% on every run; `pass^3` for race/concurrency cases |
| L3 — opt-in real-provider workflow | provider respects bounded finalisation/review protocol on a golden task | deterministic ledger/attestation grader first; human trace audit second | non-blocking until calibrated and reproducible |
| UX / Attention | card has a correct owner, why-now, evidence and safe action | blinded operator review against a written rubric | calibrate on 20+ cards before using an automated/LLM judge |

Do not use an LLM-as-judge for lifecycle correctness when event/state assertions
can decide it.  If one is added for explanation quality later, make it
reference-guided, allow `unknown`, calibrate it against blinded human labels,
and never let it decide acceptance or authority.

### CI and release placement

| Stage | Runs | Failure action |
|---|---|---|
| Local / presubmit | L0, L1, schema and privacy checks for changed command/projection code | block the change; show case and state diff |
| Post-submit / nightly | L2 fake-provider trials, golden replay, repeated race cases | open a typed failure item; no blind rerun-to-green |
| Release candidate | critical L1/L2 plus manual Attention sample | do not enable coupling if safety bar fails |
| Opt-in canary | a small real-provider golden workload, bounded by operator limits | disable the feature flag on any false acceptance, stale target, or finalisation-attestation failure |

Changes to runtime instructions, profile permission modes, provider/model
versions, scheduler policy, or context compiler selection must declare which
eval layers they re-ran.  Do not couple long/flaky provider experiments to the
fast presubmit path.

## 6. Decision-ready metrics and guardrails

The table distinguishes invariant gates (where the threshold is necessarily
zero or one) from operational targets.  Targets marked **proposed** must be
calibrated after a fresh baseline; they are not facts derived from the
24-Aug snapshot.

| Metric | Definition (numerator / denominator) | Cadence and owner | Threshold / trigger | Required action |
|---|---|---|---|---|
| Required-review pairing coverage | in-scope new task revisions entering required review with one current, non-stale, independently routable request / all in-scope new revisions entering required review | per transition + daily; workflow owner | **100% hard gate** | refuse/hold unrouteable submit; investigate any unpaired revision |
| Review disposition latency | time from valid current request to a review verdict or typed escalation; report p50 and p90 | weekly; product + review owner | establish two-week baseline; proposed operating target p50 <= 7 days, p90 <= 14 days | route/escalate ageing request; never auto-approve |
| Finalisation integrity | terminal provider outcomes that receive a valid attested canonical terminal result / terminal provider outcomes eligible for the channel | per terminal + weekly; runtime owner | preflight/parent channel: **0 invalid successes**; before broad enablement, 0 finalisation gaps in 50 observed eligible terminals | disable parent finalisation on any invalid success; classify failure and add regression case |
| `needs_operator` taxonomy completeness | terminal `needs_operator` outcomes with a recognised typed failure class / all terminal `needs_operator` outcomes | weekly; runtime owner | **100% hard data-quality gate** | no rate claim until unclassified outcomes are triaged |
| Handoff action health | open handoffs with an acknowledgement linked to a valid follow-up ref within the window / open handoffs that require acknowledgement in that window | weekly; coordination owner | establish baseline; proposed p50 <= 7 days and no automatic closure | make ageing handoff visible in Attention; audit recipient semantics |
| Attention precision | sampled cards judged by blinded operator as correctly actionable / sampled non-suppressed cards | weekly sample; product/UX owner | calibrate on >=20 cards; proposed >=90% thereafter | tune dedup/severity/source; do not expand the main surface if below bar |
| Objective coverage | active in-scope tasks with an explicit valid objective ref / all active in-scope tasks | weekly; product owner | no target until owner decides whether every task must have an objective | diagnose UX/workflow cause; do not use autonomous-work ratio yet |
| Operator routing load | manual routing/forensic actions outside an explicit next-safe-action flow / accepted or safely closed in-scope work items | baseline and per release; product owner | must improve over declared manual baseline without guardrail regression | revert/hold feature if faster closure simply moves work off-ledger |
| False acceptance | accepted tasks without required current independent evidence / all accepted tasks | per event; governance owner | **0 hard gate** | invalidate/disable path; incident review |
| Stale-target launch or self-review | prohibited launch or review events / all respective attempts | per event; runtime/governance owner | **0 hard gate** | kill switch / block relevant command path |

For rate claims, publish the sample size and the measurement window.  A low
percentage based on a handful of outcomes is not a release signal.  This avoids
the harmful incentives of closing, cancelling, or relabelling work merely to
improve a dashboard.

## 7. Owner decisions required before implementation

| Decision | Why it cannot be inferred | Recommended framing for the owner |
|---|---|---|
| Review-pairing contract | A submit can create a request, refuse when no eligible reviewer exists, or hold it in attention. These create different UX and event semantics. | Choose the explicit contract for each policy: **recommended default:** no silent transition into required-review without a current actionable request; unrouteable work gets a typed hold/refusal, never synthetic approval. |
| Reviewer routing and independence | The system cannot infer who is trusted to review a work type from a title or model label. | Name initial routing rules and fallback owner; preserve current principal/session independence predicate. |
| Parent finalisation trust envelope | A worker-composed report can be incomplete, malicious, or stale; parent authority must be narrow. | Permit only an attested bounded report that identifies existing outputs; parent validates independently and can record typed failure, never acceptance. |
| Historical backlog remediation | Creating requests for past tasks may be helpful but changes present operator workload and can surface work that should be cancelled/reopened. | Approve a separate, reversible, reviewed batch policy; never rewrite old events or auto-dispose the backlog. |
| AcceptancePolicy / light work | An additive task field changes persisted semantics and what users expect from “done.” | Defer past Phase 1. If pursued, decide policy names, authority, migration/replay, and UI disclosure; `NOT_REQUIRED` must never create `task.accepted`. |
| Objective adoption | Objectives were proposed as a denominator, not established as a required user behaviour. | Decide whether objectives are mandatory for new work, optional but encouraged, or out of scope for the first loop; measure friction before gating admission. |
| Push-dispatch | It changes the product promise and conflicts with the recorded anti-goal against an open-ended scheduler. | Keep pull/manual only. Any push trial requires a separate superseding owner decision, the security gate, operator kill switch, and the hard metrics above. |
| Context Pack/Gallery extensions | Manifest, token policy, cache and Pack diff are new scope beyond the approved MVP contract. | Keep the approved Context Pack and safe image-only Gallery scope parallel. Accept each extension only with privacy, retention, cache-invalidation and rollout criteria. |
| Trace privacy and retention | Evals need inspectable evidence but the product forbids turning workspace into transcript storage. | Adopt the bounded trace fields in section 5 and an explicit retention/erasure policy before real-provider canaries. |

## 8. Parallel Context Pack and read-only Gallery track

The approved programme remains valid as a **separate, bounded product
investment**:

```text
Context Pack revision -> frozen child-run binding -> equal baseline fingerprint
Design Package -> ordered current image refs -> safe read-only Gallery preview
```

Its user benefit is different from the control-loop hypothesis: it avoids
repeating research and lets a product operator inspect visual work in-product.
The tracks should share only proven foundations—typed boundaries, exact
revision refs, privacy classification, safe preview, and replay discipline—not
be coupled by an unproven scheduler.

Therefore:

- continue the approved image-only preview / ordered-board / feedback scope on
  its stated gates;
- keep visual editing, hotspots, SVG/HTML previews, arbitrary media, and
  provider KV-cache claims out of V1;
- preserve the rule that two children share a canonical baseline fingerprint,
  not an assumed byte-identical provider prompt or cache reuse;
- treat compiler manifest/cache/diff as separately decided extensions, and
  evaluate them with deterministic selection, leakage and truncation cases;
- do not use Gallery activity as evidence that the work-governance loop is
  improving, nor pause an already-approved safe track solely to manufacture a
  control-plane demo.

## 9. Recommendation to the planning writer

Make the updated implementation plan a decision aid, not a commitment to every
idea in either review:

1. Open with the two independent product bets: **close governed work loops**
   and **preserve shared research/visual artifact value**.
2. Put the historical 24-Aug diagnosis in an evidence register labelled
   “requires fresh measurement”; give Phase 0 a reproducible measurement
   command and data definition.
3. Make review-pairing the smallest vertical proof, subject to the owner
   decisions in section 7.  Keep finalisation as the next bounded security
   vertical, not bundled implementation.
4. Attach every roadmap item to an eval layer, dataset cases, metrics,
   failure action and owner.  Mark all thresholds as invariant, baseline, or
   proposed—never as a discovered fact when they are a recommendation.
5. Keep new schemas, AcceptancePolicy, authority grants, autonomous dispatch,
   and Gallery scope expansion behind explicit decisions and the existing
   refactor/persisted-format gates.

The near-term win worth testing is simple: **a human can trust that submitted
work either reaches the right independent review or visibly tells them why it
cannot—without turning provider completion into acceptance.**
