# ADR 0005: Workspace ownership and state-root isolation

Status: accepted for the core isolation batch.

## Context

The historical default placed operational state directly below the Git common
directory. That correctly joined linked worktrees, but two separately initialized
Agent Commons workspaces inside one Git repository received the same sessions,
claims, receipt migration marker, write lock, and SQLite database. An active
session from one workspace could consequently be selected in the other.

An explicit state location must remain usable by operators and broker/MCP launch
plans without silently changing its meaning. Existing receipt recovery state also
cannot be moved, deleted, or adopted on inference alone.

## Decision

`AGENT_COMMONS_STATE_ROOT` and `--state-root` remain exact operational roots. An
exact root is owned by one `workspace_id`; reusing it for another workspace fails
closed with `state_owner_mismatch` before session, claim, receipt, or SQLite
services are constructed.

`AGENT_COMMONS_STATE_BASE` and `--state-base` are operator-owned bases. Their
effective root is:

```text
<base>/workspaces/<workspace_id>/
```

The normal Git-common-directory default is a base and uses the same namespace.
Linked worktrees carrying the same workspace configuration therefore still share
operational state. Nested or otherwise distinct workspaces receive independent
namespaces even when they share an enclosing Git repository.

Every effective root contains canonical `workspace-owner.json` metadata. The
marker contains only its schema and workspace ID, is created without overwrite,
and is validated before operational services open. Publication rejects symlink
or non-regular collisions and fsyncs the containing directory.

Configuration precedence is source-aware. An explicit command-line root or base
overrides the other environment form. At the same source level, the exact root
wins for backward compatibility. Direct library callers cannot pass both an exact
root and a base.

`support` reports the selected configuration source, exact/base/legacy mode,
workspace ID, ownership status, and match without exposing local paths. Paths are
included only after the operator explicitly runs:

```bash
agent-commons --read-only support --show-paths
```

## Legacy compatibility

An existing unnamespaced root is reused only when its ownership marker or the
existing receipt-v2 migration document names the current workspace. A migration
is ownership proof only after no-follow regular-file/path checks, strict JSON
parsing, exact canonical-byte verification, packaged v2 schema validation, and
typed workspace-ID validation all succeed. A writable open may then add the
ownership marker, but it does not move, delete, rewrite, or migrate receipts,
sessions, claims, attempts, or SQLite data.

Legacy material with no provable owner is `state_owner_unproven` and fails before
registry parsing. A root proven to belong to another workspace is never adopted.
The operator must select an empty exact root or a state base; ambiguous legacy
state requires separate, explicit operator review.

SQLite stores the workspace ID in projection metadata as a defense in depth. A
database containing or declaring another workspace is rejected rather than
reused.

## Consequences

- Cross-workspace sessions and path claims no longer authorize or block each
  other when a base/default configuration is used.
- Exact-root launch plans retain their literal path semantics, but one exact root
  cannot be an implicit multi-workspace coordination domain.
- Linked worktrees remain joined by workspace identity, not by checkout path.
- Read-only inspection never creates a namespace or ownership marker.
- Existing receipt recovery rules remain unchanged; ownership validation occurs
  before recovery state is read and no automatic migration is introduced.
