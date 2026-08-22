# ADR 0009: Agents as first-class roles

Status: accepted; **amended 2026-08-11** after two review rounds; **superseded in part by ADR 0010** (Q2's flag-gated route table and Q4's catalogue-gate justification — the rest of this ADR stands).

> **What ships today, and what an earlier edition of this ADR overclaimed.**
> The first edition of this document said autonomous role creation ships "with
> all seven mechanisms landing together." A review
> (`docs/audits/2026-08-10-standing-roles-review.md`) found four independent
> paths defeated those mechanisms; the fixes are in, and the mechanisms now hold
> under adversarial execution. But the **automatic (`auto`) grant level is
> deliberately withheld on this branch** until it has run longer with its brakes
> proven: `effective_grants` caps every level at `ask`, so a stored `auto`
> behaves as `ask` and every structural action is human-confirmed. Read Q3 below
> with that in mind — the seven guarantees are real and tested, but the
> *autonomous* path they guard is staged, not live. The human-confirmed path
> that is live is bounded by the turnover budget, not by the strict-decrease
> ceiling (which binds only `approval: automatic`).

## Context

Today "agent" means *delegation*: one bounded attempt, one fresh child session,
no resume. That is the right model for a situational run and the wrong model for
an organisation. A canvas cannot draw a reporting structure out of records that
are terminal by construction, and there is nowhere to hang a standing permission,
a required skill, or a place in a staff index.

This ADR introduces the persistent **role**, and answers Q1–Q6.

Two things stay separate on purpose, because merging them destroys the property
that makes review trustworthy:

| | Role (`agent`) | Situational run (`delegation`) |
|---|---|---|
| Lifetime | outlives tasks | one bounded attempt, terminal |
| Context | may accumulate, or may be pinned fresh | always a fresh child session |
| Identity | in the staff index; reachable from another team | not indexed; not addressable |
| Removal | retire (never delete) | none needed — it ends itself |
| Created by | human, or agent under a standing grant | any session with authority |

A situational run creates **no** role record. "Give me an independent review with
a clean context" is already fully covered by a delegation to an
independent-reviewer profile. Modelling that run as "an instance of a role" would
invite inheriting the role's context, and independence would quietly become a
claim about `session_id` rather than a fact about what the reviewer saw.

## Q1. How a role is modelled

### Canonical: three events, one entity kind

New payload schema `commons.payload.agent.v1`, entity kind `agent`, IDs
`agent.<ULID>`.

```text
agent:  active ──reconfigured──▸ active ──retired──▸ retired
                                    └── lifetime expiry ──▸ retired (derived)
```

- `agent.created` — identity, profile, grants, context mode, lineage, rationale.
- `agent.reconfigured` — a bounded `changes` map plus a reason.
- `agent.retired` — removal from service. There is no delete: the ledger is
  immutable, so the operation is named `retire` in the domain, the CLI, and the
  UI.

`agent_link` is a second entity kind in the same family (`agent.link_opened`,
`agent.link_closed`) — see Q-links below.

### Relation to `profile_id`

A role **selects** one operator-allowlisted profile; it never describes one. The
profile keeps owning the executable, argv, sandbox, permission mode, and the
fixed tool set. The role may only *narrow* what the profile already grants. That
keeps the confused-deputy boundary from ADR 0004 exactly where it is: no role
setting can widen a child's authority, so no role setting needs to be treated as
a launch credential.

Profiles are partially ordered for role creation:

```text
codex-builder  ▸ codex-independent-reviewer
claude-builder ▸ claude-independent-reviewer
```

Cross-provider profiles are incomparable. A role may create a role on its own
profile or on the strictly narrower reviewer profile of the same provider.

### How a role survives the delegations its work is made of

Through an **event relation**, not a payload field:

```json
{"subject": {"kind": "delegation", "id": "delegation.…"},
 "predicate": "on_behalf_of",
 "object":  {"kind": "agent", "id": "agent.…"}}
```

`relation.predicate` and `typedRef.kind` are open patterns in
`common.v1.schema.json`, so this needs no schema change anywhere and an older
reader ignores it. Adding `agent_id` to the `delegation.requested` payload would
have required editing a schema with `additionalProperties: false`, which makes
every delegation event in every workspace unreadable to the previous binary —
including workspaces that never use roles. The relation costs nothing and says
the same thing.

A run may only be created for an active, non-template role whose `profile_id`
matches the delegation's `target_profile`, so the profile a role was granted is
the profile it actually runs under.

The binding is what puts a role in the staff index: the index is a projection
over `agent.*` events joined to the delegations and reviews carried out on the
role's behalf. Nothing about a role is stored operationally; a role's live state
(sessions currently running as it, open work) is derived at read time.

## Q2. How writes enter the UI without a second write path

The invariant is **one manager**, not **no HTTP writes**. `ARCHITECTURE.md` says
MCP and CLI are adapters over `CommonsManager`; the UI becomes a third adapter of
the same shape. Read-only stays the default.

- `agent-commons ui` — unchanged: manager built with `read_only=True`, only
  `GET` routes registered.
- `agent-commons ui --enable-writes` — manager built writable and bound to an
  explicit operator session, exactly like the CLI. `GET /api/meta` reports
  `writes_enabled: true` so the client cannot be wrong about which one it is.

Mutating routes, all of them thin adapters over an existing manager or service
method:

The mutating surface as it actually ships (the authority is
`ui/server.py:MUTATING_ROUTES`, pinned by test; catalogue editing sits behind its
own gate in `CATALOG_ROUTES`):

| Route | Manager entry point |
|---|---|
| `POST /api/agents` | `create_agent` |
| `POST /api/agents/proposals/{thread}/approve` | `approve_agent_proposal` |
| `POST /api/agents/{id}/reconfigure` | `reconfigure_agent` |
| `POST /api/agents/{id}/retire` | `retire_agent` |
| `POST /api/agents/{id}/messages` | `open_thread` / `reply_thread` |
| `POST /api/chat` | `open_engagement` |
| `POST /api/chat/{thread}/messages` | `reply_thread` |
| `POST /api/operations/{id}/answer` | `CommunicationRuntimeService.reply_to_input` |
| `POST /api/agent-links` | `open_agent_link` |
| `POST /api/agent-links/{link_id}/close` | `close_agent_link` |
| `POST /api/catalog/entries` (own gate) | `write_role_catalog` |
| `POST /api/catalog/entries/remove` (own gate) | `write_role_catalog` |
| `POST /api/delegations` (own gate) | `create_delegation` + `DelegationRuntimeService.run` |

The link routes surface `agent.link_opened`/`agent.link_closed` from the board:
dragging one of a role card's four ports onto another role opens the form, and a
close must carry the revision it closes. The domain remains the judge (enum,
self-link, deadline bounds, both roles active); the panel maps refusals and
never re-implements them. A link is presented as what it is today — a recorded
permission the runtime does not yet consume, not a communication channel.

The last route is the launch surface (MUST-4): it records a delegation on a
role's behalf and runs it through the same `DelegationRuntimeService` the CLI
broker uses -- one launch path, not a second -- behind `--enable-launch` plus a
`--profile-config`. It is a larger privilege than recording a role (it spawns a
billable subscription process), so it has its own gate and its own route
allowlist.

(The first edition of this table named `/api/operations/{id}/reply` and omitted
the chat, proposal, and catalogue routes; it is the test, not this prose, that is
authoritative — round 2, design.)

### What happens to the tests that prove the invariant today

`tests/ui/test_readonly_invariant.py` proves "no mutating route exists". That
assertion is kept for the default app and joined by three that carry the weight
in writable mode:

1. the writable app's mutating routes equal an **explicit allowlist**, so a route
   cannot be added without editing the test;
2. with `CommonsManager.record_event` monkeypatched to raise, **every** mutating
   route fails — no route reaches durable state by another path;
3. each mutating route is driven over HTTP and the expected canonical event is
   then found in the ledger — the same entry point a user has, per trap 1.

A fourth entry point needed its own test for a reason worth recording. All of
the above build `UIContext` directly, so the increment can lose the
`--enable-writes` flag from the CLI entirely and every one of them stays green —
which is exactly what happened while splitting these commits. `agent-commons ui`
is the seam a user crosses, so `tests/cli/test_ui_command.py` enters through the
command. Doing that immediately surfaced a second defect: `--enable-writes` with
no selected session failed with an opaque `TypeError` instead of refusing while
the operator was still at the terminal.

## Q3. The seven guarantees of autonomous role creation

**Status (2026-08-11): the `auto` level these guarantees protect is withheld;
see the banner at the top.** Each mechanism below is built, and each held under
two adversarial architecture reviews — but they guard the automatic path, which
is currently inert (`effective_grants` caps at `ask`). When `auto` is restored,
these are what make it safe. Until then, creation is human-confirmed and bounded
by guarantees 1, 2, 4, 5, 6, and 7; guarantee 3 (strict decrease) is specific to
`approval: automatic` and does not constrain the human-confirmed path, which a
human may deliberately confirm at a level equal to the creator's, bounded only by
the turnover budget.

Autonomous creation is designed with all seven mechanisms landing together. Each
is derived from the ledger or checked in `validate_transition`, which is on the
path of every adapter.

| # | Guarantee | Where it is enforced | Test at the real seam |
|---|---|---|---|
| 1 | Turnover ceiling counting creates **and** retires | `validate_transition`, counted over the human-rooted lineage against `turnover_budget` | CLI `agent create`/`agent retire` loop until refusal |
| 2 | Grants never widen | `validate_transition` on `agent.created`, componentwise `≤` against the creator's **effective** grants | CLI create with a wider grant is refused |
| 3 | Level strictly decreases | `validate_transition`: automatic creation requires `child.create_roles < creator.create_roles` | two automatic generations, third refused |
| 4 | Canonical event with a reason | `agent.created` requires `rationale`, `origin`, `approval`, `created_by_agent_id` | `agent show` returns the creating event's rationale and lineage |
| 5 | Cascade retire in one action | `retire_agent(cascade=True)` computes the closure, refuses as a whole if any member is blocked, then writes leaves-first | CLI retires a 3-deep lineage in one command |
| 6 | Visible provenance in the index | `origin` field, projected into `agent list` and the graph node | listing distinguishes human- from agent-created |
| 7 | Downgrade applies immediately | effective grants are **derived at read time** from the whole ancestor chain, never stored | reconfigure an ancestor, then the descendant's next call is refused |

### Effective grants are derived, never stored

```text
effective(agent, g) = min( stored(a, g) for a in {agent} ∪ ancestors(agent) )
deny(0) < ask(1) < auto(2);  a retired ancestor forces deny
```

This one definition delivers guarantees 2 and 7 at once. A stored copy would need
a propagation pass that could be skipped, and a skipped propagation is exactly
trap 2 — a limit that reads as enforced and cannot fire.

### The turnover budget lives on the role, not in configuration

`turnover_budget` is required on any role whose `create_roles` or `retire_roles`
grant is not `deny`, and it counts `agent.created` **plus** `agent.retired`
events across the lineage rooted at the nearest human-created ancestor. Counting
them separately is what a create/retire cycle exploits.

It is deliberately **not** a configuration key:

- `workspace.yaml` lives inside the delegated workspace, and a writable builder
  runs with `--sandbox workspace-write`. A ceiling an agent can edit is not a
  ceiling.
- `OperatorLimits` lives in the runtime config, which the worker MCP process does
  not load — the ceiling would not bind the path that actually needs it.

On the role, the budget is set by the human. A **correction** can never change it
(it is in `CORRECTION_IMMUTABLE_FIELDS`, checked on write and replay). A
**reconfiguration** can — `turnover_budget` is a mutable field, because a role
reconfigured to gain a create or retire grant needs a ceiling and a role that
dropped those grants can shed one. That mutation is a human operation
(`agent.reconfigured` is refused for a session acting as a role), it stays
monotone against the creator, and it narrows down the chain. The earlier claim
that the budget "is immutable afterwards" was wrong in both directions: it left
the correction path unguarded (round-1 C2) and left the operator without a way to
add the ceiling a widened grant requires (round-1 H1).

### `ask` keeps the proposer's provenance

Three levels, and two of them record the same origin:

- `auto` — the agent records the event itself: `origin: agent`,
  `approval: automatic`.
- `ask` — the agent cannot record. It opens a proposal thread with
  `commons_propose_agent`; a human confirms with `agent approve`, and the
  recorded event still says `origin: agent` with `created_by_agent_id` naming
  the proposer, plus `approval: human_confirmed`.
- `deny` — refused.

Recording a human-confirmed creation as `origin: human` would erase who asked
for it, which is trap 3 in a new place.

**A confirmation binds the proposal it confirms.** `agent.created` with
`approval: human_confirmed` requires a `proposal_ref`, and the lifecycle checks
that the thread is an open proposal, that the session which opened it was
running as the crediting role, and that name, profile, grants, context mode, and
rationale are unchanged. Without that binding `created_by_agent_id` is free text
a human types in: anyone could attribute a role to a proposer that never asked,
and "where did this role come from" would be unanswerable in exactly the case it
matters. Approving therefore means approving *that* proposal; changing the terms
means creating the role directly, under `origin: human`.

The first version of this ADR shipped `ask` without the proposal flow, which
made it worse than absent: the recording tool was registered at `ask`, every
call refused, and the role saw a capability it could never use. Tool
registration is now keyed on the grant *and its level* — `auto` gets the
recording tool, `ask` gets the proposing one.

The field is named `approval` rather than `authorization` because
`authorization` is a credential marker in `SecurityPolicy`, and every canonical
write is scanned before it is persisted. A payload key that the workspace's own
secret scanner rejects is not a field; the rename came from watching the first
`agent create` fail closed.

### The grant is what puts the tool in front of the agent

A permission an agent has no way to exercise is configuration nothing can act
on. Three worker MCP tools exist — `commons_create_agent`,
`commons_retire_agent`, `commons_open_agent_link` — and each is registered only
when the role that run acts for holds the matching grant above `deny`,
evaluated against its **effective** level. A run with no role, or a role at
`deny`, never receives the tool at all, and the tool never reaches the argv of
the launched provider.

That is least privilege in front of the domain check, not instead of it:
`validate_transition` still refuses on its own, so a stale tool in a long-running
session cannot outlive the grant that produced it.

## Q4. Role settings against fixed profiles

The requirement asks for permissions, autonomy level, required skills, MCP, and
model on a gear icon. Taken literally that is a checkbox UI over a tool set the
code fixes on purpose. The form that gives the operator what they want without
reopening the confused-deputy hole:

**Role settings may only narrow.** `tool_allowlist` and `mcp_allowlist` are
intersected with `_worker_tools(profile_id, purpose)` at invocation build time,
resolved from the role's current record at launch rather than carried in the
stored request — so a narrowing recorded a minute ago binds the next run. A role
can therefore be strictly weaker than its profile — "a reviewer that cannot grep
the repository" is expressible — and can never be stronger. Because narrowing is
monotone, the same machinery satisfies guarantee 2.

Terminal outcome tools are exempt from narrowing. A role that cannot report a
result would burn its budget and exit without ever closing its delegation, which
is a broken role rather than a narrower one. Selecting a tool the profile never
had fails closed before launch instead of being silently dropped.

**Choices come from an operator-owned catalog**, loaded with the same discipline
as `runtime.yaml`: outside the delegated workspace, no-follow open, regular file,
not group/world writable, owned by the operator or root, size-bounded. It
declares `skills` and `tools`. A role referencing an id absent from the catalog
fails closed — at launch, refusing the run rather than quietly executing a role
without the skill it was configured to require.

A **skill** carries operator-authored instruction text that is appended to the
run's bounded instruction. This is the one thing a role setting adds rather than
narrows, and it is safe precisely because the text comes from the operator file:
a role can change what a run is *told to do*, never what it is *allowed to do*.

**Model is chosen by choosing a profile.** A role that could name a model would
be editing argv. The gear panel lists the allowlisted profiles with the model
each one pins, which is the same choice expressed where it is enforceable.

**MCP selection was removed rather than shipped inert.** The first cut gave a
role an `mcp_allowlist`, which nothing read: a worker receives exactly one MCP
server, so narrowing a set of one means nothing, and widening it is the deferred
change. A stored field with no reader is the failure this project keeps writing
rules about, so the field is gone and the panel instead shows, read-only, which
servers the selected profile actually carries. It returns when a profile can
carry a second one, which is a separate and much larger change.

### The conflict this creates with "the human writes the catalogue from the UI"

Р3 asks that catalogue entries be written by a human from the UI. That is
delivered — as a form with plain-language fields, not a YAML box, with the
backend assembling and publishing the file atomically — but behind **its own
gate**, `--enable-catalog-editing`, separate from `--enable-writes`:

- `--enable-writes` lets the panel record roles, messages, and retirements. Every
  one of those is bounded by the invariants in this document.
- `--enable-catalog-editing` lets the panel change what delegated runs are *told
  to do*. That is a different magnitude of privilege, and one checkbox for both
  would hide it. It additionally requires `--role-catalog` naming the file, so
  turning it on cannot silently edit nothing.

Profiles stay out of the UI entirely at any gate: they name executables, and no
loopback surface should be able to change what process starts.

Removing a catalogue entry an active role requires is refused and names the
roles. Otherwise the click lands here and the failure lands at somebody's next
launch.

A **preset is a role with `template: true`**: it never runs, is never delegated
to, never authors anything, and creating from it copies its settings. That
avoids a fourth entity kind for something that is a role in every respect except
being employed.

## Q5. History storage and indexing, by the numbers

Measured on this repository — roughly four weeks of heavy multi-agent work:

| Quantity | Measured |
|---|---|
| canonical events | 651 |
| canonical event bytes | 1 198 362 (mean 1 840 B) |
| manifests | 356 KiB |
| events per delegation run | 3–6 canonical, plus 2 for a review |
| coordination operations per run | 5–20, each ≤ 4 KiB bounded metadata |

Extrapolating a year of the same intensity: ~8 500 events, ~16 MiB canonical,
plus a similar order of bounded coordination records. A decade of it stays under
200 MiB.

| Option | Cost at this size | Verdict |
|---|---|---|
| Ledger + existing SQLite projection | already built and rebuildable; exact lookup by id/type/time is already indexed | **baseline, keep** |
| + FTS5 over a derived text column | one virtual table, ~30 % index growth (≈ 5 MiB/year), zero new processes or dependencies | **chosen** |
| Embedded vectors | new model dependency and an embedding step per event, to rank ~10⁴ rows a `LIKE` already scans in milliseconds | rejected — cost with no reachable benefit |
| Redpanda + Qdrant | two servers, two retention policies, two failure modes, for a single-user local tool with ~10⁴ records | rejected — three orders of magnitude of headroom bought at the cost of an operable system |

ADR 0008 is the binding precedent, and its rules are adopted here rather than
restated: the index is a projection, is never authoritative, nothing canonical
may depend on it, and **producer and consumer land in the same change**. It
shipped with `agent-commons search` and the panel's search box in the same
commit.

What is indexed is a **positive allowlist** (`index/search_text.py`): event
type, titles, descriptions, summaries, rationales, verdicts, criteria, role
names and grants, and the actor's declared role. A denylist would have been
wrong twice — a payload field added later would be indexed by accident, and the
material worth keeping out is exactly what somebody adds without thinking about
the index. Prompts, transcripts, tool arguments, and provider output are not in
the ledger to begin with and so cannot reach it.

Two properties the implementation had to carry beyond the storage choice:

- **A read-only caller never builds one.** Opening the index creates
  directories, a file, tables, and a WAL. Read-only search reads a projection
  that already exists or reports that it cannot answer — this project has
  already shipped a read-only command that created state.
- **A widened query says it widened.** Operators type questions, not FTS5
  grammar, so a query matching nothing under all-terms semantics is retried
  as any-term. Doing that silently would make a loose match read as a precise
  one, which is the `succeeded`-looks-like-`accepted` defect in a new place.

The projection moves to schema v2. Because it is disposable, an older version is
dropped and rebuilt rather than migrated; a newer one still fails closed.

## Q6. Review independence once roles exist

Independence is checked today by comparing `session_id` values
(`_subject_author_sessions`). A persistent role authoring work in one session and
reviewing it in another passes that check while being the same judgment. This
hole has been closed three times in this branch — for tasks, then for artifacts,
then by subject class — and each fix covered exactly the case reported.

It is closed here **by class**: the unit of independence becomes a **principal
set**, not a session set.

```text
principals(event) = {session:<sid>}  ∪  {agent:<aid>}  when that session ran on
                                        behalf of role <aid>
```

Every existing check — `review.completed` against subject authors,
`task.accepted` against `work_author_session_ids` — is rewritten once, in terms
of principals, through a single helper. A future principal kind (a human
operator, a team) is covered by extending that one function, without revisiting
any call site. That is the difference between this fix and the previous three.

Note what this does **not** forbid: the same role reviewing the same subject
again at a later revision. Re-review is the point of re-review. Only *authoring
then judging* is refused.

## Q7-adjacent: fresh context as a mechanism

`context_mode: fresh` is not a sentence in a prompt. It is:

- a distinct child session, no persistence, no resume, reduced tools — all of
  which the delegation runtime already provides; and
- **no prior-position framing.** The role's own earlier verdicts on the same
  subject are never assembled into its instruction as "your previous position".

"Role memory" is defined as *receiving its own prior judgment on the same
subject*, not as *knowing the past*. The second is unverifiable and harmful to
forbid: reading the ledger is a reviewer's job, and the same records are open to
every agent. The first is checkable, because the ledger knows which verdicts a
role recorded against a target.

A fresh-context role therefore starts empty and reads the ledger itself. Its own
past verdicts on the subject are **not hidden or filtered** — they come back as
ordinary records under their own author. Hiding them would give the reviewer an
incomplete picture it believes is complete, destroy the "this was flagged and not
fixed" signal that re-review exists for, and be discovered anyway through another
query. What anchors judgment is the framing of ownership, so the framing changes
and the result set does not. Attribution is never stripped: the ledger is
immutable.

Monotonicity: strengthening isolation is an ordinary reconfigure. Weakening it —
`fresh` → `accumulated` — requires the acting session to declare the
`agent:isolation_downgrade` capability and to record a reason, in the same shape
and with the same honest limits as `delegation:recover` and `receipt:abandon`.
It is a local coordination gate, not authentication.

Visibility at the consumer: a review carries the context mode of the role that
produced it, plus the count of that role's earlier verdicts on the same subject.
A review from an accumulated-context role reads differently from a clean-slate
one, instead of both rendering as equally independent — the same class of defect
as a green tick on `succeeded`.

## Q-links: temporary links between roles

A link is a **typed grant**, never an open/closed flag:

```json
{"link_id": "agent_link.…", "from_agent_id": "…", "to_agent_id": "…",
 "allowed_action": "ask", "deadline": "…", "reason": "…"}
```

`allowed_action` is an enum, and `handoff_work` was added to it exactly as
predicted: the enum and its validation grew, the record did not change shape.
It also turned out to be the thing that made the model necessary rather than
decorative — staffing a run with a role is an authority, so a link that widens
who may do it needs a typed action rather than an open/closed flag.

A link has no expiry, and `deadline_seconds` is optional. Replay has no clock,
and using one would make the same events project differently over time; this is
the same reason mutable session liveness is excluded from replay. So nothing can
retire a permission on a schedule: a link is ended by an explicit
`agent.link_closed`. The field remains readable — history carries it, and an
operator may record an intended horizon — but requiring it only made four
callers invent a number no reader consumed, and the bounds that actually hold a
run are attempts, provider units, depth and its wall-time guard, none of which
are calendar time. An earlier edition of this ADR promised expiry would be
"surfaced where a clock exists"; nothing ever surfaced it, so the promise is
withdrawn rather than restated. The exchange itself runs over the existing bounded
communication channel; only the grant is canonical. Its consumer exists: an ask
with no live session on the other side surfaces in the human panel with the
blocked outline, so it is answered rather than dropped.

## Removal is retirement, and the state invariant is not lineage

Three separate rules, in decreasing order of how often people get them wrong:

1. **Never delete.** The ledger is immutable; the operation is `retire`
   everywhere, including the UI label.
2. **The real invariant is state, not parentage.** "Only roles you created" is a
   sound default and an insufficient guard. A role with a non-terminal delegation
   or an open review it owes cannot be retired **by anyone**, including the human
   who created it and including a cascade.
3. **Prefer a declared lifetime to a standing right.** `lifetime:
   {kind: task_scoped, task_id}` retires the role when its task is accepted or
   cancelled. This is **derived** in the projection, exactly like stale
   acceptance: there is no event to forget to write and no path that can skip it.
   `retired_by: lifetime` records therefore have no `agent.retired` event, and
   that is deliberate.

Because ephemeral lifetimes cover the "created for a task, done, gone" case,
`retire_roles` defaults to `deny` in every creation path and exists only for
roles whose task was re-scoped. An agent never retires a human-created role at
any level.

## Compatibility and rollback

Additive to every existing schema. `typedRef.kind` and `relation.predicate` are
open patterns, so the delegation↔role binding needs no schema edit and older
binaries ignore it.

The new event family is the one-way part. A binary that predates
`commons.payload.agent.v1` records a `domain_validation_rejected` projection
issue for each `agent.*` event, and because integrity gates fail closed on issue
severity, it will refuse ordinary writes. Rollback therefore means reverting to a
checkout taken before any role was created; there is no downgrade path for a
workspace that has employed one. This is stated rather than mitigated: a new
canonical entity has no cheaper rollback, and a silent partial read would be
worse than a refusal.

Stored document schemas are untouched, so the v4 request/attempt documents and
their rollback contract from ADR 0007 are unaffected.

## Consequences

- The canvas gains something real to draw: a staff index with standing structure,
  distinct from the causal graph of runs.
- Independence stops depending on session identity, which is the third time this
  property has needed repair and the first time the repair is stated over a
  domain rather than a case.
- Autonomous role creation is **staged, not live**: the `auto` level is withheld
  and every path is human-confirmed for now. The rule the first edition wrote and
  then broke — "if any of the seven mechanisms need a large rewrite, ship the
  level as `ask` only, because an inert brake is worse than an absent one" — is
  the rule now being followed literally. The brakes are built and proven; the
  level returns when it has run longer behind them.
- Role settings are honestly narrower than the PRD's checkbox screen. The gear
  panel can promise only what the profile boundary can enforce, and says so.
- One more reason a launch can be refused, and one more reason a retire can be
  refused. Both name the guard they tripped so the orchestrator can render it on
  the exact node.
