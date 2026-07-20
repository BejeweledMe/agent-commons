---
name: commons-coordinate
description: Coordinate shared tasks and temporary resource claims. Use before creating or taking work, editing overlapping paths, reserving an exclusive resource, or changing task lifecycle state.
---

# Commons Coordinate

Divide work around verifiable outcomes and prevent duplicate or overlapping execution.

## Workflow

1. Ensure `commons-start` has been completed for this explicit session.
2. Read the current objective, work board, inbox, active claims, blockers, and dependencies.
3. Reuse or refine an existing task when it represents the same outcome. Create a task only when its outcome, acceptance criteria, priority, and dependencies are distinct.
4. Take the task durably, then acquire the narrowest relevant temporary claims. Use typed resources such as `task:<id>` and repository-relative `path:<path>`; path claims conflict with ancestors and descendants.
5. Start, block, unblock, complete, submit, accept, cancel, or reopen work only through the matching CLI lifecycle command and the current expected revision.
6. Keep assignment and claim semantics separate: assignment records responsibility; a claim is an expiring lease. Neither grants Git ownership or permission to overwrite user work.
7. Renew only claims protecting active work. Release them before changing owner or pausing the work.

Inspect `agent-commons task --help` and `agent-commons claim --help` for syntax. If another live claim overlaps, coordinate through `commons-share`; do not bypass it.
