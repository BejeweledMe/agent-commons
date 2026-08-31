# Experimental broker operations

The optional local broker is manually enabled alpha software. A static
`broker preflight` is necessary after provider or Agent Commons upgrades, but it
is not behavioral proof: it checks fixed flags, source fingerprints, and the
purpose-specific MCP catalog without starting model work.

## Behavioral contract gate

The deterministic real-stdio canary must prove all of these in one isolated
state root:

1. a distinct child session binds to the exact active delegation;
2. the worker sees only its bounded MCP catalog and immutable scoped source;
3. an actual MCP write records a revision-bound review/result;
4. a terminal tool call is counted without retaining arguments or content;
5. process completion is followed by canonical finalization;
6. the final delegation state and result references, not provider prose, satisfy
   the grader.

For an independent review, the worker must call `commons_finalize_review` once.
That retry-convergent terminal operation records the existing `review.completed`
event and then `delegation.succeeded` with the bound `review:<id>`; a prose-only
answer, a partial first event, or process exit zero is not canonical completion.
The provider supplies only `verdict` and bounded `summary`; the server derives the
operation identity, review/delegation identities, exact revisions, result reference,
and immutable manifest evidence. Approval requires successful scoped reads of every
artifact bound to the exact task revision. If the process loses the response between
the two events, the server-bound operation converges the missing delegation result.
Task acceptance refuses an orphan delegated approval unless the matching delegation
also reached `succeeded` with exactly that review result.

CI runs this contract without credentials or network access. Static preflight,
the adapter-owned provider initialization probe, and the behavioral canary are
three intentionally separate signals. Claude initialization uses the fixed
no-model `mcp list` operation; Codex uses the fixed `app-server --stdio`
operation. Neither operation accepts workspace argv or counts as a delegation
attempt.

## Packaged skill projection

A role may select a skill identity from the operator catalogue, but a provider
launch accepts only the packaged `commons-*` allowlist. The catalogue's
instruction text is never copied into provider stdin. Codex and Claude adapters
project the packaged `SKILL.md` bytes into their own `.agents/skills` and
`.claude/skills` envelopes and bind source, projection, and installer-contract
SHA-256 digests into the exact fingerprinted invocation.

An unknown, missing, oversized, stale, or altered projection fails with
`skill_projection_unavailable` before initialization/auth probing, child-session
creation, or attempt reservation. There is no workspace skill discovery and no
fallback that silently drops a requested skill. Empty skill selection preserves
the existing invocation bytes. Only digests may appear in operational metadata;
skill source/text remains ephemeral and never enters canonical events, attempts,
telemetry, or UI DTOs.

Before calling one real provider build compatible, run the explicit canary from
the exact installed Agent Commons source:

```bash
agent-commons --json broker canary \
  --confirm-provider-run \
  --wall-time-seconds 300 \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

This command may consume subscription or billable provider capacity, so the
confirmation flag is mandatory. It creates and removes its own temporary Git
workspace and operational state, permits one `provider_units` attempt, and uses
the configured `claude-independent-reviewer` model and any configured native
budget ceiling. It never reviews or mutates the current project.

The JSON report records the Claude CLI version only when it matches the strict
numeric `N.N.N (Claude Code)` grammar; every other provider-controlled line
becomes `null`. It also records the configured model, Agent Commons source
fingerprint, MCP catalog digest/count, process byte and duration counters,
child-session closure, canonical state, mismatch flag, and terminal-tool
counters. Exit status 0 requires exactly one completed terminal tool, zero
rejected terminal calls, a typed review result, and canonical `succeeded`.
Preflight failure or prose-only provider exit returns status 2. Use separate
operator-owned profile files to qualify Fable, Opus, or another explicit model;
one model's pass is not evidence for another. Builder and independent-reviewer
profiles are qualified separately. A passing canary writes a private
operational receipt under the selected state root, bound to the exact profile,
model, adapter capabilities, Agent Commons source, and provider executable
bytes. The receipt is derived host state, never a canonical event. A missing,
failed, or stale receipt makes the profile non-launchable and returns a typed
remediation before a child session or attempt is created.

## Operator caps and backpressure

The operator-owned runtime YAML may lower global, per-provider, per-profile,
aggregate parent budget, queue capacity, and queue wait limits. All broker
processes sharing a state root use the same atomic admission journal. Effective
authority is the minimum of operator, provider, profile, parent, and canonical
delegation bounds.

`provider_units` counts process attempts, including failed attempts. A
`micro_usd` delegation budget is divided across its maximum attempts; aggregate
committed monetary caps are checked before reservation. Capacity waits in a
bounded FIFO queue. A full or expired queue fails with explicit backpressure and
does not allocate an attempt. If admission fails after allocating a prospective
child identity but before an attempt exists, the broker closes that unbound
child session immediately.

## Initial SLIs and SLOs

Use absolute counts until at least 30 launches exist for one provider/version.

| Journey | SLI | Initial objective |
| --- | --- | ---: |
| Deterministic broker contract | passing matrix cases / all cases | 100%, blocking |
| Canonical completion | valid canonical terminal results / launches | ≥95% after 30 runs |
| Safety | process exit incorrectly promoted to approval | 0 always |
| Finalization latency | process exit → canonical terminal | p95 ≤5s, p99 ≤15s |
| Diagnostic coverage | abnormal outcomes with actionable code/actions | ≥99% |
| Deadline containment | terminal/escalated by wall time + 60s | 100% |
| Cost visibility | supported exits with safe usage totals | ≥95% |

Alert or stop locally on child/session/state-root mismatch before launch,
process completion without canonical terminal state for 10 seconds, active work
past deadline, output above 75% of its cap, `invalid_result`, telemetry sink
failure, or caps near exhaustion. A deterministic P0 regression or false
approval is stop-the-line; below 20% error-budget remaining, keep manual opt-in
only.

## Release evidence gate

The broker remains experimental and manually enabled until all of the following
are recorded for the exact release candidate:

- green behavioral canary on macOS and Linux;
- 100 consecutive hermetic contract runs for each supported provider;
- 20 real local launches across supported profiles;
- zero `process_canonical_mismatch` events in those real launches.

Telemetry is metadata-only and non-authoritative. Count unique attempt IDs and
join `process_finished` with
`canonical_finalization_started/completed/failed`; never treat process success
alone as workflow success.

## Recovery

Use `broker attempts --diagnostic`, canonical `delegation list/show`, and
`broker reconcile`. Reconcile never blindly relaunches ambiguous work. If
process identity, child state, terminal tool outcome, or canonical finalization
cannot be proven, preserve the attempt and transition to `needs_operator`.
Attempts owned by another live requester remain isolated. If that requester is
unavailable, reconcile returns `requester_unavailable` with safe next actions
and makes no canonical change. Only canonical `requested` work may then use the
explicit `delegation:recover` operator path; active work still requires proven
provider termination and owner-aware reconciliation.

## Upgrading and rolling back operational state

Runtime request and attempt documents are at schema **v4**, which records the
delegation tree a launch belongs to. v3 and v2 are read and upgraded in memory,
so upgrading needs no migration.

Rolling back does. A build older than v4 refuses a v4 document by envelope and
its `reserve` and `reconcile` both fail. If you must go back, finish or abandon
in-flight work first, then remove `runtime/requests` under the state root. That
directory is operational, not canonical: nothing in the ledger depends on it.

## Stopping a running provider

`broker stop <delegation-id>` signals the recorded provider process group,
`--force` sends `SIGKILL` instead of `SIGTERM`. Only the session that requested
the delegation may stop it.

The command writes no canonical outcome. It records intent by moving the attempt
to `cancel_requested`, then you run `broker reconcile`, which refuses to record
an outcome while the process is still alive and reports it with its pid instead.
Once the process is gone, reconcile records `operator_stop_requested` — not
`broker_restart_ambiguous`, which would name a cause that never happened.

Termination is signalled by recorded pid. After a long-idle attempt the pid may
have been reused by an unrelated process of the same user, and checking that the
pid still exists cannot tell the two apart. Check `broker attempts --diagnostic`
before forcing a stop.

## Withdrawn: run observability projection

The former `run list` and `run export <run-id>` surface and its disposable run
store were withdrawn by [ADR 0008](adr/0008-run-observability-store-withdrawn.md).
There is no current `agent-commons run` command, and this historical proposal
must not be used as an implementation or release contract.

For current broker metadata and recovery use `delegation list/show`,
`broker attempts --diagnostic`, and `broker reconcile`. Provider process exit
and provider prose still never substitute for canonical finalization.

## Local UI

`agent-commons ui` binds `127.0.0.1` only — there is deliberately no `--host`
flag — requires a bearer token printed at startup, and pins the `Host` header
to loopback. By default it opens its own operator session and records
canonical events under it, the same way the CLI and MCP adapters do; pass
`--read-only` for a view that opens no session, records nothing, and registers
only `GET` routes.

Opening a browser automatically puts the token in the URL, which is visible to
other processes of the same user through the process list. Use `--no-browser`
and paste the URL yourself where that matters.
