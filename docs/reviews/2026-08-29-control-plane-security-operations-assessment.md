# Control plane security and operations assessment

**Date:** 2026-08-29
**Code base:** `dd65bdb` on `main`
**Role:** Technical director for classical system design, software engineering,
security, reliability, and operations.
**Task:** `task.6HZHN07HE10SFYVJ90R4Y981XR`
**Scope:** Review the proposed programme as a long-lived control plane. Does not
edit source, tests, existing plans, static UI, or any other path.

## 1. Executive summary

Agent Commons implements a durable, append-only coordination workspace for
multi-agent software collaboration. Architecturally it is a file-ledger control
plane with an optional local delegation runtime. The design demonstrates
unusual discipline in separating canonical truth from derived state, but the
gap between the ledger's strong invariants and the runtime's operational
maturity is the system's primary risk as it moves toward a long-lived control
plane.

This assessment covers twelve dimensions. The overall verdict is that the
canonical core is sound and the programme correctly sequences derived-first
changes before canonical-semantic changes. The delegation runtime and provider
supply chain carry the highest residual risk and need the strongest gates.
Three stop-the-line conditions are identified.

---

## 2. Canonical vs derived truth

### 2.1. Current state

The ledger design (ADR 0001) correctly enforces a strict separation:

- **Canonical truth** lives in immutable, schema-validated JSON event and
  manifest files under `.agent-commons/events/` and `.agent-commons/manifests/`.
  These are append-only, content-addressed, and Git-friendly.
  Ref: `storage/events.py:EventStore.append`, `storage/manifests.py`.

- **Derived state** is produced by deterministic replay
  (`domain/projection.py:project_events`) into a `ProjectSnapshot`. The SQLite
  index is a disposable query acceleration layer, never authoritative.
  Ref: ADR 0001, `ARCHITECTURE.md` §Deployment topology.

- **Operational state** lives under `.git/agent-commons-state/` (sessions,
  claims, runtime attempts, idempotency receipts). It is explicitly
  non-authoritative and may be rebuilt or conservatively reconciled.
  Ref: `ARCHITECTURE.md` §Deployment topology.

### 2.2. Assessment

| Property | Verdict | Evidence |
|---|---|---|
| Canonical events are immutable and append-only | **Sound** | `atomic_write_immutable` in `storage/atomic.py`; hash verification on read in `EventStore` |
| SQLite is never the only copy of meaning | **Sound** | `index rebuild` reconstructs from canonical files; normal reads replay the ledger |
| Corrections preserve immutable history | **Sound** | `event.corrected` and `event.invalidated` are additive events; original events are never rewritten (`domain/invalidations.py`) |
| Thread messages are canonical events, not a separate store | **Sound** | `thread.replied` is a canonical event type; no separate message write path exists |

### 2.3. Risks and recommendations

**R1. Replay cost grows linearly with ledger size.** Normal reads currently
replay the full canonical ledger. The `replay_metrics` counter in
`ProjectSnapshot` makes this measurable, and the 10,000-event / 1,000-correction
benchmark from ADR 0001 establishes a ceiling, but there is no current mechanism
to short-circuit replay. The architecture-improvement plan correctly sequences
A6 (replay profiling) before any indexed-read migration.

**Recommendation:** Before any semantic migration, establish a golden replay
corpus (W0 from the implementation plan) and measure p50/p99 replay latency as
a baseline. Define the threshold at which indexed reads become mandatory, and
gate any semantic change that would invalidate the current O(N) replay contract.

**R2. Correction lookup is indexed, but the index is disposable.** Correction
lookup uses the SQLite projection by target rather than scanning every correction
for every event. If the index is stale or absent, replay falls back to the
scanning path. This is correct for consistency but could surprise an operator
after `index rebuild` on a large ledger.

**Recommendation:** `doctor` should report correction-scan cost as a structured
diagnostic, not just a warning.

---

## 3. Task / plan / run / attempt state

### 3.1. State machine boundaries

The system maintains four distinct state machines with clear separation:

| Entity | States | Ref |
|---|---|---|
| Task | ready → assigned → active → completed → review → accepted (with blocked, cancelled, reopened branches) | `domain/projection.py:TASK_STATES` |
| Delegation | requested → active ↔ input_needed → {succeeded, failed, cancelled, timed_out, needs_operator} | `domain/projection.py:DELEGATION_STATES` |
| Attempt | reserved → launching → running → {succeeded, failed, cancelled, timed_out, needs_operator} | `runtime/attempts.py:AttemptState` |
| Agent | active ↺ reconfigured → retired | `domain/projection.py:AGENT_STATES` |

### 3.2. Assessment

**Sound design decisions:**

1. **Task completion ≠ acceptance.** `task.completed` means the author considers
   work ready; `task.accepted` requires a current approved independent review
   bound by revision. This is enforced at the lifecycle layer
   (`domain/lifecycle.py:validate_transition`, lines 258-303).

2. **Delegation success ≠ task acceptance.** The documentation and code are
   consistent: `delegation.succeeded` is a result report, not project truth.
   Ref: `ARCHITECTURE.md` §Lifecycle invariants, `PROTOCOL.md` §9.

3. **Attempt state is operational, not canonical.** The broker's attempt
   journal (`runtime/attempts.py`) is explicitly non-authoritative. Canonical
   delegation transitions pass through `CommonsManager`. This separation is
   load-bearing and correctly maintained.

4. **Work authorship accumulates.** The task projection tracks
   `work_author_session_ids` across take, start, block, unblock, and complete
   transitions. Independence is checked against this accumulated set, not just
   the last actor. Ref: `domain/lifecycle.py:_subject_author_sessions`.

### 3.3. Risks

**R3. Submit and review-request are not atomic.** `task.submitted` and
`review.requested` are two separate canonical writes. A crash between them
leaves a submitted task with no review request — the `ReviewLoopGap` the
corrected control-plane assessment already identified. The implementation plan
(§3.1 rule 1, §4 W3) correctly sequences this as a semantic decision requiring
H0, not a quick fix.

**R4. No `RunView` join exists yet.** Delegation intent and attempt process
state must currently be joined manually. The implementation plan correctly
identifies `RunView` as a derived read model (W1), not a new canonical entity.
Until W1, operators lack a single view of "what happened to this delegation."

**R5. `needs_operator` taxonomy is incomplete.** Nine of thirty-one terminal
delegations (per the 2026-08-25 measurement) ended in `needs_operator`.
`runtime/diagnostics.py:DiagnosticCode` provides typed codes, but the
canonical `needs_operator` reason field carries only a bounded summary string,
not a structured taxonomy. The implementation plan targets 100% taxonomy
coverage in W4.

---

## 4. Concurrency / admission / backpressure

### 4.1. Current mechanisms

The system implements a layered admission control hierarchy:

| Layer | Mechanism | Ref |
|---|---|---|
| Cross-process canonical write lock | File lock (`canonical-write.lock`) with reentrant depth counter in `CommonsManager` | `services/manager.py` line 177, `platform_support.py:lock_exclusive` |
| Per-delegation serialization | File lock + threading lock per delegation ID | `services/delegation_runtime.py:_delegation_lock` |
| Delegation tree limits | `max_depth`, `max_fanout`, `max_concurrency`, `max_attempts` — monotonically non-increasing | `runtime/policy.py:RuntimePolicy` |
| Operator-owned global caps | `OperatorLimits`: global concurrency, queue capacity, per-provider/profile caps, parent budget, total delegation subtree | `runtime/policy.py:OperatorLimits` |
| Bounded FIFO queue | Wait with `queue_wait_seconds` timeout; full/expired queue fails before allocating an attempt | `runtime/attempts.py:AttemptStore.reserve` |
| Writable-profile concurrency guard | `profile_concurrency > 1` for a non-reviewer profile refuses at config load | `runtime/policy.py:OperatorLimits._assert_writable_concurrency_preconditions` |

### 4.2. Assessment

**Sound:** The monotonic limit reduction (`RuntimePolicy.assert_reduction_of`)
is comprehensive: depth, fanout, attempts, concurrency, timeout, output bytes,
and monetary budget are all checked. A bounded parent can never produce an
unbounded child.

**Sound:** The subtree backstop (`max_delegations_total = 16`) caps total
delegation count regardless of tree shape. This addresses the gap where depth ×
fanout alone could permit a supervisor to open compliant delegations indefinitely.

**Sound:** Single writable worker per checkout scope is enforced at config load,
not at runtime. This is the correct place: a race at launch time would be harder
to diagnose than a startup refusal.

### 4.3. Risks

**R6. Lock primitive duplication.** The private-directory + exclusive-lock
primitive is implemented separately in `coordination/sessions.py`,
`runtime/attempts.py`, `runtime/communication.py`, and
`services/delegation_runtime.py`. The copies have diverged: `sessions.py` has
no symlink guard, while `attempts.py` and `communication.py` raise
`IntegrityError` on symlinks but disagree on path resolution for
non-existent parents. This is a known issue (flagged multiple times in
`thread.3Y4JX67VM1BY7AHHBGHJ9RMMWG`).

**Recommendation:** Consolidate to a single implementation before layering the
state-root namespace contract or any new ownership-marker mechanism.
Priority: **before G0** of the provider-adapter plan.

**R7. Admission journal is not atomic across process boundaries.** The attempt
store uses file-level locking and atomic writes, but the queue admission and
reservation sequence involves multiple file operations. Under high contention
from multiple broker processes sharing one state root, the admission window
between checking capacity and writing the reservation could admit more
concurrent work than intended.

**Recommendation:** The current `max_delegations_total = 16` and single-writer
checkout scope make this unlikely to manifest in practice. If multi-broker
support is ever added, the admission journal must be transactional.

---

## 5. Authority and claims

### 5.1. Authority model

Authority is derived at read time from the immutable ledger — the narrowest
grant across a role and every creator above it. This is the correct architecture:
a stored-and-propagated model needs a propagation pass that can be skipped.

Key invariants, all verified in code:

| Invariant | Enforcement | Ref |
|---|---|---|
| Effective grants are narrowest-across-lineage | `domain/roles.py:effective_grants` walks ancestor chain | `domain/agents.py` |
| A created role has strictly narrower creation grant | `validate_role_transition` checks `narrower_strictly_required` | `domain/roles.py` |
| Turnover budget counts creations AND retirements | `turnover_used` aggregates both | `domain/roles.py:turnover_used` |
| Automatic level (`auto`) is withheld to `ask` | `AUTOMATIC_LEVEL_WITHHELD = True` caps effective grants | `domain/roles.py` line 32 |
| Profile narrowing: builder can create reviewer but not vice versa | `PROFILE_NARROWING` dict | `domain/roles.py` line 42 |
| Tool selection is intersection, never union | `_worker_tools` intersects role selection with profile set | `runtime/model.py:_worker_tools` |
| Independence is over principals, not sessions | `principals()` maps sessions through delegation bindings | `domain/roles.py:principals` |

### 5.2. Claims

Claims are correctly modeled as temporary coordination leases with TTL, renewal,
release, and audited break. They are explicitly not Git ownership or authorization.
Ref: `coordination/claims.py`.

### 5.3. Risks

**R8. Authority is coordination, not authentication.** The threat model
correctly states this residual: a process with filesystem write access can
bypass the CLI and write events directly. This is an inherent MVP-0 limitation
of same-filesystem coordination without a trust boundary.

**R9. `delegation:recover` is a coordination gate, not an authentication
primitive.** The threat model documents this honestly. An operator-authorized
session declaring this capability can terminalize `requested` delegations when
the original requester is absent. The exact CAS on the current `requested`
revision prevents race with provider start.

**Sound:** `session end` refuses a requester that owns non-terminal delegations
(`domain/lifecycle.py`), so ordinary shutdown cannot manufacture orphaned work.

---

## 6. Prompt / skill / provider supply chain

### 6.1. Current architecture

The provider supply chain has three layers:

1. **Profiles** are operator-owned fixed configurations (`runtime/model.py`).
   A profile names an executable, model, sandbox mode, and MCP configuration.
   Profiles are loaded from operator-controlled YAML outside the delegated
   workspace. Ref: `runtime/model.py:RunnerProfile`.

2. **Skills** are operator-authored instruction text from an operator-owned
   catalogue. A role selects skill IDs; the catalogue resolves them to bounded
   instruction text. Skills cannot contain executable paths or secrets.
   Ref: `catalog.py:skill_instructions`.

3. **Provider adapters** are fixed implementations selected by profile. The
   current system supports Codex and Claude with four built-in profiles.
   Ref: `runtime/model.py:BuiltinProfileId`.

### 6.2. Assessment

**Sound:** The delegation instruction is composed server-side
(`services/delegation_instruction.py:compose_delegation_instruction`), not by
the worker or the UI. The instruction is ephemeral and never written to the
canonical ledger.

**Sound:** Provider output is untrusted. The MCP terminal tools
(`commons_succeed_delegation`, `commons_complete_review`) are the only
canonical completion path. Process exit alone is never sufficient.
Ref: `BROKER_OPERATIONS.md` §Behavioral contract gate.

**Sound:** The `provider-adapter-architecture-plan.md` correctly identifies that
provider adaptation exists but is not formalized as a self-contained registry
with capability negotiation. The plan sequences the adapter registry (P2) before
capability validation (P3) before skill projections (P4) before UI unification
(P5).

### 6.3. Risks

**R10. Preflight does not prove runtime capability.** A successful MCP
handshake during `broker preflight` does not prove the provider can start in
the configured sandbox. This is a known gap (provider-adapter plan §9) that
manifested with the Codex reviewer in a read-only environment. The behavioral
canary (`broker canary`) is the correct mitigation but consumes a paid attempt.

**R11. Claude builder has no OS-enforced sandbox.** The Codex builder runs
under `--sandbox workspace-write`; the Claude builder has no OS-enforced
boundary and retains shell and file-write tools. The threat model (§Residual
risks) correctly names this asymmetry but it is not surfaced to the operator at
profile selection time. The `trusted_workspace` opt-in does not distinguish
the two.

**Recommendation:** The provider-adapter plan's typed refusal mechanism
(`provider_capability_unsupported`) should include a structured
`sandbox_boundary` field so the operator can see exactly what isolation each
profile provides before authorizing a launch.

**R12. Skill file identity is not provider-specific.** Skills currently have a
single physical bundle, though client discovery rules differ. The provider-adapter
plan (§7) correctly proposes `SkillProjector` with per-provider projections.
Until then, silently assuming Codex and Claude treat the same file identically
is a compatibility risk.

---

## 7. Secrets

### 7.1. Current enforcement

The `SecurityPolicy` (`security/policy.py`) scans all write surfaces before
assigning durable IDs or receipts. It uses pattern matching for credential
markers (`_CREDENTIAL_MARKERS`), quoted assignments (`_QUOTED_ASSIGNMENT`),
PII key classification (`_DEFAULT_CLASSIFIED_KEYS`), and configurable custom
patterns.

Key boundaries:

| Surface | Protection | Ref |
|---|---|---|
| Canonical events | `_validate_stored_event` calls `policy.assert_safe` | `services/manager.py` line 242 |
| Manifests | `_validate_stored_manifest` calls `policy.assert_safe` | `services/manager.py` line 245 |
| Operational attempt state | Sanitized stderr tail with secret/PII redaction | `runtime/diagnostics.py:sanitize_provider_stderr_tail` |
| Telemetry | Metadata-only; prompts, reasoning, tool payloads excluded | `runtime/telemetry.py`, `ARCHITECTURE.md` §Security and trust |
| Worker instruction | Ephemeral, never persisted in canonical or operational state | `services/delegation_instruction.py` |
| Terminal tool audit | Error type + fixed refusal string only; free exception text never persisted | `runtime/tool_audit.py` |
| Operator config | `0700` directories, `0600` files, outside delegated workspace | `operator_files.py` |

### 7.2. Assessment

**Sound:** The defense-in-depth approach is correct: scan before persistence,
reject with redacted output, never store the matched value. The threat model
(§Secret or sensitive-data persistence) correctly identifies this as a recursive
scan requirement.

**R13. Secret detection cannot guarantee coverage of project-specific values.**
The threat model honestly states this as a residual risk. Custom patterns in
workspace security configuration extend the defaults but cannot cover all
possible project-specific secrets.

**R14. Bearer token exposure.** The UI bearer token is printed to stdout and
passed via URL when launching a browser. The process list exposes it to other
processes of the same user. The threat model and `BROKER_OPERATIONS.md` §Local UI
correctly document this.

---

## 8. Path isolation

### 8.1. Current boundaries

The workspace reader (`mcp/scoped_repo.py:ScopedRepoReader`) enforces:

- Path normalization and scope validation
- Symlink rejection for tracked paths (final component and parent traversal)
- Descriptor-relative no-follow semantics
- Size limits on individual files and total read volume

The exec gate (`runtime/exec_gate.py`) holds the provider PID inert until
the canonical `delegation.started` transition is durable, then replaces itself
with the provider executable via `os.execve`, preserving PID and process group.
The control frame is stripped so the provider never sees it.

### 8.2. Assessment

**Sound:** The exec gate design is minimal and correct. It uses `os.execve`
(not `exec` through a shell), validates that the provider path is absolute,
and the control frame is a fixed constant.

**Sound:** The scoped repo reader opens parents with descriptor-relative
semantics before the final regular file, so symlink-to-traversal attacks that
rely on TOCTOU between stat and open are mitigated.

**R15. Worktree isolation is explicitly not implemented.** The broker never
creates, switches, commits, resets, or removes Git worktrees. The writable
concurrency guard refuses `profile_concurrency > 1` for builders. This is
correct but means parallel implementation workers require operator-provisioned
external worktrees.

---

## 9. Crash recovery

### 9.1. Current mechanisms

| Scenario | Recovery path | Ref |
|---|---|---|
| Crash during canonical write | Idempotency receipts scope the in-flight operation; exact retry completes it | `storage/idempotency.py`, ADR 0003 |
| Crash during provider run | `broker reconcile` checks launch token, process fingerprint, provider handle, canonical state, and child session | `runtime/attempts.py:AttemptStore.reconcile`, `BROKER_OPERATIONS.md` §Recovery |
| Ambiguous post-start crash | Fails closed to `needs_operator` | `runtime/attempts.py`, `PROTOCOL.md` §9 |
| Stale operational state | `doctor` verifies/synchronizes; `index rebuild` reconstructs projection | ADR 0001 |
| Receipt orphan | Exact retry or explicit `receipt:abandon` with audit tombstone | `PROTOCOL.md` §2 |
| Session crash with owned delegations | `session end` refuses; `delegation:recover` for `requested`-only | `ARCHITECTURE.md` §Lifecycle invariants |

### 9.2. Assessment

**Sound:** The "ambiguity fails closed" principle is consistently applied.
The broker never blindly relaunches possibly-live work. `needs_operator` is the
correct terminal state for any case where process identity or termination cannot
be proven.

**Sound:** Idempotency is operation-specific with stable keys. The receipt
recovery system (ADR 0003) correctly scopes receipts to worktree-and-ref
combinations. A non-shrinking per-scope ledger anchor detects deletion or byte
changes after first observation.

**R16. Active work cancellation is explicitly unsupported.** The system can
cancel only `requested` (unlaunched) delegations. For active work, the operator
must stop the provider externally and then reconcile. This is an honest
limitation, not a gap, because the system has no authenticated canonical stop
receipt mechanism. However, it means recovery from a hung provider requires
manual intervention.

**R17. PID reuse risk on `broker stop`.** Stopping a running provider uses the
recorded PID. After a long-idle attempt, the PID may have been reused by an
unrelated process. The documentation (`BROKER_OPERATIONS.md` §Stopping a
running provider) correctly warns about this, but there is no guard beyond
documentation.

**Recommendation:** Consider adding process start timestamp comparison to
reduce the PID reuse window. On systems that support it (Linux `/proc`),
verify the process start time matches the recorded attempt's start time.

---

## 10. Idempotency

### 10.1. Current implementation

Idempotency uses a three-layer system:

1. **In-flight receipts** are written atomically to the receipt scope before the
   canonical event, creating a durable record of the intended operation.
   Ref: `storage/idempotency.py:IdempotencyStore`.

2. **Semantic hashing** produces a content-addressed hash of the event body
   (excluding `event_id` and `recorded_at`), ensuring that a retry with the
   same idempotency key and semantically identical content returns the original
   result. Ref: `storage/events.py:semantic_event_body`.

3. **Expected-revision CAS** on every state transition prevents concurrent or
   reordered writes from corrupting entity state.
   Ref: `domain/lifecycle.py:require_revision`.

### 10.2. Assessment

**Sound:** The combination of idempotency receipts, semantic hashing, and CAS
provides defense-in-depth against duplicate writes, crash-retry scenarios, and
concurrent writers. This is the correct design for a file-based system without a
serializing broker.

**Sound:** The idempotency key namespace separation prevents collisions between
different write paths (CLI, MCP, UI) operating on the same workspace.

---

## 11. Schema / versioning

### 11.1. Current state

- **Canonical events** use `commons.event.v1` schema with family-specific
  payload schemas (`commons.payload.task.v1`, etc.).
  Ref: `services/manager.py:PAYLOAD_SCHEMAS`.

- **Operational state** uses versioned schemas with forward migration:
  `runtime_request.v5` and `runtime_attempt.v5` read v2-v4 and upgrade in
  memory. Ref: `runtime/attempts.py:_READABLE_REQUEST_SCHEMAS`.

- **Semantics versioning** uses `workspace.semantics_required` to stamp the
  ledger when a new semantic entity or replay rule is introduced. The floor only
  rises; a stamp at or below the current requirement is refused. Ref:
  `domain/lifecycle.py:validate_transition` (workspace.semantics_required block).

### 11.2. Assessment

**Sound:** The operational schema versioning is well-designed. Older schemas
are read and upgraded in memory, so there is no migration step. Rolling back
requires finishing in-flight work and removing operational `runtime/requests`,
which is non-authoritative. Ref: `BROKER_OPERATIONS.md` §Upgrading and rolling
back operational state.

**Sound:** The semantics versioning stamp prevents a newer ledger from being
read by an older binary that cannot replay its semantics. This is correctly
identified as a hard rollback boundary in the threat model.

**R18. New canonical entity families require careful rollback planning.** The
threat model notes that a workspace with `commons.payload.agent.v1` events
cannot be read by a pre-agent binary. Each `agent.*` event becomes a
`domain_validation_rejected` projection issue, and integrity gates fail closed.
This is the correct behavior, but operators need clear rollback guidance before
any new entity family is introduced.

**Recommendation:** Each new canonical entity family (e.g., the proposed
`delegation_closure` from ADR 0011) must include in its ADR:
(a) the exact rollback boundary (which checkout revision is the last safe one),
(b) a feature flag that disables new writes while preserving replay, and
(c) explicit documentation of what operational state must be finished before
rollback.

---

## 12. Observability

### 12.1. Current state

The system provides three observability channels:

1. **Canonical ledger** — delegation intent, outcomes, typed references. Never
   carries operational noise.

2. **Operational journal** — attempt state, PID, exit code, bounded stderr
   tail (4 KiB after sanitization), terminal-tool audit (32 rejection details
   × 512 bytes). Private files (`0700`/`0600`), never enters canonical events
   or telemetry. Ref: `runtime/attempts.py`, `runtime/tool_audit.py`.

3. **Optional OpenTelemetry** — metadata-only spans with correlation attributes.
   No metric instruments or end-to-end span context in the current slice. Lossy
   and never affects replay. Ref: `runtime/telemetry.py`.

### 12.2. Assessment

**Sound:** The telemetry exclusion list is correct and conservative: prompts,
reasoning, transcripts, file contents, tool payloads, environment variables,
credentials, and raw process output are all excluded by default.

**Sound:** The SLIs defined in `BROKER_OPERATIONS.md` are well-chosen.
Canonical completion rate (≥95%), safety invariant (false approval = 0),
finalization latency (p95 ≤5s), and deadline containment (100%) target the
right signals.

**R19. The run-observability store was withdrawn (ADR 0008) and nothing
replaced it.** There is no way to observe an in-progress delegation except
through broker attempt state, which is operational and unstable. The withdrawal
was correct (no producer existed), but the gap remains. The implementation plan
(W1 `RunView`) addresses this as a derived join, not a new store.

**R20. `process_canonical_mismatch` is the most important operational signal.**
Process exit without canonical terminal state is the strongest indicator of a
protocol violation. The release evidence gate correctly requires zero such events
across 20 real launches. This signal must be surfaced prominently, not just
logged.

---

## 13. Performance

### 13.1. Current characteristics

| Operation | Mechanism | Scaling property |
|---|---|---|
| Canonical read | Full ledger replay | O(events × corrections) |
| Canonical write | File lock + atomic write + receipt | O(1) per event, serialized |
| Projection | Deterministic fold over events | O(events) |
| Index query | SQLite WAL | O(log N) after rebuild |
| Admission | File lock + FIFO queue | O(active attempts) |

### 13.2. Assessment

The system is currently designed for a local single-machine workload. At the
current scale (dozens to low hundreds of events per workspace), replay latency
is not a practical concern. The benchmark anchor (10,000 events / 1,000
corrections from ADR 0001) provides a known ceiling.

**R21. Replay cost will eventually require indexing.** The architecture
acknowledges this: "A future indexed read path must prove equivalent projection
semantics before synchronous maintenance is reconsidered." The implementation
plan correctly sequences A6 (replay profiling) as a measurement step before
any optimization.

**R22. Cross-process write serialization is a throughput bottleneck.** The
canonical write lock serializes all writers across all processes. For the
current same-machine deployment, this is acceptable and correct (the alternative
is distributed coordination). If multi-process write volume grows, the
per-event lock hold time becomes the capacity ceiling.

---

## 14. Migration and rollback

### 14.1. Current contract

The system provides a tiered rollback strategy:

| Layer | Rollback mechanism | Ref |
|---|---|---|
| Operational state | Remove `runtime/requests`; finish in-flight work first | `BROKER_OPERATIONS.md` §Upgrading |
| SQLite index | Delete and rebuild from canonical files | ADR 0001 |
| Canonical events | Revert Git checkout to before the new event family | Threat model §Residual risks |
| New entity families | Feature flag disables new writes; replay preserves history | ADR 0011 §Replay |

### 14.2. Assessment

**Sound:** The implementation plan (§6.3) specifies a mandatory migration
contract for each approved semantic change: owner decision → event/schema
vocabulary → golden old-ledger fixtures → round-trip invariants →
compatibility/migration → feature flag + kill switch + cleanup owner. This is
the correct sequence.

**Sound:** ADR 0011 (hierarchical delegation closure) correctly proposes an
additive entity (`delegation_closure`) rather than overloading `delegation`
states. Old delegations receive no invented closure and appear as
`legacy_not_evaluated` in derived views.

**R23. Receipt scope isolation requires coordinated rollback.** Per-checkout
receipt scopes (ADR 0003) mean that a rollback across linked worktrees requires
all worktree writers to stop before a v1 binary is used. This is documented but
could surprise an operator who only stops the broker in the main checkout.

---

## 15. Contradictions with accepted ADRs

### 15.1. ADR consistency check

| ADR | Stated contract | Current code | Status |
|---|---|---|---|
| ADR 0001 (file ledger with SQLite projection) | SQLite is disposable, never authoritative | `index.sqlite3` is rebuilt by `doctor`/`index rebuild`; normal reads replay canonical files | **Consistent** |
| ADR 0001 | Normal reads replay the ledger | `project_events` in `domain/projection.py` replays all events | **Consistent** |
| ADR 0004 (optional local delegation runtime) | Broker is operational, not canonical; canonical writes pass through `CommonsManager` | `services/delegation_runtime.py` calls `CommonsManager` for all lifecycle transitions | **Consistent** |
| ADR 0004 | Worker profiles receive no delegation-create or broker-run tools | `_worker_tools` in `runtime/model.py` does not include delegation-creation tools for any profile | **Consistent** |
| ADR 0004 | `max_depth: 0` enforced; recursive delegation rejected | `_validate_delegation_request` checks `depth > max_depth` | **Consistent** |
| ADR 0008 (run observability store withdrawn) | No current `agent-commons run` command | Confirmed: no `run` subcommand exists | **Consistent** |
| ADR 0008 | Store should not return without a producer | Code fully removed at `4525131` | **Consistent** |
| ADR 0011 (hierarchical delegation closure) | Proposed, not yet implemented; requires H0 acceptance before W3 | No `delegation_closure` event type in `domain/projection.py` | **Consistent** |

No contradictions between accepted ADRs and current code were found. The
proposed (not yet accepted) ADR 0011 is consistent with the implementation plan's
sequencing.

### 15.2. Tensions worth noting

**T1.** The provider-adapter plan proposes a `SkillProjector` and
`AdapterRegistry` that do not exist yet. The current `RunnerProfile` classes
embed both provider-specific argv construction and MCP configuration. This is
not a contradiction — the plan explicitly sequences P2 (wrap existing behavior)
before P6 (migrate profile classes).

**T2.** The implementation plan's CLI deprecation (C0–C4) depends on A8
(collaborators instead of facade), which is not yet complete. The current
`CommonsManager` still aggregates all write paths. This is documented and
correctly sequenced, not a contradiction.

---

## 16. Proposed gates and ownership

### 16.1. Stop-the-line conditions

These conditions must block all forward work on the control plane programme:

| # | Condition | Signal | Owner |
|---|---|---|---|
| **S1** | False approval: process exit incorrectly promoted to canonical success | `false_strict_acceptance > 0` OR `delegation.succeeded` without terminal MCP tool call | Runtime/security; operator |
| **S2** | Self-review: an independent review is completed by a principal that authored the subject | `self_review_count > 0` in any workspace | Domain/governance; operator |
| **S3** | Deterministic broker contract regression: any case in the behavioral contract matrix fails | `make check` CI failure in broker test suite | Runtime; release owner |

### 16.2. Phase gates

| Gate | Before phase | Required evidence | Owner |
|---|---|---|---|
| **G-R** | W1 (derived work health) | All A3–A8 structural slices have exact-revision independent review; golden replay corpus established; `make check` green | Software architecture |
| **G-H0** | W3 (hierarchical closure) | H0 semantic RFC accepted with owner and independent review; event/schema vocabulary, old-data handling, replay, rollback all specified; golden old-ledger fixtures pass | Product owner; domain/backend |
| **G-W3** | W4 (runtime preflight) | W3 exit gate met: 100% local-closure coverage, 100% review-pairing-or-hold, zero false strict acceptance, zero invalid local closure, zero self-review | Workflow owner; governance |
| **G-W4** | W5 (policy-bound pull) | W4 exit gate met: zero invalid terminal success, 100% taxonomy coverage, 50 observed eligible terminal cases gap-free | Runtime/security |
| **G-D9** | Push dispatch (W6) | Explicit VISION supersession, authority/admission vocabulary, worktree lifecycle, security threat model, kill switch, sustained zero false acceptance | Product owner; security/SRE |

### 16.3. Ownership matrix

| Domain | Owner role | Scope |
|---|---|---|
| Canonical ledger / event schema | Domain/backend | Event vocabulary, validation, projection, replay |
| Delegation lifecycle | Domain/backend | State machine, independence, work authorship |
| Runtime broker | Runtime/security | Process lifecycle, attempts, admission, telemetry |
| Provider adapters | Runtime/backend | Profile registry, capability negotiation, provider canary |
| Security policy | Security | Secret scanning, path isolation, credential boundaries |
| UI surface | Frontend/design + UI backend | Route table, typed refusals, session ownership |
| Observability | Runtime + QA | SLIs, telemetry, diagnostic codes, run metrics |
| Role authority | Domain/governance | Grant algebra, lineage, independence |
| Review and acceptance | Domain/governance + product | Review pairing, acceptance invariants |
| Skills and catalogue | Skill packaging | Neutral identity, projections, installer |
| Migration and rollback | Software architecture | Schema versioning, feature flags, rollback plans |
| Release evidence | QA + runtime | Canary evidence, contract matrix, real-provider gates |

---

## 17. Summary of findings

### High severity

| # | Finding | Location | Recommendation |
|---|---|---|---|
| R6 | Lock primitive duplicated 4× with divergent behavior | `coordination/sessions.py`, `runtime/attempts.py`, `runtime/communication.py`, `services/delegation_runtime.py` | Consolidate before G0; priority: pre-provider-adapter |
| R11 | Claude builder has no OS-enforced sandbox boundary | `runtime/model.py:ClaudeRunnerProfile` | Surface sandbox asymmetry in typed refusal; document in operator guidance |
| R5 | `needs_operator` taxonomy incomplete (9/31 terminal delegations) | `runtime/diagnostics.py` | 100% coverage required by G-W4 |

### Medium severity

| # | Finding | Location | Recommendation |
|---|---|---|---|
| R3 | Submit and review-request are non-atomic | `services/tasks.py`, `services/reviews.py` | Addressed by H0/W3; do not quick-fix |
| R10 | Preflight does not prove runtime capability | `runtime/preflight.py` | Behavioral canary is the correct mitigation; sequence with P3 |
| R17 | PID reuse risk on `broker stop` | `services/delegation_runtime.py` | Add process start-time comparison where OS supports it |
| R12 | Skill files not provider-specific | `integrations/installer.py` | Addressed by provider-adapter plan P4 |

### Low severity / monitored

| # | Finding | Location | Recommendation |
|---|---|---|---|
| R1 | Replay cost grows linearly | `domain/projection.py` | Measure baseline in W0; gate indexed-read migration on A6 |
| R4 | No RunView join exists yet | — | Addressed by W1; derived, not canonical |
| R19 | No in-progress delegation observability | — | Addressed by W1 RunView + streaming seam |
| R22 | Write serialization throughput ceiling | `services/manager.py` | Acceptable for same-machine MVP; revisit for multi-host |

---

## 18. Overall assessment

The Agent Commons control plane has a sound canonical core built on correct
principles: append-only immutable events, deterministic replay, derived-before-
canonical, and fail-closed on ambiguity. The separation between canonical truth,
derived state, and operational mechanics is consistently maintained across code,
documentation, and ADRs. No contradictions between accepted ADRs and current
implementation were found.

The programme correctly sequences its work: structural audit (A3–A8), then
derived read models (W0/W1), then semantic changes (H0/W3), then runtime
hardening (W4), then advisory planning (W5), with push dispatch (W6) deferred
behind an explicit VISION supersession gate.

The primary risks are concentrated in the delegation runtime: lock primitive
duplication, incomplete diagnostic taxonomy, sandbox asymmetry between providers,
and the inherent limitations of PID-based process management. These are
operational risks, not architectural ones, and the existing programme addresses
them in the correct order.

The three stop-the-line conditions (false approval, self-review, broker contract
regression) are the right signals to monitor. The phase gates ensure that each
new capability has its predecessor's evidence before proceeding.

**Verdict:** The programme is architecturally sound for a local single-machine
control plane. The implementation plan's ordering is correct and conservative.
The highest priority action is consolidating the lock primitive (R6) before
any new state-root or ownership-marker work.
