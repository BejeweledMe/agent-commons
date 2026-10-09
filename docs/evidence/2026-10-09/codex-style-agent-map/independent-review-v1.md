# Independent review: Codex-style / agent-map iteration

Verdicts: frontend **changes_requested**; backend **approved within reviewed source scope**.
Reviewer session: `session.871afefa5bbd4048bcb88e77007403e0` (independent of requester and implementation sessions). Requested model configuration is not provider-attested identity.

Exact source artifact: `artifact.1GRFVCE5BTGSVFKVF73SWJ4VNM` at `evt.01M4FXCVG6GFBR281HVE1ZFN4T`.
Manifest SHA-256: `a22c0aefe813c433ede6386addf76cfe8249c73795abbba053f45d24b538d6c5`.
All 138 manifest entries match both the current checkout and immutable frozen-source snapshot before and after review (including deleted paths). This boundary contains the previously accepted October 9 wave; this review does not claim authorship or reclassify that earlier work as new implementation.
Python source fingerprint independently recomputed: `285f9c773f9e650057a9ba244e1bc9a4533f43bfff6647a81fd576a683a9d109`.

## Material findings

### P2 — Successful unrelated operations discard unsaved editor fields

`frontend/work/src/agentSettings.ts:133` (also confirmed-operation retry at line 116; resetting read at lines 94–108).
Every confirmed operation calls `load(false)`, replacing the general draft and reloading methods regardless of what the operation actually committed. Reproduction against the compiled production class: load agent named QA; change the General draft name to Unsaved new name; switch to Connections and successfully create an ask link. After the link response/refresh, draft.name is QA and state is saved. The name was neither submitted nor explicitly discarded. A method-only bind similarly discards unrelated general edits; a settings/link save can discard modified method buffers.
Preserve unrelated drafts and clear/rebase only the exact fields or methods committed by the confirmed operation, including refresh-only retries. Add regressions spanning tabs and successful unrelated writes. The existing nine editor tests pass and do not cover this case.

### P2 — Project switching destroys agent-map viewport and uncertain editor intent

`frontend/work/src/components/ProjectBoard.tsx:96–106` owns ReactFlow and the local AgentEditorSession registry; `frontend/work/src/components/ProjectBoard.tsx:240–242` supplies an uncontrolled viewport with initial fitView. The entire component is below project-keyed providers at `frontend/work/src/main.tsx:1598–1599`.
Within one project, hiding the tab retains the map. Switching A → B → A changes the provider key, unmounts BoardCanvas and recreates both the viewport and session registry. Thus an agent map pans/zooms back to initial Fit; an unsaved or uncertain editor operation loses its original request body, saved library reference and idempotency keys. Concrete flow: uncertain settings/link write → close the modal → visit another project → return and reopen the agent. The new AgentEditorSession cannot offer the identical retry. This violates the explicit per-project viewport and retained uncertain intent criteria. Task-map viewport state is correctly hoisted separately and does not fix the agent map.
Keep the agent viewport and editor operation registry in project-scoped RAM state outside that keyed subtree, restoring viewport and exact pending operations on return. Verify A/B/A with distinct viewports and a lost-response mutation. This finding follows from component ownership and remount semantics; no browser geometry reproduction is claimed.

## Backend assessment and checks

Read the actual organization/specialization service, domain validators, typed envelopes, projection, revision/correction guards, v3 schema/semantics floor, bounded agent-details DTO, HTTP registration/action boundary, and frontend transport integration. No additional material backend defect found. Organization remains separate from creator authority; self/cycle/stale/ineligible parents refuse, methods remain exact references and live-work guards execute under canonical locking. Legacy events retain compatible replay.

Independently executed: `tests/services/test_agent_configuration.py` plus `tests/ui/test_agent_details.py`: **28 passed**, one Starlette/httpx deprecation warning. `frontend/work/tests/agent-settings.test.mjs`: **9 passed**. Additional production-class reproduction confirmed the first P2. Tests used temporary output/state; no source edits or provider launches.

Checked root browser evidence for native modal geometry, nested Escape, focus return, dual-map tab retention and real synthetic-fixture mutations. These observations were not independently remeasured. Exact-source Codex/Astra canary reports ok=true, canonical succeeded, matching result refs, one terminal completion, zero rejections, child closed; its source fingerprint matches the reviewed tree.

At review finalization, the final complete make check was still running (last observed about 70%). This source judgment does not assert a green release gate; owner acceptance must wait for the complete final result and a new frontend review after fixes. No physical trackpad smoothness, light-theme browser measurement, Linux or broad performance qualification is claimed.

Explicit role models reach cards unchanged. An absent explicit model currently produces no model label on specialized cards; settings honestly shows the profile-default fallback. Legacy presets lacking specialization remain blocked by existing worker-eligibility UI; backend legacy-child tests alone do not validate that UI path. These observations are not counted as newly confirmed regressions.
