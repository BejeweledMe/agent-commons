# Skills, specializations, blueprints and the task graph

The installation includes 45 professional skills and 30 specializations owned
by the service. No Codex or Claude home-directory skill installation is needed.
Each hired role pins an exact specialization and its skill versions.

## Start with a blueprint

Open **Library → Blueprints**. Choose a web application, mobile application,
Telegram Mini App, grounded AI assistant, or improvement to an existing service.
Preview its tasks and dependencies. Name the work and describe the audience,
desired outcome and constraints. Choose a profile, name and optional model for
each role; shared settings can fill the initial selections.

**Create roles and tasks** creates the displayed team and graph without launching
a model. In **Work**, open the first task, refine its criteria and prepare a run.
A suggested role is a planning hint, separate from the actual working role.
If a response is lost, retry the same operation before creating another team.

## Create a skill or specialization

In **Skills**, add an identifier, name, purpose and instructions. Instructions
grant no tools or permissions. A built-in skill exposes its instructions and
reference resources; editing creates a custom version. Custom updates retain
older versions for existing roles.

In **Specializations**, define responsibility, instructions and a primary skill.
Select additional core methods and conditional routes from the service library.
These are available methods, not a request to load every instruction at once.

In **Team**, choose the specialization, project name, provider, profile and model.
“Claude Frontend” and “Codex Frontend” can share a specialization. Existing hires
keep their versions; hire a new instance to adopt a changed definition. The primary
skill is delivered at launch; permitted companion resources are read progressively.
Missing or changed versions refuse instead of silently dropping instructions.

## Edit and observe work

**Work** offers a dependency graph and a task list. Prerequisites appear above
dependent tasks. Click a node for details, or use arrows, Home/End and Tab.
The list remains available on narrow screens and for large graphs.

Use **Edit task** for the title, description, criteria and dependencies;
**Add dependent task** for follow-on work; **Cancel task** to record a reason
without erasing history. Cycles and missing dependencies are refused. Active
runs and related live reviews must be resolved before editing or cancellation.

Automatic updates show observed task/run state without hidden model reasoning
or invented progress percentages. Colors have text and icon equivalents;
completion, review and acceptance remain separate. Reload a stale task before
preparing a new edit. An uncertain save retries its original body and key,
including after closing and reopening details.

## Design and execution

Open **Library → Design → Gallery**. Name a screen and upload PNG/JPEG content
up to 10 MiB and 16 megapixels. The imported screen is selected for publishing.
Name the package, arrange screens and publish. Inspect previews, revise the
package or leave feedback. Select its exact version explicitly for a Work run.
Importing is separate from design approval. Images become project files;
canonical history records exact metadata and provenance.

Real execution needs a configured provider CLI, account and available capacity.
Follow the refusal in **Settings**. Library tests do not certify live providers.
L1/R2 and remaining Grok constraints stay explicit in the
[current programme](../../agent-platform-implementation-program.md).
The graph does not automatically schedule every task.
