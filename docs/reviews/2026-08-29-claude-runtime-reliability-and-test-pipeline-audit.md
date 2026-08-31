# Claude runtime reliability and test-pipeline audit — 2026-08-29

Status: implementation evidence, independently challenged; not a claim that every
large review shape is supported.

## Test-pipeline decision

Keep the existing coverage. The suite is not large because of obvious duplicate
modules: the four advisory ownership targets partition every collected test module
exactly once, while `make check` remains the sole release gate. The fast targets are
for local feedback only and are not impact-complete selectors.

Implemented pipeline corrections:

- every pytest invocation clears `AGENT_COMMONS_STATE_ROOT`,
  `AGENT_COMMONS_STATE_BASE`, and `AGENT_COMMONS_SESSION_ID`;
- `test-domain`, `test-runtime`, `test-ui`, and `test-contracts` partition both
  `test_*.py` and `*_test.py` modules without overlap;
- `test-contracts` no longer installs the unrelated React work surface;
- CI retains the full two-OS by four-Python matrix and cancels only superseded pull
  request runs, never an exact main-branch run;
- the audit-capture full gate passed: 1334 tests passed, 13 intentional skips and two
  upstream deprecation warnings. Later exact runs are recorded as canonical
  verifications instead of rewriting this historical measurement.

No tests were removed. A later xdist or timing-based split requires several CI timing
samples, identical collected node IDs, and a demonstrated material wall-time win.

## Claude failures reproduced and classified

Three distinct failure classes had previously been conflated as “Claude fell off”:

1. **Large-ledger MCP startup:** each artifact operation replayed the full immutable
   ledger several times. One replay on the 2,000-plus-event workspace measured about
   6.3 seconds; repeated startup replays crossed Claude Code's roughly 30-second MCP
   initialization boundary before the first tool call. A later aggregate review also
   exposed the same N+1 shape in review/verification filtering: every verification
   predicate re-listed reviews through another full replay.
2. **Budget exhaustion:** Claude Code 2.1.220 reports an exhausted monetary ceiling as
   a non-zero structured result with subtype `error_max_budget_usd`. The old decoder
   returned `provider_nonzero_unknown`, which made a deliberate $0.05/$0.50 ceiling
   look like auth or transport instability.
3. **Unsuitable direct review shape:** a three-artifact review eventually recorded an
   exact approval and canonical delegation success, but began terminal finalization too
   close to the 900-second wall. The process was killed before the MCP response returned,
   yielding `process_canonical_mismatch=true`. A 300-second repeat correctly timed out
   without writing success. Prompt-only soft deadlines did not make this workload shape
   reliable. Later exact reviews reproduced a narrower semantic gap: Claude recorded
   `review.completed`, then spent the remaining time without making the separate
   `delegation.succeeded` call. A prompt cannot make a two-model-turn commit protocol
   reliable.

## Implemented runtime corrections

- Worker artifact authorization and immutable manifest metadata are frozen from one
  exact launch snapshot. Artifact bytes are never cached: descriptor-relative,
  no-follow, hash, size, redaction, and terminal unchanged-workspace checks remain.
- Worker task, review, and exact-revision verification DTOs are frozen from that same
  launch snapshot. Each guarded tool call still refreshes current delegation authority,
  while evidence filtering performs no nested ledger replay.
- Aggregate review instructions inspect exact-revision canonical verifications first.
  Covered criteria read only those verification evidence refs; uncovered criteria
  read each necessary bound artifact once and never preflight it with
  `commons_show_artifact`.
- MCP startup and the per-tool live-authority guard use the synchronized disposable
  SQLite projection. It is verified against immutable canonical events and rebuilds or
  falls back when stale, missing, or corrupt; canonical writes and lifecycle CAS still
  use the ledger.
- The broker prewarms that projection after canonical `delegation.started` while the
  provider is still behind the inert exec gate, keeping cold-index work outside the
  provider connection clock.
- Known structured provider errors are decoded for zero and non-zero exits. Ordinary
  assistant prose cannot impersonate an auth, budget, or MCP diagnostic.
- Reviewer instructions now reserve the final third of wall time for the required
  terminal protocol. This is defense in depth, not the sole workload-control mechanism.
- Independent reviewers receive one `commons_finalize_review` terminal operation rather
  than separate model-facing review and delegation commits. It derives the delegation
  and fixed review result from the worker binding, accepts only verdict and bounded summary,
  requires scoped reads of every exact task artifact for approval, binds immutable manifest
  evidence, uses deterministic server-owned idempotency subkeys,
  writes the existing `review.completed` then `delegation.succeeded` events, and is
  terminal-audited only after both succeed. A crash between the two events remains an
  honest partial result; retrying the same operation converges the missing second event.
- Task acceptance now rejects an approved delegated review until its matching delegation
  has canonically succeeded with exactly that review. This closes the orphan-approval gap
  after a crash or timeout between the two immutable events; manual independent reviews
  retain their existing path.
- The paid Claude canary exposed malformed provider-authored evidence references and
  redundant post-result retries. Removing IDs, revisions, evidence, result refs, and the
  idempotency key from the model-facing schema eliminated that stochastic surface.

## Live compatibility evidence

Both canaries used Claude Code 2.1.220, the current checkout MCP executable, strict MCP
configuration, a fresh child session, one exact immutable artifact, and no provider
output persistence.

| Flow | Duration | Process | Canonical | Terminal call/completion/rejection | Mismatch |
| --- | ---: | --- | --- | --- | --- |
| verification | 38.923 s | exit 0 | succeeded | 1 / 1 / 0 | false |
| independent review | 31.828 s | exit 0 | succeeded | 1 / 1 / 0 | false |

Both preflights also passed static flag checks, MCP contract hashing, and a real stdio
initialize/tools-list handshake. Each child session closed. Output remained bounded and
was not copied into canonical events, SQLite read models, UI data, or this report.

## Supported circuit and remediation

The Claude provider and the short terminal MCP flows are available. Direct multi-file
review on a large ledger is **degraded and not advertised**. The supported large-work
shape is:

1. deterministic, exact-revision verification shards with bounded artifacts and
   criteria;
2. canonical verification records for every completed shard;
3. a bounded independent aggregate review over those exact evidence records;
4. acceptance only when the aggregate review and its delegation finish with
   `process_canonical_mismatch=false`.

A missing, timed-out, or rejected shard blocks aggregate approval. Process exit alone,
an incomplete terminal audit, and a prose verdict remain non-success. Resume remains
unavailable because provider/checkpoint identity has not been proven.

Rollback is file-level: revert the MCP snapshot/projection changes, broker prewarm,
instruction contract, diagnostic decoder, and fast-target/CI changes together with
their tests. That restores the earlier behavior but also restores the reproduced
startup N+1, unknown budget diagnosis, and duplicate feedback costs; it is an emergency
compatibility rollback, not an equivalent operating mode.
