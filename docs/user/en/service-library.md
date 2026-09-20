# Skills, specializations, blueprints and the task graph

The installation includes 45 professional skills and 30 specializations owned
by the service. No Codex or Claude home-directory skill installation is needed.
Each hired agent pins an exact specialization and its skill versions. The
ledger and the hire form call that standing record a *role*.

## Start with a blueprint

Open **Library → Blueprints**. Seven are built in: web application,
mobile application, Telegram Mini App, grounded AI assistant, improve an
existing service, feature delivery, and product discovery. Preview the tasks
and dependencies. Name the work and describe the audience, desired outcome and
constraints. Choose a profile, name and optional model for each role; shared
settings can fill the initial selections.

**Create roles and tasks** creates the displayed team and graph without
launching a model; its confirmation leads to **Prepare run** for the first
ready task. In **Tasks**, open that task, refine its criteria and prepare the
run. A suggested role is a planning hint, separate from the agent that finally
does the work. On the **Board**, the new agents and tasks appear inside a
department frame. If a response is lost, retry the same operation before
creating another team.

Use **New blueprint** to define your own roles, tasks and dependencies. You can
copy a built-in to a custom definition, edit custom versions, and archive them.
**Refresh library** reloads the catalog; it does not launch or approve work.

## Create a skill or specialization

In **Skills**, expand a group or search across groups. **New group** creates a
custom group, which can later be archived and restored; **Add skill** opens the
editor. Add an identifier, name, purpose
and instructions. Instructions
grant no tools or permissions. A built-in skill exposes its instructions and
reference resources; editing creates a custom version. Custom updates retain
older versions for existing roles.

In **Specializations**, define responsibility, instructions and a primary skill.
Select additional core methods and conditional routes from the service library.
These are available methods, not a request to load every instruction at once.

In **Agents**, choose the specialization, project name, provider, profile and
model. You can hire from the same form or from the Board's side panel. “Claude
Frontend” and “Codex Frontend” can share a specialization. Existing hires keep
their versions; hire a new instance to adopt a changed definition. The primary
skill is delivered at launch; permitted companion resources are read
progressively. Missing or changed versions refuse instead of silently dropping
instructions.

## Edit and observe work

**Tasks** offers a dependency graph and a task list. Prerequisites appear above
dependent tasks. Click a node for details, or use arrows, Home/End and Tab.
The list remains available on narrow screens and for large graphs. The
**Board** shows the same team as cards, links and department frames; its
arrangement is your layout and changes no work record.

Use **Edit task** for the title, description, criteria and dependencies;
**Add dependent task** for follow-on work; **Cancel task** to record a reason
without erasing history. Cycles and missing dependencies are refused. Active
runs and related live reviews must be resolved before editing or cancellation.

Automatic updates show observed task/run state without hidden model reasoning
or invented progress percentages. Colors have text and icon equivalents;
completion, review and acceptance remain separate. Reload a stale task before
preparing a new edit. An uncertain save retries its original body and key,
including after closing and reopening details.

## Conversations and results

Open **Conversation** for the project, or on a task or hired agent for that
scope. It shows the recipient's availability and a delivery stepper. Project
messages address current and future project agents; task messages address
workers on that task. Sending a message does not start a run.
A running worker checks messages at its supported checkpoints. Recorded, queued,
fetched, acknowledged and answered are separate states; an exited process is not
a read receipt.

A message supports up to 10 PNG/JPEG images and 10 other files, 15,000,000 bytes
each. Attachment bytes stay in private service storage outside the project and
canonical ledger. Drafts survive switching views/projects in the open app;
a page reload clears the local text draft. An uncertain send must retry the same
operation before editing its frozen content.

**Results** appears on a task or agent when accessible outputs exist. It shows
that producer's image designs and separate live frontend previews, labelled as
the latest version, an earlier version, an expired preview, an unavailable
address or a result awaiting a check, with access to history. Other producers'
screens stay separate. Reported readiness does not prove reachability. Design
packages can still be selected explicitly when preparing a run.
Design results belong to project work, not the reusable Library.


Provider abilities differ. A worker may read an attachment and publish an existing
image without being able to create a new image or run build commands. Current
Claude native shell execution needs approval; Grok terminal execution is denied
by its builder profile. A successful run does not remove these restrictions.
See the [tested provider boundaries](../../audits/2026-09-08-project-native-collaboration-validation.md)
for the exact verified capabilities and remaining checks.

Real execution needs a configured provider CLI, account and available capacity.
Follow the refusal in **Settings**. Library tests do not certify live providers.
L1/R2 and remaining Grok constraints stay explicit in the
[current programme](../../archive/plans/agent-platform-implementation-program.md).
The graph does not automatically schedule every task.
