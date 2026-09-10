# Agent Commons: user guide

Agent Commons is a local web application for running work through a team of AI
agents. You define the work, choose who does it, follow the result, and make the
final decision. It does not make product decisions or accept work for you.

## In this guide

1. [Understand the workspace](#understand-the-workspace)
2. [Open the app safely](#open-the-app-safely)
3. [Set up a workspace and runtime](#set-up-a-workspace-and-runtime)
4. [Create a role and task](#create-a-role-and-task)
5. [Start and follow a run](#start-and-follow-a-run)
6. [Review and accept completed work](#review-and-accept-completed-work)
7. [Handle blockers and common problems](#handle-blockers-and-common-problems)
8. [Know the current boundaries](#know-the-current-boundaries)

## Understand the workspace

The product follows one simple chain:

```text
Task → choose a role → Run → independent Review → human Acceptance
```

### Role

A **role** is a continuing responsibility in your team: for example, `Backend
developer`, `Product designer`, or `Independent reviewer`. In **Team**,
create one with a name, a **Runtime profile**, a short explanation of what it is
responsible for, and a starting context.

Choose **Fresh** for an isolated start. **Accumulated** requires one exact,
current Context Pack revision when launching. It carries that bounded baseline,
not the previous provider conversation. This provenance remains visible during
review. A role is not the same thing as a single attempt to do work.

### Task

A **task** says what should be done and how you will judge the result. In the
UI it has a title, a description, and **Acceptance criteria**. Write one clear
result per criterion. Small, testable tasks are easier for a role to complete
and for a lead to review.

### Run

A **run** is one bounded attempt by a role to work on one task. Starting a run
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

In **Work**, use **Needs attention** and select a task to see its next step.
The full panel also marks items that are **waiting on you**. A blocker means an
agent, run, task, or review cannot continue without a decision or correction.
Open the item to see the next available action. Do not treat a blocked item as
finished work.

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
library; apply a template there or return to **Work** for an empty start.
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

To start a role, the product needs a local provider runtime. Choose **Configure
runtime** in **Settings** when offered. The setup checks for locally installed
Claude, Codex, or Grok Build tools and writes only generated configuration.

If no provider is available, install and authenticate either provider CLI using
your own account, then return to the panel and refresh its status. The current
Settings screen may not say exactly which local requirement is missing; use the
full panel or the [technical troubleshooting guide](../../TROUBLESHOOTING.md)
when the next action is unclear.

Do not edit generated runtime files through the browser: the current UI does
not provide a configuration editor. If you maintain a custom setup, use the
technical operator documentation.

## Create a role and task

Start with the task; you can prepare the team before its first run.

### Create a role

Open **Team** and its role form:

1. Enter a **Role name**.
2. Select a **Runtime profile**.
3. State **What this role is responsible for**.
4. Choose **Starting context**: **Fresh** or **Accumulated**.
5. Select **Create role**.

The profile controls how that role can run. If the form says **No runtime
profiles are available yet**, finish runtime setup before creating a role.

### Create a task

Open **Work → New task**:

1. Give the task a short **Task title**.
2. Explain **What should be done** and any important constraints.
3. Add **Acceptance criteria**, one result per line.
4. Optionally choose prerequisite tasks.
5. Select **Create task**. Its details open automatically.

The task is intentionally separate from a role. You can decide which active
role should work on it when you start a run.

## Start and follow a run

Select a ready task in **Work**, then **Prepare run**. The task is already
selected. Choose an active role and a run preset, inspect the budget and any
Context Pack or Design Package requirements, then explicitly select **Start
run**. Creating a task never starts a provider. Readiness and dependency checks
can prevent launch even when the task's recorded state is `ready`.

The inspector shows the goal, dependencies, run history and next available
action. **Technical details** exposes exact identifiers and revisions when
needed. Use the legacy panel for provider input, detailed run recovery and
advanced record editing.

In the full panel, a run can be requested, active, `input_needed`, succeeded,
failed, timed out, or `needs_operator`. `input_needed` means the run is waiting
for a human answer. Open **Attention**: when this panel can answer the request,
answering it resumes the run. If the provider already exited or no answer is
available, the run becomes `needs_operator`. `needs_operator` means the system
cannot safely continue on its own. Inspect it before retrying or creating
another run; it is not a successful result.

## Review and accept completed work

Use the selected task's actions in **Work**; advanced recovery is also available
in the full panel. Review and acceptance always target an exact task revision.

1. Open the task after its work is ready.
2. Select **Request review**. The task now waits for an independent review.
3. Start or ask an independent reviewer to check that task. The person or role
   doing the work cannot provide the independent review needed for acceptance.
4. If the verdict is approved and still matches the current task version, open
   the task, write an **Acceptance summary**, then select **Accept exact task
   revision**. The full panel uses its own acceptance dialog for the same rule.

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

Open **Library** for 30 specializations, 45 grouped skills and seven built-in
blueprints. Create or update methods, role definitions and custom blueprints in
the UI. A blueprint creates roles and tasks from your project brief; launching
remains explicit. The **Context** tab holds project background and constraints.
See the [library and task graph guide](service-library.md).

In **Context**, use the guided editor for a bounded summary, facts with exact
source references, decisions and open questions. The source picker shows
eligible metadata, not source bodies. Refreshing it never upgrades an already
selected revision. An unavailable or outdated reference must be resolved before
publication. The advanced editor preserves structured JSON when needed.

Open **Results** on a task or hired agent when accessible outputs exist. Results
show that producer's images and separate live previews; generated designs are
not a Library tab. A Design Package is selected explicitly during launch and is
checked again for freshness and access; merely viewing it does not attach it to
a task or run.

Use **Conversation** in Work for project messages, or on a task or agent for that
scope. Sending a message does not start a run or prove that a worker read it.
See [conversations and results](service-library.md#conversations-and-results)
for delivery states, private attachments and draft recovery.

### Use the full panel for team operations

The full panel's **Board** shows your roles, tasks, and their relationships.
Use **Attention** to triage items waiting for a person, **Runs** to inspect
attempts, and **Main chat** for a conversation that is not itself a task. Use
**Role catalogue** to reuse a saved role template. A template makes hiring
faster; it does not change the local provider configuration.

To change a task, open it and use **Record → Edit task…**. Saving creates a new
version. The UI warns that prior reviews then become stale, so review the
updated task again.

## Handle blockers and common problems

| What you see | What it means | What to do |
| --- | --- | --- |
| **This browser session is no longer active** | The one-time local sign-in link was used or expired. | Restart the UI and open the new printed URL once in the intended browser. |
| **Workspace files have not been created** | This repository has not been initialized for Agent Commons. | Confirm the terminal is in the right Git repository, then select **Initialize workspace**. |
| **Runtime configuration has not been created** or **The runtime is not ready to start work** | No usable local provider runtime is available. | Configure the runtime; if it remains blocked, install/authenticate Claude, Codex, or Grok Build and refresh status. |
| **No runtime profiles are available yet** | A configured runtime has no selectable profile. | Use the full panel or technical operator guide to check the runtime configuration. |
| **waiting on you** | A run, task, review, or question needs a human decision. | Open the linked item and take the next action shown. |
| `input_needed` | A live run is waiting for an answer from a person. | Open **Attention** and answer it if this panel offers an answer action. If it cannot be answered or the provider has exited, inspect the resulting `needs_operator` item. |
| `needs_operator` | The system reached an outcome it cannot safely resolve. | Inspect the run and task in the full panel. Do not assume success or blindly retry. |
| Acceptance is refused | The task lacks a current approved independent review, or the task changed after review. | Send the current task version for an independent review, then accept only after approval. |

An action error appears beside its form. For an uncertain outcome, use the
offered retry: it preserves the original request and its identity. Do not
submit a second independent request merely because the response was lost. A
confirmed refusal can offer a new attempt after you correct the cause.

If workspace access itself has expired, restart the local UI and use its new
one-time URL. Use the full panel for advanced recovery. Do not paste
provider output, prompts, credentials, or the local sign-in URL into a bug
report.

## Know the current boundaries

Agent Commons is alpha software for macOS and Linux. It supports local provider
tools; Windows is not supported. The provider runtime is experimental and may
require operator action.

This checkout provides **Work**, **Team**, **Library** and **Settings**, with
Gallery and the legacy panel for specialized views. Projects can be created
and switched in the sidebar. Automatic task scheduling and provider conversation
resume are not implemented. A successful local test does not qualify a live provider.

Library includes built-in **Feature delivery** and **Product discovery**
blueprints. Applying a blueprint creates the selected roles and their task graph
with deny-by-default grants. It does not change runtime profiles, download
external skills, or start a run.

The product records coordination and decisions. It does not itself grant
permission to commit, push, deploy, publish, contact people, or perform other
external actions.
