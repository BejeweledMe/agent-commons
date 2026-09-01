# Grok runtime behavioral canary evidence

This artifact records only closed verification facts for
`task.2BM6RQY93G0E8Q6TKRD23VSAXY`. It intentionally excludes provider output,
prompts, transcripts, reasoning, credentials, executable paths, and operator
configuration.

## Exact revisions

- Terminal MCP transport and trust projection: `d2eafd4c6c3e2466d44d1f507b567b02e9c3d391`
- macOS reviewer sandbox compatibility: `74ec517a52d24d9f8b81ffc8a34fefee7fdbcdb9`
- Qualified provider version: Grok Build 1.0.13

## Regression gates

- Focused runtime, installer, adapter, and canary suite: 206 passed.
- Full command: `env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE -u AGENT_COMMONS_SESSION_ID make check`.
- Full result: 1810 passed, 13 skipped, 2 warnings.

## Isolated behavioral canaries

| Profile | Preflight | Initialization | Process | Canonical | Calls | Completions | Rejections | Diagnostic | Child session |
|---|---|---|---|---|---:|---:|---:|---|---|
| `grok-builder` | ready | ready | succeeded | succeeded | 1 | 1 | 0 | `none` | closed |
| `grok-independent-reviewer` | ready | ready | succeeded | succeeded | 1 | 1 | 0 | `none` | closed |

The reviewer canary retained its read-only native tool surface: no shell or
write tool was allowed. No process exit was promoted without a canonical
terminal completion. The closed failure classifications confirmed during the
remediation were `terminal_tool_not_called` and `provider_sandbox_failed`.
