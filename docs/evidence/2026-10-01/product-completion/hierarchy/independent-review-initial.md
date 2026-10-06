# G6 early independent source review

Verdict: changes requested. This is source review of the isolated snapshot, not canonical acceptance or a full release review.

Reviewed source root: `/private/tmp/commons-product-g6-20261001`.
Patch SHA-256: `20405870579c434694f46fd3b2af817e94841e916a037c7592fa45c399a8fe77`.
Integration manifest SHA-256: `129efaec1b393f7b263ac643d6b7e155efd7325eebd79e69b773fff8118a4af4`.
All 42 manifest file hashes matched. The excluded WP20 `manager.py` verification overlay matched `36cb00f5742828885e5b297a45125f8a094bd3e830e1e1b418858a801dc62b2a`. Overlay code is a dependency of the tested same-manager race behavior, not part of G6's delta.

## Findings

### P2 — Freeze hierarchy fields under correction, including nested revised changes

Locations: `src/agent_commons/domain/validation.py:592`, `src/agent_commons/domain/revisions.py:10`, `src/agent_commons/services/manager.py:1044`, `src/agent_commons/domain/projection.py:1003`.

G6 rejects ordinary task-kind edits but does not extend the existing structural-correction contract. `CORRECTION_IMMUTABLE_FIELDS` contains `dependencies`, but neither `task_kind` nor `parent_task_id`. Correcting a `task.created` event with an otherwise identical replacement payload plus `task_kind="component"` and a valid earlier `parent_task_id` succeeds. The resulting projected task changes kind and parent with no integrity issue. Its `revision` remains the original creation event; only `effective_revision` changes. This bypasses the declared immutable-kind policy and the parent-edit CAS/idle-work route, including on tasks that ordinarily need reopening before edits. The lifecycle-field check at line 592 does not apply because the corrected event type is `task.created`.

Reproduced in a synthetic private workspace `/private/tmp/commons-g6-review-repro-ku_n8w5c`: ordinary child became component with existing parent through `correct_event`, and snapshot issues were empty. The same structural omission also allows rewriting `changes.parent_task_id` of an existing `task.revised` event; freezing only top-level fields would leave that path open.

Repair: compare hierarchy semantics in the shared correction check used by both write validation and replay. Treat absent `task_kind` as `task`, absent/null parent as equivalent defaults, but reject semantic kind/parent changes. For revised payloads, compare presence/value of `changes.parent_task_id` so a correction cannot add, remove, or change a parent edit. Keep wording corrections working. Add supported correction and imported-correction replay tests, including legacy default equivalence and kind conversion after task lifecycle progression. Parent cycles and missing parents already have canonical validation; this finding concerns changing valid relationships through the wrong operation.

### P2 — Hierarchy writes do not raise the compatibility floor

Locations: `src/agent_commons/services/tasks.py:67`, `src/agent_commons/domain/projection.py:116`, `src/agent_commons/services/manager.py:1265`.

Creating the first component leaves `snapshot.semantics_required == 1`; creating a parented ordinary task or a parent-only revision also has no hierarchy-sensitive writer stamp. Current maximum semantics remains 6, the same as the captured pre-G6 code. The version machinery expressly exists to identify histories that old code cannot correctly interpret, and ADR 0024 states older readers require upgrading.

Verified actual older-reader behavior by copying `src` to `/private/tmp/commons-g6-old-reader-g27r_ab7`, restoring every changed source/schema from the captured baseline, and reading the synthetic hierarchy workspace in `read_only=True`. Its doctor reports `ok=False` with `commons.payload.task.v2: Additional properties are not allowed ('task_kind' was unexpected)`; `snapshot()` raises `ValidationError`. It fails closed; no old-reader silent acceptance, write bypass, or data exposure was observed. Severity is compatibility/diagnostic P2, not a security finding. The recorded floor nevertheless inaccurately says version 1 can read this healthy ledger.

Repair: establish a newer coordinated semantics version for this integration wave and stamp hierarchy-sensitive creation/revision at the universal canonical write boundary, before append. Cover direct `record_event`, not only convenience creation/edit methods. Ordinary default task creation, wording-only legacy edits, failed hierarchy validation, and identical old-create retries should retain their prior floor/request identity. Add focused tests for the floor and existing `ledger_ahead_of_code` projection behavior. Verify actual prior readers separately: a strict storage schema can reject before projection sees a stamp, so do not claim that adding the stamp alone guarantees the human-friendly doctor upgrade message.

## Verified behavior

- 97 focused Python tests passed: task edits (including shared-manager/separate-manager reverse-parent races), task envelopes, task projection, and editor routes. Python came from the main checkout's existing venv; `PYTHONPATH` selected the isolated copy, Commons environment variables were removed, bytecode/cache writes were disabled, and test fixtures wrote under `/private/tmp/commons-g6-independent-tests2`.
- 26 task graph and task creation Node tests passed. This local command used Node `v23.11.0`, not repository-pinned Node 24; treat it as an additional behavioral probe rather than the green contract.
- A real legacy task created by captured pre-G6 source retried through G6 with both omitted defaults and explicit `task_kind="task", parent_task_id=None` returned the exact same event identity.
- Source review confirmed parent validation is iterative and under canonical lifecycle validation, malformed hierarchy DTOs are rejected, dependency edges remain a separate readiness relation, containment focuses/searches before its 128-task cap, and the graph has native list fallback plus arrow/home/end focus handling. Component parent acceptance remains scoped to its own revision and does not aggregate child completion.

## Limits

No main checkout, installed runtime, provider, or canonical project coordination state was changed. No full `make check`, shipped-asset rebuild/parity, wheel/canary, or real browser keyboard journey was run. Frontend behavior review used actual source and the server-rendered test harness, so live DOM focus preservation and browser SVG rendering remain final integration checks. The checked hash set predates any repairs and any later byte changes need re-review. Test logs: `/private/tmp/commons-g6-independent-tests-output.log`, `/private/tmp/commons-g6-independent-node.log`.
