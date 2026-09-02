# CentOS 9 L1 provider qualification rerun: fail-closed

Date: 2026-09-02

Source: operator-supplied CentOS 9 qualification summary from the external Linux host.

Commit under test: `f81e21bb1f5f47e2a7152097927048520679d2c4`

Local Linux evidence path reported by the external host:
`/home/bejeweled/cv_projects/agent-commons-l1-f81e21bb/docs/evidence/2026-09-02/linux-centos9-skill-aware-provider-canaries-f81e21bb.json`

## Deterministic checks

- `make sync`: passed.
- `make check`: passed, with 1828 Python tests passed and 13 skipped.
- `uv run agent-commons doctor`: failed closed with diagnostic
  `receipt_scope_bootstrap_required` in the fresh checkout.
- Provider preflight: all six configured profiles passed provider help, MCP
  contract, and stdio handshake checks.
- Canonical artifact and verification were not recorded on the external host.

## Live canary result

| Profile | Result |
| --- | --- |
| `codex-builder` | Passed. Provider work process started, canonical state was `succeeded`, terminal calls/completions were `1/1`, and there was no process/canonical mismatch. Provider version: `codex-cli 0.152.1`. |
| `codex-independent-reviewer` | Passed. Provider work process started, canonical state was `succeeded`, terminal calls/completions were `1/1`, and there was no process/canonical mismatch. Provider version: `codex-cli 0.152.1`. |
| `claude-builder` | Passed. Provider work process started, canonical state was `succeeded`, terminal calls/completions were `1/1`, and there was no process/canonical mismatch. Provider version: `2.1.258 (Claude Code)`. |
| `claude-independent-reviewer` | Passed. Provider work process started, canonical state was `succeeded`, terminal calls/completions were `1/1`, and there was no process/canonical mismatch. Provider version: `2.1.258 (Claude Code)`. |
| `grok-builder` | Failed closed. Provider work process started, canonical state was `timed_out`, terminal calls/completions were `0/0`, and there was no process/canonical mismatch. Provider version: `grok 1.0.13`. Workflow diagnostic: `terminal_tool_not_called`. |
| `grok-independent-reviewer` | Failed closed. Provider work process started, canonical state was `needs_operator`, terminal calls/completions were `0/0`, and there was a process/canonical mismatch. Provider version: `grok 1.0.13`. Workflow diagnostic: `terminal_tool_not_called`. |

## Conclusion

L1 is not proven at this revision. The remaining blocker is that both live
Grok skill-aware canaries started real provider work but did not complete the
audited worker-MCP terminal outcome tool. The prior direct control-plane CLI
bypass was not reproduced: the Grok builder no longer produced a canonical
`succeeded` result with zero terminal MCP calls.

Privacy boundary reported by the external host: no credentials, auth paths, raw
provider output, prompts/transcripts, skill text, or temporary profile config
were persisted.
