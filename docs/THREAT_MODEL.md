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

Delegated source reads open the repository and every relative parent component
with descriptor-relative no-follow semantics before opening the final regular
file. A tracked path whose final component or any parent is a symlink is omitted
from the frozen review snapshot. Prefix checks on a fully resolved pathname are
not treated as a sufficient boundary.

### Identity and authority spoofing

An agent may claim a trusted model, role, or capability. Such metadata never
proves authority. The protocol requires explicit lifecycle transitions and
independent task review, while operator-controlled principal authorization is a
future service concern. The local trust limitation is displayed rather than
hidden. `delegation:recover`, like `receipt:abandon`, is therefore an explicit
local coordination gate that must also have external operator/user authority;
it is not an authentication primitive.

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

The broker passes the manager's effective state root to the child MCP as fixed
argv and fingerprints it with the rest of the launch plan. It validates the
new child session through that same root before starting provider code, which
prevents external-state deployments from binding the provider to an unrelated
repository-local session store.

Purpose/profile pairing is also fixed: implementation uses a builder profile,
while independent review and verification use an independent-reviewer profile.
Relabeling a writable builder as a reviewer is rejected before publication.

### Autonomous role creation

**Withheld on this branch (2026-08-11).** The automatic (`auto`) grant level is
capped to `ask` at read time (`effective_grants`), so no role changes the staff
without a person confirming it, and the mechanisms below currently guard a path
that is inert. A review defeated four of them; the fixes landed and the
mechanisms now hold under adversarial execution, but the level itself stays
withheld until it has run longer behind proven brakes. What follows is the design
of the autonomous path for when it is restored.

A role holding `create_roles: auto` would change the staff without a person in
the loop. Unlike a delegation, which is terminal and ends itself, a role is
persistent and keeps receiving work, so automatic creation grows standing
structure. Seven mechanisms bound it, and all of them are derived from the
ledger or checked in `validate_transition`, which every adapter crosses:

- a `turnover_budget` on the role counting creations **and** retirements below
  it, so a create/retire cycle cannot walk past a headcount ceiling;
- grants that never widen — a created role's permissions, profile, tool
  selection, and context isolation are all componentwise no wider than its
  creator's *effective* values;
- a strictly narrower creation grant on any automatically created role, which is
  the only thing between one grant and an unbounded number of generations;
- a canonical `agent.created` event carrying the rationale, the proposing role,
  and whether it was recorded automatically or confirmed by a person;
- a single cascade retire that computes the whole lineage, refuses as a whole if
  any member owes live work, and only then writes;
- an `origin` field, so a role an agent hired is distinguishable in the index
  from one a person created;
- effective grants derived at read time from the whole ancestor chain, so a
  downgrade binds the next call from work already running rather than waiting
  for a propagation pass.

Two second paths have to re-check what creation checks, or the mechanisms read as
enforced and are not (round-1 C2/H1): an **`event.corrected`** cannot change a
role's authority, identity, isolation, or lineage — those fields are frozen in
`CORRECTION_IMMUTABLE_FIELDS`, checked on write and again on replay; and an
**`agent.reconfigured`** re-applies the creation invariants — a role that gains a
create or retire grant needs a ceiling, the ceiling stays monotone against the
creator, and an automatically-created role keeps a strictly narrower create grant.
The run/role binding a delegation carries is authorised and read only on
`delegation.requested`, so a relation on a later delegation event cannot rebind a
run on replay (round-2, architecture).

The budget deliberately lives on the role rather than in configuration.
`workspace.yaml` sits inside the delegated workspace and a writable builder runs
with `--sandbox workspace-write`, so a ceiling stored there is a ceiling the
agent can edit; `OperatorLimits` is not loaded by the worker MCP process, so a
ceiling stored there would not bind the path that needs it.

Residual: this is coordination, not authentication. A process running as the
same operating-system user can write agent events directly. The mechanisms bound
what the protocol does, not what the filesystem allows.

### Acting for a role without being entitled to

Acting for a role *is* holding its authority: a session bound to one receives
that role's effective grants and its staff-changing tools. Naming a role on a
delegation is therefore a privileged operation, and until it was checked, any
session able to open a delegation could name the most privileged role in the
workspace and hand a session of its choosing everything that role may do.

A session running as no role — a human window — may staff any active role,
because that is the ordinary way work starts and every local session is equally
trusted in MVP-0 regardless. A session already running as a role may staff only
itself or a role below it in its own lineage. The check lives in the domain
lifecycle and reads the run/role relation off the `delegation.requested`
envelope, so replay revalidates it there rather than trusting that the write path
checked once. The binding is read *only* on that event: a relation attached to a
later delegation event (say `delegation.started`) is ignored on replay, so it
cannot rebind a run to a role it was never authorised for (round-2, architecture).

The narrower reach this closed was not live: worker profiles receive no
delegation-creation tools, and a caller with direct manager access already holds
the state root, which is a standing residual risk. It is checked anyway, because
the check did not exist at all and would have become live the moment any profile
gained delegation creation.

### Role settings widening a worker's authority

An agent could try to reach new capability by naming a tool or a model on a
role. Tool selection is **narrowing only**: `tool_allowlist` is intersected with
the profile's fixed set at invocation build time, and a selection naming
anything the profile lacks fails closed before launch. A role cannot name a
model — that is a property of the operator-allowlisted profile it selects,
because a role that named a model would be editing argv.

A **skill** is the one setting that adds rather than narrows: operator-authored
instruction text appended to the run's bounded instruction. It is safe because
the text comes from the operator catalogue and never from an agent, so a role
changes what a run is told to do and never what it is allowed to do. A role
selecting a skill the catalogue does not define refuses the launch rather than
running without it.

The catalogue is an operator file loaded with the same discipline as the runtime
profile config: outside the delegated workspace, regular non-symlink file, not
group/world writable, owned by the operator or root, size-bounded. It is
deliberately a different file from the profile config, because the two have
different writers: the panel may edit this one, and may never edit the one that
names executables.

The terminal outcome tools are exempt from narrowing. A role that cannot report
a result would consume its budget and exit without closing its delegation, which
is a broken role rather than a narrower one.

### A weakened reviewer that still reads as independent

A role pinned to a fresh context can be relaxed to an accumulated one by a later
"optimisation", after which its verdicts look exactly like clean-slate verdicts.
Strengthening isolation is an ordinary reconfiguration; weakening it requires the
acting session to declare the `agent:isolation_downgrade` capability and record a
reason, in the same shape and with the same honest limits as
`delegation:recover`. Role memory is defined as *receiving your own earlier
judgment on the same subject*, which the ledger can check, rather than as
*knowing the past*, which it cannot and which would be harmful to forbid. A
fresh-context role's own prior verdicts are never hidden from it — hiding them
would hand a reviewer an incomplete picture it believes is complete, destroy the
"flagged before and not fixed" signal, and be discovered through another query
anyway.

### The local UI as a write surface

With `--enable-writes` the loopback server records canonical events. The
mutating surface is a fixed enumerated list of routes, each a thin adapter over
an existing `CommonsManager` method — the same manager the CLI and MCP adapters
use. Anyone holding the bearer token writes as the operator session the server
was started with, and the startup banner says so. Writes stay off by default.

`--enable-catalog-editing` is a **second, separate** gate and additionally
requires `--role-catalog`. It lets the panel add and remove skills and tools,
which changes what delegated runs are told to do — a different magnitude of
privilege from recording a role, and one flag for both would hide that. The
catalogue is written atomically at mode 0600 after full validation, so a
rejected edit leaves the previous file byte-identical and a partial write cannot
break the next launch. Removing an entry an active role requires is refused and
names the roles.

Provider profiles are never editable from the UI at any gate: they name
executables, and no loopback surface should decide what process starts.

Residual: with both gates open, the bearer token is the only thing between
another local process and both the ledger and the instruction text of every
subsequent run.

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
Task artifact bindings use the same rule: changed, corrected, or invalidated
artifacts and missing manifests stale the task review and make its prior
acceptance ineffective.

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

If a requester is absent, expired, or closed, the distinct
`delegation.recovered` transition may terminalize only canonical `requested`
work. Exact CAS decides any race with provider start, whose inert gate withholds
the instruction until `delegation.started` commits. Recovery never applies to
`active` or `input_needed`, and normal requester cancellation is not widened.

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
  security boundary unless enforced by the operating system. The two builder
  profiles differ here and the single `trusted_workspace` opt-in does not say
  so: the Codex builder runs under an OS sandbox (`--sandbox workspace-write`),
  while the Claude builder has no OS-enforced boundary and retains shell and
  file-write tools. For the Claude builder, external isolation is the only
  boundary, not a recommendation.
- The local UI (`agent-commons ui`) opens a loopback listening socket. By
  default it is read-only, registers only `GET` routes, requires a bearer token,
  pins the `Host` header, and emits no CORS headers, but it is still a new
  network surface on the host. With `--enable-writes` it also records canonical
  events as the operator session it was started with, and the bearer token is
  then the only thing between another local process and those writes. Its token is held in memory and printed once; launching a
  browser automatically exposes that URL to other processes of the same user
  through the process list.
- The UI renders agent-written text — task titles, delegation purposes,
  self-declared session roles — as ordinary graph nodes. It is injection-safe
  (rendered as text, never markup), but a node label is still attacker-chosen
  prose and can be written to look like a verdict.
- `broker stop` terminates by recorded pid. A pid may have been reused by an
  unrelated process of the same user, and checking that the pid still exists
  cannot distinguish the two, so a stale attempt can signal a foreign process
  group.
- Process cancellation cannot guarantee that an external API call, spawned
  descendant, or provider-side job was undone.
- Optional telemetry exporters extend metadata to another trust domain whose
  access control, retention, and availability Agent Commons does not control.
- A delegation recorded by a newer schema may make an older fail-closed binary
  unable to read the checkout; disabling the broker, not downgrading the reader,
  is the normal rollback.
- A workspace that has created a standing role cannot be read by a binary that
  predates `commons.payload.agent.v1`: each `agent.*` event becomes a
  `domain_validation_rejected` projection issue, and integrity gates fail closed
  on issue severity. Rollback means reverting to a checkout taken before the
  first role was created. This is stated rather than mitigated — a new canonical
  entity has no cheaper rollback, and a silent partial read would be worse than
  a refusal.

These limits must remain visible in documentation and diagnostics. They are not
silently upgraded into security claims.
