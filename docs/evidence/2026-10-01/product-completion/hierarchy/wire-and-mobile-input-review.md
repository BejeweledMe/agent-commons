# Mobile board input and hierarchy wire follow-up

Verdict: no actionable source finding established for the reported gallery click failure. Hierarchy wire compatibility fix has no finding in the reviewed four-file scope. This is a bounded read-only source follow-up, not browser verification or final release acceptance.

Root-reported observation: on a board at 320 CSS px (Chrome 110% zoom and a 352px viewport override), Playwright gallery button clicks failed twice, including after Fit; Enter opened the same dialog. Desktop click worked. I did not independently reproduce those browser interactions.

## Board input inspection

- `frontend/work/src/components/OutputsPanel.tsx:65` renders the gallery button as a native button with `board-role-gallery nodrag nowheel`, an ordinary React `onClick`, and the same Outputs workspace dialog selection path. It has no viewport-specific handler or conditional pointer refusal. Keyboard and pointer activation therefore reach the same callback if a click reaches that element.
- `frontend/work/src/components/ProjectBoard.tsx:161` derives role nodes with `draggable: writesEnabled`. Installed React Flow `NodeWrapper` automatically adds its default `nopan` class to draggable nodes (`frontend/work/node_modules/@xyflow/react/dist/esm/index.mjs:2348` nearby). The board does not override `noDragClassName` or `noPanClassName`, so their defaults remain `nodrag` and `nopan`.
- Installed XYDrag explicitly declines targets inside `nodrag` (`frontend/work/node_modules/@xyflow/system/dist/esm/index.mjs:2350` nearby). Its pan/zoom filter declines targets within `nopan` (`index.mjs:2923` nearby). This excludes the straightforward “gallery button starts a node drag or pane pan because the classes are absent” explanation on a writable board.
- `frontend/work/src/board.css:59` nearby defines the gallery button's flex layout and minimum 32px untransformed height. Its mobile breakpoint changes board arrangement/canvas height, not gallery pointer events, z-index, or click handlers. React Flow applies node and viewport transforms itself; board CSS does not introduce a second gallery transform. Neither the gallery nor its avatar/name receives a pointer-events override.
- Fit can scale every card control down (board minZoom is 0.2), so real pointer targets can be smaller than their untransformed CSS sizes. That alone does not explain a failed locator click centered on a stable, unobscured target, and is not classified as the trigger without the actual browser geometry.

Conclusion: no grounded nodrag/pointer/transform code defect was identified for this observation. A browser coordinate or hit-test issue under combined viewport override and 110% zoom remains plausible, but is not proved. Do not change handlers/classes based on this source review alone.

The useful next browser evidence is the button's `getBoundingClientRect`, `window.innerWidth`/visualViewport geometry, and `document.elementFromPoint` at the actual pointer-event coordinates, plus a content-free pointerdown/up/click target trace. Run these in the already authorized fixture; compare 100% and 110% zoom. That distinguishes a click landing on a neighboring/overlay element from a click reaching the button but being suppressed. A missing click despite pointerdown/up on the same gallery descendant would justify examining movement/gesture handling; a mismatched hit target would support the coordinate explanation. No browser or canonical actions were taken here.

## Hierarchy wire compatibility patch

Reviewed `docs/evidence/2026-10-01/product-completion/hierarchy/g6-wire-compatibility.patch` and actual integrated source. It removes eager `task_kind="task"` and `parent_task_id=None` insertion from `TaskRecord` projection, preserves absent legacy keys in raw snapshot serialization, keeps explicit component/parent/null values intact, updates tests, and clarifies ADR 0024.

Checked hierarchy field consumers with repository search: canonical parent validation and ancestry traversal use `.get("parent_task_id")`; correction comparison applies semantic defaults; tracker reader/detail DTO apply task/unparented defaults; hierarchy schemas/envelopes retain presence fidelity. Explicit created children retain their parent through the existing merge reducer, and explicit detach remains present/null. The four-file change does not alter event payloads, retry identities, correction guards, or semantics stamps. No consumer in the reviewed product paths requires projected default keys. The added wire test verifies explicit values and absence independently.

No tests were launched while the root's full make check ran; findings here are source inspection. Main checkout/ledger/assets and browser were untouched. Report output is private temp only.

## Exact source binding

- `frontend/work/src/components/ProjectBoard.tsx`: `9dd99ee55fcdc97ba414681fc84f816218e82bb4faf6ddccc109362fd684de41`.
- `frontend/work/src/components/OutputsPanel.tsx`: `219ead74830db36341c99ed2b682bb6a6ca866a92a02e1a6c4cd7438ac0c1582`.
- `frontend/work/src/board.css`: `5ae00d7e36bd1d9e8caa1f16add2d19250edc915de4d67e7dd2c7d51c7dcdbca`.
- `src/agent_commons/domain/task_projection.py`: `f8b67ff0f0f99d50052a08adddaa2b1fd924870f974e2b5d15992162cd2bdcf8`.
- `docs/evidence/2026-10-01/product-completion/hierarchy/g6-wire-compatibility.patch`: `e4d044924a457c87d7313177bca82fdc37e4b06b7b3e7fcaf93a218e0124308c`.
