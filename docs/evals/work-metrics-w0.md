# W0 work metrics and deterministic evaluation contract

## Status and boundary

**W0 specification — documentation and fixture contract only.** It makes the
work-loop diagnosis reproducible before a production read surface exists. It is
not a dashboard, a task command, a scheduler, a canonical metric event, or an
authority decision.

The contract is derived and read-only. It consumes a projected snapshot plus a
sanitised synthetic event sequence and an injected clock. It must not write
events, manifests, operational state, or a provider transcript; read live
workspaces in fixture tests; or add a method to `CommonsManager`, root CLI,
MCP builder, `UIContext`, or legacy static UI.

The public provider-free `agent_commons.evals` catalog remains in place. The
new test-only W0 harness belongs under `tests/evals_harness/`, as required by
the target audit map. This contract does not move or redefine the public
catalog.

## Observation contract

Every future calculation yields a typed, frozen `MetricObservation`. It has no
`dict[str, Any]` metadata bag and it is never persisted as a canonical event.

```text
metric_id
definition_version
query_version
state: complete | empty | not_measurable | unsupported
numerator | null
denominator | null
value | null
sample_size
exclusions: tuple[MetricExclusion, ...]
source_snapshot_sha256
fixture_sha256 | null
code_revision
ledger_semantics_version
window_start | null
window_end | null
policy_version | null
observed_at
```

`source_snapshot_sha256` hashes canonical serialisation of the selected
snapshot. `fixture_sha256` is present only for synthetic input. `observed_at`
is injected by the caller; fixtures use fixed UTC timestamps. A zero
denominator produces `empty` and `value=null`, never a misleading 0% or 100%.
`unsupported` says that the current ledger lacks the required semantic input;
it is not a passing result.

## Version 1 metric dictionary

| Metric | Exact definition | State and failure action |
| --- | --- | --- |
| Current review coverage | `paired_current_review_tasks / task.state == review`; a pair has a requested, non-stale, independent review whose target and target revision equal the task effective revision. | A missing pair is included, not excluded. Hold the work-loop change and investigate the route. |
| Review queue age | p50/p90 of `now - recorded_at` on the current `task.submitted` event for current review tasks. | Invalid/missing submission time is `not_measurable`; do not call it zero age. Product/review owner investigates ageing. |
| Review disposition latency | p50/p90 of valid `review.completed_at - review.requested_at` joins by review ID and expected revision. | Open requests remain in coverage, not latency. Bad join is an integrity failure. |
| Evidence churn | stale completed reviews / all completed reviews. | Diagnostic only; never optimize it by suppressing staleness. |
| Handoff acknowledgement latency | p50/p90 of `acknowledged_at - created_at`, plus open handoffs older than 14 days / all open handoffs. | Ack without a valid create is input-integrity failure, not an exclusion. |
| `needs_operator` rate | current `needs_operator` delegations / terminal delegations, where terminal is succeeded, failed, cancelled, timed out, or needs_operator. | Live states are excluded. Runtime owner classifies before claiming improvement. |
| `needs_operator` taxonomy completeness | needs_operator records with a known reason code / all needs_operator records. | Must be 100% before rate improvement is announced. Unknown is visible, not silently grouped. |
| Rework rate | task reviews completed `changes_requested` / all completed task reviews. | Diagnostic. Recovery requires a later independent approval on a new task revision. |
| Operator attention load | current count of needs_operator + proposed decisions + open handoffs to operator + uncovered review tasks. | Point-in-time diagnostic, not an automatic queue or action. |
| False strict acceptance | invalid applied `task.accepted` transitions / all applied task.accepted transitions, checked by prefix replay. | L1 hard invariant: 0. Disable/invalidate the faulty path and add a regression. |

The following metrics are intentionally `unsupported` until their inputs exist:
local parent closure coverage, required-review pairing coverage, invalid local
closure, finalisation integrity, stale launch, objective coverage, attention
precision, handoff action health, and operator routing load. W0 must not infer
them from matching text, provider summaries, or untyped extensions.

Known `needs_operator` vocabulary for W0 is limited to the current typed set:
`provider_unavailable`, `provider_auth`, `rate_limited`, `policy_denied`,
`launch_failed`, `runtime_error`, `invalid_result`, `integrity_error`,
`budget_exhausted`, `orphaned`, and `unknown`. Future finalisation gap labels
need their own semantic decision; they cannot be silently derived here.

## Synthetic corpus and graders

The initial corpus is synthetic-only and uses fixed IDs, fixed UTC timestamps,
and no copied workspace ledger:

```text
tests/evals_harness/
  fixtures/work_metrics_v1.json
  fixture_loader.py
  work_metrics_cases.py
  test_fixture_privacy.py
  test_work_metrics_l1.py
tests/services/test_work_metrics.py
```

The fixture loader allowlists event/payload fields and rejects prompts, messages,
transcripts, reasoning, stdout, stderr, tool arguments, credential-like keys,
absolute paths, human names, task text, and provider output. It rejects a
fixture with a random timestamp or unknown top-level data rather than dropping
that data.

`work_metrics_v1` must include: a valid current review pair; missing, stale, and
non-independent requests; approved and changes-requested reviews; acknowledged,
fresh-open, and aged-open handoffs; all terminal delegation states; known and
unknown reason codes; empty denominator; valid and intentionally invalid strict
acceptance; duplicate/reordered retry; and repeatability with the same fixed
clock.

L0 checks pure formulas, revision equality, stable order/hash, empty and
unsupported states. L1 replays the synthetic event sequence to check correction,
staleness, order/CAS behaviour, and the false-strict-acceptance invariant. No
LLM judge or fake provider is useful for these deterministic contracts.

## Gates and ownership

| Gate | Requirement | Owner and response |
| --- | --- | --- |
| Presubmit L0/L1 | Fixtures load from a fresh temporary workspace; deterministic cases pass; privacy loader rejects unsafe input. | QA/ML-evals; block merge. |
| Replay | Unchanged behaviour replays equivalently from the golden corpus. | Python/backend; stop and repair the projection. |
| Metric usability | Every complete metric names a product, workflow, runtime, or governance owner and a failure action. | Program lead; do not expose a vanity dashboard. |
| Human interpretation | `empty`, `not_measurable`, and `unsupported` are displayed as such. | Product/UX; do not convert them to a success rate. |
| Future production read | G1 confirms typed seams and owner approval before any read surface. | Program lead and relevant domain owner. |

The fast local command, once the planned files exist, is:

```bash
env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE \
  uv run --locked python -m pytest -q tests/evals_harness tests/services/test_work_metrics.py
```

Every commit still runs the repository green contract:

```bash
env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE make check
```

## Sequencing and non-blocking decisions

This document is W0 only. It does not authorize W1 work-state reads, W3 local
closure, W4 finalisation, W5 pull planning, Context Pack, Design Package, or
Gallery feedback. D5 (objective binding), D7 (L3 retention/privacy), H0/W3
(closure/review semantics), and W4 (finalisation attestation) each provide
inputs for later metrics but do not block the documented W0 baseline.
