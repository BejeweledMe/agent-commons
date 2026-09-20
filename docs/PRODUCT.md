# Agent Commons: product, positioning and glossary

This page is the single owner of three facts: what Agent Commons is, who it is
for, and what its words mean. Every other document links here instead of
restating them. Counts of skills, blueprints or tests are not facts of this page;
the code defines them and the user guides state them once.

Supersedes `docs/VISION.md`, `docs/visual_multi_agent_orchestrator_prd.md`,
`docs/visual_orchestrator_plan.md` and the product summary of
`docs/current-product-and-architecture.md`, all archived byte-for-byte under
[`docs/archive/plans/`](archive/plans/) on 2026-09-18. Decision of record:
`product/primary-surface` (`decision.61BDS4NC4GVK9K50R09XK98A07`, UI first).

## What it is

Agent Commons is a local web application in which one person runs a product
through a team of AI agents. The person describes the outcome, hires agents from
a shared library of specializations, watches the work on a board and in a task
tracker, talks to agents in scoped conversations, opens each agent's results,
and records the final human decision on every piece of work.

Behind the application sits an immutable, Git-friendly file ledger. It gives
otherwise isolated agent windows one durable project memory: what is being done,
by whom, what was reviewed at which exact revision, what was decided, and what
the next session should do. Codex, Claude Code, Grok Build and future clients
read and write the same ledger through the same contract.

## Who it is for

The primary user is a person who understands what an AI agent is and wants to
build or improve a product with several of them, without learning an
orchestration protocol. They own product decisions, provider accounts and the
acceptance of finished work.

Agents are the second audience: a fresh Codex or Claude window entering a project
must be able to orient in minutes from the ledger alone, coordinate with other
sessions, and leave a handoff the next one can act on.

## The product promise

- **Work is visible as work.** A project opens on its board; the task tracker
  answers what the team is doing now, what is blocked and where a human is
  needed. History is available for investigation, never as the home screen.
- **Provider output is never truth.** A finished run is an outcome report. An
  independent review judges one exact revision. Acceptance is a separate,
  recorded human decision, and it is the only thing rendered green.
- **Creating a plan and starting a provider are different actions.** Applying a
  blueprint creates agents and tasks and starts nothing; every launch is
  explicit, bounded in time and shown truthfully while it waits.
- **Everything survives a context reset.** Tasks, claims, reviews, findings,
  decisions and handoffs are append-only records; boards, briefs and indexes are
  rebuildable projections.
- **The tool never grants authority.** Agent Commons records coordination. It
  does not authorize committing, pushing, deploying, publishing, contacting
  people or destroying data.

## Surfaces

| Surface | Route | What it holds |
| --- | --- | --- |
| Board | `/work` (home of a project) | Agents as cards, agent links as edges, department frames from blueprint applications; the arrangement is the user's layout, never a canonical fact ([ADR 0020](adr/0020-project-board-home.md)) |
| Tasks | `/work?view=work` | The tracker: now / map / all-tasks views, task inspector, run preparation, review and acceptance actions |
| Agents | `/work?view=…` | Hiring an agent from a specialization; the same action is available from the board |
| Library | `/work?view=library` | Skills in groups, specializations, blueprints; service-wide, shared by all projects ([ADR 0016](adr/0016-service-library-and-live-workspace.md)) |
| Settings | `/work?view=settings` | Workspace initialization, provider runtime, project settings |
| Results | button on a task or agent | That producer's images and live previews, latest first, history kept ([ADR 0019](adr/0019-project-native-collaboration-and-outputs.md)) |
| Gallery | `/gallery` | Design packages and their screens |

The legacy single-file panel at `/` is being retired (decision scope
`repo/legacy-panel-retirement`, ADR 0022 pending). It is not served inside a
project and must not gain features.

## The working model

```text
Project → Agents (from specializations) → Tasks → Run → independent Review → human Acceptance
```

A run reports an outcome; a review judges one exact revision; acceptance is the
person's decision. Editing a task creates a new revision and makes earlier
reviews stale. Nothing on the client advances this lifecycle; every state comes
back from the server.

## Principles

- One shared contract for every model family and client.
- Explicit truth promotion: messages, task completion and model agreement are
  not evidence.
- Key points, not telemetry: no private reasoning, transcripts or raw logs in
  the ledger.
- Immutable history, rebuildable views.
- Revision-aware evidence: a later revision never inherits an earlier approval.
- Lightweight by default; governance grows with risk.
- Human authority stays outside the tool.
- Complement Git, CI and issue trackers; do not replace them.

## What it is not

Not an autonomous agent swarm or general scheduler; not a store of hidden
reasoning or full chats; not a voting system; not a replacement for Git, CI or
the person who approves external effects; not a multi-host platform in its
local-filesystem release.

## Glossary

The left column is the word the UI and the guides use. The right column names
the ledger or code term where it differs. One thing, one word, per language.

| UI word (EN / RU) | Meaning | Internal term |
| --- | --- | --- |
| Project / Проект | A connected repository folder and the boundary of its board, agents, tasks, conversations and results | `workspace` (ledger root), `project` (registry) |
| Skill / Навык | A versioned instruction set in the service library; grants no tools or permissions | `skill`; the repository's `commons-*` client skills are a client integration, not library skills |
| Specialization / Специализация | A provider-neutral definition of a responsibility and its skill set; the template an agent is hired from | `specialization` |
| Agent / Агент | A named instance of a specialization inside a project: name, provider, profile, model, pinned skill versions. Target first-level word; the shipped UI still says "role" in the hire form, the model hint and the board introduction (`Role name`, `role_model_help`, `board_intro`) until work package WP-30 of the [consolidation plan](plans/2026-09-18-consolidation-and-wave-4-plan.md) renames those strings | ledger `agent`, historically and in several current strings "role" |
| Profile / Профиль | The operator-owned provider launch configuration (CLI, model, permissions) defined outside the project | `profile` in `runtime.yaml` / `profiles.yaml` |
| Blueprint / Шаблон проекта | A versioned definition of a team, its tasks and dependencies; applying it creates agents and tasks and starts nothing | `blueprint`, `blueprint_application` ([ADR 0021](adr/0021-objective-blueprint-application-provenance.md)) |
| Objective / Цель | The canonical record of what a set of tasks is for; a task inherits the objective of its blueprint application when it has none | `objective`, `objective_id` |
| Task / Задача | The expected result, its result criteria and its dependencies | `task`; "acceptance criteria" is the stored field name |
| Run / Прогон | One bounded attempt by an agent at a task, with a time limit shown before launch | `delegation` + `attempt` (broker mechanism, [ADR 0004](adr/0004-optional-local-delegation-runtime.md)) |
| Result / Результат | A file, image or live preview bound to the exact task and producing agent | `output`; `artifact`/`revision` are ledger-level records |
| Review / Проверка | An independent judgment of one exact revision; never acceptance | `review` (`approved`, `changes_requested`) |
| Acceptance / Приёмка | The person's recorded decision that reviewed work is good enough; the only green state | `task.accepted` |
| Conversation / Разговор | A project, task or agent scoped message thread with private attachments; sending does not start a run | `thread`, `conversation` |
| Handoff | A record left by an ending session: state, blockers, next action | `handoff` |
| Wave / Волна | A planning unit: an ordered group of work packages closed by one integration and one green `make check`; never a UI or ledger concept | plan documents only |

Words that stay out of the first level of the UI and the guides: canonical,
evidence, readiness, revision, qualification, canary, delegation, ledger,
snapshot. They remain in technical disclosures, contracts and the CLI.
Canonical values (`succeeded`, `needs_operator`, `fresh`, `accumulated`, entity
kinds, profile ids) are never translated; they carry a human gloss beside them.

## Direction

Direction lives in [ROADMAP.md](ROADMAP.md); the active plan lives in
[`docs/plans/`](plans/); accepted decisions are read with
`uv run agent-commons decision list`; the architecture is in
[ARCHITECTURE.md](ARCHITECTURE.md).
