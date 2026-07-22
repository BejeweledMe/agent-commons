# ADR 0004: Optional local delegation runtime

- Status: accepted
- Date: 2026-07-20
- Scope: MVP-2 local service and provider adapters

## Context

Agent Commons currently lets separate Codex, Claude Code, and other agent windows
coordinate through one durable ledger, but a user still has to prompt each window
in turn. The core deliberately does not launch models: local session metadata is
not authentication, a task is not permission to execute a process, and durable
coordination must remain useful when no daemon is running.

The next local iteration should allow an authorized client to delegate a bounded
piece of work to another provider without turning Agent Commons into a general
scheduler, an agent-to-agent chat protocol, or an Agent Host Protocol (AHP)
implementation. The feature must remain optional and reversible. It must preserve
the existing ledger, exact-revision review, work-authorship, Git authority, and
same-host trust boundaries.

The expected first users are individual developers running trusted local Codex
and Claude installations. The repository maintainers own the domain contract,
broker, provider adapters, tests, documentation, and operational migrations.
Provider CLIs and SDKs are external dependencies whose observable output and
session behavior can change independently.

## Decision

Add an **optional local delegation runtime** made of three narrow layers:

1. a canonical `delegation` aggregate records durable intent and outcome;
2. a local broker owns process execution, attempts, cancellation, and live state;
3. MCP and CLI are adapters over the existing `CommonsManager` and broker APIs.

The file-ledger core remains independently usable. Enabling the runtime grants a
local broker authority to start only operator-allowlisted profiles; it grants no
new Git, deployment, publication, network, or project-truth authority.

### Goals

- Let Codex request bounded Claude work and Claude request bounded Codex work.
- Preserve a typed, inspectable parent-to-child delegation chain across clients.
- Bind every delegation to an exact current canonical revision.
- Give each launched worker a distinct Agent Commons session and preserve the
  independent-review authorship invariant.
- Bound recursion, concurrency, attempts, wall time, and provider budget.
- Survive broker and child-process crashes without silently duplicating work.
- Expose useful live status and metadata-only diagnostics without storing prompts,
  reasoning, transcripts, or raw command output in canonical history.
- Keep provider and AHP integrations replaceable adapters rather than core
  dependencies.

### Non-goals

- Arbitrary model-to-model conversation or unbounded recursive delegation.
- A general shell, job scheduler, IDE session host, or remote multi-tenant service.
- Driving an already-open private Codex or Claude UI tab.
- Treating agent count, completion, or provider success as verified project truth.
- Authenticating a human from self-declared Commons session metadata.
- Implicitly committing, pushing, merging, deploying, publishing, contacting
  people, approving permissions, or widening a parent's authority.
- Persisting chain of thought, full prompts or responses, file contents, tool
  arguments or results, environment variables, or complete stdout/stderr.

## Canonical delegation contract

A delegation is a durable request for one agent session to perform one purpose on
one exact subject revision. Its stable public terms are:

- `delegation_id`;
- `target_ref` and `target_revision`;
- `target_profile` (`codex-builder`, `codex-independent-reviewer`,
  `claude-builder`, or `claude-independent-reviewer`);
- `purpose` (`implementation`, `independent_review`, or `verification`);
- `parent_session_id`, optional `parent_delegation_id`,
  `root_delegation_id`, and derived `depth`;
- immutable limits for maximum depth, wall time, attempts, concurrency, and one
  provider budget;
- on start, a distinct `child_session_id` and the selected attempt number;
- a bounded state and sanitized reason/result metadata.

`target_ref` may name a canonical entity the manager can resolve. At request
time, `target_revision` must equal that entity's current effective immutable
revision. A delegation cannot target itself or an ancestor delegation. A later
target change does not retarget the request: the worker's result remains bound to
the original revision and normal staleness rules apply.

Purpose and profile are a closed pairing: `implementation` requires a `*-builder`
profile, while `independent_review` and `verification` require a
`*-independent-reviewer` profile. A caller cannot relabel a writable builder as a
reviewer to evade the read-only profile or authorship rules.

The canonical lifecycle is:

```text
requested ── start ──> active ── succeed ──> succeeded
    │                    │
    │                    ├── input-needed ──> input_needed ── resume ──┐
    │                    │                                             │
    │                    └─────────────────────────────────────────────┘
    │
    └── cancel | fail | time-out | needs-operator

active | input_needed ── fail | time-out | needs-operator
```

`succeeded`, `failed`, `cancelled`, `timed_out`, and `needs_operator` are
terminal. Retrying terminal work creates a new delegation rather than rewriting
history. `input_needed` records only a bounded sanitized summary; failure and
operator-needed outcomes also use a bounded reason-code vocabulary. Sensitive
interactive input stays in the local runtime channel; a durable requirement or
decision belongs in the normal typed Commons workflow.

Canonical transitions state what was requested and what outcome was observed.
Queue position, PID, process handle, heartbeat, provider transport, partial
output, cancellation intent, and launch reservations are operational data and
never become project truth.

## Limits and delegation lineage

Limits are validated before publication and cannot be widened by a descendant:

- `max_depth` is between 0 and 8;
- `wall_time_seconds` is between 1 and 86,400;
- `max_attempts` and `max_concurrency` are between 1 and 32;
- a provider budget has a positive bounded amount and an explicit unit
  (`tokens`, `micro_usd`, or `provider_units`).

The executable MVP accepts only `provider_units` and `micro_usd` at the broker
boundary. One `provider_units` unit authorizes one provider-process attempt and
does not select or change a billing account. `micro_usd` is explicit opt-in to a
provider-native monetary cap when that profile can enforce one. `tokens` remains
reserved in the canonical schema but fails before reservation because the local
adapters cannot enforce it. A local Claude Code subscription stays selected by
the already authenticated Claude CLI; Agent Commons never falls back to API
credits or changes credentials.

The broker also applies operator-configured global, per-provider, per-profile,
aggregate parent-budget, queue-capacity, and queue-wait limits shared through
the operational state root. Effective child authority and budgets are the
minimum of the parent grant, delegation, target profile, and operator policy.
The first rollout uses depth 1 by default. A child may not create a sibling
through a second client to evade lineage or budget checks.

Every worker gets a newly registered Commons session. Launching a reviewer does
not waive the existing independent-review rule: `CommonsManager` still rejects a
reviewer in the target task's accumulated work-author set. Model-family diversity
is useful review context, not proof of independence or correctness.

## Broker trust boundary and grants

The broker is a trusted local process controlled by the operating-system user.
It is the only component permitted to translate a delegation into a provider
process. A canonical delegation is an execution request, not authority to honor
it.

Launch authority comes from an operator-managed local grant associated with the
authenticated broker connection. Self-declared principal, model, role,
capability, task text, thread content, and MCP arguments are untrusted data. The
grant specifies at least:

- permitted workspace and caller identity;
- permitted target profiles and purposes;
- read-only or writable workspace mode;
- maximum lineage depth, concurrency, wall time, attempts, and provider budget;
- whether interactive input and cancellation are allowed.

Provider profiles are named, operator-controlled records. A profile fixes the
adapter, executable, argument template, working-directory policy, environment
allowlist, permissions, and limits. The public delegation API accepts a profile
ID and typed parameters only. It never accepts an executable, arbitrary argv,
shell fragment, environment mapping, or credential. A bounded work instruction
is untrusted ephemeral input: the runner sends it through stdin, never
interpolates it into argv or the environment, and never persists it in canonical
or operational state.

Local authentication should use an operating-system-protected endpoint and
short-lived broker credential stored outside the canonical workspace. Loopback
TCP without authentication is not acceptable. Remote and hostile-user isolation
remain out of scope and require a new service threat model.

## Provider runner contract

Each provider adapter implements one versioned runner contract over a validated,
immutable launch plan:

```text
capabilities() -> runner/version/features
prepare(plan)  -> validated launch description
launch(plan)   -> opaque process/provider handle
observe(handle) -> running | input_needed | exited | unknown
provide_input(handle, value) -> acknowledgement
cancel(handle, deadline) -> stopped | still_running | unknown
recover(attempt) -> running | exited | absent | ambiguous
```

The initial Python boundary expresses these concepts as `ProfileRegistry`,
`RuntimePolicy.derive_child`, `CorrelationIds`, `BrokerRequest`,
`LocalBroker.run`, `BrokerResult`, `AttemptStore`, and `SubprocessRunner`.
Cancellation, recovery, and provider-session continuation may be implemented in
stages behind that versioned boundary; an unavailable capability fails closed
rather than being emulated unsafely.

The plan contains the delegation and attempt IDs, exact target reference and
revision, selected profile, purpose, workspace/scope, child session bootstrap,
deadline, inherited limits, and a one-use launch token. The broker builds the
provider-specific invocation from the profile. Adapters return structured status
and opaque provider session IDs; they do not interpret or write canonical
lifecycle state directly.

Capability and version mismatch fails before launch with a safe classification.
Provider output is untrusted. A runner may summarize a bounded diagnostic and
return canonical references produced through Agent Commons, but raw output is
not copied into events. Codex non-interactive execution, Claude headless/SDK
execution, and a later AHP connection are separate adapters behind this contract.

The credential-free broker preflight starts only provider `--help` and MCP
`--preflight` processes. Contract v2 compares every generated provider flag, the
purpose-specific worker tool allowlist, its catalog digest, and a full Python
source fingerprint between the invoking CLI and separately installed MCP
runtime. Malformed output, a different purpose catalog, or a stale build fails
closed without allocating a delegation attempt or starting model work. The
operator-owned profile file used for preflight must be the same file supplied to
the runtime-enabled MCP server or `broker run`.

## Crash, retry, and cancellation semantics

The broker keeps an fsync-safe operational attempt journal below the ignored
runtime state directory. For each launch it:

1. locks the current delegation and checkout scope;
2. revalidates the exact target, grant, limits, claims, and profile;
3. writes a unique `attempt_id`, launch token, and `launching` reservation;
4. starts an inert, shell-free exec gate in its own process group;
5. records the gate PID and distinct child session;
6. appends canonical `delegation.started` while the gate consumes no provider
   startup time and has received no instruction bytes;
7. sends a fixed control frame followed by the ephemeral instruction; the gate
   strips only that frame and `execve`s the fixed provider argv in place, so the
   recorded PID and process group remain authoritative;
8. observes the provider until a canonical terminal outcome can be recorded.

The gate is required because canonical publication and projection rebuilds may
take longer as the ledger grows. Starting a real headless provider before that
durable barrier can trigger the provider's own stdin/startup timeout even though
the broker is healthy. A failed durable-start hook terminates the inert gate and
never starts the provider or discloses the instruction.

Only one attempt for a delegation may be live. Automatic attempts are limited to
failures that occur before `delegation.started` and only when the journal proves
that no child can still be running. After canonical start, an unexpected process
exit produces `failed`; an ambiguous process state produces `needs_operator`.
The broker never blindly relaunches after a crash. A terminal retry is a new
delegation with a new idempotency identity.

The runtime's operational retry flag therefore remains subordinate to canonical
state: integration must reject it unless the delegation is still `requested`.
An operational failure after a process became identifiable must first drive the
corresponding canonical terminal transition; it cannot be hidden by another
attempt under the same delegation.

On restart, the broker reconciles every non-terminal journal entry against the
canonical delegation, process start fingerprint, provider handle, and child
session. It resumes observation only when identity is exact. If it cannot prove
that a process is alive or absent, it fails closed to `needs_operator` rather
than creating a possible duplicate worker.

For an unlaunched request, cancellation can transition directly to `cancelled`.
The process runner has a bounded stop primitive, but this protocol version does
not yet expose authenticated active cancellation through CLI or MCP. Once work
is active, the operator stops the provider, then reconciliation records
`timed_out`, `failed`, or `needs_operator` according to the evidence; it never
turns a ledger state into `cancelled` without a canonical stop receipt. Failure
to prove termination records `needs_operator`. Stopping a process never claims
that an external side effect was undone.

Canonical writes retain the existing expected-revision and stable idempotency
requirements. The broker does not weaken receipt recovery or bypass the shared
canonical-write lock.

## Checkout and worktree constraint

The initial runtime permits at most one writable worker in a checkout scope. A
broker-held runtime lease and the normal narrow Commons claims must both permit
the work; neither is Git ownership. A parent must stop writing overlapping paths
before transferring writable work to a child.

A read-only reviewer may share a quiescent checkout only when the adapter can
enforce read-only execution and the exact subject is immutable. Otherwise the
operator supplies a separate worktree or immutable snapshot. The broker does not
create, switch, reset, commit, or delete Git worktrees implicitly. Distinct
worktrees retain the checkout-aware receipt behavior defined by
[ADR 0003](0003-ledger-derived-checkout-aware-receipt-recovery.md).

## MCP and CLI adapter boundary

One optional MCP server exposes bounded Agent Commons operations to Codex,
Claude, and future clients. MCP handlers parse transport values, resolve the
authenticated broker grant, and call `CommonsManager` or the broker API. They do
not publish files, mutate SQLite directly, duplicate lifecycle validation, or
invent a second write path.

The stable orchestration concepts are:

- create/request a delegation;
- list and show delegation status;
- report start, input-needed/resume, and terminal outcomes through the manager;
- inspect operational attempts and reconcile ambiguous outcomes.

Provider input continuation and authenticated active cancellation remain
versioned runner capabilities, not exposed operations in the current headless
MVP.

CLI commands expose the same domain operations for diagnostics and recovery.
The MCP surface is intentionally not a generic `run(command, env, prompt)` tool.
Transport names may evolve before the first stable release, but entity, state,
revision, and authority semantics may not diverge between CLI and MCP.

## Observability and privacy

The implementation separates three stores:

| Layer | Purpose | Authority |
| --- | --- | --- |
| Canonical ledger | delegation intent, exact subject, state, outcome, provenance | authoritative project history |
| Runtime journal/status | queue, attempt, process handle, heartbeat, cancellation, bounded local diagnostics | recoverable operational state |
| Telemetry | lifecycle milestones, duration, byte counts, failure classification, and correlation IDs | optional, lossy diagnostics |

Local status is useful without an external collector. The current CLI exposes
delegation list/show plus a bounded attempt snapshot and explicit reconciliation;
it has no watch stream. An optional OpenTelemetry sink emits short-lived
milestone spans with the same correlation attributes as local JSONL. It does not
yet propagate one shared span context through provider or MCP processes. A no-op
sink remains the default, and deletion, loss, sampling, or exporter failure
cannot change canonical behavior.

The implemented signals are lifecycle kind/state/reason, duration, PID/exit code,
bounded-output byte counts/truncation, provider/profile, correlation IDs, queue
wait/depth, canonical finalization phase/state/reason, process/canonical
mismatch, and content-free terminal-tool call/rejection/completion counters.
Provider-reported budget totals and long-lived causal spans remain future
additions. Unique task, session, delegation, and attempt IDs must not become
metrics labels with unbounded cardinality.

Prompts, responses, reasoning, complete conversations, file contents, tool
arguments/results, shell commands, environment variables, credentials, and raw
stdout/stderr are excluded by default. If bounded provider logs are enabled for
local diagnosis, they live below ignored operational state with restrictive
permissions and explicit retention. Export endpoints and credentials are
operator configuration and pass through the normal secret boundary, never the
ledger.

## Compatibility, migration, and rollback

The runtime is disabled by default and adds no mandatory daemon, provider SDK,
MCP, or OpenTelemetry dependency to file-ledger use. Core CLI workflows remain
available when the broker is absent. New provider integrations are optional,
version-pinned extras with contract tests and explicit capability negotiation.

Delegation schemas and events are additive, but a checkout containing them
requires a reader that knows those schemas; older fail-closed binaries are not
promised forward compatibility. Runtime operational formats are versioned and
rebuildable from canonical state except for live process identity, which is
reconciled conservatively.

Rollback is:

1. stop accepting new delegations;
2. stop each live provider under operator control and explicitly reconcile/classify its attempt;
3. stop the broker and disable its local grants/MCP configuration;
4. continue using the current core CLI against the preserved ledger;
5. retain canonical delegation history and discard only confirmed-dead,
   non-authoritative runtime state.

Rollback never edits or deletes canonical events and never assumes that stopping
a process reversed its external effects. Downgrading to a binary that does not
know delegation schemas is supported only before delegation events are written
or after upgrading that reader; disabling the runtime is the normal rollback.

## Staged rollout and acceptance gates

1. **Domain-only:** schemas, replay/projection, manager and CLI lifecycle, fake
   runner contract, exact-revision and lineage tests; no process launch.
2. **Shared MCP:** bounded read/coordination tools over `CommonsManager`, local
   authentication, parity tests, and no arbitrary execution parameters.
3. **Read-only vertical slice:** Codex-to-Claude independent review, then the
   reverse direction, with one quiescent checkout and explicit user budgets.
4. **Writable delegation:** operator-provisioned checkout/worktree, exclusive
   runtime lease, claims, cancellation, crash injection, and provider failure
   classification.
5. **Diagnostics:** local status first, optional metadata-only OpenTelemetry
   export, retention/redaction tests, and optional AHP observer/runner adapter.

Each stage is separately feature-gated and removable. Acceptance requires
behavior-focused schema and lifecycle tests, fake-runner contract tests, CLI/MCP
parity, duplicate-launch and crash-boundary tests, cancellation/timeout tests,
lineage and budget enforcement, worktree/claim conflicts, secret and output
redaction, full repository tests and static checks, and independent review bound
to the exact submitted revision. Real-provider end-to-end tests are explicit and
opt-in; deterministic CI must not require provider credentials or network access.

## Alternatives considered

- **Adopt AHP as the core.** Rejected. AHP can later provide a session-host
  adapter and useful reconnect conventions, but it does not replace Commons
  project truth, delegation policy, or revision-bound governance and remains an
  external evolving dependency.
- **Expose each provider directly as MCP.** Rejected as the authority, lineage,
  budgets, process recovery, and audit semantics would diverge by client.
- **Use durable chat messages as work requests.** Rejected because prose is
  untrusted and has no bounded lifecycle, exact revision, idempotency, or
  cancellation contract.
- **Let agents supply arbitrary command lines.** Rejected as a shell-injection
  and confused-deputy boundary that makes grants unreviewable.
- **Make the daemon mandatory.** Rejected because offline inspectability and the
  file ledger are core product properties.

## Consequences

- Users can replace manual window-to-window prompting with explicit, bounded
  local delegation while retaining an inspectable audit trail.
- The broker becomes a security- and reliability-sensitive component with a
  stricter trust boundary than ordinary Commons sessions.
- Provider adapters and their version drift require ongoing ownership and
  contract tests.
- Exact revisions, conservative crash recovery, and one-writer checkout limits
  trade some automation speed for explainable, reversible behavior.
- Agent Commons remains useful without the runtime, and AHP, OpenTelemetry, and
  provider SDKs remain optional edges rather than architectural authorities.
