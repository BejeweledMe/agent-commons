# Troubleshooting Agent Commons

Start with safe metadata. These commands never need raw provider output:

```bash
agent-commons --version
agent-commons --read-only --json support
agent-commons doctor
agent-commons receipt status
agent-commons --read-only broker attempts --diagnostic
```

Use an explicit state location when the repository Git directory is read-only or
when diagnosing clone/worktree behavior:

```bash
agent-commons --state-root /operator/owned/path --read-only --json doctor
```

Global `--read-only` never creates operational directories, cache, SQLite,
sessions, claims, receipts, or recovery anchors. It validates canonical state
only; remove the flag when an explicitly authorized repair must write.

## Provider/MCP preflight

After upgrading Claude Code, Codex, Agent Commons, or MCP dependencies, run:

```bash
agent-commons broker preflight claude-independent-reviewer \
  --purpose independent_review \
  --profile-config /absolute/path/to/agent-commons-profiles.yaml
```

Preflight invokes provider `--help` and the exact generated MCP executable in
credential-free `--preflight` mode. It checks required flags and a real FastMCP
tool catalog without allocating a delegation attempt or starting model work.
Contract v2 also compares the generated profile allowlist with the exact
purpose-specific worker catalog and the SHA-256 fingerprint of all Agent Commons
Python source in the CLI and separately installed MCP runtime. A mismatched build
therefore fails before reservation even when its tool names happen to match.
Reinstall it from the same Agent Commons build and repeat preflight instead of
spending the one real provider attempt. A failed preflight exits with status 2.

## Closed diagnostic codes

Agent Commons inspects only its bounded in-memory provider buffers and persists
one closed code. It never stores the matched fragment, prompt, response,
reasoning, file content, tool payload, path, environment, credential, stdout, or
stderr.

| Code | Meaning / next safe step |
| --- | --- |
| `provider_start_failed` | Check the operator-owned profile and executable permissions, then run preflight. |
| `provider_auth_failed` | Authenticate the provider CLI directly under the intended subscription/account; Agent Commons does not switch credentials. |
| `provider_budget_exhausted` | Inspect the canonical state before any retry. Choose a new explicit budget/delegation only after proving no child remains live. |
| `unsupported_provider_flag` | Provider CLI drift; run preflight and compare the installed provider version with supported docs. |
| `mcp_config_invalid` | The provider rejected the generated strict MCP config; run preflight and report the safe support bundle. |
| `mcp_spawn_failed` | Check the configured `agent-commons-mcp` executable and optional MCP installation. |
| `mcp_handshake_failed` | Provider/MCP protocol startup failed; run preflight after checking dependency versions. |
| `mcp_binding_timeout` | The worker did not observe its canonical child binding; reconcile instead of blind relaunch. |
| `mcp_tool_contract_failed` | Required bounded MCP tools are missing/incompatible; reinstall matching Agent Commons code and run preflight. |
| `broker_control_error` | Broker lifecycle control failed; inspect canonical state and reconcile. |
| `provider_nonzero_unknown` | No safe classifier matched. Share `support`, versions, attempt ID, closed code, and byte/truncation counters—not raw output. |
| `terminal_tool_not_called` | Provider exit is not workflow success. Inspect the exact delegation and fixed worker catalog; do not promote or blindly retry it. |
| `terminal_tool_rejected` | Refresh the canonical revision, inspect content-free terminal-tool counters, and reconcile the ambiguous attempt. |
| `process_canonical_mismatch` | Join the attempt to its canonical delegation and finalization telemetry, then reconcile; never infer approval from process exit. |
| `canonical_finalization_failed` | Run `doctor`, inspect canonical state, and reconcile the terminal attempt before creating replacement work. |
| `requester_unavailable` | Reconcile did not mutate foreign-owned work. Recover only canonical `requested` work with an operator-authorized `delegation:recover` session; active work requires proven termination. |
| `legacy_unclassified` | Attempt predates sanitized diagnostics. Do not infer a cause from it. |

`agent-commons --read-only broker attempts --diagnostic` adds only
maintainer-authored fixed
hints to the stored metadata.

## Admission backpressure or budget rejection

The experimental broker applies the effective minimum of global, provider,
profile, parent-session, and delegation limits. Processes using the same state
root share one bounded FIFO admission queue and aggregate parent/provider
budgets. A queue-full, queue-expired, concurrency, `provider_units`, or monetary
budget rejection happens before a new attempt is allocated.

Inspect the operator-owned `limits` section in the runtime profile YAML and the
effective caps reported by:

```bash
agent-commons --json broker profiles --profile-config /absolute/path/to/profiles.yaml
agent-commons --read-only --json broker attempts --diagnostic
```

Do not raise a cap until canonical state proves the earlier work is terminal.
Use a separate explicit state root only when isolation is intentional; it is a
separate admission and budget domain, not a way to bypass operator policy.

## Common recovery paths

### Fresh clone or new worktree cannot write

```bash
agent-commons receipt status
agent-commons receipt reconcile
agent-commons doctor
```

Reconcile derives post-commit receipts from the validated current-checkout
ledger and a non-shrinking completeness anchor. It fails closed if anchored
events disappeared or changed.

### In-flight receipt without a canonical event

Reconcile intentionally does not delete it. Retry the identical operation with
the same idempotency key. If that is permanently impossible, inspect
`receipt abandon --help` and use a session explicitly granted
`receipt:abandon`; the tombstone is permanent audit evidence.

### Provider exited but delegation is not terminal

Do not launch another child blindly:

```bash
agent-commons broker reconcile
agent-commons delegation list
```

Ambiguous post-start work becomes `needs_operator`. Create a replacement only
after the earlier attempt is terminal and operator inspection proves no child
can remain live.

### Requested delegation belongs to an unavailable session

First confirm that the delegation is still exactly `requested` and inspect any
available attempt diagnostics. Then open a distinct operator-authorized session
with `--capability delegation:recover` and run:

```bash
agent-commons delegation recover DELEGATION_ID EXPECTED_REVISION \
  --reason 'Requester unavailable before canonical provider start' \
  --idempotency-key 'stable-recovery-key'
```

The command fails while the requester is active and for every state beyond
`requested`. It records `delegation.recovered` rather than impersonating the
requester's normal cancellation. Do not use this path for an active or
input-needed provider.

### The optional broker is unavailable

Use the manual two-window flow in the [Quickstart](QUICKSTART.md). The core CLI,
distinct sessions, exact target revisions, claims, evidence, and independent
review remain mandatory; only provider process launch becomes manual.

## What to include in a bug report

Include:

- `agent-commons --version` and provider CLI version;
- the JSON from `agent-commons --read-only --json support`;
- the closed diagnostic code, attempt/delegation IDs, exit classification,
  byte counters, and truncation flag;
- whether `broker preflight` passed;
- operating system and Python version;
- a minimal credential-free reproduction if possible.

Exclude raw provider output, prompts/responses, reasoning, credentials, private
paths, environment variables, customer data, and complete operational state.
