# Cold-run usability: three blind rounds, one consolidated plan

Status: **rounds 1–3 complete; waves A–C and 1–4 all landed (2026-08-13).**
Of the 34 findings below, 31 were implemented, 3 were rejected on the
record, and the two compromises those rejections carried were built. Two
things this plan asked for are deliberately absent and named as such in the
entries: a run's spend, which nothing in this codebase records, and a
demo-mode acceptance that would have required fabricating a review verdict.
Three blind walkthroughs ran against the panel, each by an agent with no access
to the source, the tests, or the documentation — knowing only three sentences
about the product and given the same job: get a simple site built by someone
else. Round 3 ran two testers independently in parallel, on separate
workspaces, on different models, so their findings never contaminated each
other.

Date: 2026-08-12 (round 1), 2026-08-13 (rounds 2 and 3)

| Round | Tester | Ran against | Outcome |
|---|---|---|---|
| 1 | PM (Sonnet) | `6415885`'s parent | 9 findings; all fixed (`6415885`) |
| 2 | PM (Sonnet) | `6415885` | 8 findings; all fixed (waves A–C, `7109660`..`6c31745`) |
| 3 | PM (Sonnet) + product designer (Opus), parallel and blind to each other | `e249873` | 38 findings, deduplicated to 34 below |

The NPS trend is the honest headline. Round 1: "5–6/10, an early engineer's
alpha." Round 2: "I'd recommend it to an engineer, with caveats." Round 3, both
testers independently: **the loop does not close.** They agree on why — a run
reaches `succeeded` and there is no way, anywhere in the panel, to accept the
work. Each round's tester pushed one step further down the same path, so the
score did not regress; the wall moved into view.

Both round-3 testers praised the same foundation: append-only history, the
honest demo-mode label, the deliberate split between "the run succeeded" and "a
human accepted it", the tooltips, and the "In short" overview page. The
criticism is aimed at the layer above that foundation, not at it.

## What the acceptance chain actually requires

The designer called the missing acceptance a blocker. The PM went further and
tried to work around it: he hired an independent reviewer and launched it, and
got a raw English refusal about needing a review request that no screen can
create. He was right, and the gap is larger than a button. `lifecycle.py`
requires all of this before `task.accepted` is legal:

- an approved review, `independent: true`, not stale;
- bound to the task's current `effective_revision`;
- whose own revision the acceptance names;
- with the task already in state `review`.

So the panel is missing a chain — request a review, run an independent reviewer
against that request, then accept against the verdict — not a single action.
That shapes wave 1 below.

## Consolidated findings

Legend: **[done]** landed · **[rejected]** deliberately not done, with the
reason. Two rejected items carried an [open] compromise instead; both
compromises were built and are marked [done] inside those entries.

### Wave 1 — close the loop (both testers' blocker)

1. **[done]** No way to accept finished work. Add "Send for review" to the task
   drawer over `request_review`, run an independent reviewer against that
   request, and offer "Accept" / "Send back" bound to the approved review's
   revision. (Designer 2, PM 1.) Built as three sealed routes. Note that in
   demo mode the chain stops one step short by design: `DemoRunner` will not
   fabricate a review verdict, so an approved independent review can only
   come from a real reviewer. The panel says that plainly rather than
   simulating an acceptance nobody made.
2. **[done]** The review refusal (`an independent_review delegation requires an
   open independent review request…`) reaches the operator raw and in English
   under a Russian UI. Map it to a human sentence that names the next action.
   (PM 2.)
3. **[done]** A finished run leaves nothing in Attention. A succeeded run whose
   task is unaccepted belongs in the queue: "X handed work back — accept or
   send back." (Designer 2.)
4. **[done]** "In short" never says why a succeeded run does not finish a task —
   the most practically important rule is buried in the second tab. One line
   in the first tab, linking onward. (PM, overview section.)
5. **[rejected]** Advance the task automatically on `succeeded`. Acceptance is
   the human decision the whole design exists to protect; automating it would
   delete the property both testers praised.

### Wave 2 — trust in forms and reading

6. **[done]** Entity panels open on raw ledger JSON. Lead with a human summary
   (what to do, criteria, state, last run, what is next); keep JSON behind a
   "Show record" control. The task drawer already does the right thing —
   generalise it. (Designer 3, PM 10.)
7. **[done]** Hire's "Why this role exists" is required but unmarked, and its
   refusal renders below the buttons, outside the modal's scroll — it reads as
   a dead button. Mark required fields, put the error at the field, scroll it
   into view. (Designer 1, PM 3; round-2 PM raised it as minor and it was
   under-rated.)
8. **[done]** The same error placement problem in "New task", plus the canonical
   server text leaking in brackets. (Designer 14.)
9. **[done]** The hire modal does not close on success, and both testers saw it
   change state on its own (self-closing, unexpected prefill). Close on
   success; investigate the state change — a repaint is the suspect. (Designer
   7, PM 4 and 14.)
10. **[done]** Modals have no focus trap: Tab leaves the dialog and it appears
    to vanish while still open. Trap focus, Esc closes with a guard when the
    form is dirty. (Designer 8.)
11. **[done]** Confirmations speak in ids (`нанята agent.61JBN…`, `launched
    delegation.6S7B…`) and double a status (`SUCCEEDED · SUCCEEDED`). Say what
    happened in words; keep the id one click away. (Designer 6.)
12. **[done]** The onboarding card hides whenever any node exists, so a newcomer
    who was handed a task never sees the first step. Condition it on having no
    hired agent. (Designer 19, PM's run — he never saw it once. Confirmed in
    code: `graph.nodes.length > 0`.)
13. **[done]** An empty template catalogue offers "— none —" with no way
    forward. Offer the CTA that creates the first template. (PM 5.)
14. **[done]** The skills multiselect renders as an empty box with no empty
    state and reads as broken. (Designer 23.)

### Wave 3 — the board and the language

15. **[done]** The board mixes the domain (agents, tasks) with the runtime
    (sessions, delegations, "DEPTH 1/2"): one launch added four technical
    nodes. Show the team by default; put runtime nodes behind a toggle or on
    the run's card. (Designer 5.)
16. **[done]** "Fit" ignores that the dock covers the right of the canvas, and
    at nine nodes it scales to roughly six pixels. Fit the visible area, floor
    the scale, lay nodes out as a grid rather than one row. (Designer 4.)
17. **[done]** Link ports are about six pixels after autofit — unhittable. Grab
    area ≥24px, hover affordance, cursor hint. Both testers failed to open a
    link by drag, independently. (Designer 17, PM 12.)
18. **[done]** Language switching does not repaint already-open panels: the
    links list keeps the previous locale. Confirmed — `applyLanguage()` does
    not call `paintLinks`. (PM 9, designer 10.)
19. **[done]** Untranslated strings remain (`No links yet. Drag a port…`).
    Sweep them. (Designer 10, PM 7.)
20. **[rejected]** Translate canonical values (`deny`/`ask`/`auto`, `fresh`,
    `succeeded`). They are the shared vocabulary of the panel, the CLI and the
    ledger; translating them would make the same state read two ways. **[done]**
    instead: show a human gloss beside the canonical value. (Designer 10, PM 6.)
21. **[done]** One thing has four names: "Запуски" in the nav, "Запуск" on the
    tab, "прогоны" in the guide, "делегация" in the drawer; and "Скиллы"/"Туллы"
    sit beside "Инструменты". Pick one term per thing. (Designer 11, PM 8.)
22. **[done]** State glyphs (◑ ○ ⊘ ●) are never explained, and the `operator`
    card does not say it is you where a person can see it — the tooltip exists
    but is not discoverable. Add a legend. (Designer 18, PM 11 and 13.)
23. **[done]** Russian overflows the dock: the drawer title clips to "nt ·
    Верстальщик", a horizontal scrollbar appears, "+ Задача" wraps and breaks
    the toolbar row. Test layout at +30% string length. (Designer 9.)
24. **[done]** Radio controls in agent settings stack the dot above its label —
    `.field span{display:block}` catches them. Confirmed in code. (Designer 13.)
25. **[done]** The "Settings" tab sits off the baseline of its neighbours, with
    the indicator dot above it. (Designer 12.)
26. **[done]** Scrollbars are white on the dark surface — the highest-contrast
    object on screen. Confirmed: no scrollbar styling exists. (Designer 15.)
27. **[done]** The footer is 11px, unseparated, and says "устаревших
    предупреждений" without explaining it. (Designer 22.)
28. **[done]** Runs carry no start time, duration or spend, and cannot be
    filtered or grouped. (Designer 16.) Start time, duration, purpose,
    filtering and grouping landed. **Spend did not, on purpose:** nothing in
    this codebase records consumed `provider_units`, so the panel shows the
    budget the run was *permitted* and says in words that consumption is
    recorded nowhere. Showing an estimate would have been the one thing this
    surface must never do.
29. **[done]** The header spends half its width on two ULIDs. Show the
    workspace by name; keep the id one click away. (Designer 18.)
30. **[done]** Below ~900px the board is unusable: the sidebar does not
    collapse and the dock takes half the width. (Designer 24.)
31. **[done]** Form microcopy speaks system ("Бюджет ротации", "Контекст:
    fresh", "Отставка ролей"). (Designer 21.)

### Wave 4 — the guide, and the record

32. **[done]** "In short" is the onboarding (9/10); the other five tabs are a
    specification (8/10 as reference, 3/10 as onboarding). Frame them as a
    reference, and link terms in the interface to the paragraph that explains
    them. (Designer, overview section.)
33. **[done]** The guide has no picture of the thing it describes, and its
    monospace examples are dim grey on dark. (Designer, overview section.)
34. **[rejected]** Remove SGR and MCP from the sidebar as dead ends. Both pages
    carry real content and name their unlock condition; hiding the roadmap
    would be less honest, not more. **[done]** instead: group them visually as
    forthcoming. (Designer 20.)

## Post-implementation verification (2026-08-14)

An independent verifier read the seven implementation commits against this
plan and the invariants. It confirmed the domain and services are untouched
across the range (`git diff --stat` over both trees is empty), one write path,
CSP clean, locale parity, and the suite passing. It also found three real
defects, all fixed before the push, plus four locale keys that no surface
renders — removed, so parity now guards 516 real keys rather than 520 with
four ghosts among them. The suite finished at 716 passed, 12 skipped,
against a 654-passed baseline.

- **A language switch discarded a half-typed acceptance summary.**
  `paintTaskAcceptance()` closed the confirm form on every repaint, and it is
  a language surface — so switching locale mid-acceptance hid the typed text
  and the next click wiped it. This contradicted the rule the file states
  about itself: a repainted surface rewrites labels, never an operator's
  input. The reset is now conditional on the shown task actually changing.
- **The one-write-path test covered five of fourteen routes while claiming
  all of them.** It now exercises twelve and names the two it cannot — a
  proposal approval and an operation answer both refuse on a missing subject
  before reaching any write, and are driven end to end elsewhere. The
  exemptions are asserted as a set, so a new route cannot be added without
  being either exercised or explicitly exempted.
- **The review walk recorded `task.completed` with the same sentence whether
  or not any run had finished.** Judgment call, since the verifier asked for
  one: refusing the walk would have been wrong — an operator may legitimately
  have done the work by hand, and the panel cannot know otherwise. But the two
  are not the same claim, so the ledger now records which one it was: "the
  operator sent finished work for review" when a run succeeded, and "the
  operator judged this work done… no run had finished on it" when none did.
  Honest attribution rather than a refusal that would block a real flow.

## Landed before this plan (do not redo)

From rounds 1–2, already shipped and verified live: task creation from the
board (`POST /api/tasks`), Runs cards and "Runs on this task" opening the
delegation record, the board's Fit button with a one-shot autofit and a pan
hint, templates kept off the board with `counts.templates` split from
`counts.agents`, the hire modal's field reset and "Save the template" verb,
field-name refusals in five forms, the template rationale prefill, the
read-only catalogue hiding its form, translated footer counts, the "this is
you" tooltip, and the removal of the link deadline nothing could enforce.

Two of these were reported again in round 3 — the hire modal's state and the
footer — so they are re-opened above as items 9 and 27 rather than trusted as
done.
