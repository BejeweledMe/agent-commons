# Независимое повторное ревью UX-волны v2

Verdict: changes_requested.
Reviewer: distinct new session.7e391de2587c46edb61529f09f753c04. Previous reviewer session was closed; requester/implementer is session.50ac37a8c53042eba5db58b14e5b5da8.
Target: artifact.7XN0FFZ1BQZMX04B45MHAXHNXV @ evt.01M4GW19J6X0ZZ6EW1V2ZJ1GE9.
Review: review.7G5AV3QC272V008BQBZ7EM6Z7E @ evt.01M4GW3QKBYVM0TJA1DAWXD744.

## P2 — Fit не охватывает широкую группу из 50 агентов

Affected source: `frontend/work/src/components/ProjectBoard.tsx:435` (minZoom={0.2}); same component line 300 (`flow.fitView({ padding: 0.2 })`). The overview command and initial fit use the React Flow minimum because no `options.minZoom` override is passed. Actual pinned dependency implementation `@xyflow/system/dist/esm/index.mjs:411` calls getViewportForBounds with `options?.minZoom ?? minZoom`; line 726 clamps the calculated covering zoom to that minimum. `XYPanZoom` also applies the same minimum scale extent, so merely overriding one fit calculation is not a complete robust solution.

Trigger: synthetic 50-agent board, broad supervisor tree, click Fit to view. The map stays at 20%; left/right agents remain outside the frame. This is an unmet agreed UX-01/UX-10 overview criterion, inherited from v1 rather than newly introduced by the narrow v2 delta. It must not be described as passing 50-agent overview acceptance.

Independent numerical reproduction using the pinned dependency, executed against the private snapshot through Node24 stdin: one supervisor plus 49 equal sibling cards, width `49*264 + 48*48 = 15240` layout units (boardState fixed card width and Dagre sibling gap); frame 818×461px; padding .2. getViewportForBounds(...,.2,1.6,.2) yields zoom .2 and scaled width 3048px >818px, therefore cannot cover every node. With a sufficiently low allowed minimum the same call yields ≈.04475. No node geometry or manual position was changed by this check.

Corroborating integration evidence: `/private/tmp/oct09-ux-implementation/browser-evidence/05-agents50-fit.png`, supplied by the integrator and independently viewed by reviewer, shows cards/edges cut by both canvas boundaries after Fit. The integrator reports the live zoom remained20%. The screenshot alone does not establish the numeric zoom; pinned-source calculation establishes the clamp.

Requested change: derive a covering minimum zoom from measured board bounds and live frame (including owner/frames), or otherwise allow explicit Fit to use the required scale consistently with the React Flow pan/zoom extent. Preserve fixed 264×248 card geometry, stored manual positions, explicit working-scale action, and refresh/resize camera lifetimes. Add a meaningful large-board bounds test showing all 50 agents fit at wide-tree and tall-forest arrangements, then reproduce browser Fit at desktop and narrow widths. Ordinary working zoom should remain readable and explicit.

## v1 finding resolved in the reviewed delta

TaskGraph now supplies `aria-label` with task title/taskId plus human status; TaskHierarchy supplies the equivalent label using stateLabel. Both are independent of hidden descendants, so far-band `display:none` no longer removes task identity from their accessible names. The added regression checks both components and both locales with distinct same-state titles. This is SSR attribute evidence; it does not itself apply CSS or exercise a browser accessibility tree, but explicit aria-label is the correct source fix for the v1 precedence issue.

The v1-v2 delta also simplifies Reload copy while preserving the existing load(true) behavior, and removes the duplicate Prepare-run heading/close from inside the existing native Modal. These are presentation changes; no gate or mutation path changed.

## Evidence and limits

173/173 snapshot files and 173/173 actual checkout files matched MANIFEST.json before this review. Canonical read-only doctor: ok=true, issues=[]; new explicit session created through supported CLI. Parent kept checkout/ledger quiet, except reviewer session/review operations.

58 focused checks independently passed, 0 failures/skips: task-graph, work-shell, board-state, using Node24 in `/private/tmp/oct09-ux-implementation/review-snapshot-v2`. No assets were rebuilt, no checkout source was edited, no live user data/history read, no CUA/browser controls used. Source, exact v1-v2.diff, actual pinned React Flow implementation, and the supplied synthetic screenshot were inspected.

Scope is narrow v2 delta plus 50-agent Fit, relying on the separately bound v1 review for unchanged code. Full make check and browser matrices remain integrator evidence; neither was independently claimed green. This is review judgment, not user/product acceptance. No additional concrete P1/P2 found in this scope.

Canonical finalization: `review.completed evt.01M4GW9GQW18G5YXHN6WBQP97X`, existing review `review.7G5AV3QC272V008BQBZ7EM6Z7E`, verdict changes_requested, idempotency receipt key `ux-implementation-v2-independent-review-20261009`.
