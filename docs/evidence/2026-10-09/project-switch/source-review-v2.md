# Independent project-switch final source review v2

Verdict: approved for the exact frozen29-file manifest/diff against9ff2ce55ecb1d46875408c9b6c0f65b7efc76125. The v1 introduced P2 restart recovery defect is resolved. No concrete remaining P1/P2 found in the reviewed scope. This is code/evidence judgment; full make check, CI, task acceptance and Git publication remain separate integrator gates.

Reviewer session.8903e4cefae34c919bf3b6c3c982101e is distinct from requester/implementer session.4b891c477fdd40989cff0a4af8172c07. Reviewer did not implement this diff; previous reviewer session was closed. Root confirmed all source/docs/ledger writes frozen except this review lifecycle. Browser was not operated.
Target artifact.3ERCTWN1XFY9V2D1FEFX07M6YR @ evt.01M4H47CQVHW2MTV323V5614JG.
Requested independent review review.0E3S2RR5YYNR9SFG7HBQPC1MFT @ evt.01M4H48WG5HNWC4ZYF1CS08M6T, independent=true/stale=false verified through canonical CLI.
Manifest docs/evidence/2026-10-09/project-switch/review-manifest-v2.json SHA2562e13fe0aba60d237edcb46107d047a8bcd7774e3b72435ebf92dba044f426cbe matches registered content revision.29/29 actual checkout hashes matched before and after review, including final work-4G19TXus.js, CSS/index and both Python contract files. Canonical read-only doctor with explicit absolute repo/state-root returned ok=true/issues=[].

## Restart/authentication assessment

V1's shortcut kept a stale process-local opaque prefix after the server restarted and returned404. V2 connect accepts explicit revalidate; ordinary navigation still reuses a connected instance only when there is no fresh fragment and its prefix equals validated sessionStorage. Explicit refresh calls load(true), which forces host validation and registry reload. Fresh fragments bypass the shortcut and the existing host/projects→host/setup fallback detects a retired prefix before exchange. Failed host401/404 clears the stored prefix and yields session recovery; live entity404 does not globally clear authentication.

Main loader now revalidates the host upon a bootstrap404 before constructing the failure panel. This fixes the case where the original404 failure had canRetry=false, so merely changing an inaccessible Retry handler would have been insufficient. Old-prefix404 becomes typed401 recovery when both host probes fail; genuine missing project/entity404 is preserved if host validation succeeds. Recovery awaits use the same signal; after recovery, AbortError returns and the project/read generation guard is checked again before any UI failure commit. A switch or newer read while recovery is pending cannot publish that old failure into the new project.

Independent runtime regression tests against actual WorkApi confirm already-connected restart recovery for explicit refresh and fresh handoff, clearing stale prefix, exactly one fresh-code exchange, resumed zero-probe ordinary navigation, and live entity404 session retention. Existing unauthorized/legacy/transient/malformed/prefix tests still pass. Authentication cookies, same-origin credentials and server project binding remain unchanged; no credential is stored by the new fast path.

## Project/provider/navigation assessment

Configured core reads skip setup guidance and optional provider CLI probes. Required setup/meta/catalog/launch still complete before core publication; catalog/launch401 is propagated instead of partial readiness. Deferred provider arrays begin empty/unknown, and selected launch availability must explicitly report launchable=true. Provider availability/auth observations are concurrent optional enrichment; aborted signals refuse publication,401 is not an offline provider, and absent/failed observations do not invent readiness.

Main loader/enrichment success and401 failure capture both selection epoch and read generation. bindProject clears ready data, advances selection and aborts previous reads; clients remain immutable and project-scoped. A–B–A, out-of-order completion and superseded same-project refreshes cannot merge old provider data. Mutations abort the older read and authoritative follow-up refresh takes a new read generation. New404-recovery work retains the same guard, including the post-await check. Existing per-project RAM drafts/uncertain intents remain scoped.

Pending/failure shell renders registry project name/layout/navigation only, never the prior workspace snapshot, actual chat or task data. Desktop sidebar and compact open/close/Escape/inert/focus-trap behavior retain existing semantics; state-kind reset does not prevent opening a pane while checking. Core map reads can still take seconds. Copy promises retained navigation and removal of repeated blocking access/provider probes, not instant project data.

## Documentation portability assessment

Independently reconstructed the expected transformation from baseline for all17 imported evidence/handoff files: exactly95 external blizhe-workspaces Markdown links become plain provenance text with original labels and exact relative paths. The transformed content matches checkout byte-for-byte; no other historical content was altered and external evidence is not falsely presented as an in-repository downloadable artifact.

The documentation contract now resolves each local link and checks both existence and containment beneath root.resolve(). This rejects an existing neighboring file and a symlink to outside-root data; the two new isolated regressions exercise those cases. Repository documentation links pass even while this Mac has neighboring workspaces. The stale UI string assertion was updated consistently with configured-guidance skipping; surrounding closed typed/setup-order checks remain in place and real WorkApi runtime tests prove the behavioral change.

## Independent checks and evidence

111 Node24 checks passed0failed0skipped: task-inspector/project-workspace/provider-availability/mutation-registry.24 Python checks passed1 TestClient/Starlette warning: documentation-map and Work-app contracts, cacheprovider disabled and bytecode writes disabled. These focused checks cover actual API recovery, selection/read ownership, readiness/expiry, project-specific mutation state, documentation containment and source/bundle contract behavior. git diff --check passed. No full15-minute suite rerun was requested or performed.

Final build log shows tsc -b and Vite success producing current work-4G19TXus.js/work-BOmGhuEa.css. All three installed uv-tool Work static files were independently byte-compared equal to current checkout. Final browser evidence is integrator-recorded: navigation retained while loading at340ms and278ms in opposite transitions, oldBlockingAccess=false, current bundle identified. Earlier rapid-switch/compact behavior evidence remains separately attributed; no independent browser execution or universal latency bound is claimed. Final Claude-builder canary reports succeeded, childclosed, matching refs and no process-canonical mismatch; this is provider/MCP compatibility evidence, not a full user-task provider UI execution.

Final make-check-final.log was still progressing at~30% Python when checked during review. V1 run's3033pass/1fail was an obsolete source string assertion now covered by the independently passing affected24 Python checks. No final full-suite or remote CI-green claim is made. Current portability regression has source/tests supporting its repair; remote CI execution remains a later gate. Numeric concurrency reasoning and API regressions do not imply a mounted React stress test of every timing combination.

Canonical approved receipt: review.0E3S2RR5YYNR9SFG7HBQPC1MFT @ evt.01M4H4FM26N1YE6RFN1GJ0XCZF; idempotency key project-switch-source-review-v2-20261009.
