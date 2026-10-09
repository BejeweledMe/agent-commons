# The frontend contract

The product UI is the **Work** application: `frontend/work/` (React, Vite,
TypeScript) compiled into `src/agent_commons/ui/static/work/` and served at
`/work`. The **Gallery** for design packages is `frontend/gallery/`, compiled
into `src/agent_commons/ui/static/gallery/` and served at `/gallery`. Both
bundles are committed and shipped as wheel package data, so installing the
product never asks an operator to run npm; `npm ci && npm run build` on the Node
from `.node-version` is the reproducible maintainer rebuild path.

The legacy single-file panel at `/` (`src/agent_commons/ui/static/index.html`)
is not served inside a project and is being retired (decision scope
`repo/legacy-panel-retirement`, ADR 0022 pending). Its remaining laws are the
appendix at the end of this page; it must not gain features.

Every rule below is enforced by a named test, so this document is a map of the
laws, not their only copy: break one and the suite says so. Read the applicable
section before editing a surface; otherwise the tests will teach it to you one
failure at a time.

## One builder, one bundle

- Exactly one hashed `work-<hash>.js`/`.css` pair and one Gallery pair are
  tracked. The asset integrator rebuilds once per wave under the workspace
  claim `path:src/agent_commons/ui/static/work`; everyone else edits sources and
  tests only. (`tests/ui/test_work_app_contract.py`,
  `tests/contract/test_clean_generated.py`)
- Worker sandboxes cannot run node or uv; the coordinator runs
  `node --test frontend/work/tests/*.test.mjs`, `npx tsc --noEmit -p
  frontend/work` and the Python contract tests before any package is accepted.

Agent cards use a shared measured size for automatic layout: long names and
summaries do not change rank spacing. The card leads with the catalog profession,
retains the distinct agent name, and exposes the full summary on hover. Board
commands have concise localized hover help and align on one toolbar row when
space allows. Initial fit waits for the graph, saved layout, role data and node
measurements; subsequent refreshes and map switches preserve the viewport.

## Task-first Work increment

[ADR 0014](adr/0014-task-first-workspace-ux.md) defines the Work contract; its
completed plan is archived at
[`archive/plans/task-first-workspace-implementation-plan.md`](archive/plans/task-first-workspace-implementation-plan.md).

- State glossaries are domain-specific: evidence `complete` describes data
  completeness, never task completion, run success, review approval or acceptance.
- Navigation filters do not create canonical states. Task selection, view and
  destination are allowlisted query state (`view`, `tasks`, `agent`, task id);
  drafts and exchange secrets never enter URLs or localStorage. Auth fragment
  removal preserves safe navigation.
- A local action refusal preserves shell, selection and draft. An uncertain
  mutation retains its original body, revision and idempotency identity.
- Task creation selects the exact returned task. Blueprint application, agent
  hire and run launch remain separate, explicit operations.
- The exact-source picker consumes a closed metadata DTO. Current catalog
  eligibility never replaces source validation at publication and execution.

## Project-native collaboration increment

[ADR 0019](adr/0019-project-native-collaboration-and-outputs.md) governs the
project shell, library, outputs and conversations.

- Project actions belong to the project row. Accessible search labels remain
  present but visually hidden. A native folder chooser supplements manual paths;
  a browser directory upload never establishes a trusted server path. Archiving
  a project is confirmed with its name and unchanged path and never deletes
  files.
- Reusable skills/groups, Roles and versioned Team templates belong to Library.
  Role instructions must open in a native modal whose close restores the catalog
  opener without changing its scroll position or filters; the library editor
  retains its separate workflow.
  Producer outputs are task/agent scoped; legacy Design links redirect away
  from the reusable catalog. Counted Results buttons require scoped server counts.
  An agent name/avatar is a permanent gallery entry, including an empty result
  set or unavailable count; it never infers an output count.
- Output labels distinguish latest, earlier version (viewable), preview expired,
  address unavailable, preview not verified and the additive nullable
  `review_state` (`awaiting` | `approved` | `returned` | null). Review state is
  never derived from output freshness; a missing field renders the neutral
  label. Historical bytes are never presented as current. The current DTO joins
  review state from the task: copy says "Current task" and must not imply that
  an old output version received that review. Output-specific review binding
  remains separate work.
- Conversations retain unsent local drafts by project and scope in memory; one
  global `beforeunload` guard fires only with unsent text or attachments. The
  recipient's availability chip reads the server-joined nullable
  `recipient_availability` (`active` | `inactive` | `unknown`; missing is
  `unknown`). Delivery is a stepper of recorded → queued → fetched →
  acknowledged → answered and never implies acceptance. Unknown recipient
  availability must not prevent an otherwise valid message from being recorded;
  recording, delivery and response are separate facts. "Prepare run" only
  navigates and prefills.
- An explicit Save draft stores bounded text and reply metadata under the
  private principal/project/scope state root, separately from attachment/send
  reservations. Restore and Delete are explicit, never auto-send. Revision CAS
  protects a newer tab's draft; only an identical lost-response retry converges.
  Pane widths, collapse and swap live in separate bounded operational layout
  state. Both new POST routes belong to `PRIVATE_COLLABORATION_ROUTES` and are
  absent in readonly apps (`tests/ui/test_workspace_preferences.py`).
- Attachments use bounded private upload/download routes and validated descriptors.
  Source paths and raw bytes never enter URL query state or localStorage.
  Actual image bytes require the addressed MCP image tool, not a filename string.
- Private operational mutations are separately enumerated by
  `PRIVATE_COLLABORATION_ROUTES`. Canonical conversation create/send remain in
  `MUTATING_ROUTES` and the manager-write invariant. Readonly apps mount neither
  kind of mutation. New routes require first-run and real transport tests.
- Runtime/run freshness is not the freshness of an independently observed task.
  Preserve old-run warnings without disabling a freshly observed unrelated task.

## Mutations: scoped, confirmed, uncertain

- In-flight writes live in a registry keyed by `{projectId, surface,
  operation}`. Only the initiating control and conflicting writes are blocked;
  navigation, reading, search and other surfaces stay usable.
- A confirmed POST and a fallible refresh are separate outcomes: a failed
  refresh after a confirmed write says "saved, but the latest data could not be
  loaded" and the refresh action never repeats the write.
- An uncertain outcome keeps the original body and idempotency key and offers
  only the identical retry. The 2 s / 8 s waiting hints are presentation, not a
  performance target (`ux/latency-thresholds`).
- Focus returns deterministically after a control is disabled or unmounted;
  live regions announce once per state change. (`frontend/work/tests/
  mutation-*.test.mjs`, `tracker-actions.test.mjs`)

## Blueprints, agents and run limits

- Applying a blueprint reports the created agents and tasks and states that
  nothing has started; the primary action opens Prepare run for the first
  server-confirmed ready task. The UI never guesses that task from array order.
- Built-in blueprints and catalog copy are authored in EN and RU. User-authored
  names, descriptions and task text are entered ONCE, stored without translation
  and rendered byte-for-byte under either locale; duplicating the value into the
  `{en, ru}` slots the persisted schema keeps is storage plumbing. A record
  whose two slots differ was written by the old two-field editor; it is shown as
  both texts with an explicit choice and saving is blocked until one is chosen.
  (`frontend/work/tests/library-authoring.test.mjs`,
  `frontend/work/tests/work-library.test.mjs`)
- Skill groups are archived and restored server-side with revision CAS; the
  `skill_organization.groups` DTO carries `archived: true`, archived groups are
  never offered as move targets, and a client never hides a group before the
  returned catalog confirms it.
- Prepare run shows the factual `wall_time_seconds` from the DTO as a minutes
  control (1–60); a null value reads "limit not provided" and is never replaced
  by a default in the UI. Server validation (60–3600 s for runs, 30–1800 s for
  check runs) remains authoritative; a 422 keeps the entered value in memory.
- Readiness stages are rendered as text in human words; canonical values appear
  only in disclosures. Green is never used for `ready` or `launchable`.

## Two languages, one registry

- Every Work string exists in both locales of `frontend/work/src/i18n.json`;
  feature string modules are thin shims over that registry with namespaced keys
  (`conversation.*`, `outputs.*`, `taskGraph.*`, `projectCreation.*`), and the
  parity test diffs the key sets. (`frontend/work/tests/i18n-registry.test.mjs`)
- A language switch repaints every surface and never takes a half-typed input
  off screen. Work and Gallery may share exactly one nonsecret locale preference
  (`en` or `ru`) in `localStorage`. Validate stored values and tolerate storage
  failure, preserving the existing `en` fallback. This exception permits no
  drafts, conversation content, attachments, paths, identifiers, map state or
  credentials in `localStorage`; authentication restoration stays in
  `sessionStorage` as specified below.
- Gallery keeps its own paired locale source `frontend/gallery/src/i18n.json`;
  a term rendered by both applications must move to a shared source first.

## Vocabulary is law

- One thing, one word, per language, as defined in the
  [glossary](PRODUCT.md#glossary): agent/агент, task/задача, run/прогон,
  skill/навык, role/роль, team template/шаблон команды, result/результат,
  review/проверка, acceptance/приёмка, board/доска. Library navigation uses
  Roles / Роли and Team templates / Шаблоны команд. A role is the library
  definition; an agent is its project instance. Internal `specialization`,
  `specialization_ref`, `blueprint` and `blueprint_application` remain unchanged.
  No transliterations (скилл, тулл, борд, делегац…) in the Russian table.
- Canonical values — `deny`/`ask`/`auto`, `fresh`/`accumulated`, states like
  `succeeded`, entity kinds, profile ids — are NEVER translated. They keep the
  ledger's spelling and gain a human gloss beside them, never instead of them.
- Protocol words (canonical, evidence, readiness, revision, qualification,
  canary, delegation, ledger, snapshot) stay out of first-level labels and live
  in technical disclosures.

## CSP-safe DOM only

- Never `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`,
  `eval`, `dangerouslySetInnerHTML`.
- Never inline `style` attributes: the CSP carries no `style-src-attr`. All
  styling goes through classes and the compiled stylesheet; third-party CSS
  (React Flow) ships as a file under `style-src 'self'`.
  (`frontend/work/tests/style-tokens.test.mjs`)
- Inline SVG takes its colours from the palette through classes, never through
  `fill`/`stroke`/`style` presentation attributes.
- Raw hex colours outside the token block are counted by a ratchet lint that
  only goes down. Semantic tokens (`surface`, `text`, `border`, `status/*`,
  `accent`) are the only colour vocabulary components may use.

## A registered route is not a usable route

A writing panel registers its whole canonical non-`GET` surface
unconditionally — the union of `MUTATING_ROUTES`, `CATALOG_ROUTES`,
`LAUNCH_ROUTES`, and `SETUP_ROUTES` in `src/agent_commons/ui/server.py` — the
instant it can hold a session at all, before the workspace, the operator
runtime config, or the catalogue exist. A read-only panel registers none of
those canonical-write routes. Both panel modes additionally expose the one
narrow public `AUTH_ROUTE`, `POST /api/auth/exchange`: it accepts only the
short-lived exchange code with an exact same-origin check, returns only the
opaque in-memory API base, and cannot read workspace data. What changes over
the panel's lifetime is never the canonical route table, only whether a given
call succeeds: an unconfigured environment answers a real POST with a typed 409
(`setup_uninitialized`, `launch_not_configured`, a catalogue refusal), not a
404. Never treat a 404 as "this panel can't do that yet" and never treat
reaching a route without an error as proof of capability — read the response
body's `error.code`, the same way `setup_state`/`launch_enabled`/
`catalog_editing_enabled` on `GET /api/setup` and `GET /api/catalog` do, and
drive first-run and blocked-action UI off those typed codes.

The current setup tuple is exactly `POST /api/setup/initialize`,
`POST /api/setup/runtime-config`, and
`POST /api/setup/add-discovered-providers`. The last route derives its only
input from trusted discovery and a byte-for-byte proof of the currently
generated config; it never receives a YAML fragment or a path from the browser.

## Session and origin

The printed URL holds only a short-lived, single-use opaque exchange code in
its fragment. Both applications clear that fragment before their first network
request, exchange the code at same-origin `POST /api/auth/exchange`, and then
use the returned opaque per-process API base. The base is a routing capability,
not a standalone credential: the HTTP-only, `SameSite=Strict` cookie remains
required and is scoped to that base. To make a normal refresh work, the base is
kept only in `sessionStorage`; authentication never uses `localStorage`, so a
new browser session needs a fresh `#c` handoff. The locale-only exception above
does not extend to the API base or any authentication material. Clear the stored base on a failed session
restoration. Never put an exchange code or session credential in a query
string, an asset URL, source code, browser storage, or an `Authorization`
header. Untrusted live previews are served on a separate origin and never
receive Commons authentication or capabilities.

Project navigation reuses the connected host session and the in-memory project
registry. An explicit refresh revalidates the host session and reloads the
registry. A bootstrap 404 revalidates the host before treating the project as
missing, so server restarts can invalidate an old opaque prefix without clearing
valid sessions for entity 404s. Fresh handoff fragments also bypass session reuse.
The server still authenticates
every request and validates the selected project binding; an expired session is
not treated as an unavailable provider. A pending or failed project read retains
the navigation shell and never retains another project's workspace data.
Configured workspaces open without setup guidance or provider status probes in
the critical path. Provider observations arrive separately, remain unknown until
received, and are discarded if project selection or the read generation changes.

## Honesty rules

- The client never advances state: every repaint after a write comes back from
  the server, and only acceptance may render as green.
- A budget is the cap a run was permitted, never a spend — nothing measures
  consumption, so nothing may display one.
- Nothing here stores prompts or transcripts. A run surface may show the
  private attempt store's sanitized complete stderr diagnostic of at most 4 KiB
  for an unsuccessful process and bounded terminal-tool rejection reasons drawn
  from a fixed allowlist of the backend's own refusal strings (anything else
  arrives as a fixed withheld-details notice). It never receives stdout,
  successful-run stderr, or tool arguments. Incomplete stderr is withheld,
  including historical tails marked truncated; truncation and redaction remain
  visible. A fragment without its original secret markers is not evidence that
  its content is safe.
- Automatic updates show observed task/run state without hidden model
  reasoning or invented progress percentages.
- `GET /api/workers/eligibility` is server truth for specialization hiring.
  A missing or stale input is `unknown`, therefore unselectable; capability
  chips are informative descriptions, never permissions or an authorization.
- Provider availability `qualification.wall_time_seconds` is additive and
  nullable. Clients must render "not provided" when it is null rather than
  inventing a default.
- Launch refusals are a closed UI contract: `workspace_snapshot_unreadable`,
  `executor_access_missing`, `provider_mcp_handshake_failed`, and `unknown`.
  Each refusal is rendered with the server-supplied bounded reason and one next
  action; clients must not infer a different code from process output.
- A terminal `StopReason` uses the same `{code, reason, next_action}` shape.
  Its code may additionally be `executor_stopped_by_environment` or
  `provider_reported_error`; when no additive event field is available the
  canonical summary carries it as compact JSON prefixed `stop_reason:`.
- Tracker run rows carry that reason as the additive, nullable `stop_reason`
  object. The server publishes it only for a run whose own canonical stop
  transition (`failed`, `timed_out`, `needs_operator`) recorded a readable
  marker; a missing, malformed, or out-of-set reason is `null`, and a worker's
  successful-run summary is never read as one. Null keeps the surface's existing
  copy — it never means "no reason exists" and never authorizes an action.

## Map-first workspace (ADR 0026)

- A project opens on its task map with compact navigation and a main project
  conversation. Side panes resize, collapse and swap; details are on demand.
- The central workspace has Tasks and Agents tabs. Their navigation and pane controls keep fixed positions when switching. A separate narrow activity rail remains at the far left when side panes collapse or swap; the project sidebar links to the existing main project chat. Their viewports and
  selections have independent lifetimes; switching tabs never fits a hidden
  canvas or eagerly reads every agent's details.
- Task details occupy a large native modal above the workspace. Opening it
  must not change the underlying map's bounding rectangle or viewport. X and
  Escape close the topmost modal only; focus returns without scrolling. Detail,
  hire and settings windows must not create another permanent workspace column.
- Selection, branch focus, viewport and detail visibility are independent.
  Closing a detail surface or receiving a poll never silently fits the map or
  changes its selected branch. Gestures are scoped to the canvas; chat scroll
  stays local to chat. Structure and Dependencies retain different edge meanings.
  Overview must offer readable controls for the selected element instead of
  miniature card actions; a separate working-scale action focuses the element
  without changing node geometry. Keyboard Arrow/Home/End navigation on both
  SVG maps must minimally reveal the focused node without refitting or changing
  zoom; already visible focus and ordinary refresh must not move the camera.
- The main conversation always uses project scope. Opening a task or agent
  conversation is explicit, and viewing the workspace never sends a message.

## The project board (ADR 0020, home placement superseded by ADR 0026)

- The separate team board uses standing `agent` records that
  are neither templates nor retired; edges are `agent_link` records and
  `reports_to` provenance from the `/api/graph` projection. The canvas never
  draws a canonical relation the ledger does not hold: dragging a port opens a link
  through `POST /api/agent-links`, and the edge appears only after the graph is
  re-read.
- A presentation-only human owner sits above the board and opens the existing
  project chat without sending. Dashed owner connections mean project membership,
  not supervision. Only saved supervisors determine tree ranks; creator and
  communication edges are optional layers. Parentless trees wrap into readable
  rows. A supervisor outside the graph stays outside, never becomes a false root.
- Agent settings use a compact dialog with fixed tabs and footer actions; the
  body alone scrolls. Skills show the selected set first, with a searchable
  catalog on demand. Settings and connection drafts retain their existing lifetimes.
  Configuration (including legacy skills) and specialized library methods are
  separate save scopes, named explicitly with per-tab dirty indicators. A no-op
  must issue no write and create no library version; saving one scope must not
  acknowledge unsaved changes in the other. Refresh is distinct from saving.
- Positions and department frames are the operator's arrangement: operational
  state behind `GET/POST /api/board`, stored under the state root, never a
  canonical fact, never in browser storage. A lost arrangement costs an
  auto-layout. Replacing it is compare-and-swap on the stored revision; a
  `board_revision_conflict` is shown, not retried blindly.
- A department frame groups agents on the board; it is not a canonical Team.
  Applying a blueprint creates its agents and tasks and frames them once;
  nothing starts until a task is launched. Runs from different departments
  queue through the broker: the board says "queued", never "running", for a
  run that has not started.
- Every card action is an existing operation or a navigation: Conversation and
  Results are scoped to the agent, Tasks opens the tracker narrowed by
  `?agent=`, Give a task always opens an explicit task picker before the existing
  Prepare run path. A previously selected task may be preselected, never used
  silently. Opening or choosing a task must not assign work or launch a run,
  and must not replace a pending launch intent.
- Agent settings use the full `agent-details.v1` editor DTO, never the generic
  entity response whose text and arrays can be shortened. Oversize or missing
  content refuses an editor load instead of producing a partial save buffer.
- Organizational supervision is distinct from immutable creator provenance and
  communication permissions. A supervisor edge never grants staffing rights.
  Skills of a specialized agent are exact library methods, not legacy `skills`.
  Editing them retains a new library role version and explicitly rebinds the
  agent; live work prevents that change.
- Icons are consistent vector controls with accessible names. Neutral light and
  dark surfaces, restrained selection and readable type replace decorative
  gradients; runtime state still has a text label and acceptance retains its
  distinct meaning. Russian labels avoid unnecessary implementation terminology.
- Tracker task rows may carry nullable `objective_id` (the effective objective,
  including inherited blueprint provenance) and `application_id`. Missing or
  null remains unknown; these additive fields never authorize a UI action.
- A task record carries the findings bound to it as server truth; the decision
  card lists their count and never hides them.
- React Flow's stylesheet ships as a file (CSP `style-src 'self'`); board
  styling is class-based. Agent activity is text plus a data attribute; green
  is still reserved for acceptance.

## Task dependency map

- Search and connected/upstream/downstream focus run before the 128-node render
  limit. A large project can show a small selected subgraph. Larger selections
  retain a searchable list instead of silently dropping tasks.
- Edges come from the tracker DTO and mean prerequisite → dependent. Hidden
  prerequisites are counted; these edges never mean parent/child or component
  membership. The layout is derived and deterministic.
- Zoom and fit use numeric SVG attributes under the existing CSP. Keyboard
  navigation and a list remain available. Completed tasks are neutral; only
  accepted tasks use the acceptance color. (`frontend/work/tests/task-graph.test.mjs`)

## Local instrumentation privacy

UI instrumentation collection is off by default. Its schema rejects content,
paths, and identifiers; its bounded counter bytes live only under the resolved
state root with bounded retention. The UI has no export or transmit action.
Disabling stops writes immediately, and Clear deletes only this local store.

## Testing the applications

- Work: `node --test frontend/work/tests/*.test.mjs` runs the behavioural
  harnesses (board state, tracker contract and actions, mutations, i18n
  registry, style tokens, library authoring, conversations, outputs, work-shell
  recovery); `npx tsc --noEmit -p frontend/work` must exit 0.
- Gallery: `npm test --prefix frontend/gallery` including its a11y and contract
  suites.
- Python contract tests in `tests/ui/` pin the served shells, CSP headers,
  route tables and DTO shapes; `tests/ui/test_work_app_contract.py` asserts
  the Vite `outDir` and the single tracked bundle.
- Node harness scripts travel over stdin (Linux caps argv elements at 128 KiB);
  a stdin program finds its first user argument at `process.argv[2]`.

## Appendix: the legacy panel (retiring)

Until ADR 0022 removes it, `src/agent_commons/ui/static/index.html` is served
only at `/` of a non-project host. Its laws are frozen: one nonce'd `<style>`
and one `<script>` block with no external references and no build step
(`test_the_spa_has_no_external_references`); no unsafe DOM APIs or inline
styles; every string in both `STRINGS.en` and `STRINGS.ru`; canonical values
untranslated; the suite reads it as text through `read_spa()`. Take the
workspace claim `path:src/agent_commons/ui/static/index.html` before any edit,
and edit it only to keep it serving or to remove it. New behaviour belongs in
Work.

## Explicit task structure

[ADR 0024](adr/0024-explicit-task-hierarchy.md) defines canonical components and
parent/subtask containment. Work creation exposes kind and parent; the inspector
adds subtasks and edits parents using the existing CAS/retry editor. Map offers
Structure and Dependencies separately. Structure uses a bounded containment diagram with project root, dashed parent
edges, search, branch focus, fit/zoom and native list fallback. It shows kind and
state, and labels parents outside the filtered view. It does not imply
readiness or accepted rollups. `frontend/work/tests/task-graph.test.mjs` and
`tests/services/test_task_edits.py` exercise independence and invalid parent edges.

## Retained result cards

The [retained-result protocol](PROTOCOL.md#retained-worker-results) supplies local
retained-byte state and exact result review separately from current task review.
Work accepts explicit old DTO defaults (`retained=false`, result review null); it
never derives approval from task ID or image readiness. Static build cards use a
separate Builds filter and an explicit Download build action. Verified ZIPs are
downloaded as attachments only after digest and fresh scoped identity checks.
They never create image thumbnails, iframes or HTML blob navigation. Failure stays
on the card with a retryable action. EN/RU technical disclosure gives an exact
artifact-review CLI command with a stable operation key and instructs the operator
to verify this project's active session/workspace/state before running it. The
current UI review action targets tasks; exact artifact review launch is separate.
