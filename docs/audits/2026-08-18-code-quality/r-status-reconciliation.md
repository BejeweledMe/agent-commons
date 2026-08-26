# R-status reconciliation: A3–A8

**Observed:** 2026-08-26

**Code observation:** `ac3e3075f63d5e838a09b743fe9f15ec2a37a212`

**Current reconciliation task:** `task.66DBAD3V94M3481B9ETQADEG2X`
**Supersedes as a status view:** the 2026-08-25 table whose source baseline was
`ae258349bf4e60ded6611627f4a9c62a3d3fdfae`.

## Purpose and method

The audit plan intentionally leaves A3–A8 unchecked; it is not a live delivery
board. This page reconciles that plan with the projected task ledger, exact Git
revisions and independent review records available on 2026-08-26. It does not
change the audit's target architecture or make product-semantic decisions.

`accepted` below is a ledger state, not an inference from commit reachability:
it means the task has a current accepted subject revision and an explicit
independent acceptance review. An older review can legitimately be marked
`stale` after a remediation, submit or acceptance event advances the task
revision. That stale record remains historical evidence; it does **not** undo a
later acceptance, and it must not be reused as approval for a newer source
revision. Conversely, a reachable commit or a green check never substitutes for
an exact independent review.

The old reconciliation correctly recorded the then-open review queue. Its
statements that every A3–A7 row lacked a requested review and that the first
permitted source write was `mcp/entrypoint.py` are historical only. They are no
longer current operating instructions.

## Current verified status

| Audit slice | Current ledger status | Exact accepted evidence | Consequence |
| --- | --- | --- | --- |
| A3 role domain | **accepted** | Remediation `10c752ff470226297289b58ae08518eaf9fb90aa`; `review.6K4Z0ZFG503PJKFKFKK2NQ1BRC` approved it after the earlier exact review found the missing legacy export. | `domain/roles.py` is the canonical home while `domain.agents` retains the compatibility binding. Do not repeat the original `784ead96` review queue. |
| A4 MCP scoped reader | **accepted** | `38c8f43d9a86be1bc1dbd5145a2cee365f7759af`; `review.3CZ8SP3BSJR9V0EN74HP85JKZ5`. | `ScopedRepoReader` has its target seam and compatibility import. It did not authorise a worker-scope or tool-catalog semantic change. |
| A4 UI reads/actions | **accepted** | `a455200a6ca01d6fa9801f0ff12aeb90d25a7ac3`; `review.5CV625ZZ4MB5A8EHYX8D3WA0QV`. | `UIContext` is a compatible cache/composition facade; subsequent panel workflows belong in dedicated modules, not new `UIContext` methods. |
| A4.5 instruction composition | **accepted** | `e4134a1b781a9ee6aae363b2ae6224f42c1462b8`; `review.3QNN4JPRDA5Y0PSRSVBARY5M4Q`. | This fulfils accepted `decision.0E25PERWJMD1PGRHS3K4B6QQZR` as a structural seam only. Context Pack injection remains later behavioural/data-semantics work. |
| A5.1 delegation/maintenance envelopes | **accepted** | `7a3e0f7ccc107104e615b5baaa12945d6c139a36`; `review.7DDNY7B0N75NX5DB6XTDMYYV0C`. | The selected post-validation boundary is typed without changing canonical JSON or public wire shape. |
| A5.2 task/review envelopes | **accepted** | Remediation `35d9f28596b42c884316f9e2691ddee6964bd851`; `review.38MR8VNM2P30SXG8NH34ZEFCQE`. | Direct parsing now reuses the existing fail-closed domain and schema validation; the original cast-only defect is closed without a schema, lifecycle or facade change. |
| A5.3 thread/handoff envelopes | **accepted** | Historical structural commit `42a22c9da9839e873366eb32d522c853862ebe2e`; `review.5X71Z06J1NFRGR268F0QHMV2X1`. | The task is no longer merely "submitted without review". Further A5 families still need their own current status rather than being inferred from this acceptance. |
| A6.1 profile | **completed, not accepted by this reconciliation** | `task.6JZP6J57782Y8ERHT2K140DJ13` has stale revision-bound artifacts. | Do not cite it as current exact evidence or use it to start an optimisation. It is historical input only. |
| A6.2 verified-read optimisation | **accepted** | `27b6eaaded0585a51e791cd8928a2cede261bcfb`; exact source review `review.5EBPVVX9HPY7QEE1EVGD7XBPPJ` and current submit delta review `review.708J72RENBVAJSVFBNT709503M`. | The proven duplicate verified-read materialisation was removed while hashes, ordering, fixed-point replay and persisted data remained unchanged. |
| A6.3 immutable replay profile | **accepted** | Evidence commit `baa8ff320e4f85b4776a1db1579a1be6d1e48d5f` plus rounding correction `55d3fb92d03392d3eeb2037fa19ab594a7d00a29`; current acceptance review `review.0SH7S3YK5V5B09X0DAQ4Z1SHAE`. | The isolated measurement identifies `project_events()` as the dominant measured component. It is evidence for characterization, not a latency SLO or an optimisation approval. |
| A6.4 replay-phase characterization | **accepted** | `9bf19d73ed79681b4128e7cc1cf5d392424fa347` plus context-local correction `95a9de788bc3c0d68c6d005f8f56c4c5a4ba8698` and evidence `ac3e3075f63d5e838a09b743fe9f15ec2a37a212`; `review.7JNES0FWDDCKHBWHGCGWSCMRKX`. | The evidence attributes the largest named exclusive phases to envelope parsing, transition validation and effective-event application. It permits only a separately scoped investigation of safe typed-envelope reuse; no production optimisation has been accepted. |
| A7 UI DTOs | **not cleared by this page** | Earlier A7 task records remain completed rather than accepted in this status sweep. | Do not treat the A5/A6 acceptances as an A7 or A8 gate. Reconcile each intended A7 task at its current subject revision before using it as a prerequisite. |
| A8 collaborators | **not started** | No accepted A8 task or commit. | It remains downstream of the remaining typed-projection/public-DTO work and must not be used to add `CommonsManager` methods or bypass the facade migration. |

The presentation extraction (`e3cd12d`) and UI launch coordination commits
(`0e8fb17`, corrected by `fb6fb71`) are later structural evidence, but they do
not replace the task/review records in the table and do not resolve either MCP
or CLI decision below.

## A6 evidence boundary

The accepted A6.3 snapshot measured the three verified-read components
separately; it found `project_events()` materially larger than `sync()` and
`read_projection()` on that immutable sample. A6.4 then measured named replay
phases using benchmark-local instrumentation, including a context-local guard
so a concurrent outside thread calls the original helper and cannot corrupt the
collector. Both documents explicitly state that the detached snapshot's
receipt-scope diagnostic is not a green `doctor`, that whole-run `tracemalloc`
is not phase allocation, and that component medians cannot be summed into a
user latency.

Therefore the next A6 code change is **not** implied. It needs a fresh,
separately claimed task proving that any envelope reuse is pure for the relevant
correction revision, with probe/normal/final-pass fixtures and unchanged event
order, validation, correction semantics, fixed-point result and persisted JSON.

## Decisions still required before the next A4 source slice

### 1. Reconcile the CLI freeze with `7f91803`

`decision.4TZDDRT5PF84KXV6RHQAPTG5BX` is accepted: the CLI is frozen per
capability, except security fixes, and bootstrap commands remain live only
until the panel owns workspace initialisation and session lifecycle. Its stated
consequence is that decomposing `cli.py` is spent work.

The historical A4 CLI package/workspace commit
`7f91803fe7a4d5ef2440b8832fbe15a9d0d0725b` nevertheless exists after that
decision and was recorded by the former reconciliation as unfinished work. The
conflict cannot be silently normalised by calling it an accepted audit slice.
The owner must choose one of these outcomes:

1. **Grandfather with a narrow explicit exception:** record that the existing
   package/workspace seam is retained solely for still-live bootstrap support,
   and forbid further CLI decomposition or feature growth until the panel owns
   those capabilities.
2. **Revert the structural split:** return the CLI layout to the frozen surface
   and defer any package seam until a later owner decision reopens it.
3. **Supersede the freeze decision with a bounded new policy:** state exactly
   which CLI work remains permitted, its user-facing compatibility window and
   when the exception ends.

Until the owner chooses, no new CLI structural writer may treat `7f91803` as
approval or build on it.

### 2. Reconcile MCP binding with `WorkerScope`

The audit plan orders MCP work as `ScopedRepoReader` → entrypoint →
`WorkerScope` → thematic tool registration. The target structure report also
names `mcp/binding.py` for canonical worker-to-delegation binding before it
describes `mcp/scope.py`. The accepted scoped-reader extraction and the later
entrypoint commit do not choose between those two ownership boundaries.

Before a writer creates `mcp/scope.py` or moves `register_*` functions, the
owner must select and record one of these designs:

1. **Binding-first:** extract the canonical worker/delegation binding into
   `mcp/binding.py`, then inject its typed result into `WorkerScope`.
2. **Scope-owns-binding:** make binding an explicit, tested constructor
   responsibility of `WorkerScope`, and amend the target map so there is no
   promised `mcp/binding.py` seam.
3. **Defer scope/tool extraction:** retain the present server closure until a
   separate design review settles the contract.

All three must preserve MCP tool names, schemas, worker grants, catalog
handshake and terminal-delegation checks. This is an architecture ownership
choice, not a reason to introduce a task-dependency DAG or product feature.

## Remaining operating order

1. Treat the accepted rows above as closed historical audit work; do not reopen
   them merely because pre-remediation review records are stale.
2. Reconcile still-completed A5/A7 records individually against their exact
   task revisions before relying on them. A batch `review` label from the old
   table is insufficient.
3. Obtain the two owner decisions above before any `WorkerScope` or MCP tool
   registration extraction. Keep `mcp/server.py::build_server`, `CommonsManager`,
   `cli.py` and `UIContext` from growing in the meantime.
4. Continue A6 only through the separately scoped characterization gate above.
   Structure and behavioural optimisation remain separate commits.
5. Start Context Pack injection, Gallery evolution or A8 collaborator migration
   only after their own approved plan gates; none of the accepted structural
   slices changes persisted event semantics or supplies feature authorisation.
