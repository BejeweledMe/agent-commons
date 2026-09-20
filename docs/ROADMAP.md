# Roadmap

This page records direction. It does not certify shipped or accepted work, and
it holds no status, counts or screen lists: what the product is lives in
[PRODUCT.md](PRODUCT.md), the current backlog in [`docs/plans/`](plans/)
([consolidation and wave 4](plans/2026-09-18-consolidation-and-wave-4-plan.md),
[UX closure programme](plans/2026-09-09-ux-closure-tech-plan.md)), accepted
decisions in the ledger (`uv run agent-commons decision list`), and completed
plans in [`docs/archive/plans/`](archive/plans/).

## Release qualification first

L1 (live qualification of the six provider profiles on Linux) remains unproven
and R2 release evidence remains open. Resolve the receipt-scope and
provider-host gates, qualify the profiles on the intended source/provider/host
boundary with separate operator authorization, then obtain independent release
review. Earlier successful canaries do not override later failures. Retention
and stale coordination need explicit owner decisions; the 2026-09-18 evidence
policy ([evidence/README.md](evidence/README.md)) covers the repository side.

## Daily work

Build on the board, the task tracker, agents, the library and settings with
clear task outcomes, exact context sources, independent review and human
acceptance. Open directions after the UX closure programme: first-launch
onboarding, attention outside the open tab, typed refusal text as a system,
accessibility as a gate, search and saved views, a general undo/restore
pattern, and a write path fast enough for a populated project. Dependency
visibility does not imply automatic scheduling.

Summarized workspace exchange and conformance fixtures for additional clients
remain future work. Keep user guidance focused on available behaviour.

## Runtime evolution

The [optional delegation runtime](adr/0004-optional-local-delegation-runtime.md)
retains the standalone file-ledger core. Its shared manager boundary, scoped MCP
tools, explicit budgets and checkout claims remain constraints on expansion.
The existing task-scoped communication channel does not provide provider
reattachment or by itself prove provider termination after cancellation.

Deferred work includes a long-running same-host broker, proof-driven active
provider cancellation, reattachment, changed-path attestation, DAG scheduling,
model routing, council templates, cursor subscriptions and OpenTelemetry metrics.
Recovery of requested work must not be widened to cover a possibly live process;
adjudication requires proven termination and attempt identity.

API providers are a separate deferred stage: Anthropic, OpenAI, xAI/Grok, Google,
OpenRouter and local vLLM. Provider-enforced structured output belongs there;
subscription CLI MCP schemas validate tool input without constraining generation.
A public provider adapter or other extension must preserve the same authority,
privacy and lifecycle boundaries. Every stage must be independently usable or
removable. Deterministic CI uses fake runners; live verification is explicit.

### Future role connections, SGR and conversations

Recorded from the operator's request on **2026-09-07**. This is a deferred product
direction, not implemented capability or authorization to start API/model work.
The connection contract and staged task graph are recorded in
[ADR 0018](adr/0018-role-connections-and-execution-modes.md).

Agent creation should keep the specialization and its service-owned skill
versions independent of the execution connection. A named agent selects its
connection, model and supported execution options separately:

| Connection choice | Planned examples in the agent editor | SGR option |
| --- | --- | --- |
| Subscription CLI | Codex subscription, Claude subscription, Grok subscription | Unavailable in this product mode; explain why in the UI. |
| Hosted API | Claude API, OpenAI API (working label: “Codex API”), Grok API | Optional, only after the adapter/model proves the required capabilities. |
| Local model served through an API | Local vLLM API | Optional, under the same capability checks as hosted API connections. |

These are intended product choices, not claims that every named provider already
offers an equivalent protocol. Final API names, models and supported schemas
must be verified when this stage is designed. Existing Google/OpenRouter API
expansion remains deferred alongside these choices.

- **SGR / Schema-Guided Reasoning:** offer an explicit opt-in for a schema-guided
  workflow on eligible API/local connections. API transport alone is insufficient
  evidence of support. Keep SGR unavailable for subscription CLI connections as
  the requested product boundary; this is not a claim of universal provider
  impossibility. Do not treat MCP input schemas or prompt-only JSON instructions
  as proof of constrained generation.
- **Guided and conversational execution:** plan both as explicit runtime
  capabilities. Conversational mode is separate from SGR; a dedicated ADR must
  define whether/how they compose, conversation identity, context retention,
  human input, cancellation and continuation. It must not silently imply that
  today's subscription CLI runs can reattach.
- **Agent editor:** selecting an API/local connection reveals supported advanced
  options such as “Enable SGR” and schema selection. Changing the connection
  clears incompatible options and explains the change before the next launch.
  Preserve the specialization and exact skill pins; never silently downgrade a
  requested execution mode.
- **Implementation entry gate:** first agree the connection/capability contract
  and ADR, then implement one hosted API adapter and the local vLLM path, followed
  by execution modes and UI. Define private credential/endpoint configuration,
  budgets, cancellation, retry and retention before enabling runs. Canonical
  evidence contains bounded metadata, not raw conversation or reasoning logs.
  Qualify each advertised adapter/model/mode combination independently; tests and
  separately authorized live checks remain distinct. This does not close L1/R2.

## Remote teams and extensions

Remote or multi-host operation requires authenticated principals, authorization,
server transactions, distributed leases, retention, encryption and administrative
audit. Local session metadata does not supply that trust boundary.

A stable public plugin ABI remains deferred until at least two substantial
external domain packs validate it. Optional domain workflows must preserve the
meaning of universal core entities.
