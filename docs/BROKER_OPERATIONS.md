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

For an independent review, the worker must call `commons_complete_review` and
then `commons_succeed_delegation` with the resulting `review:<id>`. A prose-only
answer, a completed review without the delegation result, or process exit zero
is not canonical completion.

CI runs this contract without credentials or network access. Static preflight
and the behavioral canary are intentionally separate signals.

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
one model's pass is not evidence for another.

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

## Run observability projection

`run list` and `run export <run-id>` read the disposable run store under the
state root. Both are read-only and never create it: on a workspace that has
never produced run events, `run list` answers with nothing and leaves no file
behind.

Retention never exports on your behalf. `run export` is how a run is kept beyond
its configured tier, and it must be run before the tier expires.

Note the current limitation plainly: no producer writes to this store yet. The
broker does not emit run events, so these commands operate on an empty
projection until the streaming runner exists.

## Local read-only UI

`agent-commons ui` serves a read-only view of the workspace. It binds
`127.0.0.1` only — there is deliberately no `--host` flag — requires a bearer
token printed at startup, pins the `Host` header to loopback, and registers only
`GET` routes. It records no canonical event.

Opening a browser automatically puts the token in the URL, which is visible to
other processes of the same user through the process list. Use `--no-browser`
and paste the URL yourself where that matters.
