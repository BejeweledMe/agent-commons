---
name: commons-record
description: Record durable artifacts, findings, decisions, and corrections. Use when a result must become reusable evidence or effective project truth rather than remain provisional discussion.
---

# Commons Record

Promote only high-value, scoped outcomes and keep immutable evidence distinct from accepted project truth.

## Workflow

1. Choose the correct record: artifact for a hashed revision; finding for an observed risk or reusable conclusion; decision for an authorized choice; correction, invalidation, or supersession for history changes.
2. Register artifacts by repository-relative reference and content hash. Agent Commons records metadata by default and does not copy raw content.
3. Report a finding provisionally, attach evidence, and promote it only after the required verification or authority check. Preserve contested state and resolution.
4. Propose decisions with a stable scope, alternatives, evidence, and dissent. Accept, reject, defer, or supersede them explicitly. Conflicting accepted decisions in one scope fail closed.
5. Never store credentials, personal data, signed links, customer content, full logs, chat transcripts, or private reasoning. Summarize the durable key point.
6. Never rewrite an existing event or manifest. Use the supported correction, invalidation, revision, or supersession operation.

Use the relevant `agent-commons artifact`, `finding`, or `decision` command help for exact syntax.
