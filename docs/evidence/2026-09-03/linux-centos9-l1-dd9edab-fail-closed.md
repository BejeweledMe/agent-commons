# CentOS 9 L1 qualification evidence for dd9edab

Date: 2026-09-03

Platform: Linux, CentOS Stream 9, x86_64

Exact commit: `dd9edab1fb9e6550b28be633ecdf57f2479b2faa`

Result: fail-closed. L1 is not proven.

## Deterministic checks

- `make sync`: passed.
- `make check`: passed with 1829 Python tests passed, 13 skipped, and 2 warnings.
- `uv run agent-commons doctor`: failed with safe diagnostic code
  `receipt_scope_bootstrap_required`.
- Six provider MCP preflights passed: provider help was present, the MCP contract was
  available, and the stdio handshake initialized.

## Live profile outcomes

| Profile | Result | Canonical state | Provider process | Terminal MCP outcome | Mismatch | Provider version | Safe failure class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `codex-builder` | passed | `succeeded` | started | 1 call / 1 completion | false | `codex-cli 0.152.1` | none |
| `codex-independent-reviewer` | passed | `succeeded` | started | 1 call / 1 completion | false | `codex-cli 0.152.1` | none |
| `claude-builder` | failed closed | `failed` | started | 0 calls / 0 completions | false | `2.1.258 (Claude Code)` | `provider_reported_error` |
| `claude-independent-reviewer` | failed closed | `failed` | started | 0 calls / 0 completions | false | `2.1.258 (Claude Code)` | `provider_reported_error` |
| `grok-builder` | failed closed | `failed` | started | 0 calls / 0 completions | false | `grok 1.0.13` | `grok_mcp_terminal_tool_not_called` |
| `grok-independent-reviewer` | failed closed | `needs_operator` | started | 0 calls / 0 completions | true | `grok 1.0.13` | `grok_mcp_terminal_tool_not_called` |

## Interpretation

The Codex profiles passed. Claude did not reach an audited terminal MCP outcome
because both Claude runs reported provider errors; the operator later stated that
Claude quota was likely exhausted at the time of the run. This makes quota
exhaustion plausible, but not a repository-proven root cause because raw provider
output is intentionally not retained in evidence.

The Grok profiles still did not call the bounded terminal MCP outcome tool after
successful MCP preflight and process start. This remains a repository-side
qualification blocker until a later live run records terminal MCP outcome calls.

No canonical artifact or verification was recorded on the CentOS host for this
run.

## Evidence hygiene

The reported evidence did not persist credentials, authentication paths, raw
provider output, prompts, transcripts, skill text, or temporary profile
configuration. Temporary profile configuration was removed after the run, and the
CentOS host did not push changes.
