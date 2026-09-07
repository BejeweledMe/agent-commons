# Service Library and live workspace verification — 2026-09-07

This increment implements ADR 0016 on `codex/service-library-workspace`, from
clean baseline `5b44a2b6af5a485181884e691caaf2b07c7e85a4` (HEAD and origin/main).
The original total audit remains [historical evidence](2026-09-06-total-plan-work-adr-prd-review.md).
This report covers the subsequent authorized implementation, not a repeated
claim of acceptance for old J1/G6 evidence.

## Scope and result

The wheel contains 45 complete professional skill trees and 30 provider-neutral
specializations. Custom skills/roles use private immutable service versions;
hired instances pin those versions while independently choosing profile/model.
Five blueprints create 24 task templates in total, with a required product brief,
real dependencies and suggested roles. Applying a blueprint does not launch work.
Work has a live editable dependency graph. Gallery imports PNG/JPEG screens and
publishes revision-bound Design Packages through the existing artifact contract.

The approved visual direction is retained. The supplied graph reference informs
vertical ordering, short labels, explicit states and a legend. No fake progress,
ETA, model-token stream, automatic task acceptance or automatic DAG scheduler is
introduced.

## Evidence

| Check | Evidence and boundary |
| --- | --- |
| Packaged source provenance | All 45 source `SKILL.md` hashes match the supplied catalog; closure digest `006ba1e8449752187293d206acb70f1be4705ce0cd8e95d919e40254591564bd`. Source attribution is in `resources/library/NOTICE.md`; no new upstream license is invented. |
| Exact source review | Non-author Astra reviewer approved 526 changed/new source, resource and test files. Sorted source-manifest SHA256 `eb8dc35aa947a181cbc50a8a51fb794258d17ab351870232caad50e727a7d12f`. Docs, canonical state and generated bundles excluded; checked separately. |
| Independent regressions | Gallery Python 9 passed; final Team/library Node 24 tests 22 passed. Reviewer independently resolves all 45 skills and composes all 30 roles, max initial instruction 40,146 bytes. |
| Installed package | Isolated wheel install with `[ui,mcp]`, Python 3.14, outside checkout: 45 skills / 30 roles / 5 blueprints; all exact resources resolve; both UI assets present; read-only inventory creates no private library state. |
| Packaged browser journey | Create custom skill and specialization; hire Claude and Codex instances of the same definition; apply web blueprint with retained brief; edit/add/cancel tasks; upload, publish, load an actual 1440×1000 preview; bind exact Design Package and observe a successful fake-provider run through SSE. |
| Recovery and accessibility | Gallery injected 409 after a successful write: two requests, same key, same task; task lost-response retry survives closing/reopening its inspector. EN/RU, keyboard graph navigation and 390 px viewport checked; document width remains 390 px. |
| Runtime bridge | `tests/mcp/test_library_bridge.py`: six real local stdio MCP cases, three provider configurations × explicit/XDG library root. Exact retained companion reads succeed; unrelated methods are refused. No provider model process is executed. |
| Role authority | `tests/services/test_roles.py`, `tests/mcp/test_service_library_skills.py`, `tests/runtime/test_library_runtime.py`: child-session hire refusal, exact pins, safe scope, progressive reads and fail-closed resolution. |
| Blueprint semantics | `tests/ui/test_library_blueprints.py`: all five plans, full brief, exact intent, partial retry and cross-blueprint conflict. |
| Task graph | `tests/services/test_task_edits.py`, `tests/ui/test_task_edit_routes.py`, `frontend/work/tests/task-graph.test.mjs`: bounded dependencies, cycles, stale edits, retry, active task/review/verification guards, keyboard graph and retained inspector drafts. |
| Image provenance | `tests/ui/test_gallery_upload.py`, `tests/services/test_artifact_expected_content.py`: invalid image/no writes, exact digest/size, symlinks, changed retry, partial recovery and metadata-only canonical records. |
| Repository integrity | Pre-merge doctor `ok: true`, zero integrity issues. 136 historical warnings remain, matching the initial audit: stale evidence and an existing orphan manifest are not silently erased. |
| Full green contract | Final frozen `make check` passed: Ruff and formatting; Work **121/121**, Gallery **26/26**, Python **2065 passed / 13 skipped**, 835.88 s. Both packaged bundles reproduce from pinned frontend source. Earlier stale-bundle failure was corrected; an intermediate run was stopped for the Team fix. |

Raw test logs, browser fixture data, source manifests and screenshots remain
outside the repository. Packaged skill resources are explicitly requested product
content, not proof logs. Auth paths, private operator configuration, credentials,
provider output and user transcripts are not evidence artifacts.

## Findings fixed during implementation and review

| Priority | Defect | Correction / regression |
| --- | --- | --- |
| P1 | A worker could inherit a different service-library root in generated provider MCP configuration. | Explicit trusted root propagation and real stdio bridge tests for all three configurations. Root never appears in canonical metadata. |
| P1 | Image source could change between import storage and artifact registration. | Compare expected SHA256 and size with the single generated manifest before its event; deterministic replacement regression. |
| P2 | Gallery discarded retry identity for 409 after partial import writes. | Guaranteed pre-write input refusal is 422; partial/unknown outcomes preserve original request. Storage-refusal retry completes the same task. |
| P2 | Blueprint and Team profile/provider changes retained the prior explicit model. | Both default/per-slot blueprint choices and Team choices clear overrides atomically; tests verify actual hire request omits the old model. |
| P1 | Existing Gallery parser rejected real `session.<uuid-hex>` producer IDs, so publishing worked but the board failed. | Strict session-only UUID support; actual import → publish → backend DTO → TypeScript parser regression. |
| P1 | Existing Gallery CSP blocked the verified preview's blob URL. | Add `blob:` only to Gallery `img-src`; retain script restrictions, revision checks and object-URL revocation. |

No unresolved actionable issue remained in the independent source-review scope.
This is a scoped review, not a claim that every repository behavior is flawless.

## Checklist and remaining boundaries

- [x] Bundle and resolve all 45 methods and 30 specialization definitions.
- [x] Use service-owned exact references, immutable custom versions and scoped MCP reads.
- [x] Create five editable role/task plans with a user-supplied product brief.
- [x] Implement graph edits, dependencies, cancellation and observed SSE updates.
- [x] Import Gallery screens with exact source integrity and safe retry identity.
- [x] Obtain independent source review and isolated full-package installation proof.
- [x] Finish frozen full gate and browser verification.
- [ ] Merge with green PR/main CI; exact Git/CI state is the integration record.
- [ ] L1: separately authorized live qualification on exact provider/host/source versions.
- [ ] R2: release owner assembles exact release evidence and rollback rehearsal.

Next work requiring an operator: qualify actual configured providers, including
the existing Claude/Grok failure boundaries and Linux receipt bootstrap; resolve
capacity/configuration refusals through Settings. The six runtime profiles are
builder/reviewer configurations for three providers, not six role definitions.

Product opportunities: browse Library before workspace initialization; localized
translations for source-catalog role prose; large-graph navigation above 128 nodes;
progressive disclosure of exact provenance in the Gallery inspector;
deliberate scheduling policy before an automatic multi-task runner. Current UI
supports selecting and launching individual tasks. Historical evidence warnings
need revision-specific re-review or explicit archival policy, not file deletion.

## Plan deltas and source of truth

ADR 0016 records the implemented boundary and ownership graph. The primary
implementation program, roadmap and current-product document receive bounded
links/status changes. RU/EN service-library guides describe the real UI journey.
Older UX and J1/G6 screenshots/manifests remain historical; their approval is not
promoted to this source revision. The August product table is explicitly dated.
No immutable Commons records are manually edited or removed during cleanup.

## Completion record

The final wheel has been installed with `uv tool install --force` and the
`ui,mcp` extras. The source-checkout entry command remains
`uv run agent-commons ui`; no provider account or private configuration was
replaced. Browser fixtures use a fake runner and synthetic qualification records
in isolated temporary workspaces; they are not evidence of live qualification.

The full gate passed before committing. The review manifest was rechecked:
526 files, zero drift. Twelve skips concern withheld automatic grant semantics;
one local-only skip is the CI-environment assertion. CI requires Node and runs
the same `make check` target.

Git integration must retain this source scope, pass PR and main CI, and leave a
clean checkout. A merged PR is an integration event, not L1/R2 qualification.
