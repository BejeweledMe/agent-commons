# Final G6 integration review

Verdict: no remaining actionable findings in the bounded G6 integration scope after the root's composer repair. An initial P2 was found and fixed during this review, as recorded below. This is independent source/integration review, not canonical review, task acceptance, or confirmation that the whole release is green.

Reviewed main checkout: `/Users/dmitrijersov/Desktop/vibecoding/agent-commons`.
Source was initially root-frozen 9b plus intentional shared integrations. Only `frontend/work/src/main.tsx` changed among the original 46 bound source/test paths during this review, for the repair below; the root also added its regression test. The final binding contains 47 exact source/test hashes. Aggregate SHA-256 of the compact sorted JSON hash map: `bd168e393943edefa3ec232fd583efa8de17baacfefd27a02f2d4b9fcdbcc130`. The full map is below and in `/private/tmp/commons-g6-final-source-hashes-post-fix.json`.

## Finding discovered and resolved

**P2: confirmed task creation retained the saved draft.** Initial location: `frontend/work/src/main.tsx:1024`; submitted hierarchy fields at line 1002. The submitted input included normalized `taskKind` and `parentTaskId`, but the deferred success updater compared a JSON object containing only title, description, criteria, and dependency IDs. They could never match, so the saved draft remained after any successful task creation, including a default task. Reopening New task restored the prior title/content/kind/parent and allowed an unintended duplicate creation with a new key.

Initial `main.tsx` SHA-256: `1c08075cee0e5a7195cda98484a2a48414af531dbb0939f55b02e5cc33bb53d0`.
Fixed `main.tsx` SHA-256: `ad06e369886627234badbf69386d5ff510a9559852f28d30211a1574ee28ceae`.
New regression test SHA-256: `9a93d5762d1fdba25a08e3b13f3914c2be2555170dc09b1c8a25b02be33c54d8`.

The root's repair at lines 1024–1029 includes `taskKind: current.taskKind ?? "task"` and `parentTaskId: current.parentTaskId ?? null` in the same normalized object/order as submission. This clears only the draft corresponding to the confirmed request. It retains a newer draft if the operator changed title, kind, or parent while the response was pending. Existing project-generation and uncertain-outcome guards remain intact.

Independently executed the new test with pinned `/opt/homebrew/opt/node@24/bin/node` (24.20.0). **8 passed**: default/component/child reset, late confirmation preserving edited title/kind/parent, lost response retaining draft, and previous-project confirmation preserving the current project's draft. The harness extracts and executes the production `submitTask` coordinator, including its deferred updater. Ran the same test against the captured pre-fix production source in a new private directory: the **three reset cases failed**, proving this harness distinguishes the reported regression. Logs: `/private/tmp/commons-g6-final-draft-fix-test.log`, `/private/tmp/commons-g6-final-draft-baseline-test.log`.

## Exact integration and shared merges

Read the prior independent review/recheck and wire/mobile report, archived G6 evidence, actual main source, and the browser report. Independently reconstructed the captured baseline and applied `g6.patch`, `g6-manager-semantics.patch`, then `g6-wire-compatibility.patch`. Every reconstructed manifest result hash matched. Before the late composer repair, **34 of 40 G6 source/test paths matched that patch chain exactly**. The other six were reviewed as intentional shared merges:

- `frontend/work/src/api.ts`: hierarchy tracker DTO defaults, enum/typed-parent validation, task creation body, and retry path remain intact. Reconnect authenticates a restored legacy prefix through setup after projects 404; static-build reading is a separate method and does not change task traffic.
- `frontend/work/src/i18n.json`: all G6 EN/RU hierarchy keys remain byte-for-byte, alongside added result/build keys. No hierarchy key collision or locale loss was found.
- `src/agent_commons/services/manager.py`: G6's v7 floor check remains after universal validation and before append, under the same owning-thread/process lock. The extra WP20 verified-file refresh optimization does not bypass validation or the hierarchy stamp. Nested semantics stamping uses the existing reentrant lock; same-manager reparenting serialization remains present.
- `src/agent_commons/services/tasks.py`: an empty-evidence early return avoids unnecessary binding, while canonical write/transition validation still runs. Creation still omits default hierarchy fields and preserves old create retry identity. Parent edits still use existing CAS/retry operations and no dependency inference.
- `src/agent_commons/mcp/server.py` and `tests/mcp/test_worker_scope.py`: added G5 exact-output reviewer tools are separate from G6's root-only create/edit tools. G6 worker authority remains unchanged; hierarchy writes call the same manager boundary.

The seventh final difference from the bounded patch chain is the reviewed root composer repair above. No unexplained G6 merge delta was found.

## Semantics, correction, wire fidelity, and graph behavior

The original two findings remain fixed in integrated source: supported semantics is v7; validated component/parented creation and parent revisions stamp it universally; ordinary defaults/wording and identical legacy retries retain their previous floor. Structural correction freezes top-level kind/parent semantics and nested parent-edit presence/value, with equivalent absent/default creation values allowed. Both supported maintenance and imported replay call the same guard.

The incremental wire repair preserves absent legacy keys in raw TaskRecord/snapshot serialization. Explicit component values, explicit parent IDs, and null detach values survive projection merges. Canonical validation/traversal, correction comparison, tracker DTOs, detail DTOs, and browser parsers supply task/unparented defaults appropriately. No legacy snapshot hash replacement was made; the repair restores the prior frozen fixture shape. The strict old-schema diagnostic limitation remains documented: the v7 stamp does not promise old storage parsers reach the projection upgrade message.

Structure and Dependencies remain separate. Structure uses only explicit parent IDs; dependency/readiness arrays cannot add containment. Search and branch/ancestor selection precede the 128-task diagram cap, cycles/oversized selections retain the native button list, and filtered parents are counted/labeled without invented project-root edges. The project root has no canonical selection action. Numeric SVG viewport attributes support fit/zoom without inline styles. Graph buttons provide arrow/Home/End focus traversal; list buttons provide a native fallback. Accepted color remains reserved for accepted canonical state; child completion does not accept its parent.

Read the integrated graph/editor/API tests and viewed `browser/hierarchy-complete-ru.png`. The screenshot is consistent with separate canonical containment and a separately rooted dependency task; it is visual evidence, not an independent ledger assertion. Existing browser report states actual parent edit/restore, ancestor-preserving search, EN/RU/narrow layout and desktop graph-keyboard checks; `after-edit-verification.json` reports containment/dependency separation and retained bytes. This review did not repeat browser interactions. The narrow gallery pointer scenario remains explicitly unverified and has no established source cause; that was separately documented and is outside G6 containment.

## Checks and limits

Did not duplicate the full suite. The root's first full check was observed running without failures during source inspection; root later reported its final result as 2843 passed / 2 failed, with both failures caused by unrelated reconnect test assumptions and subsequently repaired. This report does not claim full make check passed for the repaired bytes. The root owns a new full green check, asset parity/wheel/runtime/canary gates, and canonical submission. Only the new eight-case behavioral regression was independently executed here; prior independent 106-test hierarchy/correction/race checks remain bound to their earlier exact snapshot, supplemented by this merged-source review.

No source, main checkout ledger, installed runtime, provider, or browser was mutated by this reviewer. Reconstructed baselines and test outputs were confined to private temp paths. Main docs were allowed to change and are deliberately excluded from the exact source/test freeze; onboarding changes were still isolated/pending at review time. No canonical approval or acceptance was written.

## Bounded patch identities

- `g6.patch`: `e1948b6ac5848cc9be8b2dc124caace3d38c8730110ae39655ea4286da0e0f9f`.
- `g6-manager-semantics.patch`: `9f1220747a4b90123c49e26f3d4f8d772b8374b45badc10feda070ee0ed74214`.
- `g6-wire-compatibility.patch`: `e4d044924a457c87d7313177bca82fdc37e4b06b7b3e7fcaf93a218e0124308c`.
- `integration-manifest.json`: `cabfad11aa330749267d43d3280985109f63a8595bb4b6058c8436392f0f11a8`.
- `wire-compatibility-manifest.json`: `6637443d6cc8e8324e9c730149247910cef34a683c84c329ce56e61659c4efe8`.

## Final source/test hashes

- `frontend/work/src/api.ts`: `8212e987ab86222876dd782f89f67919f983eae24fd548b1fff159cf9ba82a66`.
- `frontend/work/src/components/TaskComposer.tsx`: `9e1432d899b8a45464d5ba374971baec57a52b8c828b05e69620ed32c7b4d6d5`.
- `frontend/work/src/components/TaskEditor.tsx`: `dfec9cedb9a0cf078c0833ec345a4fdfcfbe6574a2307e2ce555fcd4124420fb`.
- `frontend/work/src/components/TaskGraph.tsx`: `adcb2c85f346121f36eaa38875f5ce3ed4a5274f89ea4efafdf320818b98fb12`.
- `frontend/work/src/components/TaskHierarchy.tsx`: `ccd199b55f075383282f4ed7c60951a4653738453320142bd14e58cd34a4d3c4`.
- `frontend/work/src/components/TaskInspector.tsx`: `d806f8529a9551f5dd1fb7cf03d14cc7f88722b8cdb42f46287b796427186c7e`.
- `frontend/work/src/components/TaskViews.tsx`: `9d66743287f8ecdbd5d941f6b81b3a215088086bee45539430dd148e18139ac4`.
- `frontend/work/src/contracts.ts`: `564921a3c62567994d44b472edc4de62f85e74b5b6271cdabba7a9c390b1bb8b`.
- `frontend/work/src/i18n.json`: `338b985f5022c8d3c3ca613395797f70557169675c227068978006fd8f4231ae`.
- `frontend/work/src/i18n.ts`: `824bebf3421b5480d04605048c6e9b709978cd966efd5127685da5fafb2aa009`.
- `frontend/work/src/main.tsx`: `ad06e369886627234badbf69386d5ff510a9559852f28d30211a1574ee28ceae`.
- `frontend/work/src/taskEditorState.ts`: `1e9bffa3836f4d0e82123be1ead93b07d9cecfc52264386d9847b2a67ecb97ae`.
- `frontend/work/src/taskGraph.css`: `437857d76ab98c281f0139d6945baafda872d029f0ac05afa50d3d03f921ea21`.
- `frontend/work/src/taskGraph.ts`: `d5c51964c0c8fcd2c5475c26ca8efe2e7f74f133797430aa24f11d6bbea303e6`.
- `frontend/work/src/taskGraphApi.ts`: `86ab42cd49d3c8441ff9d12268da147a7c35d71113a9c22429caf3229f81ccd4`.
- `frontend/work/src/taskGraphStrings.ts`: `805073923044690a97e5966a3ae3315e8eeac2584d2d912688f4ca8d5a60cac4`.
- `frontend/work/src/taskHierarchy.ts`: `51b04f75a4d000bf9b9ac32364d7861dcbff0045bb8e9d5082bea0f13e3aef43`.
- `frontend/work/src/trackerState.ts`: `c1a02e5c2eb4a4f4a16c15bdce659cdf35fa870d568b4c1d84b816a4d4010284`.
- `frontend/work/tests/task-create-draft.test.mjs`: `9a93d5762d1fdba25a08e3b13f3914c2be2555170dc09b1c8a25b02be33c54d8`.
- `frontend/work/tests/task-graph.test.mjs`: `0a08903db16fdc3a1863f4b1c96a2469bb52676dc0f0afc32821cae6942c66f5`.
- `frontend/work/tests/work-task-create.test.mjs`: `59605e6bc8a43bb9b2fdaeeb28628f46455b94f759316d802741342fed11f94a`.
- `src/agent_commons/cli/__init__.py`: `881a35d430c1397fa6024b1652f11ea3262732817b1e2c7a656a78a743c97569`.
- `src/agent_commons/domain/lifecycle.py`: `f883c3f4667bef6d7d5efbe4279cf376098defd2384903e29813f2159d3c96b2`.
- `src/agent_commons/domain/projection.py`: `966731b45994ddcc34d325fb879d77e5c084ae285edb7e852248716f485e8d3c`.
- `src/agent_commons/domain/revisions.py`: `c14b569fc9d51cdaeaac97f6d302da54f89fefd0f75898cf0a9e7058e9963392`.
- `src/agent_commons/domain/task_edits.py`: `29fb1d3cd605ce134c6350f1cea9386078207d9e9cd8ba8d98315ce9f2a2c5c7`.
- `src/agent_commons/domain/task_projection.py`: `f8b67ff0f0f99d50052a08adddaa2b1fd924870f974e2b5d15992162cd2bdcf8`.
- `src/agent_commons/domain/task_review_envelopes.py`: `22c1c0a350bfbd98f1257b7774cf0aec42ad387f4ba78531163b18391551cbbe`.
- `src/agent_commons/domain/validation.py`: `d506631f33585447bd176e599acc5bbc9c1fd5d44156efca4bee6e44b55254b0`.
- `src/agent_commons/mcp/server.py`: `064633ba080cb2e3e1d62aef51160e7a53723c697b2cb4d787b500768bbaa611`.
- `src/agent_commons/resources/schemas/domain/task.v1.schema.json`: `ea1f08d32d89bc1c2b734ccc68acc24070a37cddd9ae55293d518bcdcd1e0c6c`.
- `src/agent_commons/resources/schemas/domain/task.v2.schema.json`: `c2dfbcaf7890eba36b47c0707a03ca7a9cb0605a99028910260fd7b5453edfdc`.
- `src/agent_commons/services/manager.py`: `4ca10ba6e7f31c2b90d153ce21675eacc75c5e8758a6cbb524fb91d84c9a64bb`.
- `src/agent_commons/services/tasks.py`: `7b044bef6bdc7fc154ff80d10e4236daf9de1e9ea236407bf94df37e6add1d75`.
- `src/agent_commons/ui/server.py`: `72d522caae5425e71a7fa32f75611fe197c105538309245cb2dcaa0e5272560e`.
- `src/agent_commons/ui/task_edit_routes.py`: `55a143ff3dfb3b35eb71eb3313f7d08b5fdaa86592fa951a58596d860097fd0c`.
- `src/agent_commons/ui/tracker_dtos.py`: `f37dfb9a4684b0beae20e11504b435440ef8fc484e4b3fae3a3c7f794e334c45`.
- `src/agent_commons/ui/tracker_reads.py`: `df127afd5ad3b2beb20cdfc1e2356a5063c83b898cfc27653e4433c6dcbf5667`.
- `tests/cli/test_cli.py`: `8f45565d6209dcdc099faeca76f28edbac9992e7ae2135bb752c469bc9cf013a`.
- `tests/domain/test_task_projection.py`: `15808f10ef5a199b6c4a9ac5726f18fd0977298df4dc42986834f636b01ba27f`.
- `tests/domain/test_task_review_envelopes.py`: `a53ff15331d44764da59ba802cf86b1c65ba0da0835231d9d0b69e34b8b21960`.
- `tests/mcp/test_server.py`: `47b54d12a3fb89dfa1a5ec725153671eda270c0add215c88aa69990c6c9dd39e`.
- `tests/mcp/test_worker_scope.py`: `92aa3f637ef42eb49938ecdbfc246fc17c80960ead9b0afda98d0b2d365aa927`.
- `tests/services/test_manager.py`: `0b54cba58ca7bf02ba8d0756d28cacb2b9a7d21242d2759f3ee6636bfe2c1f7f`.
- `tests/services/test_task_edits.py`: `49c150868be7454ae32a1d78d32deec15e4c2ad1dd95283d07729e0382fbf2ba`.
- `tests/services/test_task_hierarchy_corrections.py`: `26e106569cc455353a53904c394af8f614e17cd39a71a47b02474e5f2cee2bec`.
- `tests/ui/test_task_edit_routes.py`: `09ae98f811bf6356bd460a8ccab5396280d10c6efe188b8105b55d8290f22aa5`.
