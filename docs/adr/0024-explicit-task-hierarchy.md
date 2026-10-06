# ADR 0024: Explicit planning components and subtasks

Status: Implemented proposal; product acceptance remains separate.

## Decision

Reuse canonical task records for planning components. `task_kind` is `task`
(default) or `component`, chosen at creation and immutable. A component describes
a planned part of the product, with its own criteria and lifecycle; it is not a
source directory, board department, executable resource, or automatic rollup.
Tasks and components can have one nullable `parent_task_id`. An absent parent
means the project root. Nested components and task subtasks use the same edge.
Titles, folders and dependency edges never infer containment.

Dependencies remain the sole task-to-task readiness relation. Containment does
not block execution, complete a parent, inherit an objective, or confer acceptance.
Components retain the normal task lifecycle and can carry their own implementation
work. A parent's acceptance covers its own exact revision and evidence, not a
mutable aggregate of its descendants.

## Mutation and replay

Creation validates kind and parent. Parent edits use `task.revised.changes` with
exact revision CAS; the operator editor, CLI `task edit`, and root MCP
`commons_edit_task` use the existing idle-work guard and identical retry contract.
Explicit null detaches. Parent existence and cycles are checked under canonical
validation, including direct revisions and replay; traversal is iterative and
bounded to 4096 visited records. A lifecycle event cannot smuggle hierarchy fields
outside creation or revision changes. Corrections preserve kind and parent,
including presence/value of `changes.parent_task_id`; they still permit wording
repairs and absent/default equivalence on creation. Kind conversion is
deliberately unsupported.

A parent edit changes the child's revision and therefore invalidates reviews of
its old revision through the existing review contract. It does not change the
parent revision. Hierarchy is organizational metadata, not evidence aggregation.
Accepted or cancelled tasks must use the normal reopen lifecycle before editing.
Existing operational live-work restrictions remain in the supported editor.

There is no task deletion/archive command. Cancellation retains the record and
its containment edges, does not cascade to children, and remains visible with its
state. Children can be moved away explicitly. Invalidating parent creation through
the correction workflow cannot silently manufacture valid orphans: normal replay
integrity checks report the missing parent. Project archival retains the ledger.

Old events omit both fields and retain absent keys in raw snapshot mappings;
consumers and browser DTOs apply the unparented/task defaults. Typed envelope
serialization preserves the original payload; new default task creation also
omits these fields so old identical create retries keep their request identity.
Schemas and browser DTO parsers accept the additive fields and reject malformed
kind/parent values. The universal writer raises the semantics floor to v7 for
component/parented creation and parent revisions, after validation and before append. Default task
creation, wording-only revisions and identical retries keep their previous floor.
Older readers require upgrading before reading hierarchy events. The projection
version guard reports `ledger_ahead_of_code`, but old strict storage schemas can
reject hierarchy fields before projection sees the stamp; the stamp alone does
not guarantee a friendly upgrade diagnostic from `doctor`.

## Surfaces and alternatives

CLI creation exposes `--task-kind` and `--parent-task-id`; root MCP exposes
`commons_create_task` and `commons_edit_task`. Worker MCP authority is unchanged.
Work creation supports components and explicit parents; the inspector supports
Add subtask and parent editing. Map offers Structure and Dependencies. Structure
shows a bounded containment diagram with a project root, component/task kinds,
canonical state, dashed parent-to-child edges, search, branch/ancestor focus,
fit and zoom. A native button list retains indentation and parent labels;
filtered/missing parents are labeled, never silently treated as canonical roots.
The existing dependency graph, capacity handling and list fallback remain intact.

A separate generic component entity would duplicate task lifecycle and review
semantics without a current need. Inferring components from directories or graph
clusters would give presentation heuristics canonical authority. Neither is used.
