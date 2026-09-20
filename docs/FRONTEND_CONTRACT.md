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
- Reusable skills/groups and versioned Blueprint definitions belong to Library.
  Producer outputs are task/agent scoped; legacy Design links redirect away
  from the reusable catalog. Results buttons require scoped server counts.
- Output labels distinguish latest, earlier version (viewable), preview expired,
  address unavailable, preview not verified and the additive nullable
  `review_state` (`awaiting` | `approved` | `returned` | null). Review state is
  never derived from output freshness; a missing field renders the neutral
  label. Historical bytes are never presented as current.
- Conversations retain local drafts by project and scope in memory only; one
  global `beforeunload` guard fires only with unsent text or attachments. The
  recipient's availability chip reads the server-joined nullable
  `recipient_availability` (`active` | `inactive` | `unknown`; missing is
  `unknown`). Delivery is a stepper of recorded → queued → fetched →
  acknowledged → answered and never implies acceptance. "Prepare run" only
  navigates and prefills.
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
  off screen.
- Gallery keeps its own paired locale source `frontend/gallery/src/i18n.json`;
  a term rendered by both applications must move to a shared source first.

## Vocabulary is law

- One thing, one word, per language, as defined in the
  [glossary](PRODUCT.md#glossary): agent/агент, task/задача, run/прогон,
  skill/навык, specialization/специализация, blueprint/шаблон проекта,
  result/результат, review/проверка, acceptance/приёмка, board/доска. No
  transliterations (скилл, тулл, борд, делегац…) in the Russian table.
  Transition note: several shipped strings still say "role"/«роль» for the
  hired agent (`Role name`, `role_model_help`, `board_intro`); they are
  renamed by the wave-4 packages WP-12.2 and WP-23, and no new string may
  introduce the old word.
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
kept only in `sessionStorage`; `localStorage` is never used, so a new browser
session needs a fresh `#c` handoff. Clear the stored base on a failed session
restoration. Never put an exchange code or session credential in a query
string, an asset URL, source code, browser storage, or an `Authorization`
header. Untrusted live previews are served on a separate origin and never
receive Commons authentication or capabilities.

## Honesty rules

- The client never advances state: every repaint after a write comes back from
  the server, and only acceptance may render as green.
- A budget is the cap a run was permitted, never a spend — nothing measures
  consumption, so nothing may display one.
- Nothing here stores prompts or transcripts. A run surface may show the
  private attempt store's sanitized stderr diagnostic of at most 4 KiB for an
  unsuccessful process and bounded terminal-tool rejection reasons drawn from a
  fixed allowlist of the backend's own refusal strings. It never receives
  stdout, successful-run stderr, or tool arguments. Truncation and redaction
  remain visible.
- Automatic updates show observed task/run state without hidden model
  reasoning or invented progress percentages.

## The project board (ADR 0020)

- A project opens on its board. Agent cards are standing `agent` records that
  are neither templates nor retired; edges are `agent_link` records and
  `reports_to` provenance from the `/api/graph` projection. The canvas never
  draws a relation the ledger does not hold: dragging a port opens a link
  through `POST /api/agent-links`, and the edge appears only after the graph is
  re-read.
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
  `?agent=`, Give a task opens the existing Prepare run for the selected task.
- Tracker task rows may carry nullable `objective_id` (the effective objective,
  including inherited blueprint provenance) and `application_id`. Missing or
  null remains unknown; these additive fields never authorize a UI action.
- React Flow's stylesheet ships as a file (CSP `style-src 'self'`); board
  styling is class-based. Agent activity is text plus a data attribute; green
  is still reserved for acceptance.

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
