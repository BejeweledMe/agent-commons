# Task-first UX pivot: implementation verification

**Status:** bounded branch implementation and full green contract verified;
exact canonical artifact review approved and non-stale. No task acceptance or release.
**Branch:** `codex/task-first-ux-pivot`.
**Base HEAD and local origin/main:** `329d2f3920753853ce265536d9d3ceb4e35f1f91`.
The working tree was already dirty (53 status entries) before this increment.
Earlier audit remediation is retained. No commit or push was performed.

[ADR 0014](../adr/0014-task-first-workspace-ux.md) records the design, API,
interaction invariants, alternatives, ownership and implementation graph.
The [delivery checklist](../task-first-workspace-implementation-plan.md) gives
Astra, inspector, Library and root integration responsibilities. This report
supersedes the ADR's frozen design-time “pending verification” header for the
implementation result; the ADR remains proposed for owner governance.

## What was delivered

Work now opens as a task workspace with a persistent Work / Team / Library /
Settings shell. Task creation selects the exact returned task and does not
require a launchable provider. Launch is an explicit selected-task action.
The inspector prioritizes outcome, criteria, state, blockers and evidence;
technical identities remain available in details. Task completion, readiness,
run success and evidence completeness use distinct EN/RU labels.

Library separates recipes, Context and Design. Exact source discovery uses a
bounded authenticated metadata-only catalog; the guided and advanced editors
preserve in-memory drafts. Unknown mutation outcomes retain the original body,
revision and idempotency key. An uncertain launch visibly names its original
task and role when another task is selected. Narrow layout, focus restoration,
local errors, URL restoration and diagnostic access were integrated.

Browser integration also exposed a backend defect: immutable task age made a
quiet workspace permanently stale. Coherent observation now confirms unchanged
canonical records without refreshing an active run's stale heartbeat, mutating
canonical timestamps or bypassing revision checks.

The scope does not add a scheduler, canonical Team entity, project switching,
provider resume, light theme or replacement ledger. It retains CLI/MCP and
existing Gallery/recovery paths. L1/R2 are not closed by this pivot.

## Opus and independent review

Two local Opus consultations were read-only. The runner reported
`claude-opus-5` for their main assessments. The first inspected the two baseline
screenshots; the second inspected seven before/after screenshots and relevant
source. Opus judged the task-first hierarchy and inspector materially improved.
Its required refinements were implemented: explicit Task/Readiness labels,
distinct readiness copy, one freshness notice with adjacent Refresh, visible
Team target, compact filters, quieter metadata and consistent inspector width.

Opus contributed design and is not the canonical independent approver of its
own decisions. A separate non-author reviewer checked the source delta, all
469 source hashes, all five saved screenshot hashes and actual saved images.
It reran 42 focused frontend tests, 19 backend observation tests and the final
four provider-auth UI tests. No remaining app P0/P1/P2 was found in the bounded
pivot delta. That scoped judgment does not reclassify the prior audit's open
provider risks.

Independent review caught a **P2 proof-metadata error**: screenshot bytes were
JPEG while their filenames and dimension parser assumed PNG. Image bytes and
hashes were unchanged; final extensions, format and bitmap dimensions were
corrected. The browser artifact was revised through the supported CLI from
`evt.01M1WE4NB8J308W1GZXTREK38X` to
`evt.01M1WEGEV7CQPEEDM0KEJ6777V`. The first revision is superseded proof.
Historical before-image `.png` paths contain JPEG 1440 × 1000 bytes; this is
explicit in the correction to preserve frozen design references.

## Exact evidence

| Artifact | Exact evidence and boundary |
| --- | --- |
| [Source manifest](../evidence/2026-09-07/ux-pivot-source-manifest.json) | 469 hashes, 50 pivot-delta records against the 609-file pre-pivot inventory; prior runtime remediation preserved. SHA256 `c44b28c59f2fc645bd5504054c0202732ca76597849fb0e7991a5825f40c735d` |
| Source canonical identity | `artifact.564MPYTS632BVPWWAWVW30Y1ZJ` at `evt.01M1WE38WQ5FKNK2H0M8MHPD25` |
| [Corrected browser evidence](../evidence/2026-09-07/ux-pivot-browser-verification.json) | Five synthetic screenshots, their hashes and actual bitmap dimensions; separate observed CSS viewport dimensions |
| Browser canonical identity | `artifact.5DMF26K420TKPZ6FSJPWXN0Y2V` at `evt.01M1WEGEV7CQPEEDM0KEJ6777V` |
| [Full test verification](../evidence/2026-09-07/ux-pivot-test-verification.json) | Hash-binds both manifests; command results, earlier failures, skip reasons and claim boundary |
| Final canonical independent review | `review.6GD3GXYBA8W22SJ6RBQG1K7S9Z` at `evt.01M1WF8EV1WYDFXJPQKVPDQDMB`: approved, `stale=false`; exact target `artifact.1V751DH7TNSB7APB8DXS4FHMR9` at `evt.01M1WEPXKVVQ8E2BHTP8JBWQRT`. Independent readback and all 469 source hashes confirmed after recording |

All browser journeys used disposable synthetic workspaces and the test
`ControlledRunner`, with fixed synthetic auth readiness. The response-loss
fixture deliberately returned an error after one successful delegation record.
No live provider was called by these runs; no actual operator state, raw provider
output, prompts, transcripts or private configuration were copied into evidence.
The Opus design consultations are distinct from live provider qualification.

## Commands and results

| Command / check | Result |
| --- | --- |
| `git status --short`, `git rev-parse HEAD`, `git rev-parse origin/main` | Separate branch, exact base above; dirty before and after; no new commit |
| Correctly scoped `doctor` | Final doctor healthy: zero integrity issues, 136 warnings; no bootstrap/reconcile repair performed |
| `session show`, `orient`, `inbox`, `task list`, `claim list` | Workspace orientation completed before implementation; project state kept separate from synthetic fixtures |
| `git diff --check` | PASS |
| Full `make check` with selected Node 24.20.0 / Python 3.14.5 matching `.node-version` / `.python-version`, and locked Ruff 0.16.3 | PASS, exit 0 |
| Ruff check / format check | PASS; 470 Python files already formatted |
| Work / Gallery frontend suites included in `make check` | 85 / 22 passed; zero skips |
| Full pytest included in `make check` | 1976 passed, 13 skipped, 2 warnings; 738.54 seconds |
| Independent focused checks | 42 frontend + 19 observation + 4 final provider-auth tests passed; 469/469 source hashes and 5/5 image hashes matched |

The 13 Python skips are 12 deliberately withheld automatic-grant cases and one
CI-only invariant that is inapplicable locally. They do not conceal skipped
frontend harnesses. Two existing warnings concern Starlette/httpx TestClient
deprecations. A first gate failed two long test lines; a subsequent full gate
failed two old provider-auth source assertions. Those assertions were migrated
to the frozen launch-intent contract with behavioral coverage, and the complete
gate was rerun successfully. No subset is substituted for the final result.

## Browser proof and limits

- Created a task before a role existed; selected its exact identity, with launch
  visibly blocked by the missing role.
- Lost the successful launch response for task A, selected B, restored A and
  retried the original operation. A showed exactly one succeeded run; B had none.
- Displayed accepted/completed dependency choices from the actual tracker.
- Preserved exact Context source and multiline fact text, including literal
  `::`, through editor-mode and destination changes.
- Preserved form input after local validation errors; verified shortcut behavior
  outside versus inside editable controls.
- Reload restored selected task; Library tabs were addressable.
- At a CSS viewport of 390 × 844, document width was 390 and inspector selection
  received focus. Closing details restored row focus. At 1440 × 1000, inspector
  width was 480. A separate 720 × 900 reflow check had no document overflow.

Actual browser Back/forward was not exercised successfully by the automation;
its route behavior is covered hermetically. A resized viewport is not a claimed
200% browser zoom test. Context Pack publication was not clicked after automatic
approval review rejected it as a possible canonical write; isolated regressions
verify publication and exact retry behavior. A separate **Close task** rejection
was resolved by showing that its handler only clears selection, and the label
was changed to **Close details**. These are explicit proof limits.

[Work, Russian](../evidence/2026-09-07/ux-pivot-work-ru.jpg) ·
[Narrow blocked task](../evidence/2026-09-07/ux-pivot-narrow-blocked-ru.jpg) ·
[Original-target recovery](../evidence/2026-09-07/ux-pivot-recovery-target-en.jpg) ·
[Library](../evidence/2026-09-07/ux-pivot-library-ru.jpg) ·
[Exact Context source](../evidence/2026-09-07/ux-pivot-context-source-ru.jpg).

## Resolved findings and code pointers

| Priority | Finding and fix | Evidence pointer |
| --- | --- | --- |
| P1 | Immutable record age permanently blocked quiet tasks; coherent observation separated from active heartbeat | `src/agent_commons/ui/tracker_reads.py`, `services/execution_plan.py`, `services/work_metrics.py`; observation regressions |
| P1 | Cross-task retry obscured the original launch target; frozen intent and explicit restoration retain exact request | `frontend/work/src/main.tsx`, `components/LaunchRecoveryPanel.tsx`; response-loss browser proof |
| P1 | New Context Pack could erase an uncertain save; pending operation now blocks replacement | `frontend/work/src/components/ContextPacksSection.tsx:97`; Context save recovery tests |
| P2 | Capacity metadata gap disabled a current task; task-specific observation gate retains real unknown/stale blockers | `frontend/work/src/components/TaskInspector.tsx`, tracker state tests |
| P2 | Evidence completeness looked like task completion; domains now have separate labels and readiness copy | `taskPresentation.ts`, `components/TaskInspector.tsx`, `i18n.json` and locale tests |
| P2 | Team target/read-only state ambiguous; named target and write guards added | `frontend/work/src/main.tsx`; component contracts |
| P2 | Creation dependencies inherited run filtering and omitted accepted tasks | Task observation callback and `TaskComposer.tsx`; real browser dependency choices |
| P2 | Metadata catalog needed a strict privacy boundary | `src/agent_commons/ui/context_source_dtos.py:20`, `ui/server.py:672`, `tests/ui/test_context_sources.py` |
| P2 | Navigation/close wording and narrow focus were unclear | Router, shell and inspector contracts; saved narrow screen and DOM focus checks |
| P2 | Generated screenshot metadata overstated invalid dimensions | Corrected browser artifact revision above; unchanged JPEG hashes and independent image inspection |

## Open risk register and next owners

| Priority | Open item | Who or what can close it |
| --- | --- | --- |
| P1, carried forward | L1 is not proven; R2 release evidence open, including retained Linux receipt-scope refusal and Claude/Grok qualification failures | Operator-authorized live verification on the intended source/host/profile boundary, then independent release review |
| P2, carried forward | Grok argv transport privacy | Provider-runtime engineer implements agreed transport contract, regressions and separately authorized external verification |
| P2, decision | Retention policy and actual operator data lifecycle | Product/security owner selects policy; engineering verifies it against the earlier audit |
| P2, validation | Real first-use comprehension and comprehensive accessibility not established by synthetic fixtures | Operator/user sessions, keyboard/screen-reader and actual browser Back/forward checks |
| P3 | Ready provider-auth panel still says “Critical attention required” | State-specific copy cleanup with locale regression |
| P3, opportunity | Density preference, light theme, attention digest | Product owner chooses a small measurable experiment after usability feedback |

No P0 was established by the scoped pivot review. These priorities do not erase
findings in the [September 6 total audit](2026-09-06-total-plan-work-adr-prd-review.md)
or expand the prior [remediation report](2026-09-06-audit-remediation.md)'s claims.

## Lifecycle and handoff

Automatic approval review rejected the proposed `task complete/submit` sequence
under the user's original prohibition on claiming completion instead of review.
No such transition was executed. The safer outcome is exact artifact review,
honest targeted handoff and release of temporary claims. Task acceptance,
commit, push, merge and release remain separate owner actions.

The current ADR/PRD/roadmap deltas describe the bounded branch implementation.
Old architecture programme and product snapshot remain explicitly historical;
this work does not cosmetically rewrite them or declare the entire historical
programme delivered. The canonical review identifies an artifact, not a submitted task. It cannot
be silently substituted for future exact task acceptance review. The reviewer
closed its distinct session after confirming current bindings. Root handoff
identifiers are recorded below.

### Changed files and preserved baseline

The [source manifest](../evidence/2026-09-07/ux-pivot-source-manifest.json)
contains the exact per-file inventory and pivot-versus-baseline hashes.
The aggregate Git diff also includes the earlier September 6 audit remediation;
it must not be attributed entirely to this pivot. At the final documentation
checkpoint Git reports 95 status entries, versus 53 before the pivot; these are
status entries, not a count of individual files in untracked directories.

| Area | Files changed by this increment |
| --- | --- |
| Work UI | `frontend/work/src/main.tsx`, API/contracts, router, task presentation, launch intent, inspector, composer, recovery, Library/source picker, styles and EN/RU strings |
| Backend | `ui/context_source_dtos.py`, `ui/reads.py`, `ui/server.py`, `ui/tracker_reads.py`, `services/execution_plan.py`, `services/work_metrics.py` |
| Regression coverage | Work component/state tests, source catalog and observation tests, provider-auth and generated Work asset contracts |
| Packaged UI | Rebuilt `src/agent_commons/ui/static/work/index.html` and its hashed JS/CSS pair; superseded assets removed |
| Design and plan | ADR 0014 + ADR index, task-first implementation graph, this verification report, four primary plan checkpoints, frontend contract and EN/RU user guides |
| Evidence | Source/test manifests, corrected browser metadata and five JPEG screenshots; two baseline images and design-input metadata |

Worker handoffs are recorded as `handoff.70D8D332B888RVN6ZQ81NTKQA5`
(Astra), `handoff.3EZ0PYZK1JHDHWE1MG99AM0V4M` (Inspector) and
`handoff.3W36N1CJ7S9HHRDA1GS9XZAET8` (Library). Their temporary claims
were released and sessions closed. Tasks remain active, with explicit handoff
rather than silently fabricated completion. The next owner should use those
handoffs and the exact integrated artifact review before advancing lifecycle.

The test artifact records the selected runtime patch versions. Repository
version files constrain Node major 24 and Python 3.14, not those exact patch
versions; the reviewer explicitly checked that distinction.

Central operator handoff: `handoff.6P676VWHRRK5X303THY6KPSGM8` at
`evt.01M1WFFGS7XW6BDHPGG03RP5CX`. It links the four active tasks,
three exact evidence artifacts and independent review. Root closure uses the
supported claim-release and session-end workflow; no canonical file is edited
manually. The retained branch remains deliberately uncommitted.
