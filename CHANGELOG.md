# Changelog

All notable changes are documented here. This project follows Semantic
Versioning once a stable release line is declared.

**How this file is maintained.** Every merged pull request adds an entry under
`## Unreleased`, newest first. A completed plan contributes exactly one summary
line here and then moves to `docs/archive/plans/`, linked from that line.
Counts of tests are never recorded in this file; they live in the evidence
manifests under `docs/evidence/`, bound to the revision they were measured on.
This file is the structured history the repository keeps instead of retaining
finished plans at the top level.

## Unreleased

- **The second dogfooding wave: measured write latency, one name field,
  archivable skill groups, and no more Starter Packs.** Before any latency
  target is declared, there is now a measurement: warm `task_create` has a
  median of 4.4 s on a 300-task ledger (420 events) and 33 s at 3206 events,
  and 62–64 % of that time is `canonical_reads_validation` — a full double
  pass over the ledger on every single write. No p50/p95 gate is asserted
  until the owner sets one against these numbers
  ([baseline](docs/performance/2026-09-10-mutation-baseline.md)).
  User-authored text is entered once, in one field, and stored exactly as
  typed, so a Russian name is a name rather than a locale key. A skill group
  can be archived and restored: an empty group goes directly, a non-empty one
  must name the target group and moves its skills atomically, revision-bound
  with typed 409/422 rather than optimistic client state. Mutation feedback is
  scoped to `{project, surface, operation}` instead of one global "busy" flag,
  so reading and navigating stay available while a write is in flight; the 2 s
  and 8 s notes a slow write shows are presentation hints, not a performance
  promise. Starter Packs are gone — their content moved into the built-in
  blueprints, and the backend surface and the Work screens were removed with
  them. Finally, a canonical objective and blueprint-application provenance
  were decided and accepted on 2026-09-11
  ([ADR 0021](docs/adr/0021-objective-blueprint-application-provenance.md)):
  each successful blueprint application will record one immutable
  `blueprint_application` naming the exact blueprint version and the tasks and
  roles it created, so a department is recoverable after a reload or a retry.
  The implementation of that record is still open work.
- **A project opens on its board.** The home screen of a project is an
  infinite canvas again, as the original product intent described it. Hired
  roles are cards carrying name, specialization, provider and model, the state
  of their current run, a task count and whether they are waiting on a person,
  with Conversation, Results, Tasks and "Give a task" on each card. Edges are
  `agent_link` records: dragging a port from one card to another opens a real
  link through the existing route, and an arrow that has no ledger record
  behind it is never drawn. Applying a blueprint from the board draws a
  department frame around the roles and tasks that application created, and
  roles can be dragged between frames. Frames and node positions are
  deliberately operational layout — per-project JSON under the state root
  behind `GET/PUT /api/board`, compare-and-swap on revision — not canonical
  truth, and the decision says so rather than promoting a drawing to a record.
  The task tracker becomes its own Tasks tab with a per-role filter, hiring
  moved into the board's side panel, the Agents tab is retired, and the old
  `view=team` route redirects to the board
  ([ADR 0020](docs/adr/0020-project-board-home.md)).
- **The first dogfooding wave answered what a returning operator hits first.**
  Applying a blueprint no longer looks like it started working: the
  confirmation states that roles and tasks were created and that zero runs
  started, and the primary action opens Prepare run for the first task the
  server confirms is ready. Output labels stop over-promising — latest,
  historical, expired and unavailable are distinguished, and a separate
  additive `review_state` (`awaiting`, `approved`, `returned`, or absent)
  carries review status instead of the panel inferring it from staleness.
  Archiving a project asks for confirmation naming the project and its
  unchanged path, because archiving never touches the folder, and readiness is
  said in words rather than signalled by a colour. A conversation now shows
  whether its recipient is actually reachable: `recipient_availability` is
  joined from one server snapshot, and missing, partial or stale data reports
  `unknown` rather than claiming inactive. The five delivery states became a
  stepper that is not mistaken for acceptance, and unsent text or attachments
  are guarded before a page is left. A run's time limit is editable —
  `wall_time_seconds`, between 60 and 3600 seconds, enforced by the server and
  shown where the run is prepared.
- **A project gets its own conversations, library and results.** The shell
  becomes a compact sidebar with one search field, one create-or-connect
  action and an ellipsis beside each project, with every action attached to
  the subject it acts on. Built-in skills sit in named collapsible groups with
  counts and grouped search instead of one flat catalogue. Custom blueprints
  are persistent versioned definitions with slots, exact specialization
  references and a validated acyclic task graph, and applying one uses an
  exact version. Results appear on a task or a hired agent only when the
  server reports accessible outputs, scoped to the producer that actually made
  them — ownership is never inferred from a later assignee or from who last
  revised the task. Conversations accept attachments (up to 10 images and 10
  other files per message, 15 MB each) stored in private service-owned storage
  outside the repository, the ledger and the webroot, referenced canonically
  by opaque identity, digest and verified media type, with no operator path in
  the record. A running frontend preview is a different kind of output from an
  image, and an untrusted preview is served on a separate origin that never
  receives Commons authentication
  ([ADR 0019](docs/adr/0019-project-native-collaboration-and-outputs.md)).
- **One service host, many isolated projects.** Projects are created or
  connected from the sidebar by typed path, with a native folder chooser where
  the host supports one. Each project owns its team, task graph, Gallery,
  Context Packs and run history, while installed skills, specializations and
  blueprints stay service-wide. The host keeps a separate project context and
  session owner per project rather than repointing one shared context, so
  switching projects cannot stop a running launch or show another project's
  draft or response. A private registry — outside every delegated repository,
  its state root and the service library — holds an opaque `project.<32 hex>`
  identity, the workspace id, a verified checkout and state binding, an
  archive flag and bounded receipts, with a default capacity of 64 and no
  briefs, credentials or provider configuration in it. Archiving or removing a
  project never deletes its folder, and public list DTOs omit filesystem paths
  ([ADR 0017](docs/adr/0017-project-scoped-service-host.md)).
- **A freshly installed service already knows 45 skills.** The library ships
  45 professional skills, 30 specialization definitions and — after the second
  dogfooding wave — seven built-in blueprints: web-app, mobile-app,
  telegram-mini-app, grounded-ai-assistant, improve-service, feature-delivery
  and product-discovery. Skills and role definitions are added and edited in
  the browser rather than in a YAML file, several named instances of one
  specialization can be hired with independently chosen providers and models,
  a dependency graph of tasks is created and adjusted in the panel, and a
  selected task is launched from it and its run inspected. Design screens can
  be imported and assembled into exact Design Packages. The separation is the
  point: a skill version is a service-owned method with its allowed resource
  closure, a specialization describes responsibility and entry method, and an
  instance is what a project hires — definitions, instances and executions
  stay three different things. Console setup becomes the exception rather than
  the way in
  ([ADR 0016](docs/adr/0016-service-library-and-live-workspace.md)).
- **Work opens on the work.** In the measured baseline the task tracker began
  at y=5822 of a 5986-pixel page, behind setup, role creation, launch and a
  full context editor, so a returning operator could not see their own work
  without scrolling past everything they had already done. The workspace is
  reorganised around tasks: task creation comes first, launching is
  contextual, and the library and settings sit behind the work itself. The
  inspector leads with task state, readiness, blocker, responsible role and
  the next safe action, keeping goal, criteria, empty summaries, run history
  and technical identity behind native disclosure that survives routine
  updates. Three primary filters — all, attention, active — sit beside a
  labelled select for the remaining seven states, with all ten query values
  and URL addressing preserved. The visual pass that followed keeps the dark
  palette and system fonts, reserves blue for action, selection and focus,
  amber for attention and red for refusal, and keeps task state, readiness,
  evidence, freshness, review and acceptance as separate domains, each with
  its canonical value and an EN/RU gloss. The team surface this increment left
  unresolved was answered later by the board
  ([ADR 0014](docs/adr/0014-task-first-workspace-ux.md),
  [ADR 0015](docs/adr/0015-browser-native-visual-refinement.md)).
- **Repository guidance shrank, and cleanup learned its own scope.** Current
  work routes through one implementation programme, roadmap direction is
  separated from historical evidence, and two unregistered historical inputs
  moved into `docs/archive/` byte-for-byte rather than being rewritten or
  dropped. `make clean` is preview-first with a fixed generated-output scope
  and refuses tracked files, case aliases and symlink roots; canonical state,
  registered proof and the shipped UI asset are never in its reach (#6).
- **Context and design become canonical, revisioned objects.** A Context Pack
  is a canonical entity (`context_pack.created` / `context_pack.revised`)
  whose revision is a full snapshot rather than a patch: every fact carries at
  least one exact revision-bound canonical source, and publishing v2 cannot
  mutate v1. An accumulated-context run therefore has a reproducible baseline
  that inherits no provider transcript, credential or authority from another
  run. Design Packages do the same for screens — an ordered list of bindings,
  each naming the exact artifact revision, its `sha256:` content revision and
  the exact producing task revision, with a preview marked safe only after the
  verified reader has proved the current bytes below every descriptor, hash,
  pixel and media-type limit. A Design Package can then be bound to a launch,
  so a run is told exactly which screens it is working from instead of
  guessing from whatever is on disk
  ([ADR 0012](docs/adr/0012-context-packs.md),
  [ADR 0013](docs/adr/0013-design-packages.md)).
- **Grok Build joins Codex and Claude Code as a third local provider.**
  Allowlisted builder and independent-reviewer profiles run through the same
  runtime, authentication, qualification, `init`, panel and documentation
  paths as the other two. The installed CLI's real headless flags are used
  rather than invented ones; project MCP configuration is managed by the
  service with fixed environment isolation and a fail-closed inspect probe, so
  MCP servers already active on the operator's machine cannot quietly widen
  what a worker may do. Instructions reach the process through a fixed stdin
  endpoint — never process arguments, never a prompt file on disk — and a
  failure there must not silently restore either. Terminal MCP rules were
  hardened after the canary runs: a Grok process that exits without calling a
  terminal tool is reported as needing an operator rather than counted as a
  result. What this does not settle is live qualification. **L1 — Linux
  receipt scope, a green doctor, and six-profile live qualification on an
  exact source, provider and host boundary — remains unproven, and R2 release
  evidence remains open.** A green canary on one macOS machine is neither of
  them, and nothing below should be read as claiming otherwise.
- **A provider process that exits is no longer read as work that succeeded.**
  Launch planning moves behind adapters that build an exact, validated,
  immutable launch plan, and the execution-host binding boundary refuses
  mutable duck-typed substitutes instead of accepting anything with the right
  attribute names. Admission to the local execution host is authenticated and
  owner-only: each request binds to an immutable launch identity and leaves a
  crash-safe, exactly-once operational receipt that carries no provider-private
  material and no canonical lifecycle state. Qualification canaries check an
  exact provider, model and source boundary, diagnostics are bounded to what
  can be said safely, and the panel gained a recovery flow for a provider whose
  authentication has lapsed instead of presenting the failure as a broken run.
  A process exit is an observation about a process; the canonical outcome still
  has to be written by the run itself.
- **Documentation consolidation, 18 September 2026.** `docs/PRODUCT.md`
  becomes the single owner of positioning, the target user and the EN/RU
  glossary, so a product fact has one home instead of being restated in five
  documents that drift apart. Sixteen completed plans and eleven review and
  audit reports move under `docs/archive/plans/` and `docs/archive/reviews/`
  with a status stamp and byte-identical contents; the top level keeps only
  what is active. Evidence gains a written retention policy — what belongs
  under `docs/evidence/`, in what form, and when a dated directory closes.
  Retiring the legacy panel in favour of Work is decided in principle, with
  ADR 0022 still to be written and the redirect and test removal still to be
  done. See the
  [plan](docs/plans/2026-09-18-consolidation-and-wave-4-plan.md) and the
  [audit](docs/audits/2026-09-18-repository-and-documentation-audit.md).
- **The terminal step disappears: `git clone … && make sync && agent-commons
  ui` is now the whole bootstrap.** Reaching a working panel used to mean
  opening a terminal four times — `init`, `session start`, `ui` with three
  capability flags, and hand-writing a `runtime.yaml` whose shape was
  documented nowhere but the error messages that rejected it. All four moves
  into the panel itself. `agent-commons ui` on an uninitialized directory no
  longer refuses; it serves a first-run screen that creates the project here
  (the same initializer `agent-commons init` runs, not a second one), finds
  `claude`/`codex` on `PATH` and writes an operator runtime config for
  whichever it found. Since Grok Build was added as a third local provider,
  that scan covers `claude`, `codex` and `grok`, and the case below is none of
  the three. If it finds none of them, it says run functionality is
  unavailable and directs the operator to install a subscribed CLI before
  looking again. The panel opens, renews on a 15-minute heartbeat, and closes an
  operator session of its own — nobody runs `session start` for it, and an
  externally selected `AGENT_COMMONS_SESSION_ID` is deliberately not adopted,
  since the panel would hold no ownership nonce for a session it did not open
  and so could never renew it. Two panels on one project is refused rather
  than silently shared: an exclusive lock file under the state root holds the
  bound port, and the second panel to start names the first one's address
  instead of racing it for one session's renewal window.
- **All three capability flags are gone; `--read-only` replaces them.**
  `--enable-writes`, `--enable-catalog-editing`, and `--enable-launch` are
  removed from `agent-commons ui`. There are no capability flags left at
  all — what the panel can do follows from what is configured (a session, an
  operator runtime config, a catalogue beside it), not from what was passed
  on the command line, and the panel says which of those pieces are missing
  rather than which flag would unlock them. `--role-catalog` and
  `--profile-config` remain, now as pure path overrides rather than
  privilege switches. The route-registration formula changes with them: a
  writing panel now registers its whole non-`GET` surface — the union of
  `MUTATING_ROUTES`, `CATALOG_ROUTES`, `LAUNCH_ROUTES`, and the new
  `SETUP_ROUTES` — unconditionally, the moment it can hold a session at all,
  because the panel's own first-run screen can create the workspace, write
  the runtime config, and adopt a catalogue beside it while already serving,
  and FastAPI builds its route table exactly once. What used to be "the route
  doesn't exist" is now a typed 409 from the handler instead —
  `setup_uninitialized`, `launch_not_configured`, the catalogue's own
  refusal — and the invariant test now asserts the registered surface
  against that literal union rather than against the same conditions the
  registration used, so the two cannot silently agree with each other while
  disagreeing with the code.
- **The panel no longer offers demo mode.** The product route
  `POST /api/setup/demo-config`, its response field, and its controls are
  removed. With neither supported provider present, first run honestly says
  execution is unavailable and points to documentation plus a rescan. The
  internal `demo: true` runner seam remains for development and hermetic tests,
  but is not an onboarding promise.
- **Generated runtime configuration is written once and only regenerated
  additively when ownership is proved.** A second
  `POST /api/setup/runtime-config` refuses a working file without changing its
  bytes. `POST /api/setup/add-discovered-providers` is parameterless and can
  add profiles for a newly discovered provider only after reconstructing the
  exact expected bytes of the existing generated config. A hand edit gives a
  typed refusal; there is no YAML merge. These boundaries implement
  `decision.1A08MD6B8TXRWVNX00DJZD98DY` alongside the product-surface decision
  `decision.2ZTHNGPQVMHZ5RF614HQPWCKYV`.
- **A hired role may now choose its model, and only once.** The hire form
  offers a model list assembled server-side from configured profiles' models
  and the models already running on the project's roles, plus free text; the
  asset itself names no model, which a test asserts, so the operator never
  reads the offered list as complete. The choice is stored in the existing
  `agent.v1` schema's `payload.extensions.model` — no schema change, no
  migration — and delivered to the launch through `dataclasses.replace` on
  the frozen profile, which re-runs its own validation for the new model.
  There is deliberately no way to change a hired role's model later: a model
  change means rebuilding the role's context for a different model, which
  this version does not do, so the setting is absent from role settings
  rather than present and inert.
- **There is no confirmation dialog before a launch spends your provider
  subscription, and that is the owner's decision, not an oversight.** Coming
  to Codex or Claude Code directly does not ask per window whether spending
  is authorized; the panel now holds itself to the same standard instead of
  interposing a click the underlying tools do not have. The fact that this
  product runs billable provider processes on the operator's own subscription
  moves into documentation instead of a dialog — stated plainly in the
  [README](README.md#configure-a-provider-before-running-work) and the
  [threat model](docs/THREAT_MODEL.md) rather than learned from a bill.
- **The loop closes: a finished run now has somewhere to be accepted.**
  Two blind round-3 testers, working in parallel and on different models,
  reached `succeeded` and independently reported the same blocker — nothing
  anywhere in the panel could accept the work. Acceptance turned out to be a
  chain rather than a button: `task.accepted` is legal only from state
  `review`, and only with an approved independent review that is not stale,
  is bound to the task's current effective revision, and was written outside
  the principals that did the work. Three routes now offer the steps of that
  chain — `review-request` walks the task to `review` through the manager's
  own transitions and opens the independent request, `accept` records the
  human decision with the manager choosing which review qualifies, `reopen`
  sends the work back — each a thin adapter over the same `CommonsManager`
  the CLI uses and each sealed into `MUTATING_ROUTES` with its test. Nothing
  was weakened to get there: the task never advances itself, the operator
  cannot approve their own review request, and accepting with no approved
  verdict is refused. That refusal is pinned by test, because it is the
  point of the design rather than a gap in it. Lifecycle refusals stop
  arriving as lifecycle vocabulary — a table maps each to the next action a
  person can take, canonical text kept in brackets — and a succeeded run
  whose task is unaccepted lands in the attention queue with the role's name
  and a click through to the task.
- **Every drawer leads with an answer instead of a ledger record.** Each
  entity kind gets a short human summary built only from fields the server
  returned; the JSON moved behind a toggle that re-collapses on each opening.
  Canonical values keep the ledger's spelling everywhere and carry a human
  gloss beside them — the recorded compromise in place of translating them,
  since the panel, the CLI and the ledger have to say the same state the same
  way. One glossary above the strings table now fixes one word per concept in
  each language, ending the four names one thing had, and records the split
  that resolves the canonical twins: `delegation` names the record, a run is
  the activity; `agent` is the ledger's kind, a role is what a person hires.
- **Three modal bugs that looked like the panel changing its own mind.** A
  click is delivered to the nearest common ancestor of press and release, so
  selecting text in a field and releasing past the dialog's edge dismissed
  the form. `applyI18n()` runs on every snapshot and rewrote the hire heading
  from its `data-i18n`, undoing a JS-written title. And `fillSelect` drops
  every option and re-selects the default, so any repaint reaching an open
  modal silently reset profile, grants and context mode — widening a grant
  decision without telling anyone — and took the chosen task off the Run tab
  mid-run. All three are fixed at the cause. The four dialogs also gained one
  shared focus trap, with Esc guarded when a field has moved.
- **The board shows the team, and its ports can be grabbed.** Runtime nodes
  — sessions and delegations — sit behind a counted toggle that persists, so
  one launch no longer adds four technical nodes under "depth 1" captions.
  Fit measures the actually visible rectangle rather than the whole canvas
  and floors the scale where a card is still legible; a band wraps into a
  grid derived from its own size instead of a fixed eight per row. Link ports
  carry a 24px hit target counter-scaled from the one place the zoom changes,
  after both testers failed independently to open a link by dragging.
- **The panel survives Russian, a narrow window and a dark scrollbar.**
  +30% string length is the stated design margin and a test measures the
  Russian table against it. Scrollbars take the palette, the footer explains
  what a stale warning is rather than naming it, radio dots sit beside their
  labels again (two causes, not the one reported), and below 900px both side
  columns become overlays — toggled with the `hidden` attribute the board's
  fit computation already measures, so fitting stays honest at any width.
  Runs say when they started, how long they took, what they were for and
  what budget they were permitted; they deliberately show no spend, because
  nothing in this codebase records consumed provider units and the panel says
  so rather than estimating. The header shows the workspace as its directory
  with the ids one click away — the only honest human name available.
- **Agent links stopped asking for a deadline they cannot keep.**
  `deadline_seconds` is optional now: replay has no clock, so no reader ever
  enforced expiry, and requiring the field only made the panel, the CLI, the
  MCP tool and the manager invent a number nothing consumed. A link lives
  until an explicit `agent.link_closed`; the field stays readable for the
  history that carries it and for an operator who wants to record an intended
  horizon. Agents are bounded by attempts, `provider_units`, depth and a live
  run's wall-time guard — not by calendar time. The ADR's old promise to
  "surface expiry where a clock exists" is withdrawn rather than restated.
- **The Overview grew a CTO track.** The two-minute page stays as the first
  tab; five more — agents and memory, tasks and runs, links, limits and
  safety, skills and tools — explain the actual backend mechanics with
  micro-examples in canonical names, verified against the code before
  writing. Where a thing is recorded but not yet consumed, the page says so:
  `auto` is withheld and acts as `ask`, `context_mode: accumulated` feeds no
  context into a launch today, an `ask` link opens no channel and
  `deadline_seconds` is enforced by no timer — while `handoff_work` is named
  as the one link the lifecycle actually reads.
- **A second cold run drove eight fixes toward a findable result.** The
  follow-up PM walkthrough passed hire → task → run → terminal state both
  ways, then failed to find the result anywhere; the panel now answers.
  Runs cards and a "Runs on this task" section in the task drawer open the
  run's delegation record — with its canonical summary — in one click (the
  task's own state stays a human decision and never advances itself). The
  board fits itself once on first paint, offers a Fit button and a plain
  pan/zoom hint, and stops rendering role templates as live agents: the
  catalogue is their shelf, and the graph tally splits `counts.templates`
  from `counts.agents`. The hire modal resets every opening (a cancelled
  attempt no longer leaks into the next), says "Save the template" when
  that is what the click records, and prefills the rationale a chosen
  template already carries. Known field refusals lead with the field's
  label in the panel's language, canonical text kept in brackets; unknown
  refusals stay verbatim. The read-only catalogue hides its form under the
  banner instead of dangling live inputs, footer counts speak both
  locales, and the operator's own session card says plainly that it is
  them. Graph card states stay canonical by position.
- **The panel is rebuilt around one home per kind of thing.** A library
  sidebar (board, runs, a two-minute Overview, Skills and Tools as two doors
  over the one operator catalogue, honest SGR/MCP placeholders that name their
  unlock condition), the board in the centre, the conversation — main chat and
  the attention queue — docked right, and a per-entity drawer where Record,
  Settings, Links, Run and Message are tabs of the selected thing. Hiring
  moved behind a + button; a cold start shows a guided three-step card; every
  control carries a translated one-line tooltip; the whole surface commits to
  one dark, dense look. No write path changed.
- **Links are first-class on the board.** Dragging one of a role card's four
  ports onto another role opens a recorded permission through the new
  `POST /api/agent-links` route (closed via
  `POST /api/agent-links/{link_id}/close`, always with the revision it
  closes); the form states plainly that a link is a journal permission, not
  yet a communication channel, and closing keeps history — the UI never says
  "delete". Both routes are sealed into `MUTATING_ROUTES` with their tests.
- **Granting cannot outrun the catalogue any more.** `GET /api/catalog`
  serves each profile's real tool sets (`profile_tools`, the same composition
  a launch receives, locked by a bit-for-bit test), reconfigure mirrors the
  hire-time checks (unknown skills and out-of-profile tools are refused at
  click time with their ids named), catalogue entries carry their active
  holders, and the attention queue warns when a hand-edited catalogue leaves
  a role holding a vanished skill — before any run fails.
- **The agent catalogue turns role templates into a shelf.** Templates
  (`template: true` roles — still not a fourth record kind) get their own
  library view with a two-click "Hire from this", and the hire modal now leads
  with the choice — a new agent from scratch, or a ready template — showing
  only the fields that choice needs. Hiring from a template sends only
  `from_preset_id`, inheriting profile, permissions and budget, guarded by an
  HTTP test.
- **A cold-run usability round drove nine fixes.** An independent PM-style
  walkthrough (no source access, browser only) found the gaps a newcomer
  actually hits, and the panel answers them: tasks are created from the board
  (`POST /api/tasks` + a "+ Task" button beside hire — the chat form records a
  thread, not a task, and could not put work on the board), the board's
  buttons became a toolbar row so floating chrome can never cover a card's
  link ports (a real drag died on exactly that), a link drop snaps to the
  nearest role within reach instead of demanding a pixel-exact release, the
  Links tab offers a button path ("Open a link with…") beside the drag, an
  empty agent catalogue disables the "from the catalogue" hire mode with its
  reason instead of a raw refusal, the catalogue editor no longer bleeds onto
  other library views, Runs show role and task names with the run's canonical
  summary line (ids stay in the tooltip), the header's trust wording collapsed
  into an ⓘ tooltip, and the hire form's terms carry translated tooltips.
- **The internal demo runner remains a development seam.** `demo: true` can
  bind it for hermetic runtime tests, but it is not a panel bootstrap or a
  product promise for users without a provider.

- **Agents become a first-class standing role**, separate from the situational
  run they perform. New canonical events `agent.created`, `agent.reconfigured`,
  `agent.retired`, `agent.link_opened`, and `agent.link_closed`, with the
  entities `agent` and `agent_link`. A role carries a name, an
  operator-allowlisted profile, three permissions, a context-isolation mode, and
  its lineage; a delegation stays terminal and declares which role it acts for
  through an `on_behalf_of` event relation, so no existing schema changed. Full
  contract in [ADR 0009](docs/adr/0009-agents-as-first-class-roles.md).
- **Review independence is now decided over principals, not sessions.** A
  standing role that authored work in one run cannot approve it in the next,
  even though every session identifier differs. The rule is expressed once, over
  the class, so a principal kind added later is covered at every call site.
- **Autonomous role creation is built with all seven of its brakes, and the
  automatic (`auto`) level is currently withheld behind them.** The brakes — a
  turnover budget counting creations and retirements together, grants that never
  widen, a strictly narrower creation grant on each automatic generation, a
  canonical event carrying rationale and proposer, one-action cascade retire,
  visible `origin` in the index, and effective grants derived on every read so a
  downgrade binds work already running — held under two adversarial reviews. But
  a 2026-08-10 review found the first edition of this line untrue in practice, so
  `auto` is capped to `ask` at read time (`AUTOMATIC_LEVEL_WITHHELD`): every
  structural action is human-confirmed until the level has run longer behind its
  proven brakes. See ADR 0009 and
  `docs/audits/2026-08-10-standing-roles-review.md`.
- Role settings **narrow and never widen**: `tool_allowlist` is intersected with
  the profile's fixed tool set at invocation build time, and the narrowing is
  asserted against the argv of a launched run rather than against a settings
  object. Terminal outcome tools are exempt, since a role that cannot report a
  result is broken rather than narrower. A role selects a model by selecting a
  profile.
- Removal is `retire`, never delete. No role may be retired while it owes a live
  delegation or an unfinished review, whoever created it. A task-scoped lifetime
  retires a role when its task is accepted or cancelled, derived in the
  projection so there is no event to forget to write.
- Added `agent-commons agent create|list|show|reconfigure|retire|link|unlink`
  and `delegation create --on-behalf-of`.
- **`agent-commons ui --enable-writes`** adds a fixed, enumerated set of
  mutating routes for the role panel, each a thin adapter over the same
  `CommonsManager` the CLI and MCP adapters use. Read-only stays the default.
  The invariant test now proves three things instead of one: the mutating
  surface equals an explicit allowlist, every route dies when `record_event` is
  removed, and each route's event is found in the ledger after being driven over
  HTTP. (`--enable-writes` itself is gone — see "the terminal step
  disappears" and "all three capability flags are gone" above; the allowlist
  and its invariant test outlive the flag.)
- **`agent-commons ui --enable-launch --profile-config <file>`** lets the panel
  put a role to work: a Run action records a delegation on the role's behalf and
  runs it through the same `DelegationRuntimeService` the CLI broker uses — one
  launch path, not a second. Provider and model come from the role's profile. It
  is a third, separate gate (spawning a billable process, not recording metadata)
  with its own route allowlist. A Runs surface shows each run's live phase —
  launching → running → terminal — as metadata only, never any provider output,
  and the change detector folds in the runtime attempt so the panel moves as the
  run does. (`--enable-launch` is gone too; the launch route now registers on
  every operator panel and refuses `launch_not_configured` until a runtime
  config exists — see "all three capability flags are gone" above.)
- The graph shows roles as nodes with their reporting lineage, and rings any
  node waiting on a human decision so a blocker is visible without opening a
  list. Both sources of that state — a delegation in `input_needed` and an open
  decision-request thread — already have producers.
- The `ask` level is executable: a role at that level receives
  `commons_propose_agent` rather than the recording tool, opens a proposal
  thread, and a person confirms it with `agent approve`. Tool registration is
  keyed on the grant *and its level*, so no role is handed a tool that can only
  refuse.
- A human-confirmed role must bind the proposal it confirms. The lifecycle
  checks that the thread is an open proposal, that the session which opened it
  was running as the crediting role, and that the confirmed terms match what was
  proposed. `created_by_agent_id` stops being free text: a role can no longer be
  attributed to a proposer that never asked.
- Added `agent propose`, `agent proposals`, and `agent approve`, plus
  `POST /api/agents/proposals/{thread_id}/approve` and a Proposals tab in the
  panel. An open proposal rings its role on the graph like any other blocker
  waiting on a person.
- **Naming a role on a delegation is now an authorized operation.** Acting for a
  role means holding its effective grants and its staff-changing tools, and that
  binding was unchecked: any session able to open a delegation could name the
  most privileged role and hand a session of its choosing everything it may do.
  A human window may still staff any active role; a session already running as a
  role may staff only itself or a role below it in its own lineage. Checked in
  the domain lifecycle against the event relation, so replay revalidates it.
- The operator catalogue is editable from the panel as a form, not a YAML box,
  behind its own gate: `agent-commons ui --enable-catalog-editing` additionally
  requires `--role-catalog`. It is separate from `--enable-writes` because
  recording a role and changing what every delegated run is told to do are
  different magnitudes of privilege. Provider profiles stay out of the UI at any
  gate. The file is validated in full and published atomically at mode 0600, so
  a rejected edit leaves the previous one byte-identical. (`--enable-catalog-editing`
  is gone; the privilege distinction it named is unchanged, it is just no longer
  a flag — see "all three capability flags are gone" above.)
- **A required skill now reaches the process.** Catalogue skills carry
  operator-authored instruction text appended to the run's bounded instruction,
  resolved at launch and asserted against the bytes the provider receives. A
  role selecting a skill the catalogue does not define refuses the launch rather
  than silently running without it. Removing a catalogue entry an active role
  requires is refused and names the roles.
- Removed `mcp_allowlist` from role settings. Nothing read it: a worker receives
  exactly one MCP server, so narrowing a set of one meant nothing and widening
  it is a separate change. The panel now shows the profile's actual servers
  read-only instead of offering a control with no effect.
- A temporary link gains the `handoff_work` action, which is what lets a role
  staff a run with a role outside its own lineage. Adding it extended the enum
  and left the record's shape alone, as the typed action was meant to. An `ask`
  link does not widen staffing, and closing the link takes the widening back.
  The deadline is not checked during replay, which has no clock; a link ends by
  an explicit close.
- **Blocked runs are answerable from the panel.** A live request for input is
  listed with its bounded metadata and answered in place; answering also resumes
  the run, so the ring clears on the canvas. The communication channel
  authorizes by participant, so the panel answers only requests its own session
  owns — the rest are still listed, naming the session that can answer, because
  an invisible blocker is worse than one you cannot yet act on.
- **A main chat**, where a person states the work and hears back. It is a
  canonical thread of the new type `engagement`, addressed to every role that
  answers to the operator, optionally bound to the objective it is about. The
  panel opens on it before any node is clicked; a per-role chat stays for
  stepping in on one role.
- Several roles at the top share **one thread with several recipients**, not
  several threads merged in the view. Merging would invent an ordering no record
  has. A role created after a chat opened is reported as unaddressed rather than
  quietly folded in, because recipients are canonical and a projection must not
  rewrite them.
- Workers gain `commons_list_my_threads` and `commons_reply_thread`, so feedback
  can actually come back — without them the main chat would have been one-way.
  Both are bounded by the domain to threads that address the acting role, and
  both are narrowable by a role's tool selection.
- The addressed inbox now matches the role a session is running as, not only its
  session id and self-declared label, so a thread addressed to a standing role
  reaches it.
- Added `agent-commons chat open|show|say`, `GET/POST /api/chat`.
- **Full-text search over canonical history**, landing with the two things that
  read it: `agent-commons search <query>` and a search box in the panel. What is
  indexed is a positive allowlist of canonical fields, so a payload field added
  later is invisible to search until somebody adds it on purpose — a denylist
  would silently index whatever came next. Prompts, transcripts, tool arguments,
  and provider output are not in the ledger and so are not in the index.
- Search widens in steps and says which step answered — every term, any term, or
  a literal phrase — because a query that quietly fell back to any-term would
  read as a precise match.
- A read-only search never builds a projection. Opening the index creates
  directories, a database, tables, and a WAL; read-only callers answer from a
  projection that already exists or report that they cannot answer.
- The SQLite projection moves to schema v2. It is disposable, so an older
  version is dropped and rebuilt rather than migrated; a newer one still fails
  closed.
- A workspace that has created a role cannot be read by a build predating
  `commons.payload.agent.v1`; rollback means reverting to a checkout taken
  before the first role existed. Recorded in the threat model rather than
  papered over.
- Corrected an earlier entry that claimed `agent-commons run list` / `run
  export` shipped. They were withdrawn with the run-observability store
  ([ADR 0008](docs/adr/0008-run-observability-store-withdrawn.md)) and never had
  a producer.
- **Runtime request/attempt schemas move to v4** so a launch records the
  delegation tree it belongs to, which lets provider budget and the new subtree
  ceiling be charged against the tree rather than the requesting session. v3 and
  v2 remain readable. The change is one-way: a build that predates v4 refuses a
  v4 document by envelope, so rolling back requires clearing `runtime/requests`
  under the state root.
- Added `agent-commons broker stop <delegation-id>`, the operator action that
  terminates a live provider. It writes no canonical outcome; a following
  `broker reconcile` refuses to record one while the process is alive and then
  records `operator_stop_requested` rather than a broker restart that never
  happened. Only the requesting session may stop a delegation.
- Added `agent-commons ui`, a loopback-only read-only view of the workspace with
  bearer-token auth.
- Independent review now refuses any session that authored the subject, for
  every reviewable kind rather than tasks alone.
- Renamed the worker MCP tools `commons_workspace_*` to `commons_repo_*`, since
  they read repository files and the workspace name belongs to the communication
  channel it describes.

- Added workspace-namespaced state bases and fail-closed exact-root ownership
  checks before operational registries open, with source-aware/path-opt-in
  support diagnostics and non-destructive legacy compatibility. Legacy receipt
  migration proves ownership only after no-follow path, strict JSON,
  canonical-byte, packaged-schema, and typed workspace-ID validation.
- Added compact bounded `orient`/`inbox` defaults backed by a verified
  incremental SQLite read path, with canonical `--fresh` and expanded
  `--verbose` modes.
- Added `session current`, one-time zsh/bash/fish session exports, and
  field-specific typed-reference diagnostics without session auto-selection or
  nonce repetition.
- Added authenticated task-scoped request/progress/blocker/reply/ack operations.
  Full message content remains private operational state; canonical
  `input_needed`/`resumed` events use fixed maintainer-defined text.
- Added the first parent-to-child control slice: bounded guidance and safe
  checkpoint operations with exactly-once child acknowledgement, exact
  delegation binding, and a disable-able MCP surface.
- Added a privacy-safe 25-case orchestration eval catalog: executable P0 cases,
  explicit planned cases, and unsupported non-passing cases share one bounded
  result schema without prompts, reasoning, or raw provider output.
- Added an MCP-specific unavailable-executable preflight diagnostic, safe
  installed-source fingerprints in support/preflight output, and a cache-safe
  uv source reinstall path.
- Documented a package-scoped uv refresh for same-version source reinstalls,
  avoiding noisy cold dependency-index scans while replacing stale local
  wheels.
- Added an explicit isolated real-Claude compatibility canary that reports
  provider/model/catalog metadata and fails unless the terminal MCP result and
  canonical delegation agree.
- Added capability-gated recovery for requested delegations whose requester is
  unavailable, effective session-expiry reporting, requester shutdown guards,
  pre-admission child cleanup, and visible foreign-owner reconcile diagnostics.
- Made independent-review terminal calls explicit and stopped diagnostics from
  claiming no tool was called when terminal-tool audit is unavailable.
- Hardened immutable-ledger integrity, correction replay, evidence bindings,
  worker-scoped reads, and shared state-root propagation.
- Added canonical-finalization telemetry, terminal-tool audit counters,
  actionable diagnostics, operator-owned broker caps, aggregate budgets,
  bounded admission queues, and backpressure.
- Deferred the optional SQLite projection out of the canonical write path and
  added deterministic replay work counters and a scale benchmark.
- Declared macOS/Linux support, Apache-2.0 licensing, CI, package metadata, and
  experimental broker release criteria.

## 0.1.0

- Initial experimental file-ledger and local coordination release.
