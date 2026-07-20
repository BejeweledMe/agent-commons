---
name: commons-review
description: Review or verify another session's work against an exact revision. Use for independent code review, design critique, security assessment, reproducible checks, or task acceptance evidence.
---

# Commons Review

Keep expert judgment, reproducible verification, and governance acceptance separate.

## Workflow

1. Identify the exact typed target reference, its current immutable revision, the requested criteria, and whether independence is required.
2. Refuse to complete an independent review from the same session that requested it. Do not claim independence merely by changing a role label.
3. Inspect the actual artifact or worktree and run checks proportionate to risk. Record a review verdict for scoped judgment; record a verification for a reproducible factual claim with evidence references.
4. Report concrete findings with severity, affected scope, evidence, and uncertainty. Use `changes_requested`, rejection, or abstention when the evidence warrants it.
5. Bind the result to the reviewed revision. A later revision makes the result stale and requires a new check.
6. Accept a task only when project policy and required evidence permit it; review approval alone does not silently promote project truth.

Use `agent-commons review --help` and `agent-commons verification --help` for exact syntax. Never review only a generated summary when the target itself is available.
