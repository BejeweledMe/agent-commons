# Roadmap

Agent Commons develops a local application for work by agent teams, backed by
an immutable shared-filesystem protocol. The [implementation programme](agent-platform-implementation-program.md)
is the single current-work list and links exact verification boundaries. This
page records direction; it does not certify shipped or accepted work.

## Release qualification first

L1 remains unproven and R2 release evidence remains open. Resolve the remaining
Grok transport contract, receipt-scope and provider-host gates; qualify the six
implemented profiles on the intended source/provider/host boundary with separate
operator authorization, then obtain independent release review. Earlier successful
canaries do not override later failures. Retention and stale coordination also
need explicit owner decisions.

## Daily work

Build on Work, Team, Library and Settings, with clear task outcomes, exact context
sources, independent review and human acceptance. Further work includes inbox
cursors and acknowledgements, duplicate-work suggestions, stale-work handling,
large-graph navigation and configuration presets. The service-owned library,
five blueprints and editable graph are defined in
[ADR 0016](adr/0016-service-library-and-live-workspace.md), with verification
separate from L1. Dependency visibility does not imply automatic scheduling.

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

API providers are a separate deferred stage: Anthropic, OpenAI, Google,
OpenRouter and local vLLM. Provider-enforced structured output belongs there;
subscription CLI MCP schemas validate tool input without constraining generation.
A public provider adapter or other extension must preserve the same authority,
privacy and lifecycle boundaries. Every stage must be independently usable or
removable. Deterministic CI uses fake runners; live verification is explicit.

## Remote teams and extensions

Remote or multi-host operation requires authenticated principals, authorization,
server transactions, distributed leases, retention, encryption and administrative
audit. Local session metadata does not supply that trust boundary.

A stable public plugin ABI remains deferred until at least two substantial
external domain packs validate it. Optional domain workflows must preserve the
meaning of universal core entities.
