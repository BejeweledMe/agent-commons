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

The optional local delegation runtime adds a narrower execution boundary. Its
broker is trusted to start only operator-allowlisted provider profiles through an
authenticated local connection. A Commons session, task, thread, delegation, or
self-declared capability is not a launch credential. The broker does not provide
hostile-user isolation; another process running as the same operating-system user
may still tamper with its operational state or provider processes.

## Protected assets

- integrity of project policy and effective truth;
- immutable history, manifests, artifacts, and idempotency receipts;
- task, review, decision, and handoff provenance;
- confidentiality of credentials and sensitive project data;
- availability of the workspace under concurrent or interrupted writers;
- project-authored instruction files outside managed integration blocks;
- the user's Git state and authority over external actions;
- operator-controlled broker grants, provider profiles, and launch credentials;
- provider-process identity, delegation limits, and cancellation state;
- confidentiality of local runtime diagnostics and exported telemetry.

## Untrusted inputs

- agent-written messages, summaries, metadata, and suggested commands;
- imported documents and artifact contents;
- filenames, aliases, paths, tags, and external references;
- self-declared model, role, and capability information;
- stale caches and generated views;
- malformed or partially written local state;
- content designed to manipulate another agent through embedded instructions;
- delegation targets, purposes, limits, parent lineage, and interactive input;
- provider stdout/stderr, structured events, exit codes, and session identifiers;
- MCP requests and any provider capability or version claims.

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

For the optional runtime, the broker authenticates the local connection and
intersects its operator-managed grant with the selected profile, inherited
delegation limits, and workspace policy. It never derives launch authority from
the canonical actor's self-declared metadata. Loopback reachability alone is not
authentication.

### Confused-deputy process launch

An agent may try to turn a delegation tool into arbitrary code execution, inject
shell syntax or environment variables, select an unexpected binary, expose a
credential, or widen a child profile. The public API accepts a named profile and
typed parameters only. Operator-controlled profiles fix the adapter, executable,
argument template, environment allowlist, checkout mode, and limits. The broker
does not accept arbitrary argv, shell fragments, environment maps, or credentials
and never gives a child more authority than the intersection of parent grant,
profile, workspace policy, and broker policy. An inert local exec gate is the
only process running before canonical `delegation.started`; after that barrier
it replaces itself with the fixed provider without changing PID or process
group. A bounded work instruction remains untrusted data, follows the gate's
fixed control frame through process stdin, and is excluded from canonical,
operational, and telemetry persistence.

Reviewer source reads are hash-bound to the frozen raw file. A blocked
credential/PII-shaped line is replaced with a stable marker plus safe category
metadata instead of exposing the line or quarantining unrelated code; line
numbers remain stable and a final whole-document scan still fails closed.

Purpose/profile pairing is also fixed: implementation uses a builder profile,
while independent review and verification use an independent-reviewer profile.
Relabeling a writable builder as a reviewer is rejected before publication.

### Recursive delegation and resource exhaustion

Agents may create a Codex-to-Claude-to-Codex loop, evade limits through another
client, flood the queue, or consume unbounded time and provider budget. Every
delegation records parent, root, and depth; self/ancestor targets are rejected;
depth, concurrency, attempts, wall time, and provider budget are bounded and
cannot be widened by descendants. Broker-global and per-profile ceilings still
apply, and the initial rollout defaults to depth one.

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

Runtime launch uses a separate fsync-safe attempt journal and one live attempt
per delegation. A reservation is durable before spawn; canonical start is
recorded only after the distinct child session and process/provider handle are
identifiable. Restart reconciliation checks the launch token, process start
fingerprint, provider handle, canonical state, and child session. Ambiguity fails
closed to `needs_operator`; the broker never blindly relaunches a possibly live
worker. Canonical publication still uses `CommonsManager`, expected revisions,
stable idempotency, receipt recovery, and the shared write lock.

### Cancellation and orphaned processes

A timeout, broker crash, or failed signal may leave a child running after the
caller believes it stopped. The current public surface cancels only requested,
unlaunched work. For active work, an operator must stop the provider and invoke
reconciliation; confirmed timeout may become `timed_out`, while unknown process
identity or termination becomes terminal `needs_operator`. This protocol version
does not record active work as `cancelled` because it has no authenticated
canonical stop receipt. Stopping a process never claims to reverse a provider,
Git, network, or other external side effect.

### Checkout collision and Git mutation

A parent and child may edit overlapping files, or a broker may accidentally
switch/reset a branch while another window is working. The first runtime permits
one writable worker per checkout scope and requires both a broker runtime lease
and ordinary narrow Commons claims. Read-only sharing requires an enforceable
read-only adapter and a quiescent immutable subject. The broker never creates,
switches, commits, resets, or removes Git worktrees implicitly; an operator
provisions a separate worktree when isolation is required.

### Claim abuse or abandonment

A session may hold broad claims indefinitely or use them as ownership. Claims
have normalized scopes, TTL, renewal, release, and audited break. Diagnostics
surface overlap and stale leases. Claims remain coordination metadata.

### Git or external side effects

Initialization or recording must not imply authorization to stage, commit,
push, merge, deploy, publish, message people, or invoke destructive operations.
The tool performs none of these actions implicitly.

A delegated worker inherits this restriction. Successful process exit is not
authorization, review approval, verification, task acceptance, or evidence that
an external action was safe. Provider permission prompts move the delegation to
`input_needed` or `needs_operator`; the broker does not approve them on the
user's behalf.

### Provider output and telemetry leakage

Provider streams and diagnostics may contain prompts, reasoning, source, secrets,
tool payloads, environment values, or terminal output. Canonical events retain
only bounded state, safe reason codes, summaries, and typed references. Runtime
logs are disabled or bounded by default, stored only in ignored local state with
restricted permissions and explicit retention. OpenTelemetry is optional and
metadata-only by default; prompts, responses, reasoning, transcripts, file
contents, tool arguments/results, shell commands, environment variables,
credentials, and raw stdout/stderr are excluded. Export endpoints and credentials
are operator configuration and never ledger data.

### Provider and protocol drift

Provider CLIs, SDKs, output formats, and AHP capabilities may change without an
Agent Commons release. Adapters implement a versioned runner contract, declare
capabilities, use pinned optional dependencies, and fail before launch on an
unsupported version or missing feature. Deterministic CI uses fake runners and
contract fixtures; real-provider tests remain explicit and opt-in.

### Denial through noise

Excessive messages, tasks, and unresolved threads can hide important state and
exhaust context. Orientation is scoped and bounded; inboxes are addressed;
duplicate and stale work is surfaced; threads have explicit resolution states;
routine logs and private reasoning are excluded.

## Residual risks in local deployments

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
- A broker or provider process compromised under the same operating-system user
  can bypass local grants and tamper with non-authoritative runtime state.
- Provider read-only or sandbox modes may reduce accidental writes but are not a
  security boundary unless enforced by the operating system.
- Process cancellation cannot guarantee that an external API call, spawned
  descendant, or provider-side job was undone.
- Optional telemetry exporters extend metadata to another trust domain whose
  access control, retention, and availability Agent Commons does not control.
- A delegation recorded by a newer schema may make an older fail-closed binary
  unable to read the checkout; disabling the broker, not downgrading the reader,
  is the normal rollback.

These limits must remain visible in documentation and diagnostics. They are not
silently upgraded into security claims.
