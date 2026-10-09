# Agent Commons: user guide

Agent Commons is a local web application for running work through a team of AI
agents. You define the work, choose who does it, follow the result, and make the
final decision. It does not make product decisions or accept work for you.

## In this guide

1. [Understand the workspace](#understand-the-workspace)
2. [Open the app safely](#open-the-app-safely)
3. [Set up a workspace and runtime](#set-up-a-workspace-and-runtime)
4. [Create an agent and a task](#create-an-agent-and-a-task)
5. [Start and follow a run](#start-and-follow-a-run)
6. [Review and accept completed work](#review-and-accept-completed-work)
7. [Handle blockers and common problems](#handle-blockers-and-common-problems)
8. [Know the current boundaries](#know-the-current-boundaries)

## Understand the workspace

The product follows one simple chain:

```text
Task → choose an agent → Run → independent Review → human Acceptance
```

A project opens on its **task map**, between compact navigation and the main
project conversation. Structure shows components and subtasks; Dependencies
shows prerequisites. The default working set omits accepted and cancelled tasks;
select All to include history. Task details, results and run preparation open when needed.
The **Board** remains available for agent cards, recorded agent links and
department frames. Its arrangement never changes work or acceptance.

Resize the side panes by dragging their separators, or focus a separator and
use the arrow keys. Layout controls collapse either pane, swap sides or restore
the default. The map keeps its reading position when details close. Two-finger
scroll pans the map; a trackpad pinch zooms around the pointer. Zoom, Fit and
keyboard controls remain available. Scrolling the chat scrolls its history.

The main chat stays addressed to the project when you select a task. Open a
task or agent conversation explicitly to change scope. **Save draft** retains
only unsent text and its reply target in private workspace storage; saving does
not send a message. Restore or delete it explicitly. Attachments are separate,
and an unsaved local draft can still be lost on reload.

### Agent

An **agent** is a continuing responsibility in your team: for example, `Backend
developer`, `Product designer`, or `Independent reviewer`. In **Agents**,
create one with a name, a **Runtime profile**, a short explanation of what it is
responsible for, and a starting context. You can also hire one from the side
panel of the Board. The ledger and its forms call this standing record a
*role*, so you will still read that word in the UI and in technical details.

Choose **Fresh** for an isolated start. **Accumulated** requires one exact,
current Context Pack revision when launching. It carries that bounded baseline,
not the previous provider conversation. This provenance remains visible during
review. An agent is not the same thing as a single attempt to do work.

### Task

A **task** says what should be done and how you will judge the result. In the
UI it has a title, a description, and **Acceptance criteria**. Write one clear
result per criterion. Small, testable tasks are easier for an agent to
complete and for a lead to review.

### Run

A **run** is one bounded attempt by an agent to work on one task. Starting a run
uses the local Claude, Codex, or Grok Build provider configured on your computer. It can
consume your provider account or subscription just as running that provider
directly would.

A run finishing does not move a task to accepted. It only reports the attempt's
outcome. A task can have more than one run.

### Review and acceptance

A **review** is a judgment by someone independent of the work. It is tied to
the specific version of the task that was reviewed. **Acceptance** is your
recorded decision that the reviewed work is good enough to use.

This distinction matters: a successful run is not an approval, and an approval
of an older task version cannot be used after you edit the task.

### Attention and blockers

In **Tasks**, use **Needs attention** and select a task to see its next step.
The task inspector marks what is waiting on you. A blocker means an agent, run,
task, or review cannot continue without a decision or correction. Open the item
to see the next available action. Do not treat a blocked item as finished
work.

## Open the app safely

From the Agent Commons source checkout, run:

```bash
make sync
uv run agent-commons ui
```

The app runs only on your computer and prints a local URL. The command may open
your default browser. That URL contains a short-lived, one-time sign-in code.

- Open the printed URL only once.
- If you want a particular browser, run `uv run agent-commons ui --no-browser`
  and open the newly printed URL once in that browser.
- If the browser says its session is no longer active, stop the local UI, start
  it again, and use the newly printed URL. Do not expect an old URL to work.

The current failure text can suggest opening the printed URL again. Do not use
that as a way to reuse a consumed or expired code: start the local UI again and
use a fresh URL instead.

Keep the local URL private while the panel is running. It gives a browser access
to this local panel; it is not a link to share with collaborators.

## Set up a workspace and runtime

The startup repository becomes the first project. Use **Projects** in the left
sidebar to select another one. Each project has its own team, tasks, design
gallery and run history; installed skill and blueprint definitions are shared.

Choose **New project**, enter its name and an absolute workspace path, then
**Check workspace** and **Create project**. Choose the existing-repository option
to connect a checkout you already have. A new project opens its blueprint
library; apply a blueprint there or open **Tasks** for an empty start.
Creating a project does not launch a provider.

Task and Library drafts are kept separately in browser memory while switching.
Reloading or closing the page clears unsaved drafts. Existing runs continue
while the local service remains running. **Project actions** contains rename,
archive and restore; archiving does not delete files or stop a run.

If initialization was interrupted and its result is ambiguous, keep the path,
go back and explicitly inspect/connect that existing repository. The app does
not silently assign an unrelated workspace to the original request. Operators
can use `ui --single-project` when they need the previous single-project entry.

### 1. Initialize the workspace

Open **Settings**. If it says **Workspace files have not been created**, choose
**Initialize workspace**. Agent Commons creates its workspace files in the
current repository. If the screen says the folder is not a Git repository,
initialize or choose a Git repository first, then start the UI again.

### 2. Configure the runtime

To start an agent, the product needs a local provider runtime. Choose
**Configure runtime** in **Settings** when offered. The setup checks for
locally installed Claude, Codex, or Grok Build tools and writes only generated
configuration.

If no provider is available, install and authenticate either provider CLI using
your own account, then return to the panel and refresh its status. The current
Settings screen may not say exactly which local requirement is missing; when
the next action is unclear, follow the
[technical troubleshooting guide](../../TROUBLESHOOTING.md) or ask an operator
to check the runtime from a terminal.

Do not edit generated runtime files through the browser: the current UI does
not provide a configuration editor. If you maintain a custom setup, use the
technical operator documentation.

## Create an agent and a task

Start with the task; you can prepare the team before its first run.

### Create an agent

Open **Agents** and its hire form, or hire from the Board's side panel:

1. Enter a **Role name**.
2. Select a **Runtime profile**.
3. State **What this role is responsible for**.
4. Choose **Starting context**: **Fresh** or **Accumulated**.
5. Select **Create role**.

The profile controls how that agent can run. If the form says **No runtime
profiles are available yet**, finish runtime setup before hiring.

### Organize components and subtasks

Choose **Component** in the new task form to describe a planned part of your
product. Select a **Parent component or task** to place a record underneath it,
or leave **Project — no parent** for top-level work. Components have their own
criteria and state.

In the task inspector, **Add subtask** creates a child without adding a prerequisite.
**Edit task** lets you move a task to another parent or back to the project root.
Save uses the displayed task revision; a stale edit requires refreshing before a
new attempt. Unknown outcomes retain the original request for retry.

In **Map**, switch between **Structure** (components and children) and
**Prerequisite tasks** (execution dependencies). Both show explicit recorded
relationships. Cancelling a component retains its children; move or cancel each
child explicitly as needed. Completing a child does not accept its parent.

### Create a task

Open **Tasks** and choose **New task**:

1. Give the task a short **Task title**.
2. Explain **What should be done** and any important constraints.
3. Add **Acceptance criteria**, one result per line.
4. Optionally choose prerequisite tasks.
5. Select **Create task**. Its details open automatically.

The task is intentionally separate from the agent. You can decide which active
agent should work on it when you start a run.

## Start and follow a run

Select a ready task in **Tasks**, then **Prepare run**. The task is already
selected. Choose an active agent and a run preset, set the attempt time limit
in whole minutes from 1 to 60, inspect the budget and any Context Pack or
Design Package requirements, then explicitly select **Start run**. Creating a
task never starts a provider. Readiness and dependency checks can prevent
launch even when the task's recorded state is `ready`.

The task inspector shows the goal, dependencies, run history and the next
available action. **Technical details** exposes exact identifiers and revisions
when needed.

A run can be requested, active, `input_needed`, succeeded, failed, timed out,
or `needs_operator`. `input_needed` means the run asked a person a question.
The current headless runtime cannot deliver that answer, so the run ends as
`needs_operator`. `needs_operator` means the system cannot safely continue on
its own: inspect the run in the task inspector, fix the cause, and start a new
run. It is not a successful result.

## Review and accept completed work

Use the selected task's actions in **Tasks**. Review and acceptance always
target an exact task revision.

1. Open the task after its work is ready.
2. Select **Request review**. The task now waits for an independent review.
3. Start or ask an independent reviewer to check that task. The person or
   agent doing the work cannot provide the independent review needed for
   acceptance.
4. If the verdict is approved and still matches the current task version, open
   the task, write an **Acceptance summary**, then select **Accept exact task
   revision**.

If review finds a problem, provide a **Revision reason**, confirm reopening and
select **Reopen task**. Let the team revise the task or result. Editing a task creates a new version and makes
earlier reviews stale. Send the new version for review again.

The panel refuses direct acceptance when there is no current approved
independent review. That refusal protects the decision trail; it is not a UI
error.

### Read task state without guessing

Depending on its progress, a task can appear as `ready`, `assigned`, `active`,
`completed`, `review`, `accepted`, `blocked`, or `cancelled`. These are records
of its current step, not a quality score. In particular, `completed` means the
author reported the work complete; it still needs review before `accepted`.
`blocked` and `cancelled` have no review action available in the task drawer.
**Evidence data complete** describes the completeness of evidence metadata; it
does not mean that the task has been completed or accepted. Readiness is shown
separately from task state. An unknown or stale observation is not permission
to launch or accept work.

### Reuse templates, context and design

Open **Library** for 31 specializations, including QA reviewer, 45 grouped skills and seven built-in
blueprints. Create or update skills, specializations and custom blueprints in
the UI; skill groups can be archived and restored. A blueprint creates agents
and tasks from your project brief and starts nothing; its confirmation leads to
**Prepare run** for the first ready task. The **Context** tab holds project
background and constraints. See the
[library and task graph guide](service-library.md).

In **Context**, use the guided editor for a bounded summary, facts with exact
source references, decisions and open questions. The source picker shows
eligible metadata, not source bodies. Refreshing it never upgrades an already
selected revision. An unavailable or outdated reference must be resolved before
publication. The advanced editor preserves structured JSON when needed.

Click an agent’s name or avatar on the board to open its gallery, including
when it has no results yet. **Results** buttons on tasks and agents also open
available outputs. Filter images and live previews, switch latest/history,
and follow the task link on a result. Technical details show recorded producer
and version identifiers. “Current task” review labels describe the task, not
independent approval of an earlier image.
Results show that producer's images and separate live previews, labelled as the
latest version, an earlier version, an expired preview, an unavailable address
or a result awaiting a check; generated designs are not a Library tab. A Design
Package is selected explicitly during launch and is checked again for freshness
and access; merely viewing it does not attach it to a task or run.

Use **Conversation** for project messages, or open it on a task or agent for
that scope. It shows the recipient's availability and a delivery stepper.
Sending a message does not start a run or prove that a worker read it.
See [conversations and results](service-library.md#conversations-and-results)
for delivery states, private attachments and draft recovery.

### See the team and change a task

The **Board** shows your agents, the links you have recorded between them and
the department frames a blueprint created. Hire from its side panel and open a
card to reach that agent's tasks. **Tasks** shows the same work as a dependency
graph and as a list; its inspector holds run history and the next available
action. A saved specialization in **Library** makes hiring faster; it does not
change the local provider configuration.

To change a task, open it in **Tasks** and use **Edit task**. Saving creates a
new version. The UI warns that prior reviews then become stale, so review the
updated task again.

## Handle blockers and common problems

| What you see | What it means | What to do |
| --- | --- | --- |
| **This browser session is no longer active** | The one-time local sign-in link was used or expired. | Restart the UI and open the new printed URL once in the intended browser. |
| **Workspace files have not been created** | This repository has not been initialized for Agent Commons. | Confirm the terminal is in the right Git repository, then select **Initialize workspace**. |
| **Runtime configuration has not been created** or **The runtime is not ready to start work** | No usable local provider runtime is available. | Configure the runtime; if it remains blocked, install/authenticate Claude, Codex, or Grok Build and refresh status. |
| **No runtime profiles are available yet** | A configured runtime has no selectable profile. | Ask an operator to check the runtime configuration with the [troubleshooting guide](../../TROUBLESHOOTING.md). |
| Waiting on you | A run, task, review, or question needs a human decision. | Open the linked item and take the next action shown. |
| `input_needed` | A run asked a person a question. | The current runtime cannot deliver an answer, so the run ends as `needs_operator`. Inspect it, then start a new run. |
| `needs_operator` | The system reached an outcome it cannot safely resolve. | Inspect the run and task in the task inspector. Do not assume success or blindly retry. |
| Acceptance is refused | The task lacks a current approved independent review, or the task changed after review. | Send the current task version for an independent review, then accept only after approval. |

An action error appears beside its form. For an uncertain outcome, use the
offered retry: it preserves the original request and its identity. Do not
submit a second independent request merely because the response was lost. A
confirmed refusal can offer a new attempt after you correct the cause.

If workspace access itself has expired, restart the local UI and use its new
one-time URL. Advanced recovery is an operator task performed from a terminal;
see the [technical troubleshooting guide](../../TROUBLESHOOTING.md). Do not
paste provider output, prompts, credentials, or the local sign-in URL into a
bug report.

## Know the current boundaries

Agent Commons is alpha software for macOS and Linux. It supports local provider
tools; Windows is not supported. The provider runtime is experimental and may
require operator action.

This checkout provides **Board**, **Tasks**, **Agents**, **Library** and
**Settings**. The design Gallery remains at `/gallery` for design packages. The
older single-file panel is being retired, and this guide no longer sends you
there. Projects can be created and switched in the sidebar. Automatic task
scheduling and provider conversation resume are not implemented. A successful
local test does not qualify a live provider.

Library includes seven built-in blueprints, among them **Feature delivery** and
**Product discovery**. Applying a blueprint creates the selected agents and
their task graph with deny-by-default grants. It does not change runtime
profiles, download external skills, or start a run.

The product records coordination and decisions. It does not itself grant
permission to commit, push, deploy, publish, contact people, or perform other
external actions.

### Explore dependencies

In the task tracker’s map, search by title, task identifier or agent. Select a
task to focus its connected work, prerequisites or dependents. Zoom and Fit
help inspect the diagram; the task list is always available. Arrows mean that
the target depends on the source. They do not describe component membership
or parent/subtask hierarchy. The map reports prerequisites outside the current
filter. A focused selection can be drawn even when the project exceeds 128 tasks.

## Saved versions and builds

Open an agent's gallery, choose History, and inspect the exact version. Retained
images survive source overwrite and server restart when workspace state is kept.
Builds offers **Download build**: extract the ZIP and explicitly serve it locally
on a separate origin to inspect it. Commons does not start a server from a gallery
view. Keep the content store with backups; missing bytes stay unavailable.

**Current task** review and **Exact result** review are separate. A new image/build
never inherits an older result's approval. Technical details contain the exact CLI
review request. Run it from this project's directory, first verifying `session show`
and `orient` against the same workspace/state as the panel. A reviewer can inspect
exact pixels or all text build entries through scoped tools, then finalize the bound
review. A code/hash check does not claim runtime or visual testing. Human acceptance
remains separate. See the [result contract](../../PROTOCOL.md#retained-worker-results).
