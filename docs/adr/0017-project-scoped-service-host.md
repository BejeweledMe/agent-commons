# ADR 0017: Project-scoped service host

**Status:** Proposed — branch implementation authorized on 2026-09-08;
independent implementation review and release evidence pending.

**Baseline:** `605c0d7c9c5965c0a8303550056d07ce63afa697`.
**Delivery:** [execution plan](../project-workspace-ux-plan.md).

## Problem and decision

The operator wants to create and select projects in the sidebar. Each project
owns its team, task graph, Gallery, Context Packs and run history. Installed
skill, specialization and blueprint definitions remain service-wide; applying a
blueprint creates instances in the selected project. A project switch must not
stop an existing run or display another project's response or draft.

Implement one authenticated loopback service host containing immutable project
identity bindings and separate `UIContext` / `ProjectSessionOwner` instances.
Never implement selection by changing a shared context's `repo`, manager or
state root. Browser selection is local navigation, not server-global state.

This replaces ADR 0014's deferral of the project switcher for this implementation
scope. It preserves ADR 0005 workspace/state ownership and ADR 0016 exact service
library references. It creates no new canonical ledger entity or provider authority.

## Alternatives and evidence

| Alternative | Assessment |
| --- | --- |
| One host, independent project contexts | Selected. Existing contexts already own caches, manager factories and launch coordinators; route factories accept bound dependencies. Requires explicit host authentication, registry, lifecycle and browser scoping. |
| Supervised UI process per project | Stronger crash separation, but requires discovery/IPC, cross-port authentication handoff, supervision and recovery. Reconsider if measured contention or independent host replacement requires it. |
| Shared ledger with project filters | Rejected. Display filtering does not establish workspace or state ownership. |

Source assessment found single-repository construction in `ui/context.py`, one
session owner and panel lock in the `ui` CLI, and combined authentication / route
composition in `ui/server.py`. Existing launch threads are daemon threads;
`ProjectSessionOwner.shutdown()` stops heartbeat and releases its lock even when
live work prevents session closure. The implementation must address that lifetime
gap before claiming that host-managed projects preserve active ownership.

## Private registry and project identity

- A registry outside delegated repositories, their state roots and the service
  library stores a bounded schema, opaque `project.<32 lowercase hex>` ID,
  workspace ID, display name, verified checkout/state binding, archive flag,
  revision and bounded operation receipts. Default capacity is 64 projects.
- Use restrictive ownership/permissions, no-follow checks, bounded parsing,
  atomic publication, file locking and compare-and-swap. Validate persisted
  values; private storage is not automatically trusted input.
- Store neither briefs nor provider configuration, credentials, transcripts,
  images or browser drafts in the registry. Public list DTOs omit filesystem
  paths and state/session/receipt details.
- A handle fixes project ID, workspace ID, checkout identity, resolved state
  binding and generation. Caches and session recovery may change inside that
  binding; identity may not. Do not resolve inherited environment variables on
  individual requests.
- Registering the exact same workspace checkout is idempotent. Refuse a second
  checkout presenting the same workspace ID; linked worktrees are not new
  projects. Moving/replacing a checkout requires explicit reconnection and
  revalidation. Never silently follow a replacement directory or symlink.
- Revalidate identity before accessing a handle. Missing or conflicting projects
  remain visible with actionable status and cannot dispatch writes.

## Create, connect and retry

The first browser implementation uses a labelled absolute-directory field and
an inspection / confirmation step. A native OS picker can later enhance this
without introducing a general filesystem browser.

1. **New:** name and new directory in an existing, safe user-owned parent.
   **Connect:** name and existing Git checkout. Inspection does not initialize
   a workspace or acquire writer ownership.
2. Show whether Agent Commons initialization is needed. An opaque short-lived
   inspection identity binds mode, normalized path, name and filesystem identity.
3. Confirmation supplies that identity plus a stable idempotency key. Revalidate
   the path and identity before mutation. New-project creation initializes Git
   with a trusted executable and fixed arguments, then the standard manager
   initializer. No remote, commit or provider launch is implied.
4. Publish the registry entry and select the project. A brief and optional exact
   blueprint are applied through existing project APIs. Project creation does
   not require configured providers.
5. If the optional next step fails, retain the project and original complete
   apply intent, pins and retry identity. Do not create duplicate roles/tasks,
   reinterpret the latest blueprint head, or delete partially created files.

An initialization crash before its durable completion milestone is a special
case: the current initializer does not bind a preallocated workspace identity
to the project operation. An initialized directory at that boundary is therefore
ambiguous even if its inode is unchanged. Return
`project_initialization_ambiguous`; preserve the path and name and explain how to
inspect and explicitly connect the existing workspace. Never silently adopt it
as the result of the original create request. A completed milestone or published
registry result still supports the original idempotent retry. A future automatic
recovery path needs operation-bound initializer evidence first.

Reject collisions and overlap with registry/library/operator configuration/state
storage. Changed parameters cannot reuse an earlier idempotency identity. Rename
changes the display alias. Archive hides the project by default and is reversible;
it neither deletes files/history nor stops work. Active archived work must remain
discoverable. Missing checkouts have explicit reconnect guidance.

## Transport contract

One host owns the existing one-use browser exchange, HTTP-only cookie, opaque
per-process API base, exact loopback Host/Origin validation and CSP. Extract
project route registration from authentication; do not mount multiple complete
`create_app` instances with independent auth boundaries.

Under the authenticated opaque base:

| Route | Closed request / response |
| --- | --- |
| `GET /projects` | `{schema: agent_commons.project_list.v1, revision, default_project_id, read_only, projects}` |
| `POST /projects/inspect` | `{mode: new\|existing, path, name}` → `{inspection_id, name, mode, initialization_required, expires_at}` |
| `POST /projects` | `{inspection_id, idempotency_key}` → `{project, revision}` |
| `POST /projects/{id}/update` | `{expected_revision, name, archived, idempotency_key}` → `{project, revision}` |
| `/projects/{id}/…` | Existing project API suffix bound to this registered immutable context, including reads, mutations, streams and previews. |

Public project fields are `id`, `workspace_id`, `name`, `revision`, `archived`,
`available`, and `state` (`ready`, `missing`, `identity_conflict`). Unknown IDs
never select a default project. Legacy unqualified APIs, where retained for
embedding compatibility, remain explicitly bound to the startup context.

Host read-only mode denies registry and library mutations as well as canonical
writes. Service library storage and authorization are separate concerns:
definitions share one storage identity; project apply always uses the selected
project writer. Do not reuse one project's authorization to grant another
project writes. Route inventory tests must enumerate the entire extracted
project surface instead of losing coverage behind nested routers.

## Run and host lifetime

Navigation and browser-tab closure do not stop project handles, heartbeats or
runs. The supported background lifetime is **while the service host runs**.
This ADR does not claim detached runs, hard-crash resume or provider reattachment.

The lifecycle is `active → draining → closed`. Draining refuses new launches,
retains heartbeat and the panel lock, waits for bounded in-flight work and
reconciles its outcome before owner shutdown. Requested and input-needed work
also count; an empty process list alone does not prove closure. A timeout must
not record cancellation or release active ownership as if termination succeeded.
Bind the exact writer/runtime before asynchronous dispatch. A restart shows
unresolved work truthfully and never relaunches it by inference.

Bound open handles and host-wide admission in addition to per-project limits.
Admission does not merge canonical budgets. Never evict a context that owns
active or uncertain work. Measure cold / cached project loading separately;
large-ledger costs must not turn every inactive project into a background poller.

## Browser state and visual behavior

- Keep host authentication separate from immutable project API clients. Missing
  project/entity 404s must not clear host authentication for all projects.
- A safe project ID is allowed in Work/Gallery URLs and Back/Forward history.
  Paths, credentials, briefs and drafts are not. Selection is per tab.
- Scope request results, errors, `finally` handlers, SSE frames/cursors,
  selections, drafts, auth callbacks and uncertain mutation intents to project
  ID plus binding generation. Abort old reads and close old subscriptions, and
  reject already queued callbacks as well.
- Switching A→B preserves A's in-memory draft for return to A, but never
  displays it in B. A response arriving later updates only its original scope.
  Aborting an HTTP request does not mean a mutation was rolled back.
- Work and Gallery use the same project identity in links and bootstrap. The
  sidebar and page heading identify the current project consistently.
- Reuse the approved typography, neutral surfaces, spacing and semantic status
  colors from ADR 0015. Use native controls, visible labels, keyboard/focus
  recovery, RU/EN strings, responsive layout and honest loading/empty/error states.

## Verification, rollout and reversal

The implementation gate includes two distinct fixture repositories with
different canonical tasks, roles, contexts and Gallery images; cross-project ID
substitution; encoded/traversal routes; identity swaps and private-root overlap;
CAS/retry after response loss; partial creation/apply; read-only and route
inventory coverage; delayed A results/SSE while B is selected; two tabs;
Back/Forward; and a bounded fake run surviving selection/tab closure.

Verify create/connect/apply/edit/Gallery/switch in a real browser against packaged
assets, in both languages and at a narrow viewport. Run the complete `make check`,
build reproducibility and exact independent review before integration. A provider
canary is a separate explicitly authorized evidence gate, not a substitute for
these checks. L1/R2 remain open until their broader exact-scope criteria pass.

Roll out on `codex/project-workspaces-program`, preserve the single-project CLI
entry, and rebuild/install the reviewed wheel. Rollback restores that entry and
ignores the private registry; it never removes project directories or rewrites
ledgers. Stop or drain active service work explicitly before host replacement.

## Council provenance and open evidence

Two independent Astra source assessments and a peer critique informed this
proposal; the lead supplied integration/product synthesis. Requested models were
`gpt-6-astra`; no separate provider-reported model identity was returned by the
collaboration surface. Source code, tests and browser observations remain the
decision evidence, not model agreement.

Claude Fable was requested as the fourth participant. Its alias is advertised by
the installed CLI, but the source-sharing call was rejected by automatic approval
review and is pending explicit user consent. No Fable answer or screenshot review
has been obtained for this ADR yet. Do not describe this as a completed four-model
council. Full source assessments are available in the task's agent responses;
raw prompts/provider envelopes are not copied into the repository.
