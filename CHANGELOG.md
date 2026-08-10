# Changelog

All notable changes are documented here. This project follows Semantic
Versioning once a stable release line is declared.

## Unreleased

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
- **Autonomous role creation ships with all seven of its brakes attached**: a
  turnover budget counting creations and retirements together, grants that never
  widen, a strictly narrower creation grant on each automatic generation, a
  canonical event carrying rationale and proposer, one-action cascade retire,
  visible `origin` in the index, and effective grants derived on every read so a
  downgrade binds work already running.
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
  HTTP.
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
  a rejected edit leaves the previous one byte-identical.
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
