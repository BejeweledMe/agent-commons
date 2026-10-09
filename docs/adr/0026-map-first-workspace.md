# ADR 0026: Task map workspace with a persistent project conversation

- Date: 2026-10-09
- Status: Owner-authorized direction; implementation and verification tracked separately
- Authority: direct owner instruction with annotated Sidebar | Work Map | Chat reference
- Partial supersession: ADR 0020's project-home and tracker placement; agent board semantics remain
- Refined by: [ADR 0027](0027-agent-organization-and-method-edits.md) requires modal details and explicit task/agent map tabs

## Context

The October 8–9 dogfooding report shows the task graph confined to a small area,
details competing with it, and the conversation composer obscured on a short
MacBook viewport. The owner explicitly chose a workspace resembling an editor:
compact navigation, a dominant task canvas, and a main project conversation.
The audience remains technically skilled developers using agents.

## Decision

The default project view is its task map. Structure means containment;
Dependencies means prerequisite edges. The existing agent board remains an
explicit team surface. It retains recorded agent links and operational frames.

Navigation and the main project conversation occupy compact side panes. Both
can be resized, collapsed and swapped. Task/agent conversations are explicitly
scoped, and selecting a node never changes the recipient of the main chat.
Details, result galleries, library, settings and launch forms open on demand.
A permanent inspector must not compete with the canvas as a fourth column.

Selection, branch focus, viewport and the opened detail surface are independent
state. Closing details or polling data preserves the current map. Trackpad pan
and pointer-anchored pinch operate only over the canvas. Keyboard controls,
visible zoom/fit actions and a list fallback remain available. A compact
viewport must not hide the composer beneath message history.

Pane layout uses a bounded server operational document with revision CAS.
Conversation text may be explicitly saved/restored/deleted in separate private
principal/project/scope storage. It is neither a canonical message nor the
attachment/send draft reservation. Saving does not send; an uncertain response
permits only an identical retry, and a different later tab's draft is protected.
Unsent attachments remain subject to their existing lifecycle. Browser content
storage is not introduced.

Lifecycle, provider isolation and independent exact-revision acceptance remain
unchanged. A successful provider run is not approval. Text result publishing
uses retained immutable bytes and bounded worker scope, and authoring tools are
not inherited by verification workers.

## Alternatives and consequences

Keeping the agent board as home is superseded by the owner's task-map reference.
Keeping all panels visible at once recreates the reported loss of working space.
Browser storage for drafts conflicts with the existing privacy contract.

Opus owns design/frontend; Codex owns backend and integration. An asset integrator
builds the shipped bundle. The [action plan](../archive/plans/2026-10-09-dogfooding-remediation.md)
and its evidence distinguish implementation, local verification and remaining
external gates. Authorizing this direction does not accept the implementation.
