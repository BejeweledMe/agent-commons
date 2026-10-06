# G6 integration report — after independent review repairs

Implemented in `/private/tmp/commons-product-g6-20261001`, isolated from main.
No main canonical/config/dependency writes, installed-tool changes or asset rebuild.

## Artifacts and integration boundary

- `before-review/{g6.patch,integration-manifest.json,REPORT.md}` preserves the
  exact pre-review integration artifacts.
- `g6.patch`: 45 paths against the exact original captured source/WIP baseline,
  **not git HEAD**. Includes the whole G6 implementation and correction repairs.
- `g6-manager-semantics.patch`: separate small universal-writer stamp change,
  against the already integrated WP20 manager.py, with **no duplicated WP20 delta**.
- `integration-manifest.json`: every before/after SHA, four new files, and the
  separate manager patch's exact base/result hashes.
- `baseline/` and `base-hashes.json`: captured originals/hashes for ordinary G6 files.
- `manager-wp20-base.py`: exact manager base for the separate stamp patch.

Manager base SHA-256:
`36cb00f5742828885e5b297a45125f8a094bd3e830e1e1b418858a801dc62b2a`

Manager result SHA-256:
`a950df257bfc88b6ae5adc150c0aa737a4d4a27ec8a999eb18b7e77701b55b89`

Both patches were applied to a fresh reconstructed baseline in
`patch-verification-reviewed/`; all 46 resulting file hashes match the manifest.
Do not overwrite shared files wholesale. G5/G7 may share server.py, mcp/server.py,
api.ts, contracts.ts, i18n.json, main.tsx and product/user documentation. No output
API or OutputsPanel change is included.

New files are the hierarchy layout/component, ADR 0024, and
`tests/services/test_task_hierarchy_corrections.py`.

## Implemented behavior

- Explicit immutable task/component kind and nullable editable parent task ID.
  Components share planning-task criteria/lifecycle, not source-folder semantics.
- Parent existence/cycle checks under canonical validation, typed schemas and
  envelopes, legacy unparented/task defaults with payload fidelity, CAS/retry
  editing, root-only MCP create/edit, CLI creation flags and guarded task edit.
- Work component/subtask creation, parent editing, separate Structure/Dependencies
  map choice. Bounded containment diagram has a project root, component/task
  blocks, canonical state, dashed containment edges, search and branch/ancestor
  focus before its 128-task cap, zoom/fit, arrow/Home/End navigation and native list
  fallback. Filtered parents are labeled and never invented as project-root edges.
- Dependencies alone determine dependency readiness. Child completion never
  accepts a parent. Parent acceptance concerns its own exact revision/evidence,
  not an aggregate of descendants. Cancellation retains children and edges.
- ADR 0024, frontend/product contracts and EN/RU guides describe these boundaries.

## Independent review findings repaired

P2 structural corrections: the shared helper used by supported writes and replay
now freezes kind and parent. Creation's absent `task_kind` equals `task`; absent
parent equals null. Nested `changes.parent_task_id` compares both presence and
value: absent means no edit, null means detach. Wording corrections remain legal.
Tests reject kind/parent changes after lifecycle progression, add/remove/change
of nested parent edits, and imported corrections with valid targets/digests. Tests
also prove equivalent default corrections work in both directions.

P2 compatibility floor: supported replay version is now v7. At the universal
writer boundary, validated component/parented creation and revisions containing a
parent edit raise the floor before append. Failed validation, normal/default task
creation, wording-only revisions and exact old-create retries keep their previous
floor. Direct record_event tests prove the stamp and identical retry behavior.

A projection test exercises the existing `ledger_ahead_of_code` guard with a v6
reader. This does **not** promise a friendly doctor upgrade message: the independent
review's actual captured old reader failed closed on its strict task schema before
projection reached the stamp. ADR 0024 states this explicitly. No silent old-reader
acceptance or data exposure was observed or is claimed.

## Final verification

- `review-final-python.log`: **351 passed**. Includes new correction/floor tests,
  all manager tests, task edits and same/separate-manager races, conversations,
  task envelopes/projection, full domain projection, UI routes/DTOs, tracker,
  root MCP, schemas, docs map and the new CLI contract check.
- `review-final-node.log`: **405 Work tests passed**, Node 24.
- Work TypeScript `tsc --noEmit -p frontend/work`: passed on Node 24.
- Ruff `check src tests`: passed; format check: **751 files already formatted**.
- `review-fixes-python.log`: initial **14 focused correction/floor tests passed**.

Python selected this isolated copy through PYTHONPATH, used the existing venv
read-only, disabled bytecode writes and unset inherited Commons state/session/
delegation variables. No provider/authentication or external ledger was used.

The final boundary includes the WP20 synchronization fix already integrated by
root plus this separate manager semantics delta. Preserve the hierarchy race
tests: the shared-manager case found the original cross-thread reentrancy defect.

No full make check, Gallery/build parity, wheel smoke, broker canary, provider run,
live browser journey, approval of these repaired bytes, or human acceptance is
claimed here. Root remains the single asset integrator and owns those final gates.
