# Offline workflow evaluation catalog

`agent_commons.evals` is a versioned, deterministic 25-case catalog for the
Agent Commons workflow roadmap. It is intentionally provider-free: no model,
network service, prompt, response, transcript, secret, or customer data is
required or retained.

## Scope and truthful outcomes

The catalog is `2026-08-wave4-v1`. Cases are one of:

- `implemented`: a deterministic fixture exercises a released P0 contract.
- `planned`: a requested workflow is specified but not yet executable in this
  harness. The result is `planned_capability`, never `passed`.
- `unsupported`: the workflow is intentionally outside the offline harness;
  its result is `unsupported_offline_harness`, never `passed`.

The eight executable P0 cases cover workspace state-base isolation, foreign
exact-root rejection, ambiguous legacy-state refusal without mutation,
read-only non-mutation, support-path privacy, explicit session selection,
shell export, and typed-reference diagnostics. Every executable trial receives
a fresh temporary repository and state directory.

The other catalog cases preserve the requested coverage map: claims, DAG and
critical path, task input, resume, active cancellation, crash reconciliation,
path attestation, compact orient/inbox, council, routing, budget, stale state,
observability, secret rejection, and provider failure convergence. They are
backlog specifications, not release evidence.

## Graders and fake provider

Implemented cases use deterministic state or CLI checks. The
`DeterministicFakeProvider` accepts only a catalog identity and emits fixed
action codes. It has no prompt or command input and is a harness-contract
double, not evidence that a real provider can perform a workflow.

Results retain only case ID, terminal status, bounded outcome code, aggregate
numeric metrics, and SHA-256 evidence digest. There are no raw traces,
transcripts, user prompts, provider output, paths, tokens, or high-cardinality
labels in the records.

## Metrics and gates

`aggregate_metrics` reports pass@1 for executed cases, pass^k as all executed
trials passing, needs-operator rate, total latency milliseconds, provider-unit
count, and handoff-loop count. Planned and unsupported cases are excluded from
pass@1/pass^k denominators and remain visible as separate counts.

Run the small, hermetic suite in local and presubmit CI:

```bash
pytest -q tests/evals
```

Provider canaries, crash/concurrency scale tests, and any human or LLM-rubric
calibration belong to opt-in post-submit or release stages once their underlying
features exist. A failing executed case blocks the deterministic suite; a
planned or unsupported case is a backlog signal, not a green release signal.
Production incidents should become a new versioned case with a deterministic
grader where possible; otherwise add an explicit human-calibration plan before
using it for release approval.
