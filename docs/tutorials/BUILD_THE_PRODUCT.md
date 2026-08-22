# Build the product: empty directory to accepted work

> **Placeholder section.** The steps below are the confirmed shape of the
> flow — every screen, route, and refusal code they name exists in the code
> and is covered by tests — but the prose and screenshots are not yet real.
> This section is filled in once the whole scenario has been driven start to
> finish against a live panel: each `[SCREENSHOT: …]` marker is captured from
> that running panel, not drawn ahead of it, and the mock paragraphs below are
> replaced with what the screen actually says rather than what it is expected
> to say. Until that pass happens, treat every UI label and button name below
> as illustrative, not literal — the panel's own two-language string table is
> the source of truth for exact copy.

This is the guide for the two-step path — install, then run — with no `init`,
no `session start`, and no flags. Everything past installation happens in the
browser tab the panel opens.

```bash
git clone https://github.com/BejeweledMe/agent-commons.git
cd agent-commons
make sync
uv run agent-commons ui
```

## What this walkthrough covers

```text
empty directory → project created → runtime found and configured
  → role hired → task written → run launched → work reviewed and accepted
```

Each step below is one screen. None of them needs a second terminal command;
`agent-commons ui` is the only one this guide runs.

## 1. Open the panel on an empty directory

`agent-commons ui` binds a loopback port, prints a URL with a bearer token in
its fragment, and opens a browser tab. On a directory that is not yet a
workspace, the panel does not refuse — it serves the first-run screen instead.

*(mock)* The screen states plainly that this directory has no Agent Commons
project yet and offers to create one here, using the same initializer
`agent-commons init` runs from the terminal.

`[SCREENSHOT: first-run screen on an uninitialized git repository]`

## 2. Create the project

*(mock)* One click records the workspace. The screen updates in place — no
reload, no second command — and moves to the next open question: what will
run the roles this project hires.

`[SCREENSHOT: workspace just created, panel now asking about runtime setup]`

## 3. Find a provider

The panel looks for `claude` and `codex` on `PATH` and reports what it found.

- **A provider was found.** *(mock)* The screen names it and offers to write
  an operator runtime config for it — the generated file that pins the exact
  executable, sandbox, and trust mode this wave's contract fixes in code, not
  in the browser.
- **No provider was found.** *(mock)* The screen says run functionality is
  unavailable. Install `claude` or `codex` with the operator's subscription,
  then choose **Look again**. The guide is available from the same screen; it
  does not offer a simulated run.

`[SCREENSHOT: provider discovery result, both the found and the not-found case]`

## 4. Write the operator config

*(mock)* One click writes the config to the frozen path
(`$XDG_CONFIG_HOME/agent-commons/runtime.yaml`, `~/.config/agent-commons/runtime.yaml`
when that variable is unset) and the panel adopts it without a restart. A
config the panel's own loader will not accept is never left in place silently
— the screen names what was rejected and why.

`[SCREENSHOT: runtime config written, panel now reporting itself configured]`

## 5. Hire a role

*(mock)* The board's hire action opens a form: a profile (which fixes the
provider, sandbox, and trust mode), a name, a rationale, and — new this wave —
a model, offered as a list the server assembled from the configured profiles
and the models already running on this project's roles, plus a free-text field
for anything not on that list. The model is fixed at hire time; changing it
later means rebuilding the role's context for a different model, which this
version does not do, so there is deliberately no way to edit it afterward from
the role's settings.

`[SCREENSHOT: hire form with the model field]`

## 6. Give the role a task

*(mock)* A task is written once, with acceptance criteria, and assigned to the
hired role — the same recording surface as every other write in the panel,
going through `CommonsManager` like the CLI and the MCP adapter.

`[SCREENSHOT: task drawer with the new role attached]`

## 7. Launch the run

*(mock)* Run starts a delegation on the role's behalf and, when the runtime is
configured, launches it through the same broker path the CLI uses. There is no
separate confirmation dialog before a real provider launch spends your
subscription's usage — the panel does not ask per run, the same way opening
Codex or Claude Code directly does not ask per window. That fact belongs in
documentation instead, which is why it is stated here and in the [provider
configuration section](../../README.md#configure-a-provider-before-running-work)
of the README rather than in a click-through dialog.

`[SCREENSHOT: run in progress, phase shown as metadata only]`

## 8. Accept the finished work

*(mock)* A succeeded run without an accepted task shows up in the attention
queue with the role's name and a link to the task. From there: request review,
receive an independent verdict, and accept — the same chain
[the changelog describes](../../CHANGELOG.md), reachable from the panel
instead of the CLI.

`[SCREENSHOT: accepted task, green tick]`

## What is real today, and what is not yet

Every route, refusal code, and generated-config shape named above is landed
and tested at the time this section was written. What is not yet true is the
walkthrough itself having been driven end to end against a running panel with
a camera on it — that is the one thing this placeholder is waiting on.
