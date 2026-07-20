# Threat model

## Security boundary

MVP-0 coordinates mutually visible processes on one shared local filesystem. It
does not provide cryptographic actor authentication, hostile-user isolation,
remote authorization, or multi-host distributed locking. Session identity and
role metadata support coordination and audit; they are not proof of who controls
a process.

The user, repository permissions, and operating system remain the primary trust
boundary. A future remote service requires a separate authentication and
authorization design.

## Protected assets

- integrity of project policy and effective truth;
- immutable history, manifests, artifacts, and idempotency receipts;
- task, review, decision, and handoff provenance;
- confidentiality of credentials and sensitive project data;
- availability of the workspace under concurrent or interrupted writers;
- project-authored instruction files outside managed integration blocks;
- the user's Git state and authority over external actions.

## Untrusted inputs

- agent-written messages, summaries, metadata, and suggested commands;
- imported documents and artifact contents;
- filenames, aliases, paths, tags, and external references;
- self-declared model, role, and capability information;
- stale caches and generated views;
- malformed or partially written local state;
- content designed to manipulate another agent through embedded instructions.

Agent-generated prose is data. A recipient must not treat text found in a
message or artifact as user authority.

## Primary threats and mitigations

### Instruction injection

Artifacts or messages may contain instructions that attempt to override the
user request or workspace policy. The orientation contract labels all supplied
content as untrusted, uses typed fields, separates evidence from instructions,
and requires explicit authority for consequential operations.

### Secret or sensitive-data persistence

Credentials, private keys, tokens, signed links, direct identifiers, or private
payloads may be placed in a message, exception, filename, artifact, claim, or
idempotency record. Every write surface must scan recursively before assigning
durable IDs or receipts. Rejections must redact the offending value. Artifacts
are referenced and hashed rather than copied by default.

### Path traversal and symlink redirection

Untrusted paths may target files outside the workspace or redirect writes
through symlinks. Writers normalize and scope paths, reject traversal and
unexpected symlinks, create files atomically in known directories, and do not
derive storage paths directly from display names.

### Identity and authority spoofing

An agent may claim a trusted model, role, or capability. Such metadata never
proves authority. The protocol requires explicit lifecycle transitions and
independent task review, while operator-controlled principal authorization is a
future service concern. The local trust limitation is displayed rather than
hidden.

### False consensus and circular review

Several agents can repeat the same unsupported assertion, and one agent can
appear under multiple roles. Promotion binds exact evidence and protocol state,
records the author and reviewing sessions, and requires an independent current
approval for every task acceptance in MVP-0.

Agent count alone never establishes truth.

### Stale approval

An approved artifact may change later. Reviews, verifications, findings, and
decisions bind an immutable revision. Dependency changes derive a stale state
and exclude stale conclusions from effective truth until rechecked.

### Concurrent corruption and duplicate writes

Writers may crash, retry, or race. Canonical publication uses validation,
content checks, atomic replacement, idempotency, causal revisions, and narrow
leases. In-flight receipts are scoped to a worktree and ref; published receipts
are derivable from validated canonical events. A non-shrinking per-scope ledger
anchor detects deletion or byte changes after the first local observation.
Conflicting receipts, anchors, or active heads are reported and fail closed
rather than being resolved by timestamp.

### Claim abuse or abandonment

A session may hold broad claims indefinitely or use them as ownership. Claims
have normalized scopes, TTL, renewal, release, and audited break. Diagnostics
surface overlap and stale leases. Claims remain coordination metadata.

### Git or external side effects

Initialization or recording must not imply authorization to stage, commit,
push, merge, deploy, publish, message people, or invoke destructive operations.
The tool performs none of these actions implicitly.

### Denial through noise

Excessive messages, tasks, and unresolved threads can hide important state and
exhaust context. Orientation is scoped and bounded; inboxes are addressed;
duplicate and stale work is surfaced; threads have explicit resolution states;
routine logs and private reasoning are excluded.

## Residual risks in MVP-0

- A process with filesystem write access can bypass the CLI and tamper with
  local files; diagnostics can detect many changes but cannot prevent all of
  them.
- Local identity can be impersonated.
- Same-host leases do not coordinate independent machines.
- Separate Git worktrees do not automatically merge their branch-local canonical
  histories; receipt isolation prevents false cross-branch orphans but does not
  reconcile the histories.
- A process able to rewrite both the canonical workspace and Git-common
  operational anchors can defeat local tamper detection.
- Secret detection cannot guarantee classification of every project-specific
  value.
- Evidence quality still requires human judgment and appropriate reviewers.

These limits must remain visible in documentation and diagnostics. They are not
silently upgraded into security claims.
