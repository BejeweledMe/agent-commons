# Fact check: corrected Codex and Claude architecture reviews

**Date:** 2026-08-25
**Exact code boundary:** `4844fdbc95adc12ed9b11937d1eb6415f3fb3ba6`
**Inputs deliberately used:** only
`codex_architecture_improvement_review.md` and
`claude_architecture_improvement_review.md`.

`agent_commons_product_architecture_review.md` was not read or used as an
input. References to an earlier review inside either corrected document are
therefore not evidence in this report.

## Verdict

The two corrected reviews agree on a sound direction: retain the ledger and
governance invariants, add read/projection capability before changing canonical
ontology, close the observed review/finalisation loops, and do not enable push
dispatch before explicit authority and security gates. Their strongest factual
diagnosis is independently reproducible today: **32 tasks are in `review` and
zero have a current, non-stale `requested` review bound to their exact current
revision.**

They are not implementation specifications yet. In particular, an
`AcceptancePolicy` stored on `Task`, automatic `submit => review.requested`,
authority scopes, and any new canonical event family all change persisted
meaning and require an owner decision and a separate semantic/migration
project. Calling them “derived” does not make those writes free.

## Method and status vocabulary

I checked source, tests, the current audit target map, and the current
read-only projection. Commands used a fixed state root and
`agent-commons --read-only --json`; no canonical state was written. Counts are
therefore a point-in-time operational observation, not revision-bound evidence.

| Status | Meaning |
|---|---|
| **Supported current fact** | Directly evidenced on `4844fdb` or by the 25-Aug read-only query. |
| **Historical / stale** | Plausible for the document's 24-Aug snapshot, but not a current fact or not reproducible from current fields. |
| **Contradicted** | The current code or query shows a materially different statement. |
| **Inference** | A reasonable interpretation/design conclusion, not a code fact. |
| **Owner decision** | Changes product authority, persisted semantics, or accepted priorities; it must not be silently implemented. |
| **Unverifiable here** | The stated numerator, source data, or exact query is absent. |

## Reproduced operational snapshot

| Measure | Claude snapshot, 24 Aug | Read-only result, 25 Aug | Fact-check verdict |
|---|---:|---:|---|
| Tasks | 117 | 124 (`28 accepted`, `44 completed`, `32 review`, `4 ready`, `2 active`, `1 assigned`, `3 blocked`, `10 cancelled`) | Historical snapshot is stale; state changed. |
| Review records / independent / stale | 67 / 67 / 23 | 67 / 67 / 23 | **Supported current fact.** |
| Review coverage at exact revision | 31 in review, 0 covered | 32 in review, **0 covered** | **Supported current fact**, and the defect persists. |
| Requested review targets | all 10 accepted/completed | 10: 8 accepted, 2 completed; only 2 are non-stale and current for their completed target | **Supported current fact** (with updated denominator). |
| Delegations / `needs_operator` | 31 / 9 (29%) | 31 / 9 (29.03%) | **Supported current fact.** |
| `needs_operator` finalisation gap | 6 of 9 | 6 summaries literally say provider exited without canonical terminal result | **Supported current fact** for the symptom; “product defect” is an inference. |
| Open handoffs | 46; median 17.5d; 24 >14d | 46; median 18.20d; 24 >14d; max 35.71d | **Supported current fact**, recalculated from `recorded_at`. |
| Objectives | 0 | 0 | **Supported current fact.** |
| Decisions / findings | 30 accepted, 2 proposed / 17 reported, 7 resolved | 30 accepted, 2 proposed (36 total) / 18 reported, 7 resolved | Historical totals are stale; core state claim remains close. |
| Ledger / manifests | 1215 / 128 | `doctor`: 1243 events / 131 manifests, integrity `ok` | Historical / stale. |

### Exact review-coverage calculation

The current calculation joins every `task.state == "review"` to a
`review.state == "requested"` only when all of the following hold:

```text
review.stale != true
review.target_ref == {kind: "task", id: task.id}
review.target_revision == task.effective_revision
```

It returns `32 review_tasks`, `0 covered`, `32 uncovered`. This is stricter
and safer than merely counting requested reviews. The source cause is also
present: `TaskCommands.submit_task()` writes `task.submitted` only
([services/tasks.py](../../src/agent_commons/services/tasks.py#L148-L169)), while
`ReviewCommands.request_review()` is a separate command
([services/reviews.py](../../src/agent_commons/services/reviews.py#L19-L53)).

The Claude claim that review-state items were “18–35 days old” is **not
reproducible from the current task projection**. Its `recorded_at` values give a
current review-state median of 0.67 day and maximum 6.89 days. The review should
name the historical event query if it intends “age since first submission”;
otherwise that number must not become a roadmap threshold.

## Material claim matrix

| Topic and corrected-review claim | Evidence on `4844fdb` | Verdict / correction |
|---|---|---|
| Immutable ledger, manifests, rebuildable SQLite projection, exact-revision evidence, fixed-point staleness, and `completed != accepted` are the mature core. | [ARCHITECTURE](../../docs/ARCHITECTURE.md#L60-L79) defines immutable canonical history and rebuildable SQLite; [L168-L201](../../docs/ARCHITECTURE.md#L168-L201) defines separate completion, submission, acceptance, revision-bound evidence and staleness. | **Supported current fact.** |
| CLI, MCP, UI, and runtime use one canonical mutation boundary. | [ARCHITECTURE](../../docs/ARCHITECTURE.md#L82-L101) places CLI/MCP/broker through `CommonsManager`; runtime is operational at [L104-L119](../../docs/ARCHITECTURE.md#L104-L119). | **Supported current fact**, but “single write path” must not be read as permission to add new feature methods to `services/manager.py`; audit A8 explicitly migrates consumers away from the facade. |
| No end-to-end scheduler/readiness/DAG loop exists; dependencies only establish entity existence. | `TaskCommands.create_task()` records dependency IDs ([tasks.py](../../src/agent_commons/services/tasks.py#L19-L61)); lifecycle validates each target exists ([lifecycle.py](../../src/agent_commons/domain/lifecycle.py#L392-L399)). No cycle detector, readiness predicate, `task next`, or scheduler trace exists in source search. | **Supported current fact.** A dependency-DAG command is a behavior change, not A3–A8 refactoring. |
| `Delegation` is already the bounded run, with exact target revision and fresh child session; `Attempt` is operational process state. | [ARCHITECTURE](../../docs/ARCHITECTURE.md#L209-L215) calls delegation one bounded run; creation persists target/revision/limits ([delegations.py](../../src/agent_commons/services/delegations.py#L24-L99)); attempts have their own state machine ([attempts.py](../../src/agent_commons/runtime/attempts.py#L64-L120)). | **Supported current fact.** “Delegation is Run in every respect” is too strong: task-side aggregation, retry digest, context binding, and cost semantics do not exist. A derived `RunView` is an **inference/recommendation**, not a rename. |
| A canonical `ExecutionRun` is unnecessary now; a read model is safer. | Existing model has the needed base fields but no comparison against a second execution backend. | **Inference, directionally well-supported.** Do not decide canonicalisation without a demonstrated second execution type or a proven projection shortfall. |
| Execution and acceptance should first be derived separately. | Current lifecycle is one task-state chain ([transitions.py](../../src/agent_commons/domain/transitions.py#L34-L58)); staleness already independently removes effective acceptance ([ARCHITECTURE](../../docs/ARCHITECTURE.md#L180-L193)). | **Inference, strong candidate.** A DTO/projection can be added without event changes. |
| `AcceptancePolicy` is merely additive/derived and can drain `completed` tasks. | Current task payload/commands expose no policy field; current acceptance invariant requires a qualifying independent approval. | **Owner decision.** Storing a task field changes persisted task schema and its meaning; inventing a light path changes the acceptance contract. It belongs in a separately approved semantic/migration track, never A3–A8. |
| `submit => review.requested` should be atomic, then historical work should be backfilled. | The separation is directly evidenced above and coverage is zero. | Coupling is a **strong product recommendation**; atomic multi-event semantics, reviewer routing, debounce, and backfill/supersede are **owner decisions** with migration/replay tests. Backfill must be explicit operator work, not an implicit projection side effect. |
| Six of nine `needs_operator` cases are a finalisation gap; parent-side finalisation and preflight fix it. | Exactly six current summaries state “provider exited successfully but did not record a canonical terminal result”; the other three match the documented capability/state-root/scope cases. The broker already reports process-vs-canonical mismatch ([delegation_runtime.py](../../src/agent_commons/services/delegation_runtime.py#L980-L1021)). | Symptom **supported**; cause allocation and solution are **inference**. Parent finalisation must validate bounded report data against independently observed files/refs and preserve `succeeded != accepted`; it is behavior/security work. |
| Attention exists but is not the proposed typed operator queue. | Current `AttentionItem` has only `run_blocked`, `work_returned`, and `thread`; it is a canonical selector ([attention.py](../../src/agent_commons/domain/attention.py#L12-L99)). | **Supported current fact.** Extending it with stale reviews/aging handoffs is a behavior/product design, not a refactor. |
| Push dispatch conflicts with the product boundary and needs explicit gate/supersede. | VISION says it is not a “general autonomous model launcher, open-ended task scheduler” ([VISION](../../docs/VISION.md#L99-L107)). Current broker limits new workers to leaf-only `max_depth: 0` ([delegations.py](../../src/agent_commons/services/delegations.py#L36-L41)). | **Supported current fact.** Pull planning also changes behavior, but is the lower-risk starting proposal. Push requires explicit owner supersede plus security and eval evidence. |
| Current roles/capabilities are not authentication or broad organisational authority. | Protocol says MVP-0 records but does not authenticate actor authority ([PROTOCOL](../../docs/PROTOCOL.md#L146-L153)); existing role grants are only `create_roles`, `retire_roles`, `open_links` ([roles.py](../../src/agent_commons/domain/roles.py#L30-L31)). | **Supported current fact.** `decision_scope` is an **owner decision** and likely new canonical semantics. |
| The ledger is a useful eval substrate; current catalog has 25 cases, 8 implemented, and planned/unsupported honestly non-passing. | Catalog contract says planned/unsupported are non-passing ([catalog.py](../../src/agent_commons/evals/catalog.py#L1-L6)); it uses a fake provider and only runs implemented executors ([L451-L488](../../src/agent_commons/evals/catalog.py#L451-L488)). | **Supported current fact** for the offline harness. Ledger-graded real-provider L3 and ratchets are **recommendations** requiring privacy-safe fixtures, opt-in cost control and an owner-approved release gate. |
| Context Pack/Gallery should continue as a parallel approved track. | Owner decisions and the target modules are recorded in the current programme ([context-pack-gallery plan](../context-pack-gallery-implementation-plan.md#L12-L18), [L69-L110](../context-pack-gallery-implementation-plan.md#L69-L110)). F1 preview is implemented; Gallery is currently an honest shell that returns `gallery_data_unavailable` until Design Package reads exist ([ui/server.py](../../src/agent_commons/ui/server.py#L464-L522)). | **Supported current fact** that the track is approved and incomplete. The reviews must not represent Context Pack, Design Package, compiler, feedback provenance, or Gallery data as delivered. |
| Add context manifest, deterministic token policy, cache and Pack diff. | No such symbols exist in current source/plan; current plan specifies only the future `CompiledContext` contract. | **Inference / proposed scope expansion.** Evaluate after the approved Pack slice has a real baseline and measure cache/retrieval demand; do not silently amend its owner-approved scope. |
| Three consistency boundaries / seven contexts are over-engineering. | Fixed-point staleness crosses work and review projections, but “three” is an architectural interpretation rather than a measured invariant. | **Inference.** The auditable constraint is narrower: follow target module map and do not add facade APIs. |
| Current refactor facts from older snapshots (large `UIContext`, monolithic root CLI, old manager size) justify the same plan. | Current `CommonsManager` is 1307 lines and 12 command mixins; `UIContext` is 371 lines and `ui/reads.py`/`actions.py` exist; CLI is now a package, although `cli/__init__.py` is still 2864 lines. Audit A3–A8 remains the governing target. | Older sizes are **historical / partly contradicted**. The decision should be made against the current audit plan, not copied size claims. |

## Constraints the update must preserve

1. **Facade rule.** The audit says A2 only moves bodies; A8 later introduces
   narrow collaborators ([audit plan](../audits/2026-08-18-code-quality/audit-plan.md#L157-L174),
   [L258-L267](../audits/2026-08-18-code-quality/audit-plan.md#L258-L267)). New
   work must target thematic services/domain modules, not grow
   `CommonsManager`, root CLI, MCP `build_server`, or `UIContext`.
2. **Persistence rule.** A3–A8 retain events, schema names, JSON bytes and
   replay semantics ([audit plan](../audits/2026-08-18-code-quality/audit-plan.md#L434-L447)).
   A new policy, routing decision, task origin, event family, or atomic compound
   command needs its own owner-authorised data-semantics decision and migration
   contract.
3. **Typing and commit rule.** New in-memory seams use frozen dataclasses or
   `TypedDict`; structural and behavior commits remain separate. Do not re-open
   accepted Context Pack/Design Package or Gallery decisions.
4. **Security rule.** Any worker-provided finalisation/context/run digest is
   untrusted data, not an authority-bearing command. It must be bounded,
   classified, validated and kept free of prompt/transcript/reasoning storage.

## Decision-ready corrections for the next plan

The plan writer may adopt the following as **evidence-backed ordering**, but
not mark a product decision as already accepted:

1. Measure and repair review coupling and delegation finalisation first.
2. Build current-state metrics and an attention projection before any push
   scheduler; keep pull `task next` advisory until owner policy says otherwise.
3. Add a `RunView` only as a read model over `Task + Delegation + Attempt`;
   defer canonical `ExecutionRun`.
4. Keep Context Pack/Gallery as the separately approved parallel programme;
   record its delivered F1/F2 state and defer F3/F4 semantics until their own
   decision/migration gate.
5. Put these questions in an explicit owner-decision register: acceptance
   policy and dependency-unlock rule; submit/review atomicity and routing;
   any authority scopes; new event/migration format contract; pull versus push;
   and the conditions for writable fan-out.

## Non-results

- No source, input review, plan, canonical event, decision, or test was
  changed.
- `make check` was intentionally not run: the shared programme serialises one
  clean gate/commit after all three assessment reports are ready.
