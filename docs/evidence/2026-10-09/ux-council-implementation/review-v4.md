# Независимое финальное ревью UX-волны v4

Verdict: approved, scoped to exact v2–v4 delta and resolution of reviewed Fit defect; not product/task acceptance.
Reviewer: session.507923f57b834f1d817596efe911ea88, distinct new session; prior reviewer sessions closed. Requester/implementer: session.50ac37a8c53042eba5db58b14e5b5da8.
Target: artifact.0T8PQA5XKB2GC9H4TP9S0HDPKS @ evt.01M4GWNRK19JBK67E4A65EKG7J.
Review: review.5Q6W3GDP076MQCXW2JFJXXJGJN @ evt.01M4GWPDJS576HT9H3GWS30XVT.

## Findings

No concrete P1/P2 found in the exact reviewed v2–v4 delta. V2 Fit P2 is resolved at source and numeric-regression level. V1 far-band accessible task-name fix remains present and its regression passes.

ProjectBoard derives minimumZoom from the actual React Flow nodeLookup (including measured roles, owner and frames), measured state.width/state.height, and current transform zoom. minimumPaddedMapZoom permits a covering scale with conservative padding room instead of the previous fixed20% floor; keeping the smaller current zoom prevents a resize or bounds change from forcing zoom-in. Both primary Fit (padding .2) and React Flow Controls Fit (default .1) inherit the adaptive minimum, so it also agrees with the gesture scale extent. Working zoom still uses the existing explicit80–100% action. Node geometry/manual arrangements are unchanged.

reframeFlowViewport translates camera x/y by half the pixel-frame delta without changing zoom, exactly preserving (frame-center - translation)/zoom in both axes. BoardCanvas tracks only positive active frames, seeds its prior frame without moving the initial camera, and defers recentering while the primary fit is pending. Returning to a visible map can reconcile a changed frame. Existing onMove persists completed camera transforms; neither resize nor this helper invokes a board-layout write.

## Fit/resize ordering assessment

Reviewed actual pinned React Flow/system implementation. Primary Fit is queued in requestAnimationFrame after readiness and measured-node agreement; the library resolveFitView reads live nodeLookup/frame/minZoom when it runs, then resolves its promise after setViewport. The resize effect updates the prior frame even while fitPending suppresses an extra center translation. The fit callback stores the resulting viewport and clears pending. No duration is requested by these fit/reframe paths, so this delta does not introduce a long animated-fit interval competing with resize. Secondary Controls Fit uses the same store minimum and existing onMove lifetime. Source assessment found no concrete lost-center or clamp race warranting P1/P2.

These sequencing conclusions are code inspection plus arithmetic checks, not a claim to have run a mounted React stress test of every simultaneous click/resize timing. Browser control stayed exclusively with the integrator as instructed.

## Independent checks

173/173 actual checkout files and173/173 private snapshot files matched MANIFEST.json before review. Canonical read-only doctor reported ok=true, issues=[]. Parent held checkout and ledger quiet, apart from this reviewer's own session/review lifecycle. Exact v2-v4.diff and affected source/dependency code were read directly.

59 focused Node24 tests independently passed,0 failures,0 skipped:17 map-zoom-bands,15 board-state,27 task-graph. The new numeric tests use pinned React Flow getViewportForBounds for broad50-agent bounds, tall50-agent bounds and remote saved-position extents, both818×461 and322×373 frames, both padding .1 and .2; assert all transformed bounds remain inside the frame. Resize tests check world center/zoom across .025,.2,1,1.6 scales and322/818/1200px widths plus invalid-frame handling. Task graph regression retains unique title-bearing aria-labels in EN/RU. Tests ran against `/private/tmp/oct09-ux-implementation/review-snapshot-v4`, with temporary compiler output outside checkout; no assets built or files edited in checkout.

## Limits

Approval is exact-revision code judgment of the final delta, relying on separately bound v1/v2 reviews for unchanged implementation. Full make-check-v4 was still in progress when this review was prepared; no exact full-suite success is claimed here. Integrator reported synthetic browser confirmation of all50 agents+owner visible at desktop/390, both Fit controls, explicit working zoom1,264×248 cards and center-preserving1434→800 resize. Those are attributed integration observations, not my independent browser execution. This review does not promote product truth or accept the task/user-facing usability matrix; final make check and browser evidence remain separate requirements.

Canonical receipt: `review.5Q6W3GDP076MQCXW2JFJXXJGJN` completed as `approved` at `evt.01M4GWX2G1SRAX66PZM5ETXC4C`, idempotency key `ux-implementation-v4-independent-review-20261009`.
