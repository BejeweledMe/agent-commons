# ADR 0010: Panel access decided by typed refusal, not capability flags

Status: accepted. Supersedes part of ADR 0009 (Q2, and the catalogue-gate
subsection of Q4); the rest of ADR 0009 is unaffected — see "What this leaves
standing" below.

## What this replaces, and what it does not

ADR 0009 answered Q2 ("How writes enter the UI without a second write path")
with three capability flags: `agent-commons ui` was read-only, `--enable-writes`
made it record canonical events under an explicit operator session,
`--enable-catalog-editing` (additionally requiring `--role-catalog`) gated
catalogue edits as "a different magnitude of privilege" from recording a role,
and `--enable-launch` (additionally requiring `--profile-config`) gated
starting a provider process as "a larger privilege still". Each flag had its
own route allowlist, decided when the app was built.

That mechanism is superseded. Specifically:

- Q2's flag-gated route table (ADR 0009 lines 113–186 as recorded at the time
  this ADR was written: `agent-commons ui --enable-writes`, `GET /api/meta`
  reporting `writes_enabled`, and the "own gate" annotations on the catalogue
  and delegation routes) is replaced by the model below.
- Q4's subsection "The conflict this creates with 'the human writes the
  catalogue from the UI'" — the paragraph arguing that `--enable-catalog-editing`
  must exist as a second, separate flag from `--enable-writes` because
  recording a role and changing what a run is told to do are different
  magnitudes of privilege — is withdrawn as an argument *for a flag*. The
  underlying judgment it was protecting (catalogue editing is a distinct,
  larger privilege than recording a role) is not withdrawn; see below for
  where it now lives.

## What this leaves standing

Nothing else in ADR 0009 is touched. Roles as first-class, persistent
entities — Q1's event model, the role/delegation split, the event-relation
binding to delegations — are untouched by this ADR. So are Q3 (the seven
guarantees of autonomous role creation), the rest of Q4 (role settings may
only narrow, the operator-owned skill catalogue, terminal-tool exemption),
Q5 (indexing), Q6 (principal-set independence), Q7 (fresh context as a
mechanism), Q-links, "removal is retirement", and the compatibility/rollback
and consequences sections. This ADR is narrower than its title might suggest:
it revises *how the panel decides whether a request may act*, not what a role
is or what it may do once it can act.

## Context

The product decided to remove the terminal steps between installing Agent
Commons and having a working panel: no `init`, no `session start`, and no
capability flags before `agent-commons ui` does anything useful. That
decision is recorded in the registry as `decision.61BDS4NC4GVK9K50R09XK98A07`
(UI as the primary surface) and its consequence for this ADR is direct — a
panel that must discover its own workspace, write its own operator runtime
config, and adopt its own catalogue *while already serving* cannot also
decide its non-`GET` route table from flags read once at startup. FastAPI
builds that table exactly once; the panel's own first-run screen can bring a
workspace, a runtime config, and a catalogue into existence after the table is
already built.

Two decisions in the registry that ADR 0009's flag model depended on are
superseded as part of this change:

- `decision.3F95WMKZD5P6026RB31XVHRKR1` (preserve the deliberate act of
  consent that a capability flag represents) — superseded, replaced by
  `decision.2HAH3A8XTG8839RNR11GGR4SY6`: there are no capability gates at all.
- `decision.7PAPYKR4QRC018E372AF952ZZ9` (the broker stays experimental,
  manual opt-in only) — superseded, replaced by
  `decision.558YVVEX7D1BTEBERNBPT14XY2`: the broker stays exactly as
  experimental as before and the evidence-gate that guarded it is carried
  forward verbatim; only how that fact is *signalled* changes
  (`broker_release_stage` reports `"experimental"` rather than
  `"experimental_manual_opt_in"` — a label change, not a loosened gate).

Both supersessions are recorded in the decision registry before this code
shipped, per this project's own truth-promotion contract; this ADR is the
architectural record of the same change, not a second place the decision is
made.

## Decision

A panel's non-`GET` route table is now decided by exactly one structural
fact, settled once at construction and never re-derived: **does this panel
have the means to hold an operator session at all** (an owner, a session
provider, or an already-resolved session id), i.e. is it an *operator panel*
as opposed to a `--read-only` view. That is the only remaining structural
switch:

- `agent-commons ui --read-only` — registers zero non-`GET` routes, exactly as
  the old default did. Opens no session, records nothing.
- `agent-commons ui` (an operator panel, the new default) — registers the
  **whole** non-`GET` surface unconditionally: the union of `MUTATING_ROUTES`,
  `CATALOG_ROUTES`, `LAUNCH_ROUTES`, and the new `SETUP_ROUTES`
  (`src/agent_commons/ui/server.py`). No flag, and no other condition, changes
  which routes exist.

What ADR 0009's flags used to decide — whether recording a role is possible
right now, whether the catalogue may be edited, whether a launch may start —
moves from *route existence* to *a typed refusal inside the handler*,
evaluated per request:

- `setup_uninitialized` — the workspace does not exist yet (the first-run
  case ADR 0009 could not have this route type for, since a flag-gated table
  could not represent "not yet, but about to be").
- `launch_not_configured` — no operator runtime config resolves right now.
- the catalogue's own refusal — no catalogue path, or no session to write
  under (`_require_catalog_editing`).

Catalogue editing keeps the judgment ADR 0009 argued for — it remains a
distinct, larger privilege than recording a role, checked and refused
separately — but the argument for *why* now lives in the refusal condition
(`catalog_editing_enabled`: a catalogue path exists **and** the panel holds a
session) rather than in a second command-line flag naming a second file.
Launch keeps its own declared route tuple for the same reason ADR 0009 gave —
spawning a billable subscription process is a larger privilege than recording
bounded metadata — even though every operator panel now registers it
unconditionally; `launch_not_configured` is what stands in for the flag.

The mutating-surface tests ADR 0009 introduced are kept, generalized rather
than dropped: "the registered surface equals an explicit allowlist" becomes
"the registered surface equals the union of the four declared tuples,
asserted literally rather than derived from the same conditions the
registration used" (so the test and the code cannot silently agree with each
other while disagreeing with the frozen tuples); "every mutating route dies
when `record_event` is monkeypatched to raise" is unchanged; "each route is
driven over HTTP and its event is found in the ledger" is unchanged.

## What we lost

ADR 0009's flags were not only an access-control mechanism; `--enable-writes`,
and more so `--enable-launch`, were a **deliberate act of consent** —
something a person typed once, on purpose, before the panel could spend their
provider subscription or change the ledger. That act is gone. There is no
longer a moment at the terminal, or a click in the panel, where starting a
role's run asks to be confirmed. An operator panel can now spend the
operator's own paid subscription the instant a Run button is clicked, with
the same lack of ceremony as opening Codex or Claude Code directly and typing
a message — which is exactly the owner's argument for removing it, not a
gap in the argument.

This is a real trade, not a free improvement, and it was made consciously:
the owner decided that the friction of a capability flag (and, before this
wave, of hand-writing `runtime.yaml` to satisfy it) was costing the product's
actual first-run experience more than the flag was buying in deliberate
consent, given that the same person does not re-consent per window in the
tools this product wraps. The fact this ADR will not paper over is that
**removing the flag removed the consent act**, and the product's answer is to
say so in documentation instead of in a dialog — in the README's provider
configuration section and in `docs/THREAT_MODEL.md`'s "local UI as a write surface"
section, both rewritten alongside this ADR to state plainly that launching a role
spends a real, billable subscription with no confirmation step. An ADR that
described only the routing mechanism and stayed silent on this would be the
same kind of documentation drift this wave's own README and threat-model
fixes were written to end.

Two smaller things are also lost, both already priced into the decision
above: a flag-gated table could refuse a whole class of request at the HTTP
layer before any handler ran, which is no longer true — every operator panel
now exposes routes whose usability depends on state that can change between
one request and the next; and `GET /api/meta`'s `writes_enabled` boolean,
which ADR 0009's Q2 said made "the client cannot be wrong about which one it
is", is now one read of several typed signals (`writes_enabled`,
`launch_enabled`, `catalog_editing_enabled`, and the setup state code) rather
than a single flag echoing a single flag.

## Consequences

- The panel can now offer a working first-run experience on a directory with
  no workspace, no operator config, and no catalogue — none of which a
  flag-gated route table could represent, since all three can come into
  existence only after the table is already built.
- Capability is answered honestly per request instead of once at process
  start, which is strictly more state for a reader (frontend or otherwise) to
  track correctly; `docs/FRONTEND_CONTRACT.md` gained a rule for it
  ("a registered route is not a usable route") for exactly this reason.
- The deliberate consent gesture ADR 0009's flags provided is gone and is not
  replaced by an equivalent gesture inside the product; it is replaced by a
  documentation obligation instead, which this ADR treats as a real
  consequence to track, not a footnote.
- `decision.3F95WMKZD5P6026RB31XVHRKR1` and `decision.7PAPYKR4QRC018E372AF952ZZ9`
  are `superseded` in the registry; the broker's evidence-gate and its
  experimental status are unchanged in substance, only in how they are
  reported.
