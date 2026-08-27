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
Role → Task → Run → independent Review → human Acceptance
```

### Role

A **role** is a continuing responsibility in your team: for example, `Backend
developer`, `Product designer`, or `Independent reviewer`. In the Work screen,
create one with a name, a **Runtime profile**, a short explanation of what it is
responsible for, and a starting context.

Choose **Fresh** when a role should start without earlier work context. Choose
**Accumulated** only when you want that role to continue with its own previous
context. A role is not the same thing as a single attempt to do work.

### Task

A **task** says what should be done and how you will judge the result. In the
UI it has a title, a description, and **Acceptance criteria**. Write one clear
result per criterion. Small, testable tasks are easier for a role to complete
and for a lead to review.

### Run

A **run** is one bounded attempt by a role to work on one task. Starting a run
uses the local Claude or Codex provider configured on your computer. It can
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

The full panel marks items that are **waiting on you**. A blocker means an
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

Keep the local URL private while the panel is running. It gives a browser access
to this local panel; it is not a link to share with collaborators.

## Set up a workspace and runtime

The UI works with the Git repository from which you started it. It does not let
you choose another project in the browser. Before pressing **Initialize
workspace**, make sure the terminal is in the project you intend to use.

### 1. Initialize the workspace

If the Work screen says **Workspace files have not been created**, choose
**Initialize workspace**. Agent Commons creates its workspace files in the
current repository. If the screen says the folder is not a Git repository,
initialize or choose a Git repository first, then start the UI again.

### 2. Configure the runtime

To start a role, the product needs a local provider runtime. Choose **Configure
runtime** when the screen offers it. The setup checks for locally installed
Claude or Codex tools and writes only generated configuration.

If no provider is available, install and authenticate either provider CLI using
your own account, then return to the panel and refresh its status. The current
Work screen may not say exactly which local requirement is missing; use the
full panel or the [technical troubleshooting guide](../../TROUBLESHOOTING.md)
when the next action is unclear.

Do not edit generated runtime files through the browser: the current UI does
not provide a configuration editor. If you maintain a custom setup, use the
technical operator documentation.

## Create a role and task

### Create a role

In **2. Create a role**:

1. Enter a **Role name**.
2. Select a **Runtime profile**.
3. State **What this role is responsible for**.
4. Choose **Starting context**: **Fresh** or **Accumulated**.
5. Select **Create role**.

The profile controls how that role can run. If the form says **No runtime
profiles are available yet**, finish runtime setup before creating a role.

### Create a task

In **3. Describe the task**:

1. Give the task a short **Task title**.
2. Explain **What should be done** and any important constraints.
3. Add **Acceptance criteria**, one result per line.
4. Select **Create task**.

The task is intentionally separate from a role. You can decide which active
role should work on it when you start a run.

## Start and follow a run

In **4. Start the run**, choose a role and a task, then select **Start run**.
The role must be active and the runtime must be configured.

After a successful launch, the Work screen says **Run started** and directs you
to **Open legacy panel**. This is the current workflow boundary:

- The Work screen is for setup, creating roles and tasks, and starting one run.
- The full panel is for detailed run monitoring, the **Runs** view, review,
  acceptance, and recovery.

In the full panel, a run can be requested, active, succeeded, failed, timed
out, or `needs_operator`. `needs_operator` means the system cannot safely
continue on its own. Inspect it before retrying or creating another run; it is
not a successful result.

## Review and accept completed work

Use the full panel for this stage.

1. Open the task after its work is ready.
2. Select **Send for review**. The task now waits for an independent review.
3. Start or ask an independent reviewer to check that task. The person or role
   doing the work cannot provide the independent review needed for acceptance.
4. If the verdict is approved and still matches the current task version, open
   the task, select **Accept…**, write **What are you accepting?**, then select
   **Record the acceptance**.

If review finds a problem, use **Send back to work…**, explain why, and let the
team revise the task or result. Editing a task creates a new version and makes
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

### Use the full panel for team operations

The full panel's **Board** shows your roles, tasks, and their relationships.
Use **Attention** to triage items waiting for a person, **Runs** to inspect
attempts, and **Main chat** for a conversation that is not itself a task. Use
**Role catalogue** to reuse a saved role template. A template makes hiring
faster; it does not change the local provider configuration.

Use task **Settings** only when you mean to create a new version. The UI warns
that prior reviews then become stale, so review the updated task again.

## Handle blockers and common problems

| What you see | What it means | What to do |
| --- | --- | --- |
| **This browser session is no longer active** | The one-time local sign-in link was used or expired. | Restart the UI and open the new printed URL once in the intended browser. |
| **Workspace files have not been created** | This repository has not been initialized for Agent Commons. | Confirm the terminal is in the right Git repository, then select **Initialize workspace**. |
| **Runtime configuration has not been created** or **The runtime is not ready to start work** | No usable local provider runtime is available. | Configure the runtime; if it remains blocked, install/authenticate Claude or Codex and refresh status. |
| **No runtime profiles are available yet** | A configured runtime has no selectable profile. | Use the full panel or technical operator guide to check the runtime configuration. |
| **waiting on you** | A run, task, review, or question needs a human decision. | Open the linked item and take the next action shown. |
| `needs_operator` | The system reached an outcome it cannot safely resolve. | Inspect the run and task in the full panel. Do not assume success or blindly retry. |
| Acceptance is refused | The task lacks a current approved independent review, or the task changed after review. | Send the current task version for an independent review, then accept only after approval. |

If a page is temporarily unavailable, refresh once. If it continues, reopen the
local UI using a new URL and use the full panel for recovery. Do not paste
provider output, prompts, credentials, or the local sign-in URL into a bug
report.

## Know the current boundaries

Agent Commons is alpha software for macOS and Linux. It supports local provider
tools; Windows is not supported. The provider runtime is experimental and may
require operator action.

Today, the product has two connected UI surfaces rather than one finished
single-page workflow. The Work screen starts work; the full panel completes the
operational loop. Native Work views for Runs, review, acceptance, visual design
artifacts, context inheritance, or automatic scheduling are not available in
this release unless the UI explicitly shows them.

The product records coordination and decisions. It does not itself grant
permission to commit, push, deploy, publish, contact people, or perform other
external actions.
