# ADR 0027: Explicit agent organization and exact method edits

- Date: 2026-10-09
- Status: Owner-authorized direction; implementation verification separate
- Authority: direct owner request for task/agent map tabs, full-size temporary details and editable agent cards
- Partial supersession: ADR 0026's on-demand detail placement; ADR 0020's agent-card controls are extended

## Context

The owner's MacBook screenshot shows task details taking a second column inside
the central workspace. The remaining map is only about 300 by 242 CSS pixels.
The original August PRD also included agent settings, methods, permissions and
reporting relationships. The existing immutable `created_by_agent_id` is part
of security lineage and retirement rules; using it as an editable org chart
would change authority as an accidental effect of moving a card.

Modern agents bind exact immutable library roles. Their methods cannot be
edited through the unrelated legacy `skills` array. Generic entity inspector
responses are shortened and therefore cannot safely seed a settings form.

## Decision

The central workspace has task and agent map tabs with separate retained
viewports. Details use a large centered native modal over the workspace.
Opening and closing it changes no map dimensions or viewport. Only the topmost
dialog closes on Escape; focus returns without scrolling. Map selection never
changes the recipient of the main project conversation. Visual controls use
neutral light/dark surfaces and vector icons, with Agent Commons branding.

An agent's organizational parent is `supervisor_agent_id`, independent of its
immutable creator. Human operators can set it at creation or reconfigure it
with revision CAS; null detaches it. Persistent active agents form an acyclic
organization. Self, missing, retired, temporary and template parents refuse.
The projected edge is `supervised_by`; historical creator edges keep their
separate meaning. Organization grants no tool, staffing, communication or
retirement authority and introduces no recursive execution.

Agent settings read an exact bounded DTO from `/api/work/agents/{id}`. It
contains the current canonical revision, full editable arrays, requested and
effective grants, immutable identity, exact specialization metadata, active
links and organizational children. It contains no raw instructions and refuses
oversize data rather than truncating it into an editable buffer.

Changing modern methods first saves an immutable agent-specific library role
version, then rebinds that agent's exact `specialization_ref` with CAS. Other
agents and historical role versions remain unchanged. The two writes retain
separate retry identities; a successful library save may remain unbound if the
canonical update is refused. A retry resumes from that exact saved version.
An actual specialization change refuses while requested/active/input-needed
delegations or unfinished reviews still owe work. Model, profile, creator,
lifetime and authority are not changed by a method edit. Explicit ordinary
permission edits continue through the existing service guards.

## Compatibility and consequences

New organization fields and editable exact specialization bindings use
`agent.v3` with reader semantics floor 8. Existing v1/v2 events remain valid
with no organizational supervisor. New readers replay the old ledger without
rewriting it; old readers must refuse new semantics. Corrections cannot mutate
historical supervisor or specialization bindings.

The diagram displays recorded communication permissions separately from
organization. `ask` and `handoff_work` remain distinct permissions. Editing a
role's instructions never grants a tool or access right.

The [implementation plan](../archive/plans/2026-10-09-codex-style-agent-map.md) owns the
work graph and measured acceptance evidence. This decision authorizes the
direction; it does not claim that unfinished UI or provider checks passed.
