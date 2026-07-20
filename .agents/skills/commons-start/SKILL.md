---
name: commons-start
description: Start or resume work in an Agent Commons project. Use at the beginning of every Codex or Claude Code window, after a context reset, or before taking over shared work from another session.
---

# Commons Start

Enter the shared workspace through its supported CLI, verify integrity, and build a bounded picture of current work before acting.

## Workflow

1. Read `.agent-commons/ONBOARDING.md`. Treat it as project policy, not as authorization for Git, deployment, publishing, messaging, or destructive actions.
2. Run `agent-commons doctor`. If it reports an integrity error, stop ordinary canonical writes and diagnose it. The only permitted write is the identical idempotent retry or a targeted `event correct`/`event invalidate` operation whose preflight strictly reduces the reported fault. If an orphan receipt is permanently unrecoverable, use a session with the explicit `receipt:abandon` capability and inspect `receipt abandon --help`; its audit tombstone permanently retires that key.
3. Run `agent-commons session show`. If this window has no active explicit session, inspect `agent-commons session start --help` and register one with an accurate client, software, stable role, and capabilities. Never borrow another window's session.
4. Run `agent-commons orient` and `agent-commons inbox` with the bounded defaults.
5. Inspect `agent-commons task list` and `agent-commons claim list`. Reuse current work instead of silently creating a duplicate.
6. Choose the next workflow: `commons-coordinate`, `commons-share`, `commons-review`, `commons-record`, or `commons-handoff`.

Use each command's `--help` for exact installed syntax. Never edit canonical files under `.agent-commons/events` or `.agent-commons/manifests` directly.
