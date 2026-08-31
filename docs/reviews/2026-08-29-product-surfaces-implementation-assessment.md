# Product surfaces implementation assessment: Gallery, tracker, and context branching

**Date:** 2026-08-29
**Boundary:** current checkout at `dd65bdb` (main branch)
**Scope:** file/line evidence of what is implemented, foundation-only, planned,
and absent for (1) Design Gallery/Design Package, (2) task execution
tracker/plan DAG with live progress, and (3) context start branches.

This assessment does not edit source, tests, existing plans, static UI, or any
other path.

---

## 1. Design Gallery / Design Package

### 1.1. What is implemented

| Capability | Evidence | Status |
|---|---|---|
| Gallery React shell at `/gallery` | `src/agent_commons/ui/server.py:492-497` — `gallery()` handler serves `read_gallery_shell()` with its own CSP | **Implemented** |
| Gallery static asset mount | `server.py:477-480` — `StaticFiles` mounted at `/gallery/assets` from `gallery_static_directory()` | **Implemented** |
| Gallery's own CSP | `server.py:496` — `gallery_content_security_policy()` applied to the gallery response | **Implemented** |
| Gallery API bootstrap with typed refusal | `server.py:507-517` — `GET /api/gallery` returns `409 gallery_data_unavailable` with the message "published design packages are not available in this build" | **Implemented** |
| Artifact preview route | `server.py:519-532` — `GET /api/artifacts/{id}/preview` reads through `ArtifactPreviewReader`, verifies manifest, type, classification, SHA-256, and returns typed refusals | **Implemented** |
| Artifact preview security contract | `services/artifact_content.py` — manifest/hash/type/classification checks; PNG/JPEG `public`/`internal` only; symlink/stale/replaced fail closed | **Implemented** |
| Browser session handoff for Gallery | `FRONTEND_CONTRACT.md:34-48` — Gallery clears fragment, exchanges code at same-origin POST, stores opaque API base in `sessionStorage` | **Implemented** |
| Paired EN/RU locale for Gallery | `frontend/gallery/src/i18n.json` — Gallery-owned paired locale source, disjoint keyspace from legacy panel per `FRONTEND_CONTRACT.md:49-52` | **Implemented** |
| Gallery test coverage | `tests/ui/test_react_gallery.py` — proves shell renders, bootstrap returns `gallery_data_unavailable`, and empty state rather than demo data | **Implemented** |

### 1.2. Foundation only (seam exists, feature not delivered)

| Capability | Evidence | Status |
|---|---|---|
| Design Package domain model | `context-pack-gallery-implementation-plan.md` §4–5: target module `domain/design_packages.py` with `DesignPackageRecord`, `ScreenBinding` — **not yet created** | **Planned, not implemented** |
| Design Package service commands | Plan §5: `DesignPackageCommands.publish/revise` — no `services/design_packages.py` exists | **Planned, not implemented** |
| Gallery read model from packages | Plan §5: `DesignGalleryReads.list_for_producer(ref) -> tuple[GalleryFrameView, ...]` — not implemented | **Planned, not implemented** |
| Gallery board/card rendering | Gallery shell currently renders empty state. No card components, no screen ordering, no board layout. The React subtree at `frontend/gallery/` contains the shell only | **Absent** |
| Design Package events/schemas | Plan §3 graph: these come after A8. No `commons.payload.design_package.v1` schema exists in `core/schema_registry.py` | **Absent** |
| Feedback provenance | Plan §5: `DesignFeedbackActions.open_feedback(request) -> ThreadRef`. Feedback V1 would open existing `review_discussion`; no implementation | **Absent** |

### 1.3. What is absent

| Capability | Status |
|---|---|
| Ordered screen cards with provenance on Gallery canvas | **Absent** |
| Drag/edit/hotspot interactions on Gallery | **Absent** — explicitly excluded from V1 per plan §2 |
| SVG/HTML preview | **Absent** — explicitly excluded per decision `50RSN30Q2Q1QW7QYHXX4BZJDHQ` |
| History pixel compare | **Absent** |
| Inspector panel in Gallery | **Absent** |
| Auto-created task from comment | **Absent** — deferred to separate workflow decision |

### 1.4. Gallery delivery plan

**Journey:** Designer publishes Design Package (ordered screen list with revision-bound artifact refs) -> Gallery shows cards/board with safe image preview -> textual feedback via existing `review_discussion` thread.

**Components needed:**

| Component | Layer | Contract |
|---|---|---|
| `DesignPackageRecord` | `domain/design_packages.py` | Frozen dataclass: `package_id`, `screens: tuple[ScreenBinding, ...]`, `producer_task_ref`, `revision` |
| `ScreenBinding` | Same module | `screen_id`, `artifact_id`, `artifact_revision`, `ordinal`, `title`, `provenance` |
| `DesignPackageCommands` | `services/design_packages.py` | `.publish(draft, idempotency_key) -> DesignPackageRecord`, `.revise(id, expected_revision, draft) -> DesignPackageRecord` |
| `DesignGalleryReads` | `ui/reads.py` extension | `.list_for_producer(ref) -> tuple[GalleryFrameView, ...]` |
| Gallery React board | `frontend/gallery/src/` | `ScreenCard`, `BoardView`, `InspectorPanel`, `FeedbackForm` |
| `GalleryFrameView` | `ui/read_dtos.py` | TypedDict with `screen_id`, `title`, `preview_url`, `ordinal`, `artifact_revision`, `stale`, `producer_name` |

**API contract:**

| Route | Method | DTO | Notes |
|---|---|---|---|
| `/api/gallery` | GET | `GalleryBootstrap` with `screens`, `package_revision`, or typed refusal | Replaces current `gallery_data_unavailable` |
| `/api/gallery/packages` | GET | `list[DesignPackageView]` | Ordered by `recorded_at` |
| `/api/gallery/feedback` | POST | `{screen_id, body, expected_revision, idempotency_key}` | Opens existing `review_discussion` thread |
| `/api/artifacts/{id}/preview` | GET | Raw bytes with `Content-Type` | Already implemented |

**UX states:**

| State | Render | Accessible label |
|---|---|---|
| Loading | Spinner with `aria-live="polite" role="status"` and "Loading gallery" text | "Loading gallery data" |
| No packages published | Empty state card: "No design packages have been published yet. Use the CLI or API to publish a Design Package." | "Gallery is empty" |
| Typed refusal (`gallery_data_unavailable`) | Current behavior — honest empty with code | "Design packages are not available in this build" |
| Screens loaded | Ordered card grid, each card shows title, ordinal, thumbnail (from preview route), stale badge if `artifact_stale` | Screen title as card heading |
| Preview error (symlink, replaced, oversized) | Placeholder image with typed refusal code | Refusal code as alt text |
| Feedback submitted | Toast: "Feedback recorded" | `role="status"` announcement |

**Accessibility:**

- Card grid uses `role="list"` / `role="listitem"` with `aria-label` on each card.
- Preview images carry `alt` = screen title, or refusal code when preview fails.
- Keyboard: Tab through cards, Enter to open inspector, Escape to close.
- Focus trap in inspector panel when open.
- Color is never the sole status indicator: stale uses badge text + strikethrough.

**Metrics:**

| Metric | Definition | Purpose |
|---|---|---|
| `gallery_load_latency_p50` | Time from navigation to first card render | UX quality |
| `gallery_preview_refusal_rate` | Typed refusals / total preview requests | Content health |
| `gallery_feedback_creation_rate` | Feedbacks / published screens per window | Adoption |

**Dependencies:** A8 (collaborators), F1 (verified preview, already done), Design Package domain slice.

**Incremental gates:**
1. Design Package domain + service (backend, no UI) — separate behavioral commit
2. Gallery API routes replacing `gallery_data_unavailable` — separate commit
3. Gallery React board component consuming new API — separate commit
4. Feedback via existing thread — separate commit

---

## 2. Task execution tracker / plan DAG with live/recent progress

### 2.1. What is implemented

| Capability | Evidence | Status |
|---|---|---|
| Task lifecycle (ready→assigned→active→blocked→completed→review→accepted) | `domain/lifecycle.py`, `domain/transitions.py` — full state machine | **Implemented** |
| Task dependencies | `services/tasks.py` — tasks support `dependencies` field; `graph.py:398-400` renders `depends_on` edges | **Implemented** |
| Full graph projection | `ui/graph.py:293-462` — `build_graph()` projects all entities (objectives, agents, sessions, tasks, artifacts, delegations, reviews, verifications, agent_links) with edges | **Implemented** |
| Reporting hierarchy ranks | `graph.py:223-290` — `_reporting_ranks()` computes distance from human along real ledger links | **Implemented** |
| Staleness as a visual channel | `graph.py:156` — every node carries `stale: bool(record.get("stale") or record.get("artifact_stale"))` | **Implemented** |
| `awaits_human` attention | `graph.py:159,309` — `blocked_on_human(snapshot)` from `domain/attention.py`; each node carries `awaits_human` boolean | **Implemented** |
| Attention queue | `ui/reads.py:317-424` — `attention()` returns typed items: `RunBlockedAttention`, `WorkReturnedAttention`, `ProposalAttention`, `ThreadAttention`, `ConfigBrokenAttention` | **Implemented** |
| Run list with attempt metadata | `ui/reads.py:436-513` — `runs()` joins attempts + delegations + audit store, returns phase, live status, profile, duration, terminal tool rejection details | **Implemented** |
| SSE live streaming | `server.py:660-667,1011-1114` — `GET /api/stream` with `Last-Event-ID`, `snapshot` events, `resume_gap` detection, keepalive, session recovery announcements | **Implemented** |
| Graph shedding for large workspaces | `graph.py:189-214` — `_shed()` bounds at 2000 nodes / 4000 edges, drops terminal work first, preserves roles | **Implemented** |
| Node bands/types | `graph.py:31-42` — rank hierarchy: objective(0), agent/session(1), task/artifact(2), delegation(3), review/verification/agent_link(4) | **Implemented** |
| Edge types | `graph.py:47-49` — permanent (spawned, requested_by, runs_as, owns, depends_on, reports_to) vs temporary | **Implemented** |
| Graph counts | `graph.py:444-452` — tally of objectives, tasks, delegations, reviews, verifications, agents, templates by state | **Implemented** |

### 2.2. Foundation only

| Capability | Evidence | Status |
|---|---|---|
| Derived `RunView` | `architecture-improvement-implementation-plan.md` §3: target `domain/work_state.py` — frozen `RunView` joining `TaskRecord` + `DelegationRecord` + `Attempt` state. **Not yet created** | **Planned (W1)** |
| `AcceptanceView` | Plan §3: frozen view exposing what evidence is missing for acceptance. **Not yet created** | **Planned (W1)** |
| `ReviewLoopGap` | Plan §3: typed view surfacing unpaired review transitions. **Not yet created** | **Planned (W1)** |
| `WorkHealthMetrics` | Plan §3: `measure_work_health(snapshot, observed_at)`. **Not yet created** | **Planned (W0/W1)** |
| `TaskReadiness` | Plan §3: `domain/work_readiness.py` — advisory `task next`. **Not yet created** | **Planned (W5)** |

### 2.3. What is absent

| Capability | Status |
|---|---|
| Visual plan DAG (directed task dependency graph as an interactive canvas) | **Absent** — task dependencies are rendered as edges in the flat graph, but there is no dedicated DAG view |
| Live progress on individual runs (streaming tool calls, token usage, intermediate output) | **Absent** — telemetry is metadata-only by design (`runtime/telemetry.py`); the UI shows phase/live boolean and attention items, not streaming tool events |
| Run timeline | **Absent** — `visual_orchestrator_plan.md` §4.1 proposed a timeline as the primary run surface; current UI has run list with phases only |
| Run cost/token display | **Absent** — no token accounting exists (`runtime/telemetry.py` stores only `stdout_bytes_seen`; Codex `supports_budget = False`) |
| Gantt chart or time-based progress visualization | **Absent** |
| Capacity/queue widget | **Absent** — proposed in `visual_orchestrator_plan.md` §3 MUST-6 but not implemented |

### 2.4. Tracker delivery plan

**Journey:** Operator creates task with dependencies -> views the task graph showing dependency edges and state -> launches a run -> sees live phase transitions and attention items -> reviews work -> accepts.

**Screen: Task Execution Dashboard**

This is not a new canvas; it is a focused read surface over the existing graph projection and attention queue, delivered as a React screen that consumes `GET /api/graph` and `GET /api/attention`.

**Components:**

| Component | Purpose | Data source |
|---|---|---|
| `TaskDAGView` | Render task nodes + `depends_on` edges as a layout, filtered from `graph.nodes/edges` | `GET /api/graph` filtered to kind=task,delegation and edge kind=depends_on,targets |
| `RunTimeline` | Vertical list of delegation attempts for a selected task, with phase chips and duration | `GET /api/runs` |
| `AttentionBadge` | Badge on graph nodes where `awaits_human=true` | `graph.awaiting_human` |
| `StaleOverlay` | Visual strikethrough/dimming on stale nodes | `node.stale` |
| `ProgressPanel` | Sidebar showing attention queue items with type-specific rendering | `GET /api/attention` |

**API contract:** No new backend routes required. The existing `GET /api/graph`, `GET /api/runs`, `GET /api/attention`, and `GET /api/stream` provide sufficient data. What is missing is a frontend view that:
1. Filters and lays out nodes by task hierarchy (using `depends_on` edges)
2. Shows delegation attempts per task (using `targets` edges from delegations to tasks)
3. Surfaces attention items inline on the graph

**UX states:**

| State | Render |
|---|---|
| No tasks | Empty state: "No tasks in this workspace yet" |
| Tasks with no runs | DAG of task nodes with state badges (ready/assigned/active/blocked/completed/review/accepted) |
| Tasks with active runs | Task node shows delegation count chip; active delegations pulse |
| Attention needed | Amber ring on nodes where `awaits_human=true`; attention panel on right |
| Stale evidence | Strikethrough pattern + "stale" badge on affected nodes |
| Graph exceeds limits | Truncation banner with count: "Showing 2000 of N nodes" |

**What the tracker cannot honestly show:**
- Token usage or cost: no accounting exists. Showing "0 tokens" or an estimate would be dishonest. The tracker should show `provider_units` budget from delegation limits if available, with no dollar conversion.
- Live tool calls or intermediate output: telemetry is metadata-only by design. The tracker shows phase transitions (idle/active/succeeded/failed/needs_operator), not streaming content.
- Predicted completion time: no historical performance data is collected.

**Accessibility:**
- Task nodes are focusable with keyboard; Tab navigates between nodes, Enter opens detail panel.
- State changes announced with `aria-live="polite"`.
- Stale and awaits_human states use both badge text and visual treatment (not color alone).

**Dependencies:** A7 (narrow DTOs), existing graph/attention/runs endpoints.

---

## 3. Context start branches (fresh, accumulated, resume/checkpoint)

### 3.1. What is implemented

| Capability | Evidence | Status |
|---|---|---|
| `context_mode: fresh` as a role property | `adr/0009-agents-as-first-class-roles.md` Q7 — fresh = distinct child session, no persistence, no resume, no prior-position framing | **Implemented** |
| `context_mode: accumulated` as a role property | Same ADR — accumulated means the role receives its own prior judgment as context (but current runtime does not carry working context between runs) | **Implemented** |
| Context mode visible in graph nodes | `graph.py:128` — `context_mode` is extracted as a node attribute | **Implemented** |
| Context mode visible in reviews | `graph.py:133-134` — `producer_context_mode` and `producer_prior_verdict_count` carried on review nodes | **Implemented** |
| Context mode downgrade protection | ADR 0009 Q7 — `fresh → accumulated` requires `agent:isolation_downgrade` capability and recorded reason | **Implemented** |
| Context mode in Work app hire form | `frontend/work/src/main.tsx:407-410` — select with `fresh`/`accumulated` options | **Implemented** |
| Context mode in catalog/role read | `ui/reads.py:271` — catalog returns `context_modes: ["fresh", "accumulated"]` | **Implemented** |

### 3.2. Foundation only

| Capability | Evidence | Status |
|---|---|---|
| Instruction composition seam (A4.5) | `context-pack-gallery-implementation-plan.md` §3 — `services/delegation_instruction.py` target for typed instruction build | **Foundation seam exists** (committed as `e4134a1`); Context Pack injection comes as separate behavior |
| Context Pack domain model | Plan §5: `domain/context_packs.py` with `ContextPackRecord`, `ContextPackBinding`, parser/validator | **Planned (F3), not implemented** |
| Context compiler | Plan §5: `services/context_compiler.py` — deterministic compiled context with fingerprint | **Planned (F3/F4), not implemented** |

### 3.3. What is absent

| Capability | Status |
|---|---|
| Resume/checkpoint as a context start option | **Absent** — delegations are terminal by construction (`PROTOCOL.md §9`); a delegation cannot be resumed. The headless MVP cannot resume or reattach an exited `input_needed` attempt; it becomes `needs_operator` |
| Context accumulation across runs | **Absent** — `accumulated` mode is recorded as a role property but current runtime does not carry a run's working context into the next run for the same role. Each delegation is a fresh child session |
| Context Pack canonical entity | **Absent** — no `commons.payload.context_pack.v1` schema, no events, no projection |
| Frozen launch binding to Context Pack | **Absent** — no `ContextPackBinding` in delegation payload |
| Shared immutable baseline for fan-out | **Absent** — the plan envisions Backend+Frontend children from one frozen baseline; no implementation |
| Context Pack compiler with fingerprint | **Absent** |
| Context branching UI | **Absent** — no UI for selecting context mode at run launch (only at role creation time) |

### 3.4. Context branching delivery plan

**User contract (per approved plan):**

1. **Fresh context (existing):** Each run starts with a clean child session. The role's own prior verdicts on the same subject are not assembled as "your previous position." The reviewer reads the ledger itself. This is fully implemented.

2. **Accumulated context (partially implemented):** The mode is recorded on the role and surfaced in reviews, but no working context actually carries between runs. Implementation requires the Context Pack vertical (F3): a researcher publishes a revisioned Context Pack, and subsequent runs bind to that exact revision.

3. **Resume/checkpoint (not warranted):** The delegation model is explicitly terminal. Resume requires a fundamentally different execution model (stateful sessions, persistent process). The approved plan and architecture do not warrant this. `input_needed` is the closest thing: it pauses work to request human input, but the headless MVP cannot reattach.

**Journey for accumulated context (post-F3):**
1. Researcher role publishes Context Pack (summary, facts, decisions, source refs, open questions)
2. Operator creates Backend and Frontend roles from one Pack revision
3. Each run binds to the exact Pack revision via `ContextPackBinding`
4. Context compiler produces `CompiledContext` with fingerprint
5. Two child runs get the same canonical baseline, diverge on role/task-specific instruction

**Components needed:**

| Component | Layer | Contract |
|---|---|---|
| `ContextPackRecord` | `domain/context_packs.py` | Frozen dataclass: `pack_id`, `summary`, `facts`, `decisions`, `source_refs`, `open_questions`, `revision` |
| `ContextPackBinding` | Same module | `pack_id`, `pack_revision`, `bound_at`, `fingerprint` |
| `ContextPackCommands` | `services/context_packs.py` | `.publish(draft, idempotency_key) -> ContextPackRecord`, `.create_roles_from_pack(request) -> CreateRolesResult` |
| `ContextCompiler` | `services/context_compiler.py` | `.compile(binding, launch) -> CompiledContext` with source classification check and typed redaction/refusal |
| Launch binding | Delegation payload extension | `context_pack_ref`, `context_fingerprint` |

**UX for context branching:**

| Surface | What it shows |
|---|---|
| Role creation form | Context mode selector: `fresh` / `accumulated` (existing) |
| Run launch form | Context Pack selector when role is `accumulated` and packs exist; "No context packs published" message otherwise |
| Review card | `producer_context_mode` badge and `producer_prior_verdict_count` (existing) |
| Run detail | Context Pack revision binding, fingerprint |

**What is not warranted:**
- Resume/checkpoint UI: delegations are terminal. Adding resume would require a new execution model, new events, new process lifecycle, new security analysis. The architecture explicitly forbids stateful delegation.
- Cross-run memory: beyond Context Pack's structured baseline, carrying implicit "memory" between runs conflicts with the freshness property that makes review trustworthy (ADR 0009 Q7).

---

## 4. Cross-cutting delivery dependencies

```text
A5 (typed vertical records)
  -> A7 (narrow DTOs)
    -> A8 (collaborators)
      -> Design Package domain (F4)
      -> Context Pack domain (F3)
      -> Gallery React board (F2, depends on F1+F4)

F1 (verified preview) — already implemented
F2 (Gallery shell) — already implemented as honest empty state
```

### Incremental frontend/backend gates

| Gate | Prerequisites | Deliverable |
|---|---|---|
| G1: Task tracker React view | A7 DTOs, existing graph/attention API | Frontend-only; filters and lays out existing graph data |
| G2: Design Package backend | A8 collaborators | New domain + service; no source changes to existing modules |
| G3: Gallery board | G2, F1 (existing) | React components consuming new Gallery API |
| G4: Context Pack backend | A8, instruction seam (existing) | New domain + service + compiler |
| G5: Context-aware launch | G4 | Pack selector in launch UI; binding in delegation |

### Safe rollout

Each gate is:
1. A separate behavioral commit with its own `make check`
2. Independently reviewable at exact revision
3. Removable without affecting prior gates (no forward dependency except G3→G2 and G5→G4)

New event families (Design Package, Context Pack) are additive. A binary that predates them records a `domain_validation_rejected` projection issue and refuses ordinary writes (same rollback contract as ADR 0009).

---

## 5. What the product honestly cannot promise today

1. **Gallery is not a design board.** The React shell serves, but it shows "data unavailable" because Design Package is not implemented. No sample cards or demo screens appear.

2. **The tracker is not a live debugger.** Telemetry is metadata-only: no prompts, transcripts, tool arguments, or streaming output. The tracker shows phase transitions and attention items.

3. **Token/cost is not available.** There is no token accounting. Codex `supports_budget = False`. The only budget enforcement is `provider_units` and `micro_usd`.

4. **Accumulated context does not carry between runs.** The mode is recorded but the runtime doesn't transfer working context. Context Pack (F3) is the mechanism that will make this real.

5. **Resume is not a context branch.** Delegations are terminal by design. `input_needed` pauses for human input but the headless MVP cannot reattach an exited attempt. Adding resume would require a new execution model.

6. **The graph is a flat projection, not an interactive canvas.** The legacy panel renders the graph JSON from `build_graph()`. There is no React Flow canvas, no drag-and-drop, no inline editing. The Work app at `/work` has role/task/run creation forms but no graph visualization.
