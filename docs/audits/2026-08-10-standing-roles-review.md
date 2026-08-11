# Standing-roles review: findings and remediation plan

Status: **remediation complete; two review rounds run.** Round 1 (below) found
the branch's headline claim false. All nine remediation steps landed
(`f30604c`..`2c4c91b`), each reproduced before and after and tested through the
real seam. A second, independent six-reviewer round then ran against the fixed
branch; its results and the further fixes they prompted (`5cb081d`, `503c63d`)
are recorded under **Round 2** at the end of this document. The suite is green
throughout (638 passed, 12 skipped — the skips are the withheld-`auto` cases),
ruff clean.

Date: 2026-08-10 (round 1), 2026-08-11 (round 2)

Reviewed revision: `91c51fb` (branch `agent/visual-orchestrator-foundation`,
eleven commits on top of `52dc577`). Test suite at review time: 629 passed,
ruff clean.

## How this review was produced

Six independent reviewers, two model families across three lenses, each given
only the artifacts (ADR, invariant docs, the diff, the code) and its lens —
never the author's conclusions, so that agreement between two reviewers means
something rather than echoing one framing. Architecture reviewers were told to
be adversarial and to prove findings by running code.

| Lens | Reviewers |
|---|---|
| Product | two, independent |
| Design / UX | two, independent |
| Architecture / correctness | two, independent, adversarial |

Findings below are marked **[verified]** where the author reproduced them
directly, **[converged]** where both reviewers of a lens found them
independently, and **[reported]** where a single reviewer proved or argued them
and the author has not re-run the proof.

## The headline

**The branch's central claim does not hold.** ADR 0009 Q3 states that
autonomous role creation ships with seven mechanical guarantees, "each derived
from the ledger or checked in `validate_transition`, which is on the path of
every adapter." At least four independent paths defeat them, and the normal
successful run is one of them.

The 629-test suite is green over every finding below. The tests exercise what
was built; they do not exercise what was claimed. That gap is the review's most
important result.

## Critical

### C1. A worker stops being its role the moment it reports success — [verified]

`acting_agent_id` (`src/agent_commons/domain/lifecycle.py:460`) resolves the
acting role through `_bound_delegations`, which returns only `active` and
`input_needed` delegations. But the lifecycle *requires* the child session to
record its own `delegation.succeeded` (`lifecycle.py:207`), and the child
process keeps running until the parent reaps it
(`services/delegation_runtime.py:1132`). Every successful run therefore ends in
a live session the domain treats as an unbound human window.

Reproduced:

```
while bound:            acting -> agent.12TC…   create refused: may not create roles
after own succeeded:    acting -> None
                        created role: origin=human, approval=human, created_by=None,
                        grants={create_roles:auto, retire_roles:auto, open_links:auto}
```

Defeats guarantees 2, 3, 4 and 6 in one step, through the happy path, and the
ledger records the result as human-created. Note the asymmetry that makes this
a bug rather than a design choice: `session_agent_map`
(`domain/agents.py:149`) deliberately *includes* terminal delegations so that
independence survives a finished run, while `acting_agent_id` deliberately
excludes them. The same session is "was role R" for independence and "is
nobody" for authority.

### C2. `event.corrected` bypasses every role guard — [reported, converged]

`CORRECTION_IMMUTABLE_FIELDS` (`domain/revisions.py:10`) omits `grants`,
`turnover_budget`, `context_mode`, `origin`, `approval`,
`created_by_agent_id`, `lifetime`, `profile_id`, `template`, `allowed_action`.
`_enforce_maintenance_policy` (`services/manager.py:822`) validates the
replacement against the schema and the structural set and never calls
`validate_transition`. On replay, `_validate_agent_creation`
(`lifecycle.py:552`) returns immediately for `origin: human`, so nothing
re-checks the widened payload either.

One reviewer ran a worker correcting its own role's `agent.created` to all
grants `auto` and `turnover_budget: 1024`, with a clean projection and no
lifecycle refusal. The same path rewrites `context_mode` `fresh → accumulated`
without the `agent:isolation_downgrade` capability that
`_validate_agent_reconfiguration` (`lifecycle.py:649`) exists to demand.

ADR 0009's claim that the budget "is immutable afterwards (a correction cannot
change structural fields)" is false.

## High

### H1. `reconfigure` re-checks neither the turnover budget nor strict decrease — [verified]

Creation demands an integer `turnover_budget` for any grant above `deny`
(`domain/validation.py:419`). Reconfiguration demands nothing
(`validation.py:433`, `lifecycle.py:638`). Reproduced:

```
create with auto and no budget  -> refused (correct)
reconfigure to auto, no budget  -> ACCEPTED; effective all auto, budget None
add a budget afterwards         -> refused: turnover_budget is immutable
turnover_blockers               -> []   (no ceiling anywhere in the lineage)
```

`turnover_blockers` (`domain/agents.py:134`) skips any ancestor whose budget is
not an int, so a null-budget root removes the ceiling for its whole subtree,
and the correct operator remedy is unreachable because `turnover_budget` is not
in `_AGENT_MUTABLE_FIELDS`. The gear panel offers `auto` in a dropdown with no
budget field, so this is the natural gesture, not an exotic path.

Strict decrease has the same shape: enforced at creation
(`lifecycle.py:590`), not preserved afterwards — one reconfigure per generation
makes the chain unbounded. [reported]

### H2. Review independence: two holes — [reported]

- The requester/completer check still compares raw session ids
  (`lifecycle.py:165`), three lines above the principals helper it was supposed
  to be rewritten through. One standing role requested a review in run 1 and
  approved it in run 2.
- `_subject_author_sessions` (`lifecycle.py:749`) falls back to
  `record["actor"]`, and `_apply` (`domain/projection.py:189`) overwrites
  `actor` on every event for that entity. So for every kind except task and
  artifact the author set is the *last* actor: one unrelated event by anyone
  else makes the original author independent of its own subject. This is the
  fourth instance of the failure the ADR claims to have closed "by class", and
  it pre-dates the branch — what is new is the claim that it is closed.

### H3. Retirement is not terminal — [reported]

`_apply_derived_agent_retirement` (`domain/projection.py:223`, called at `:827`)
is a post-pass over final task state, not a transition. `task.reopened`
restores a lifetime-retired role to `active` with its grants, and with them the
whole subtree's authority. Contradicts the `agent:` diagram in
`ARCHITECTURE.md`, the ADR's "there is no transition out of `retired`", and
`effective_grants`' documented collapse-on-retired-ancestor property.

Also the one write/replay asymmetry found: the pass runs after the replay loop,
so during replay a lifetime-expired role is `active` for every
`validate_transition` call while the write path saw it as `retired`. Replay
stays deterministic; it is not re-applying the same rules.

### H4. The UI's "waiting on you" has two sources that disagree — [converged, design]

The amber ring and the footer count derive from canonical state
(`ui/graph.py:211`: `delegation.input_needed` plus open decision threads). The
Blocked tab derives from the operational communication store
(`ui/context.py:348`, `CommunicationRuntimeService.inbox()`). Both reviewers
reproduced a state with three or four glowing nodes, a footer saying "waiting
on you: N", and `/api/operations` returning `[]` — so the tab hides itself
(`index.html:737`) and the product offers no list, no answer box, no
explanation.

This happens whenever a canonical event exists without a live operation (CLI
writer, crashed worker, replay) and *always* in read-only mode. The docstring
at `ui/context.py:353` says "a blocker you cannot see is worse than one you
cannot yet act on"; this shipped exactly that.

## Medium

### M1. The panel never refreshes — [converged, design]

`handleFrame` (`index.html:1169`) repaints only the graph. Chat, blockers,
proposals and catalogue load once in `boot()`; there is no `setInterval`
anywhere. Consequences both reviewers reproduced: role replies never appear
without a full reload, and the main chat's Send posts a boot-time revision, so
after any concurrent activity it fails with a raw
`stale expected revision evt.…` and no refresh-and-retry.

### M2. Roles are anonymous on the canvas — [converged, design]

`_label` (`ui/graph.py:78`) checks `title/subject/summary/proposal/path/role_id`
and never `name`, the one display field `agent.created` guarantees. Every role
renders as its ULID, twice per card (`index.html:471`, `:475`), and in the
inspector heading. The branch's stated payoff — "the canvas gains something
real to draw: a staff index" — is defeated by a one-line miss.

### M3. The gear shows stored grants and can never warn — [reported, design]

`UIContext.entity()` (`ui/context.py:511`) returns the raw projection record,
not `_agent_view`, so `effective_grants`, `retirement_blockers` and
`live_delegations` never reach the panel. The settings form falls back to stored
grants, making guarantee 7 invisible in the one screen where a human edits
grants; the "cannot be retired yet" warning (`index.html:598`) can never fire,
so retire is confirm-then-fail.

Worse, the form then posts what it displayed: prefilled from
`effective_grants` (`index.html:570`) and saved as `changes.grants`
(`index.html:678`), it converts a derived value into a stored one.

### M4. `hidden` does not hide — [converged, design]

`.field{display:block}` (`index.html:91`) outranks the UA's `[hidden]` rule and
the file defines no `[hidden]` override. Three fields are affected: the main
chat's subject box stays visible mid-thread and anything typed there is
silently discarded; the skills-only instruction textarea shows for tool
entries; the "weakening isolation — recorded reason" input is always visible
and its value is dropped.

### M5. The catalogue is a second durable write path, and the test cited as proof does not cover it — [reported]

`save_catalog_entry` / `remove_catalog_entry` (`ui/context.py:304`) call
`write_role_catalog` directly — a durable file deciding what every subsequent
run is *told to do* — with no `CommonsManager` involvement. They live in
`CATALOG_ROUTES`, not `MUTATING_ROUTES`, so
`test_every_mutating_route_dies_without_the_manager_write_path` never sees
them; it also drives only four of the eight enumerated mutating routes. ADR
Q2's "with `record_event` monkeypatched to raise, **every** mutating route
fails" is not what that test asserts.

### M6. Cascade retire is not atomic and writes ancestors first — [converged, architecture]

`retire_agent` (`services/manager.py:1793`) computes blockers from one snapshot
then issues N separate `record_event` calls, each taking the write lock
independently. A concurrent writer that gives the root live work mid-cascade
leaves descendants retired and the root active — the orphaned authority the
method exists to prevent. Separately `descendants` returns
`tuple(sorted(found))` (`domain/agents.py:114`) — ULID order, i.e.
chronological, i.e. **ancestors first**, the opposite of the documented
leaves-first.

### M7. `prior_verdicts` is dead code with a type bug — [verified, converged]

`domain/agents.py:235` has zero callers in `src/` or `tests/`. Its signature is
`Mapping[str, str]` while `session_agent_map` returns
`Mapping[str, frozenset[str]]`, so `bindings.get(sid) == agent_id` compares a
frozenset to a string and is always false — wiring it as written would report
"no prior verdicts" for everyone. Requirement Р7.3 (visibility at the consumer)
is therefore unmet: no review surface carries the producing role's context mode
or its prior-verdict count.

### M8. The launch loop is not closed — [converged, product]

- `POST /api/agents` has no caller in the shipped client: the panel can
  reconfigure, retire and message a role but cannot hire one.
- The worker instruction (`services/delegation_runtime.py:698`) never mentions
  `commons_list_my_threads` or `commons_reply_thread`, and neither does
  `ONBOARDING.md` or any skill. The main chat is therefore one-way *by
  construction*, not merely unfinished.
- `broker preflight claude-builder` fails with `provider_start_failed` on a
  machine where the executable runs fine; the real cause is a swallowed
  `ConfigurationError` about `trusted_workspace`
  (`runtime/preflight.py:195`, `runtime/model.py:591`). `trusted_workspace`
  appears once in the whole docs tree, in an aside.
- The catalogue the UI writes (`--role-catalog`) is not the catalogue the
  launcher reads (`catalog:` inside the runtime profile config). `broker run`
  has no `--role-catalog`, and no user-facing document mentions the `catalog:`
  key — so a skill added through the form refuses the next launch.
- The plan's own MVP definition of done (§10, MUST-4 task launch from the UI,
  MUST-5 the streaming seam) is undelivered and §15 does not list it as
  deferred.

## Low

- **L1.** Read-only search creates `index.sqlite3-wal` / `-shm`.
  `search_existing_projection` (`index/sqlite.py:690`) opens `mode=ro`, which
  still materialises WAL sidecars; its docstring says "creating and changing
  nothing". The author's test asserted the main file's mtime and missed them.
  [reported]
- **L2.** An approved proposal thread stays open and can be approved
  repeatedly, creating a distinct role and spending budget each time
  (`services/manager.py:1712`). [reported]
- **L3.** `agent_link.deadline_seconds` has no reader anywhere. Since
  `handoff_work` is the only lever that widens who may staff a role, that
  widening is permanent until someone runs `agent unlink`. [reported]
- **L4.** The rollback statement is incomplete: `thread_type` gained
  `engagement` in a closed enum, so a workspace that used the main chat is
  unreadable to the previous binary even if it never created a role. ADR
  "Compatibility and rollback" and the threat model name only the agent event
  family. [converged, architecture]
- **L5.** Catalogue editing is an unlocked read-modify-write; two concurrent
  edits silently drop one, and `_catalog_users` is a TOCTOU against a role
  created between check and write. [converged, architecture]
- **L6.** CSP drops the global search box's styling: `style-src 'nonce-…'`
  with no `style-src-attr` (`ui/security.py:56`) against a markup `style`
  attribute (`index.html:115`). It is the only element in the file styled
  outside the nonce'd block. [verified]
- **L7.** The catalogue fails silently: the startup banner claims
  "catalog editable at …" after checking only that the flag was supplied
  (`cli.py:366`), and a catalogue that fails to load is indistinguishable from
  an empty one — the tab disappears and the settings pickers go blank.
  [reported]
- **L8.** `<title>Agent Commons — read-only</title>` (`index.html:6`) is
  hardcoded and stays wrong with writes enabled. [verified]
- **L9.** Read-only mode renders a live composer that fails with a raw HTTP
  status. [converged, design]
- **L10.** Search results offer "Show <kind>" which looks the subject up in
  graph nodes; threads are never graph nodes, so those results dead-end — and
  the failure text overwrites the match count and the "not synchronized"
  caveat. [converged, design]
- **L11.** Band captions assert reporting depth for nodes that were never
  ranked, so a directly requested review is captioned "delegated · depth 4".
  [reported]
- **L12.** `_SHED_ORDER` sheds delegations first, including `input_needed`
  ones, and `awaiting_human` is computed after shedding — so past the node cap
  both the rings and the count shrink, and the truncation notice does not say
  blocked work may be hidden. [reported]
- **L13.** The tab strip neither wraps nor scrolls; with all tabs visible the
  last ones sit off-screen at 1280px. `main{height:calc(100% - 84px)}`
  hardcodes a chrome height the header exceeds, pushing the footer — the only
  place reporting issues and "waiting on you" — below the fold. [converged]
- **L14.** The graph is unreachable by keyboard: nodes are SVG groups with
  click listeners, no `tabindex`, no role, and the canvas is one
  `role="img"`. Since Message and Settings appear only after selecting a node,
  configuring or messaging a role is mouse-only. [converged]
- **L15.** `_PROPOSAL_BOUND_FIELDS` (`lifecycle.py:503`) omits
  `turnover_budget` and `lifetime`, so a confirmation can change both while
  still crediting the proposer. [reported]
- **L16.** `search_existing_projection` is annotated
  `-> list[dict] | None` but returns `tuple[list, str] | None`
  (`index/sqlite.py:690`). No type checker runs in this project. [reported]

## What holds

Worth recording, because the review was adversarial and these survived it:

- **Replay determinism.** Both architecture reviewers checked every new
  check independently: none reads a clock, session liveness, or operational
  state; link deadlines are correctly excluded; the run/role binding is
  re-derived from the immutable envelope. Replay is deterministic across time
  and processes.
- **No second *canonical* write path.** Every canonical write goes through one
  `CommonsManager`. (The catalogue is a second *durable* path — M5 — but not a
  canonical one.)
- **The principal abstraction is right.** Neither architecture reviewer could
  defeat it structurally; the two holes in H2 are wrong *sets*, not a wrong
  idea.
- **Truth-layer discipline in the UI.** The tick is reserved for
  accepted/approved, `succeeded` renders as "not accepted", stale acceptance
  changes glyph, label and border, search names its layer, its widening and its
  sync state, and the absent objective→task link is reported as absent rather
  than drawn.
- **Refusal quality.** Every guard that does fire names the rule and the entity
  it refused.

## The pattern

One root cause, in three places:

**A mechanism was built, claimed in a document, and the second path was never
re-checked.** In the domain: corrections and reconfiguration do not re-run what
creation checks. In the UI: a route with no caller, two sources for one signal,
`entity()` bypassing `_agent_view`. In the documents: assertions about a panel,
a test and an ordering that do not exist.

These are traps 2, 3 and 5 from the original brief — inert configuration, a lie
in the record, a mechanism with no caller — committed while quoting them.

## Remediation plan

Not started. Ordered so that each step leaves a working state, and so that the
single fix with the widest blast radius lands first.

1. **Withhold the `auto` grant level.** The brief's own rule applies: do not
   ship the automatic level partially, because an inert brake is worse than an
   absent one. Reduce to `ask` until 2–4 land.
2. **C1** — decide what "acting as a role" means after terminalization, and
   make `acting_agent_id` and `session_agent_map` agree. One cause behind
   roughly half the defeated guarantees.
3. **C2, H1** — re-run creation validation on corrected `agent.created`
   payloads, or add the policy fields to `CORRECTION_IMMUTABLE_FIELDS`; make
   reconfiguration re-check the budget requirement and preserve strict
   decrease; decide whether `turnover_budget` must become mutable so the
   correct operator action exists at all.
4. **H2** — convert the requester/completer check to principals, and fix the
   author set so it accumulates rather than tracking the last actor. Close it
   over the class this time, not the reported kinds.
5. **H3, M6, L1** — make lifetime retirement a transition rather than a
   post-pass, or make it survive `task.reopened`; hold one critical section
   across a cascade and order it leaves-first; open the read-only projection
   with `immutable=1`.
6. **M7** — either wire `prior_verdicts` into the review surface, which is what
   Р7.3 asked for, or delete it. Not both.
7. **UI** — one source for "waiting on you", refreshed by the same stream as
   the graph; `entity()` through `_agent_view`; `name` in `_label`; the
   `[hidden]` rule; the CSP style attribute; disable the composer in read-only.
   Both design reviewers independently named the first of these as the single
   highest-value change, and both proposed the same shape: merge Blocked and
   Proposals into one always-visible attention queue.
8. **M8** — close the launch loop: a caller for `POST /api/agents`, the chat
   tools named in the worker instruction and the onboarding contract, one
   honest `trusted_workspace` example, a preflight diagnostic that names the
   real refusal, and one catalogue path shared by the panel and the launcher.
9. **Rewrite ADR 0009, `THREAT_MODEL.md` and the plan** against what exists,
   with an explicit note that the previous revision claimed more. The ADR
   currently describes a system that was not built.

Steps 7 and 8 change the product's shape rather than repairing it, and should
be confirmed with the operator before they start.

## Seventh pass: OpenCodeReview (`ocr` v1.9.0), delegation mode

Run after the six reviewers, on the same range (`52dc577..HEAD`), using
`alibaba/open-code-review` in delegation mode — the tool selects files and
supplies the rule set, the host agent performs the review. Delegation mode was
chosen deliberately: the tool's normal mode sends changed files to a configured
LLM endpoint, and nothing in this repository should leave the machine to be
reviewed. The commands are installed manually under `.claude/commands/`; the
marketplace install (`/plugin marketplace add …`) is an interactive flow this
session cannot run.

The tool selected 38 of 47 changed files (documentation excluded as
unsupported extensions) and returned one Python rule group covering typos, dead
code, mutable default arguments and shared state, boundary handling, error
handling, identity comparison, resource management, performance, and
concurrency — explicitly biased toward precision over recall.

**Result: two new findings, both minor-to-medium, and a clean bill on
everything mechanical.**

### O1. `pending_operations` swallows a real failure into "nothing needs you" — medium

`src/agent_commons/ui/context.py:363-367` catches `CommonsError` and `OSError`
around `CommunicationRuntimeService(manager).inbox()` and substitutes an empty
tuple. A corrupt or unreadable communication store is therefore
indistinguishable from "no blockers", with nothing logged. Against the rule
"exceptions caught and silently discarded without logging or re-raising", and
it directly compounds **H4**: the surface that already disagrees with the
canonical ring also hides the reason it is empty.

### O2. A type-narrowing `assert` guards the catalogue path — low

`ui/context.py:2958` (`_require_catalog_editing`) uses
`assert self._catalog_path is not None` after an explicit `ConfigurationError`
guard. Under `python -O` the assertion is stripped, `None` reaches
`load_role_catalog` (which returns an empty catalogue) and `write_role_catalog`
(which raises `TypeError` on `Path(None)`), turning a clean refusal into a
confusing failure. Against the rule "assert used for runtime validation —
assertions are stripped under `python -O`".

### What the pass cleared

Checked against added lines only, as the workflow prescribes: no mutable
default arguments, no bare or blanket `except`, no `except: pass`, no
`== True/False` or `is` against literals, no unclosed sqlite connections
(`search_existing_projection` closes in `finally`), and every `raise` inside an
`except` chains with `from exc`. One initial hit at `catalog.py:136` was a
false positive of the author's own grep — the `from exc` sits on the line after
the multi-line call.

### What this pass could not see, and why that matters

The tool is a diff-level defect finder tuned for precision. Every critical and
high finding above it — a worker losing its role on the happy path, corrections
bypassing governance, reconfiguration skipping a ceiling, two sources for one
signal — is invisible to it, because each is a **claim not matching behaviour**
rather than a defect visible in a hunk. Nothing in a diff says "the ADR asserts
this is checked."

That is the useful negative result: the branch's Python hygiene is sound, and
its problems live in a layer no line-level reviewer reaches. The tool's own
workflow ends with "automatically fix High and Medium issues"; that step was
not taken here, because a remediation plan for the larger findings is open and
unapproved, and fixing O1/O2 in isolation would touch code that plan is about
to restructure.

---

# Remediation (round 1 → fixes)

Nine steps, each a working state proven in isolation, each entering through the
seam a user crosses (CLI command, MCP tool, HTTP route) rather than a helper.

| Step | Finding | Fix | Commit |
|---|---|---|---|
| 1 | `auto` shipped partially over broken guarantees | Withhold `auto`: `effective_grants` caps every level at `ask` while `AUTOMATIC_LEVEL_WITHHELD` is set. A stored `auto` stays valid and behaves as `ask` — tool registration, launched argv, and every lifecycle check follow from the effective level. | `f30604c` |
| 2 (C1) | A worker stopped being its role the moment it reported success | `acting_agent_id` now reads every delegation whose child the session is (terminal included), preferring a live binding, ties by most-recent ULID. The binding lasts the session, matching `session_agent_map`. | `03e7163` |
| 3 (C2, H1) | Corrections and reconfiguration did not re-run what creation checks | A role's authority/identity/isolation/lineage fields join `CORRECTION_IMMUTABLE_FIELDS` (checked on write and replay through one helper); reconfiguration re-checks the budget requirement, keeps it monotone against the creator, and preserves strict decrease for automatically-created roles; `turnover_budget` becomes mutable so the operator remedy exists. | `689f2c1` |
| 4 (H2) | Independence: raw-session requester check; last-actor authorship | Requester/completer compared as principals; the projection accumulates every author session into `author_session_ids`, read for all kinds — closed by class, not by the two reported kinds. | `e7b24a9` |
| 5 (H3, M6, L1) | Lifetime retirement not terminal; cascade not atomic/ordered; read-only made WAL sidecars | Retirement applied inline at the closing event and never undone (survives `task.reopened`, write==replay); the whole cascade runs under one reentrant write lock, leaves-first; the read-only projection opens `immutable=1`. | `20b864d` |
| 6 (M7) | `prior_verdicts` dead with a type bug | Fixed (set membership, not `==` against a frozenset) and wired: the projection records the producing role's context mode and prior-verdict count on every completed review. | `2365327` |
| 7 (H4, M1–M4, O1, O2, L6, L8, L9) | Panel disagreed with itself and never refreshed | One canonical attention queue (merging Blocked and Proposals, operator-approved); the panel's data follows the graph stream; `entity()` through `_agent_view`; `name` in `_label`; a real `[hidden]` rule; the CSP style attribute removed; the composer disabled in read-only. | `76bc940` |
| 8 (M8) | Launch loop open | A Hire form calling `POST /api/agents`; the chat tools named in the worker instruction and `ONBOARDING.md`; `broker run --role-catalog` naming the panel's file; a `TRUSTED_WORKSPACE_REQUIRED` preflight diagnostic carrying the real refusal. | `2c4c91b` |
| 9 | The docs claimed more than the code did | This document, the ADR, the threat model, the plan §15, and the CHANGELOG rewritten against what exists. | (docs) |

The seventh (OpenCodeReview) pass was re-run in delegation mode over
`52dc577..HEAD` after the fixes: clean on the added lines — no bare excepts,
mutable defaults, identity-vs-literal comparisons, f-string logging, or runtime
`assert`; every wrapping `raise` chains with `from`. The mechanical layer stayed
sound, as in round 1.

---

# Round 2: six independent reviewers, on the fixed branch

Same conditions as round 1 — three lenses (product, design/UX, architecture) in
two model families (Opus, Fable), each given only the material (ADR, invariant
docs, the diff, the code) and its lens, none given the round-1 report or the
author's conclusions. Architecture reviewers were adversarial and proved
findings by running code; design reviewers raised the UI on their own ports;
product reviewers walked the first-run path by hand.

## What held (both architecture reviewers, by execution)

Neither adversarial architecture reviewer could break any of the seven
guarantees or the replay / one-write-path invariants. Each ran code that tried:
replay determinism (no clock/liveness in validation; re-projection equals the
write-time snapshot), the single canonical write path (`append_event` has
exactly one caller), the grant algebra with `auto` withheld at all three layers
(tool registration, launched argv, lifecycle), turnover-budget churn, review
independence by principal, terminal lifetime retirement in both event orders,
atomic leaves-first cascade, and corrections frozen against authority change.
This is the round-1 gap closed: the same properties that were claimed and
defeated are now claimed and hold under attack.

## New findings, and their disposition

Marked **[converged]** where reviewers of different lenses or models found it
independently.

Fixed in `5cb081d` (backend) and `503c63d` (UI):

- **Approving a proposal did not consume it** — [converged: both product
  reviewers, reproduced]. A second approval minted a duplicate role, spent
  turnover budget again, and left the item in the attention queue forever. This
  is round-1 **L2**, which the round-1 plan did not schedule. Approve now creates
  the role and resolves the proposal thread as one locked action; a second
  approval is refused.
- **`auto` withheld but undisclosed** — [converged: all six lenses touch it].
  Docs, CHANGELOG, and the CLI flag said `auto` ships while the code withholds
  it. Deciding to withhold was right; not disclosing it was the defect. Now: the
  CLI warns on an interactive `--create-roles auto`, the settings panel names the
  withhold, and the ADR / threat model / plan / CHANGELOG say it plainly.
- **`on_behalf_of` rebind on non-requested delegation events** — [architecture,
  Opus, PROVED behaviour]. The run/role binding was applied on every delegation
  event but authorised only on `delegation.requested`; a relation forged onto
  `delegation.started` rebound the run on replay with no check. Reachability was
  low (no worker path forwards relations), but the stated replay-revalidation
  guarantee was incomplete. The projection reads the binding only on the
  requested event now; the overclaimed comment is corrected.
- **Empty engagement thread on a rejected chat** — [product]. `open_engagement`
  recorded `thread.opened` before validating the message, leaving an empty thread
  in the immutable ledger. The message is validated first; the two writes are one
  locked action.
- **Invalid catalogue → silent 500** — [product]; round-1 **L7**, unscheduled.
  The `ui` command validates the catalogue at startup; the read route returns a
  named 422.
- **Hiring a role with an unknown skill** — [converged: both product]. Deferred
  the failure to the next launch. Hiring validates skill ids against the loaded
  catalogue.
- **Attention queue directionality** — [design]. An operator's own directive to a
  role (a `decision_request` addressed to the role) counted as waiting on the
  operator, inflating their own queue and ringing their own session. A shared
  predicate now counts a human-decision thread only when addressed to the human;
  the ring and the list read it, and the footer count reads the same number as
  the tab.
- **Stale-while-live, multi-client** — [design, Fable, reproduced]. `refresh_if_changed`
  is a one-shot consumer of a shared fingerprint, so one stream connection got
  each update and the rest stayed stale while showing "live". Each connection
  now emits whenever the shared sequence advances past its own last-sent value.
- **Gear-panel Save wiped skills/tools** — [design, Fable]. Save sent skills and
  tools from selects that are empty when no catalogue is loaded, silently
  destroying configuration. It sends them only when the catalogue is present.
- **The decision-request dead end** — [converged: both design, both product]. The
  attention card told the operator to "open this thread from the graph", but
  threads are not graph nodes. The card carries a reply box now, replying through
  the canonical thread-message route.
- Plus: inspector heading leads with the role name; the weakening-isolation field
  starts hidden; the layout is a flex column with wrapping tabs so the footer and
  the Settings tab are not clipped at 1280×800; the "narrowed by an ancestor"
  note distinguishes the global withhold.

## Deferred, with the human's decision recorded

These are product-shape changes, not defects, and are marked deferred in the
plan §15 rather than built in this pass:

- **Launching a delegation from the UI** — [converged: both product]. Plan §10
  stage-3 "запускает делегацию из UI" (MUST-4) and the streaming seam (MUST-5)
  are undelivered. The panel builds the org chart, talks to it, and answers it;
  every unit of work still starts from the CLI. Marked deferred in §15.
- **Full human↔role threading** — [design, Fable]. The Message tab opens a new
  thread per send and the role's reply is only readable in the attention queue,
  not threaded. The reply affordance closes the acute dead end; a threaded
  human↔role conversation is a larger surface, deferred.
- **Keyboard reachability of the canvas** — [converged: both design]. Graph nodes
  are SVG groups with click handlers and no `tabindex`/`role`, so role selection
  is mouse-only. Deferred; a real a11y pass is its own change.

## Where a reviewer was wrong, with evidence

- One design reviewer read the single-browser "panel stays current" as contradicting
  the multi-client "stale-while-live" finding. They do not contradict: the first was
  verified with one browser (its own SSE poll notices the change), the second with two
  clients (a second consumer wins the fingerprint race). Both are true; the fix
  addresses the multi-client case without regressing the single-client one, which the
  new multi-connection test pins.
- The product reviewers' "auto is broken" framing is imprecise: `auto` is not broken,
  it is deliberately withheld and behaves as `ask`. The defect is the missing
  disclosure, not the mechanism — which is why the fix is a warning and a doc
  correction, not a code change to the grant algebra.
