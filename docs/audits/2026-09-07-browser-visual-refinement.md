# Browser visual refinement verification

Date: 2026-09-07. Status: bounded implementation, browser verification and full
green contract passed; exact independent artifact review approved.

The user authorized the design council's bounded refinement of the task-first
workspace. [ADR 0015](../adr/0015-browser-native-visual-refinement.md) specifies
the visual, browser, ownership and evidence contract. The earlier audit and
task-first increment remain preserved; their proof applies to their recorded
hashes rather than automatically to this new working tree.

## Baseline

- HEAD and local `origin/main`: `329d2f3920753853ce265536d9d3ceb4e35f1f91`.
- Branch: `codex/task-first-ux-pivot`.
- Before refinement: dirty, 95 git-status entries; private hash-only inventory
  contained 646 tracked/untracked nonignored files.
- Correctly scoped doctor: healthy, zero integrity issues, 136 warnings.
- Three Astra workers own Work/shared presentation, inspector and Library.
  Root owns documentation, synthetic browser evidence and Figma integration.
- No live provider qualification or canonical task promotion is performed.

## Result and evidence

Three Astra workers implemented the council's agreed direction. Work now has
three primary filters and seven native select options, a single persistent
Refresh, neutral surfaces and system-font typography. The inspector groups
state, blockers and next action, with native supporting disclosures. Library
puts recipe effect/roles before technical detail. Context sources show readable
names and exact-version state, retaining typed references in accessible details.

The root used `commons-start`, coordination and delegation workflows,
`web-frontend-engineering` (state ownership, browser focus and verification),
the prior product-design/council synthesis, and Figma creation/use guidance.
`commons-record`, `commons-review` and `commons-handoff` preserve exact evidence
and independent judgment without promoting task completion or acceptance.

| Evidence | Exact result / boundary |
| --- | --- |
| [Source manifest](../evidence/2026-09-07/visual-refinement-source-manifest.json) | 470 current file hashes; 16 delta records against the 646-file pre-refinement inventory; independently matched |
| Source artifact | `artifact.378BZQDMCH67ES4K6H3DNQE87X` @ `evt.01M1XW75Z6HV2BPRC88C3NBDBZ` |
| [Browser evidence](../evidence/2026-09-07/visual-refinement-browser-verification.json) | Six exact JPEG hashes and actual dimensions; synthetic fixtures only |
| Browser artifact | `artifact.6M9RBDQN54K4H86TS1FMMVGPH3` @ `evt.01M1XWGY77F7CKYJSPWMN2M2CV` |
| [Full gate evidence](../evidence/2026-09-07/visual-refinement-test-verification.json) | Work94, Gallery22, Python1976 passed; 13 explicit/local Python skips, two existing deprecation warnings; no frontend skips |
| Test artifact | `artifact.7ASWA086K0WEJ45TGXRP5N6Y8Q` @ `evt.01M1XWS0C5Q3KFNNPMW1BRANQE` |
| Independent targeted checks | Reviewer ran 47 inspector/Library tests under Node24.20.0, zero failures/skips; matched source and image hashes |
| [Figma before/after](https://www.figma.com/design/5GbVebHulITT4gWLVTmtGh) | Four baseline screens above four final screens, with editable annotations; raster UI captures, not a component library |

Final build is `work-hWtPVhEt.js` / `work-DUxshpr4.css`. The CSS uses the
system sans-serif stack, 14px UI and 15px long prose. This is verified source
and computed-family evidence, not an assertion that a particular physical font
face renders identically on every operating system.

### Commands and results

- Scoped `doctor`, `session show`, `orient`, `inbox`, `task list`, `claim list`:
  completed through the in-repo CLI. Initial doctor had zero issues and 136
  warnings; a later independent doctor had zero issues and 138 warnings.
  Canonical ownership and generated receipts were never edited manually.
- `git status --short`, `git rev-parse HEAD`, `git rev-parse origin/main`:
  baseline SHA above, dirty before and after. Final documentation checkpoint
  has 97 status entries; existing dirty work was preserved.
- Node24 `npm run build --prefix frontend/work`: TypeScript and packaged build
  passed after integration and again after review corrections.
- `git diff --check`: passed.
- Final `make check`, with the three inherited Agent Commons environment
  overrides removed and repository-compatible Node selected: exit0. Locked
  Ruff check passed; 472 files passed format check; Work94/Gallery22 and full
  pytest passed. Pytest duration 549.69 seconds.
- First full gate: 1975 passed, one failed, 13 skipped in 535.35 seconds. The
  new ADR existed before its index entry was added; index corrected and the
  whole gate repeated after source freeze. Do not cite that first run as green.
- CUA operated actual packaged UI in isolated synthetic workspaces. A second
  disposable fixture altered GET observations and delayed/failed GET details;
  no actual provider or real workspace task was mutated for this browser proof.
- `sips` inspected JPEG format/dimensions; files were visually inspected.
  Desktop CSS viewport1440×1000 produced1440×842 bitmaps; narrow390×844;
  EN fault screenshots1280×720. Library/Context are scrolled fragments.

The inherited state-root environment targeted a different workspace. Ordinary
sandbox CLI access could also report false SQLite/receipt failures. Correct
scope and allowed access established healthy state; no bootstrap or reconcile
was performed to suppress those environmental errors. Two test-fixture setup
mistakes (auth fragment name and synthetic tracker revision format) were
corrected before drawing browser conclusions.

## Checklist and observed behavior

- [x] Three primary Work filters and native remaining-status selection preserve
  all ten query values and show the selected value.
- [x] One persistent Refresh control retains focus across warning changes.
- [x] Inspector preserves visible blocker, unknown/stale state and evidence
  semantics; supporting disclosures work with keyboard and selected-task changes.
- [x] Library puts effect/roles first without hiding grants or explicit consent.
- [x] Context shows readable exact source identity without revision upgrading;
  drafts and uncertain operation recovery remain intact.
- [x] Packaged assets rebuilt after integration and review corrections; focused checks pass.
- [x] Actual browser screenshots and narrow layout inspected.
- [x] `git diff --check` and full `make check` pass on final frozen source.
- [x] New exact source and browser evidence registered; independent hashes checked.
- [x] Canonical independent verdict recorded against the exact browser revision.
- [x] Figma before/after board updated from actual browser captures.

Actual browser Back/Forward now has bounded evidence: Back restored the active
filter and Forward restored all. Native summary Enter opens the goal and
focuses SUMMARY; Close returns focus to the selected row. A changed observation
with a 15-second detail delay and then503 preserved the open, focused goal,
showed loading/failure and a cached-data warning; successful retry preserved it,
while switching tasks reset disclosure and removed previous-task content.
Refreshing a newer source catalog preserved the original exact reference in
both select and advanced JSON, with stale text and `aria-invalid=true`.

Forced-colors selected-tab matching and reduced-motion rules received **source
inspection**, not browser emulation or a dedicated new CSS test. Existing
Library tests cover `aria-selected` semantics. The browser evidence JSON's
`hermetic_only` CSS-contract category refers to this static/source boundary;
it must not be read as automated browser accessibility certification.

Independent review `review.4TW9K8X39XQBV4123ZD0ZYJPEC` is **approved** at
`evt.01M1XX22D73N20AKSEZPQ4WK72`. Its readback confirmed `stale=false`,
exact browser target `evt.01M1XWGY77F7CKYJSPWMN2M2CV` and exact source/test
evidence revisions above. The non-author reviewer checked all hashes after a
shared-checkout write freeze, ran targeted tests and inspected saved images;
the root performed the actual browser fault checks and full gate. The reviewer
session closed before these final narrative status lines were added. Registered
source, browser and test artifacts were not modified by this status update.

## Findings and disposition

| Priority | Finding / code pointer | Disposition and closure |
| --- | --- | --- |
| P2, fixed | Same-task source refresh unmounted goal/summary, losing native disclosure and focus. `TaskInspector.tsx:34`, `:54`, `:101` | Retain only same-task cached details with explicit stale/loading/error notices. Transition tests and actual delayed/failing browser check passed. |
| P3, fixed | Forced-colors CSS matched `aria-pressed` while Library tabs expose `aria-selected`. `styles.css:1164`, `LibrarySection.tsx:33` | Selector corrected; source inspected. Actual forced-colors browser/screen-reader assessment remains open. |
| P3, open | On source drift the select replaces the readable title with long stale text. `ContextSourcePicker.tsx:44` | Status remains fully readable alongside it and exact binding stays intact. Later retain last-confirmed display label separately from the immutable ref; no silent revision upgrade. |
| P3, open | Technical source text sits close to the summary focus outline. `styles.css:1148` | Later spacing polish; retain visible focus and wrap long refs without hiding identity. |

No new P0/P1 remained in this scoped refinement review. The reviewer used `rg`
on the nongenerated delta and manually rejected false positives: generic UI
privacy wording, a same-origin fetch contract and synthetic session-storage
tests are not leaked credentials. New screenshots contain synthetic task refs,
not real session/receipt/operator state. Runtime/API/launch files match the
pre-refinement inventory. This is a scoped negative result, not a renewed full
security certification of the entire repository.

## Actionable next work

- **Must fix before release:** existing L1/R2 and Grok transport privacy gates
  from the September6 audit. Provider/runtime owner plus authorized operator
  must produce live evidence on the intended six-profile/source/host boundary;
  a non-author reviewer must evaluate exact release evidence.
- **Should fix soon:** preserve readable stale-source display labels, tune source
  disclosure spacing, and perform a full keyboard/screen-reader/forced-colors
  pass with long RU content. Frontend owner closes with browser evidence.
- **Growth/opportunity:** first-use operator study, density preference and light
  theme experiments; do not invent usage evidence or ship modes solely because
  a council preferred them. Product/design owner defines observable outcomes.
- **Needs decision:** ADR0015 governance, retention policy, project switching and
  any larger design-system/Figma component-library scope remain owner decisions.
- **Needs external/live verification:** L1, real Context publication browser
  journey, accessibility assistive-technology coverage and real operator study.

## Files and plan deltas

Application delta: TrackerSection, TaskInspector, LibrarySection,
StarterPacksSection, ContextPacksSection, ContextSourcePicker, shared styles and
i18n; focused task-inspector/work-library tests; generated Work index/assets.
Exact before/after hashes, including removal of previous generated assets, are
in the source manifest. No backend, legacy UI or Gallery application edit was
introduced by this refinement.

Documentation: ADR0015 and ADR index; this report; task-first implementation
plan; targeted checkpoint paragraphs in ROADMAP, current-product-and-architecture,
architecture-improvement plan and agent-platform programme; three new evidence
JSON files and six JPEG screenshots. Earlier ADR0014 and registered source,
browser and gate artifacts remain unchanged historical evidence.

Coordination tasks remain explicit and unaccepted: root
`task.2516VV5KCMP2SVPWS2T26CA0X2`, lead `task.0WEFP8J3SNGXG3T65GT9RBEXDP`,
inspector `task.5834H1JYK7P8TZJSZKH5HJ7Z28`, Library
`task.43PGGBJECJH5WH26AZEQR6M4Q4`. Inspector remained assigned; root/lead/Library
were active during implementation. Closure uses targeted handoffs and claim/
session release, not completion/submit/accept transitions.

Worker handoffs: lead `handoff.7PFAKXTZE44KX7NEWHQVDYY09Z`, Library
`handoff.0TS86FN5M7X49JW02VRPX5GE8B`, inspector correction
`handoff.2QWANMXPCSNQZP2S5772X5TE2K`. All worker claims were released and
sessions closed. The lead corrected an overbroad static-CSS-test description
through the supported event correction `evt.01M1XWT58K9T9E5FQSJXFH7FRT`;
use the precise source-inspection boundary stated above.

Root handoff `handoff.5R0CZ1KHG2RGB42H0VZCC267ST` at
`evt.01M1XX9FAAYX02J081M1209FA2` addresses `principal-reviewer` with the
exact artifacts, approved review and ordered remaining actions. Root closes its
documentation claim and session after confirming this handoff through the CLI.

## Open boundaries

L1/R2, existing Claude/Grok qualification failures, Grok argv transport privacy
and retention policy are unchanged. Browser success against an isolated
synthetic runner cannot close them. Actual provider work requires explicit
operator authorization and subsequent exact independent evidence.

No task completion, submission or acceptance, commit, push or release is part
of this checkpoint. Prior automatic approval review rejected lifecycle promotion
under the original review-only boundary; later implementation authorization did
not authorize those transitions. Exact artifact review and handoffs retain the
result without silently changing task truth.
