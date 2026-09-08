# Project workspaces: implementation and verification

Status: **implemented, independently source-reviewed and locally installed;
L1/R2 remain open**. This pre-merge evidence record tracks the
project-host implementation on `codex/project-workspaces-program` and separates
implementation, hermetic tests, independent review, browser evidence and release.

## Baseline and scope

Baseline HEAD and local `origin/main` were both
`605c0d7c9c5965c0a8303550056d07ce63afa697`. The initial tree contained three planning
changes: ROADMAP, implementation program and the new project UX plan. No unrelated
source changes were present. Work is authorized on a separate branch; commit,
merge and cleanup follow verification.

The deliverable is a top-level project list, UI create/connect/rename/archive,
project-bound Work and Gallery APIs, independent writers and runtime ownership,
and separate browser drafts. Existing service skills, specializations and five
blueprints remain shared definitions with project-local instances. API/local
models, SGR and conversations remain a future wave under ADR 0018.

References: [execution plan](../project-workspace-ux-plan.md),
[ADR 0017](../adr/0017-project-scoped-service-host.md),
[ADR 0018](../adr/0018-role-connections-and-execution-modes.md).

## Evidence and confidence

| Evidence | Result | Limits |
| --- | --- | --- |
| Baseline `doctor`, in-repo CLI and explicit repository state scope | Healthy: 3,054 events, zero integrity issues, 136 historical warnings | Point-in-time baseline; external projection access requires the host permission boundary. |
| Orientation, inbox, task list and claims | Read; separate implementation tasks and sessions established | Historical open tasks are not completion evidence for this wave. |
| Registry v1 manifest `ee758689913243027104d5ea0c2c35bcb9c43fc71392248a69ac58661eaeabe6` | Independent review: changes required | Four source/test files, immutable snapshot; superseded for implementation review. |
| Registry v2 manifest `c49f9846c31d5c1207b273936f28051e86356e00576c47d8476dc0ee18073988` | Independent review: changes required | Fixes closed some v1 findings, not the registry gate. |
| Registry v3 manifest `76dc2819ffb631c2d8e35c628c86df11e462bb5ed99b57fdffd54221865b94fd` | Independent review: changes required | Remaining state-binding check, persisted-path validation and initialization crash window; remediation underway. |
| Host v1 manifest `32baa804a232c7d0e40e31190ae662802d2bfb8eaf0b9cff818638695fd85423` | Independent review: changes required | Startup, read-only, launch lifetime and bounded request-body findings. |
| Host v2 manifest `60f737071cbb520959589d25aaed222b7b299b8f7bc789375ce5801220bd56ec` | Independent review: changes required | Read-only library routes and actual shutdown callback ordering remained unsafe; superseded by v3. |
| Registry v5 manifest `84412c02dd947b070ee103fadd63f8c3669535f891228b21b1e9b8df5551ea78` | Independent review approved; 27 focused tests passed | Ambiguous initialization requires explicit inspect/connect; not automatic adoption. |
| Host v3 manifest `ebe0191effbbb8277bcc3f54c7208f2a89dab2c208986eb9c7830e706bdd8398` | Independent review approved; 46 selected tests passed | One real socket test was sandbox-blocked. Later CLI test formatting needs final-source confirmation. |
| Frontend v1 manifest `42ca69067c710db40f14cafce2da3018c30e032dec4cb13c77c60c9af792eddc` | Independent review: changes required | Actual callback harness reproduced A→B→A task retry stuck busy and pending Context Pack/Blueprint remount stuck busy. |
| Frontend v3 manifest `cb625418ecf5aa847ccbda93e45d9a55175c295cba7170fe3dc99ec05746a3e2` | Independent scoped review approved | Actual callbacks, real project/read generation helpers, pending Library remount and obsolete completion verified; not independent Chromium evidence. |
| Initial actual CLI + Chromium, isolated synthetic Alpha/Beta workspaces | Found Alpha's label with Beta's tasks and a stale busy state | Failed historical run; superseded by the corrected final journey below. |
| Frontend v4 manifest `8bb5d564487943107b0dd4c7038c62526cf1cd56cc57ba8aef3d9ec5cc1e21ad` | Independent scoped review approved | Fixed a further P1: tracker EventSource used an unqualified startup-project route. Actual callbacks and late SSE handling checked; not independent Chromium evidence. |
| Final source manifest `2acd1c56321425eda70ea671e9ab2f746dab34115889dff8d27cc3d424874aaf` | Independent scoped source review approved; all 45 changed/deleted paths matched | 35 selected tests passed; six tests could not run in the snapshot without TypeScript dependencies. Parent full gate remains required. |
| [Final browser verification](../evidence/2026-09-08/project-workspaces-browser-verification.json) | 12-step real CLI/Chromium journey passed: create, task, switch/draft/retry, rename/archive/restore, RU mobile, actual Gallery upload/publish and project isolation | Synthetic fixtures, no provider execution. All observed task streams project-qualified. Lead integration verification, not independent acceptance. |
| Receipt diagnosis, supported plain `receipt reconcile`, fresh `doctor` | Five missing derived receipts restored; 3,073 events/receipts, zero integrity issues, 136 historical warnings | Ordinary canonical writes resumed only after fresh healthy doctor. No manual history edits, adoption, rollback or abandonment. |
| `git diff --check` | Passed during implementation | Must be repeated at final freeze. |
| First full `make check` | 10 failed, 2,093 passed, 13 skipped; 838.72 seconds | Nine stale frontend contract assertions/mocks and one asset directory removed during concurrent Vite rebuild. Not a final frozen-source gate; repairs and full rerun required. |
| Final frozen-source `make check` | Passed: Work 132, Gallery 27, Python 2,103 passed / 13 skipped; 868.93 seconds | 12 skips concern withheld automatic governance grants; one is a CI-only environment check. Two upstream Starlette deprecation warnings. Node 24.20.0; Python 3.14.5. |
| [Wheel and release verification](../evidence/2026-09-08/project-workspaces-release-verification.json) | Wheel built, installed with `mcp,ui` extras and locked constraints; all 12 browser steps passed outside checkout | Initial temporary install omitted the UI extra and correctly refused startup; corrected install passed. No provider calls. |
| Canonical independent review | `review.4A9YS62RHV353KPM4RK93BY59P`, approved at `evt.01M206BABSRQBZYD9VC34KYFGZ` | Exact artifact revision `evt.01M205ZD3CA8WY70GJHMWP947G`; distinct reviewer session closed. Does not accept tasks or qualify providers. |
| Installed local service | Previous host drained after confirming no requested/active/input-needed delegations; new wheel running on loopback port 8787; browser sign-in opened without persisting its code | Runtime ownership belongs to the new host. A restart does not establish provider reattachment. |
| Repository cleanup | Preview and removal of owned `build/`, `.pytest_cache/`, `.ruff_cache/`; replaced obsolete Work/Gallery generated bundles | Canonical history, review provenance and required runtime assets retained. |
| Remote PR/main CI and Git delivery | Verified separately in the associated GitHub delivery checks | This file records local pre-merge evidence; it does not claim a future CI result. |

Manifests are under `docs/evidence/2026-09-08/`; their hashes identify exact
review inputs. Source assessments were performed by requested Astra reviewers;
implementation workers used requested Terra models through Codex collaboration.
The collaboration API does not attest the model identity. Root browser checks
are integration evidence, not an independent review of root's own changes.

## Risk register and checklist

Registry v5 and host v3 close the registry and host findings below within their
exact reviewed scope. They remain listed to explain the regression contract.
Frontend v3 closes the retry/pending-save findings in scoped independent review;
v4 closes the unqualified tracker stream. The final browser journey also verifies
project-isolated Gallery publication and readable dark-mode upload controls.
The full release/live gates remain open.

| Priority | Finding / risk | Owner and closure criterion |
| --- | --- | --- |
| P1, closed in reviewed source | Late requests, retries, loading transitions or an unqualified SSE route can show or dispatch project A work in B | Frontend remediation `task.7D9HNAQ60G192XA1ECKRR64DGY`; immutable project bindings, separate read/mutation epochs, late-callback guards, scoped EventSource, independent v3/v4 review and final browser evidence. |
| P1 | Registry loading must not turn one moved checkout into a service-wide outage | Registry owner: lexical validation of persisted receipt paths, separate current filesystem binding checks; prove one unavailable project leaves others usable. |
| P2 | A fresh retry key must not bypass changed state-directory identity | Registry owner: exact state identity checked on every duplicate and receipt branch. |
| P2 | Crash after Commons initialization but before its progress receipt can strand a new project | Registry owner: owned, recoverable initialization with no adoption of an unrelated checkout; inject the exact crash boundary. |
| P1 | Read-only or first-run host composition may accidentally retain write routes or fail startup | Host owner and independent reviewer: complete route inventory with writer-capable fixtures, no canonical POST in read-only mode, and actual first-run CLI regression. |
| P2 | Launch start failure or shutdown preparation race can lose ownership or capacity | Host owner: close all admission before draining, track preparation, finalize unlaunched work and drain started threads before closing sessions. |
| P2 | Oversized chunked project requests can allocate unbounded memory | Host owner: streaming 16 KiB request limit, independently checked. |
| P2 | Project names, archived work and failed mutations may lack usable recovery | Browser QA: align backend/UI bounds; visible inline errors; rename/archive/restore and Gallery links retain project identity in RU/EN and narrow layouts. |
| P1 gate | L1/provider qualification is not established by project tests | Operator plus provider verification owner; explicit authorized live attempts and exact sanitized evidence required. |

Must fix before this wave is released:

- [x] Close the remaining registry findings with exact independent re-review.
- [x] Close host review findings and verify complete routing/lifetime contracts.
- [x] Reject stale success, failure, retry, secondary-await and `finally` callbacks
  in the reviewed project epoch/intent paths.
- [x] Preserve per-project task/context/blueprint drafts and isolate loading state;
  remounted uncertain writes retain their original retry identity.
- [x] Pass browser create/switch/task/retry, rename/archive/restore, real Gallery
  upload/publication, RU narrow-screen and SSE project-routing checks with
  synthetic data. Extended keyboard/history usability testing remains follow-up.
- [x] Pass full `make check`, reproduce frontend assets, build/install the wheel
  and review the final exact source revision.
- [ ] Observe green PR/main CI and merge only the checked head (Git delivery gate,
  reported with exact run IDs separately from this pre-merge record).
- [x] Inspect cleanup candidates by provenance; preserve canonical history and
  meaningful evidence, remove only owned temporary or reproducible duplicates.

Should fix soon / growth:

- [ ] Measure cold/cached switch latency and idle project resource use.
- [ ] Evaluate an OS-native folder picker after the safe inspect/confirm flow.
- [ ] Validate discoverability and project destination with the operator.

Needs decision or external verification:

- [ ] Fable consultation: automatic approval review rejected sending the proposed
  private source files; an explicit file-scoped permission request is pending.
- [ ] One Codex canary: automatic approval review requires separate permission
  for the quota-consuming attempt. No live canary has run in this wave.
- [ ] Future API/local/SGR decisions follow ADR 0018's capability and safety gates.

## Explicit L1 status and plan deltas

L1 and R2 remain open. Installed/auth-ready/preflight-ready profiles are not
live-qualified profiles. One Codex canary, if authorized and successful, would
prove only its exact scoped attempt; it would not qualify all six CLI profiles.
The proposed 300 seconds is the bound for that test, not a universal agent-task
limit. Fable's assessment is also pending; no four-model consensus is claimed.

Primary plans now identify the project wave as implemented and locally verified,
link ADR 0017 and its implementation DAG, and retain API/local/SGR as future work.
ADR 0014's historical deferral of the project switcher is superseded for this
scope by ADR 0017, without rewriting the historical decision. Earlier source
manifests in this wave are stale for approval but retained as finding provenance.
No completed, accepted or released status is inferred from worker messages.
