# Real-world usage audit

Status: P0/high-value-P1 implementation slice complete; deferred scope remains
explicit below.

Date: 2026-08-06

Baseline revision: `a1bf79e43ea3ea680627bc91b9aff14695545adc`

## Scope and invariants

This audit follows several days of local Codex/Claude coordination and targets
the highest-cost daily failures without replacing the file-ledger design. The
immutable canonical ledger, schema validation, exact-revision CAS, idempotent
crash recovery, secret scanning, typed references, independent review,
explicit truth promotion, fail-closed process ambiguity, and the non-shell
runtime boundary remain release blockers.

No evidence run intentionally used, changed, migrated, or removed the
operator's pre-existing global state root. Collision reproduction used two
disposable Git repositories and a disposable shared state root. During the
audit, one isolated worker accidentally inherited the ambient exact-root
variable while running focused session tests and wrote test-only operational
session events there. The worker was stopped immediately; no cleanup or
migration was attempted because ownership and recovery were ambiguous. This is
treated as additional P0 evidence, not as an accepted operational repair.

## Reproduced baseline

### Cross-workspace operational-state collision

Two independently initialized Git repositories were pointed at one exact
`AGENT_COMMONS_STATE_ROOT`.

1. A session opened in repository A appeared in `session show` from repository
   B.
2. The foreign session identity was accepted as an active writer in repository
   B and could append a canonical task event there. Session and claim records
   carry no workspace ID, so this is not merely a confusing list view.
3. Read-only doctor in repository B returned `ok: true` because it deliberately
   skipped receipt recovery state, even though the foreign session was visible.
4. After repository A made one canonical write, ordinary doctor in repository
   B failed with only `events: idempotency migration belongs to another
   workspace`.
5. `support` reported only `state_root_explicit: true`; it did not report the
   resolved path, configuration source, repository root, workspace ownership,
   or safe migration/activation commands.

The failure is therefore both an isolation defect and a diagnostic-ordering
defect: operational registries can be consumed before state-root ownership is
checked, while the eventual error is emitted from the idempotency subsystem.

### Read-path cost and context size

The repository contained 485 canonical events and 58 manifests before audit
coordination records were added. Five consecutive warm CLI runs produced:

| Command | p50 | observed p95 | Output bytes | Replay behavior |
| --- | ---: | ---: | ---: | --- |
| `orient` | 1.24 s | 1.26 s | 105,039 | full canonical replay |
| empty `inbox` | 1.20 s | 1.22 s | 49 | calls `orient`, then discards almost all output |
| read-only `doctor` | 1.28 s | n/a | 3,176 | full integrity replay, expected |

Default orientation serializes complete projected entities, including actor
objects, descriptions, acceptance criteria, and historical groups. Its nominal
128 KiB bound is applied to a recursively copied object rather than to the final
serialized output. Empty inbox latency is dominated by the same replay and
orientation construction as the 105 KiB response.

Phase timing on the later 496-event ledger localized the warm read cost:
canonical event discovery, byte loading, and schema validation consumed about
0.81-0.83 seconds; manifest loading about 0.03 seconds; the pure projection
step about 0.02 seconds. The useful optimization boundary is therefore a
verified incremental source/index read path, not micro-optimizing projection
reducers. The current global copy budget can also be exhausted by earlier work
and review groups before inbox/handoff fields are copied, which explains the
nearly empty truncated inbox response.

### Clean starting point

- `pytest`: 376 passed in 69.45 seconds.
- `ruff check .`: passed.
- `ruff format --check .`: 96 files already formatted.
- Read-only doctor: no integrity issues; warnings were dominated by expected
  historical review/verification staleness.

## Risk-ranked gap matrix

| Rank | Area | Evidence / failure mode | Invariant or user impact | Disposition and first batch |
| --- | --- | --- | --- | --- |
| P0 | State-root ownership | Foreign sessions are visible; first canonical write later yields a low-level migration mismatch | Cross-project isolation, idempotency, operator confidence | Implement base-directory namespacing plus an exact-root ownership marker and fail before registries/writes. Keep exact-root compatibility; never auto-migrate data. |
| P0 | Task-scoped input | Delegated workers have no bounded question/reply path; exited `input_needed` becomes `needs_operator` | Correctness, unnecessary abandoned work, no safe resume | ADR-first communication aggregate/runtime channel, fake-provider wait/reattach/continuation, strict content and participant bounds. |
| P0 | Active cancellation | No authenticated proof-driven active cancel path | Duplicate/live process risk and false terminal claims | Two-phase cancellation intent/termination evidence/reconciliation with race and crash tests. |
| P0 | Writable isolation | Claims are cooperative and successful builders lack changed-path attestation | Silent out-of-scope writes | Freeze allowed paths, attest before/after diff, fail closed on parent/out-of-scope mutation; retain operator-owned worktree boundary. |
| P1 | Orientation/inbox | 105,039-byte orient and 1.20-second empty inbox at 485 events | Daily context and latency tax | Compact DTOs by default, hard serialized byte bound, indexed warm reads, cursor/ack operational state, full replay via `--fresh`. |
| P1 | Session UX | Missing active session errors require manual discovery/export; no `session current` | Repeated setup friction and unsafe temptation to reuse a foreign session | Explicit current lookup and shell export; show safe next step, never auto-select and never repeat nonce. |
| P1 | Typed references | Parser error does not identify the option or list valid kinds | Slow recovery and avoidable retries | Field-aware Click type and structured error code with examples; add diagnostic parse/show surface if it stays small. |
| P1 | Task orchestration | Dependencies are stored but readiness, cycles, next work, and critical path are manual | Lead toil and duplicate/blocked work | Derived DAG views and `task next`; no automatic acceptance or second truth system. |
| P1 | Model routing | Profiles are static and route explanations are unavailable | Poor heterogeneous allocation and hidden mismatch risk | Operator-owned metadata and `broker route --dry-run`; core remains provider/model neutral. |
| P1 | Council fan-out | Parallel independent branches and synthesis require manual bookkeeping | Recursion, budget, and false-consensus risk | Template that expands to ordinary tasks/delegations with one target revision, aggregate limits, preserved dissent, and ordinary review. |
| P1 | Stale maintenance | Healthy doctor emits dozens of historical stale warnings | Warning fatigue hides actionable failures | Classify integrity/actionable/history/maintenance/info; compact summaries and explicit maintenance plan, no deletion. |
| P1 | Observability | Milestone spans exist, but no metrics or end-to-end propagation | Weak diagnosis of queue, input, resume, cancellation, and finalization | Content-free bounded-cardinality metrics/traces; telemetry remains optional and non-authoritative. |
| P1 | Workflow evals | Runtime contracts are well tested but real daily orchestration failures are not one named suite | Release confidence and regression attribution | Add 25+ hermetic state/tool/security/trace cases; keep provider canaries opt-in and nonblocking. |

## Batch boundaries and rollback

1. State isolation and diagnostics: config, support, manager construction, and
   focused state-root tests. Rollback retains exact-root behavior and ignores
   new base namespaces; it never deletes or moves existing roots.
2. Runtime communication/resume/cancel: additive schemas and ADR, then
   fake-provider domain/runtime slices. Disable the optional runtime to roll
   back; canonical history remains readable by the new reader.
3. Compact indexed reads and cursor state: disposable projection and operational
   cursor files only. `--fresh` always rebuilds from the ledger; deleting the
   projection/cursors loses no project truth.
4. DAG/routing/council/path attestation: derived or template-driven views over
   existing entities, operator-controlled profiles, and fail-closed builder
   completion. No template or model vote can accept work.
5. Eval, docs, and independent review: deterministic presubmit first; crash,
   scale, and opt-in provider evidence in later gates.

Each batch receives focused behavior tests and an exact-revision review before
acceptance. Security-sensitive runtime changes require a separate independent
security review and migration/rollback verification.

## Implemented evidence and measured result

The reviewable slice delivered four compatible batches:

1. Workspace ownership and session/ref DX: state bases resolve to
   `<base>/workspaces/<workspace_id>`, exact roots fail closed on mismatch before
   registry construction, support reports configuration source/ownership and
   reveals paths only with `--show-paths`, and linked worktrees keep one
   workspace identity. `session current` and one-time shell exports do not
   auto-select a session or repeat its nonce. ADR 0005 contains the migration
   and rollback contract. The isolation review was approved after two
   legacy/broker-path defects were remediated. A later combined review found
   that an underspecified legacy migration document could still be mistaken for
   ownership proof; the follow-up now requires safe-path, strict-JSON,
   canonical-byte, packaged-schema, and typed-workspace validation before any
   owner marker is published.
2. Compact verified reads: default orientation and inbox use bounded DTOs and a
   ledger-head/fingerprint-verified SQLite projection. Corruption or mismatch
   rebuilds or falls back to canonical files; read-only mode never mutates.
   `--fresh` forces canonical replay and `--verbose` preserves expanded output.
3. Task-scoped live communication: a child can request input, publish progress
   or a blocker, poll, and acknowledge; the canonical parent can reply and
   resume the exact task-target delegation. The private store is
   HMAC-authenticated, participant/attempt/revision bound, size/depth/deadline
   limited, idempotent, and non-oracular. A security review found a
   caller-controlled canonical reply summary; remediation removed both request
   and reply summary parameters. An independent rerun proved five unique body
   markers absent from canonical history and approved the exact remediation.
4. Offline eval foundation: 25 catalogued cases share a bounded, content-free
   result schema. Eight P0 cases are executable, 13 are marked planned, and four
   unsupported capabilities remain explicitly non-passing. The fake provider
   accepts catalog IDs only and cannot persist prompts, reasoning, or raw model
   output.

### Before/after benchmark

The original reproduced baseline used 485 events and 58 manifests. A later real
workspace measurement at 560 events and 64 manifests produced:

| Read | Before | After warm compact | Output before | Output after | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| `orient` | ~1.24 s | ~0.48 s | 105,039 bytes | 19,887 bytes | byte target met; 300 ms latency target not yet met |
| empty `inbox` | ~1.20 s | ~0.33 s | 49 bytes* | 952 bytes | zero canonical content files read; 200 ms target not yet met |

\*The old 49-byte inbox was misleadingly tiny because it paid for full
orientation/replay and then discarded almost everything. The new response
includes bounded diagnostics and useful state. These are observed local runs,
not a cross-platform p95 claim.

Repository verification after the communication remediation passed 436 tests
in 102.73 seconds. After the final legacy-ownership remediation, the expanded
suite passed 441 tests in 105.15 seconds; Ruff lint, Ruff format,
`git diff --check`, isolated wheel CLI smoke, and the packaged 25-case catalog
smoke were clean.

## Delivered, partial, and deferred scope

| Area | Result |
| --- | --- |
| State isolation | Delivered and independently approved. No existing state was moved, cleaned, or migrated. |
| Session/ref ergonomics | Delivered for current/export and field-aware validation; shell completion, `ref show`, and operation-plan key generation remain deferred. |
| Compact/indexed reads | Delivered and measured; hard 20 KiB output bound met, latency stretch targets not yet met. Cursors, acknowledgement state, subscriptions, and richer filters remain deferred. |
| Live task input/resume | Delivered for a still-running provider through MCP/service and independently security-reviewed. Reattaching an exited provider is not claimed. |
| Communication privacy | Delivered after removal of caller-controlled canonical summaries; full bodies remain authenticated operational state. |
| Active provider cancellation | Deferred. Operation-level two-phase cancellation exists, but proof-driven process termination/reconciliation is not implemented. |
| Writable path isolation | Deferred. Claims remain cooperative; changed-path attestation and operator-provisioned worktree enforcement are still required. |
| DAG/task-next, routing, council | Deferred. No scheduler, model consensus, or template can promote accepted truth. |
| Observability | Existing content-free runtime milestones retained; end-to-end metrics and trace propagation deferred. |
| Eval harness | Partial: catalog and eight executable P0 cases delivered; planned/unsupported cases intentionally do not pass. |

The first combined umbrella review correctly returned `changes_requested` for
a reproducible MEDIUM: a noncanonical/underspecified
`idempotency-v2/migration.json` containing only the expected workspace ID could
be accepted as legacy ownership proof and cause marker publication. The
remediation treats underspecified, noncanonical, duplicate-key, untyped-ID,
malformed, or symlink-routed migration state as unproven without mutation;
valid canonical v2 migration remains compatible. Its exact-revision security
review reproduced the attack variants and approved the fix.

Two exact Claude review delegations were attempted after successful static
preflight and model canaries: an `opus` alias for compact/index review and exact
`claude-fable-5` for eval architecture. Both provider processes exited zero but
made no required terminal-tool call, so the broker correctly recorded
`needs_operator`; neither review was counted as completed, and no prose/raw
output was promoted. A Claude Sonnet builder did implement a substantial
communication-core batch earlier in the work. This is evidence of the current
long-run terminal-protocol gap, not a reason to relabel an OpenAI reviewer as
Claude or to claim an unavailable exact "Opus 5" identity.
