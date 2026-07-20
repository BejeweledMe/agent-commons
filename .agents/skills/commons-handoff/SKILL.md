---
name: commons-handoff
description: Pause or transfer shared work without losing context. Use before ending a window, changing task owner, handing work to another role, or leaving active blockers and next actions.
---

# Commons Handoff

Leave the next session enough durable context to continue safely without replaying the chat.

## Workflow

1. Bring each task to its honest lifecycle state. Do not mark unfinished work complete.
2. Register durable artifact revisions, findings, decisions, review results, blockers, and unresolved threads before summarizing them.
3. Create one targeted handoff containing completed work, active work, typed task and artifact references, blockers, risks, open questions, and concrete ordered next actions. Register revision-bound evidence separately whenever the recipient must recover an exact content revision.
4. Release claims that no longer protect genuinely active work. Transfer responsibility through task and handoff records, never by sharing a session nonce.
5. Run `agent-commons orient` and confirm the handoff is visible to the recipient. End the explicit session only when this window is actually leaving.

A handoff does not authorize Git operations, deployment, publication, destructive changes, or external communication. Use `agent-commons handoff --help`, `agent-commons claim --help`, and `agent-commons session end --help` for exact syntax.
