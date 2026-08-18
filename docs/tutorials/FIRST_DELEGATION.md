# First delegation: one coordinator, one worker

This tutorial starts in a new customer repository and asks one direct Claude
worker to build a small static site. The hierarchy has two levels only:

```text
your coordinator session
└── one direct worker (`max_depth: 0`, so it cannot create grandchildren)
```

This is an implementation exercise, not an independent review or an automatic
approval flow. A successful provider process still does not authorize a commit,
push, deployment, or task acceptance.

## Choose the path before you start

There are two honest ways through this guide:

- **Manual two-window coordination** uses the core `agent-commons` CLI and an
  interactive Codex or Claude window. Choose it when you want coordination but
  do not have the optional broker, MCP package, provider profile, or a workspace
  you are willing to trust to a writable headless process.
- **Real provider execution** uses `broker preflight`, `delegation create`, and
  `broker run`. Choose it only when the provider CLI is already installed and
  authenticated, and the repository is trusted or externally OS-isolated.

Both paths preserve the same task and session boundaries. The real-provider
path is below; the [manual fallback](#manual-two-window-fallback) is available
immediately after preflight, before a delegation is recorded or a provider
attempt is consumed.

## 1. Install the optional runtime

Install the locked environment from the exact Agent Commons source checkout
you intend to use. The repository's `make sync` target runs
`uv sync --locked --extra test`; that locked extra includes the MCP dependency
and installs the worker-scoped MCP entry point used by the broker. Do not create
a second venv or add packages to this uv-managed one with pip.

```bash
git clone https://github.com/BejeweledMe/agent-commons.git
cd agent-commons
make sync
export AGENT_COMMONS_PY="$PWD/.venv/bin/python"
export AGENT_COMMONS_MCP="$PWD/.venv/bin/agent-commons-mcp"
export PATH="$PWD/.venv/bin:$PATH"
"$AGENT_COMMONS_PY" -m agent_commons --version
"$AGENT_COMMONS_MCP" --help
```

If you want coordination only, the ordinary Quickstart installation without
the `mcp` extra is enough.

## 2. Create a clean customer project

Run this from a directory in which you keep projects. The state base is a
sibling of the project rather than a directory inside the delegated workspace.

```bash
export FIRST_DELEGATION_ROOT="$PWD/first-delegation-site"
export AGENT_COMMONS_STATE_BASE="$PWD/agent-commons-state"
mkdir -p "$FIRST_DELEGATION_ROOT" "$AGENT_COMMONS_STATE_BASE"
cd "$FIRST_DELEGATION_ROOT"
git init
unset AGENT_COMMONS_STATE_ROOT
"$AGENT_COMMONS_PY" -m agent_commons init \
  --integration codex --integration claude
"$AGENT_COMMONS_PY" -m agent_commons \
  --read-only --json support --show-paths
```

The support report should say that the canonical workspace is available and
show a state root derived under `AGENT_COMMONS_STATE_BASE`. `init` does not
stage or commit anything. In this otherwise empty tutorial repository, the
operator may choose to make a baseline commit before allowing a worker to edit:

```bash
git add .
git -c user.name='Tutorial Operator' \
  -c user.email='tutorial@example.invalid' \
  commit -m 'Initialize coordinated site project'
git status --short
```

That commit is an explicit operator action, not authority granted by Agent
Commons.

## 3. Start the coordinator session

Stay in this shell through delegation creation and launch. `--shell-export`
selects the new session and keeps its rotating ownership nonce in the shell;
the guide never asks you to paste the nonce into a prompt, config, or log.

```bash
eval "$("$AGENT_COMMONS_PY" -m agent_commons session start \
  --stable-instance-id first-site-coordinator-01 \
  --principal local-operator \
  --client codex \
  --software codex-cli \
  --role coordinator \
  --ttl-seconds 3600 \
  --shell-export zsh)"
export COORDINATOR_SESSION_ID="$AGENT_COMMONS_SESSION_ID"
"$AGENT_COMMONS_PY" -m agent_commons session current
"$AGENT_COMMONS_PY" -m agent_commons doctor
"$AGENT_COMMONS_PY" -m agent_commons orient
"$AGENT_COMMONS_PY" -m agent_commons inbox
```

The session that records a delegation is its canonical requester. `broker run`
must use that same explicit session; a different coordinator session cannot
launch it. If only the shell selection is accidentally lost, recover the
non-secret selection and verify that the session is still active before
continuing:

```bash
export AGENT_COMMONS_SESSION_ID="$COORDINATOR_SESSION_ID"
"$AGENT_COMMONS_PY" -m agent_commons session current
```

The export restores selection only; it does not revive a closed or expired
session. Do not borrow this session from another agent window. If the original
requester is absent, expired, or closed, stop rather than guessing. An
operator-authorized recovery session can inspect
`"$AGENT_COMMONS_PY" -m agent_commons delegation recover --help` and terminalize
only the exact current `requested` delegation. Recovery is not takeover: create
new work from the new active session afterward. It cannot take over active work
or reattach an exited provider.

## 4. Configure and preflight the worker first

A builder profile launches a process that may edit the checkout. The profile is
the operator-controlled recipe that fixes the executable and its permissions.
It therefore fails closed unless the operator explicitly sets
`trusted_workspace: true` or supplies external OS isolation. Use that opt-in
only for a repository whose contents and hooks you trust. Do not use this
example to run untrusted downloaded work directly on the host.

The runtime config is operator-owned and must be outside the delegated project.
The commands below configure one Claude builder using executables already on
`PATH`:

```bash
export PROVIDER_BIN="$(command -v claude)"
export COMMONS_MCP_BIN="$AGENT_COMMONS_MCP"
export GIT_BIN="$(command -v git)"
export RUNTIME_CONFIG="$AGENT_COMMONS_STATE_BASE/first-delegation-runtime.yaml"
umask 077
printf '%s\n' \
  'profiles:' \
  '  claude-builder:' \
  "    executable: $PROVIDER_BIN" \
  "    mcp_executable: $COMMONS_MCP_BIN" \
  "    git_executable: $GIT_BIN" \
  '    permission_mode: acceptEdits' \
  '    trusted_workspace: true' \
  > "$RUNTIME_CONFIG"
chmod 600 "$RUNTIME_CONFIG"
```

Confirm that the provider CLI is authenticated through the account or
subscription you intend to use. Agent Commons never changes credentials or
billing modes. Then run the non-billable compatibility checks:

```bash
"$AGENT_COMMONS_PY" -m agent_commons --json broker profiles \
  --profile-config "$RUNTIME_CONFIG"
"$AGENT_COMMONS_PY" -m agent_commons --json broker preflight claude-builder \
  --purpose implementation \
  --profile-config "$RUNTIME_CONFIG"
```

Continue only when preflight returns `"ok": true`. It starts provider `--help`
and the generated MCP contract, but it starts no model work and consumes no
delegation attempt. It does not prove that provider authentication or a later
model response will succeed.

If the profile, MCP executable, provider, authentication, or trust decision is
not ready, stop here and use the
[manual two-window fallback](#manual-two-window-fallback). There is no need to
create a doomed delegation first.

## 5. Create the site task

The target text is the worker's product brief, so keep it concrete and
testable. The following shell variables extract the public task ID and exact
revision from the CLI's JSON; neither is a secret.

```bash
TASK_JSON=$("$AGENT_COMMONS_PY" -m agent_commons --json task create \
  --title 'Build a one-page coffee shop site' \
  --description 'Create index.html with embedded CSS for Moonbeam Coffee. Include a hero, three menu items, opening hours, and a contact link. Keep it responsive and dependency-free.' \
  --acceptance-criterion 'index.html opens as a complete static page without a build step' \
  --acceptance-criterion 'the page contains a hero, three menu items, opening hours, and a contact link' \
  --acceptance-criterion 'the layout remains readable on a narrow viewport' \
  --idempotency-key first-site-task-create-v1)
TASK_ID=$(printf '%s' "$TASK_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["entity_ref"]["id"])')
TASK_REVISION=$(printf '%s' "$TASK_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])')
printf 'Task: %s @ %s\n' "$TASK_ID" "$TASK_REVISION"
```

An ID names the task; its revision identifies the exact version being handed
off. Each idempotency key is a retry label: reuse it only for an identical
operation, and use a different key for a different operation.

Immediately before transferring the writable path, claim the task and output
file and stop editing them in the coordinator window. The JSON stays in a shell
variable so the private claim ownership value is not printed or pasted into a
prompt.

```bash
CLAIM_JSON=$("$AGENT_COMMONS_PY" -m agent_commons --json claim acquire \
  --resource "task:$TASK_ID" \
  --resource path:index.html \
  --ttl-seconds 1800 \
  --description 'Transferred to the first direct worker' \
  --idempotency-key first-site-scope-claim-v1)
CLAIM_ID=$(printf '%s' "$CLAIM_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["claim_id"])')
CLAIM_OWNERSHIP=$(printf '%s' "$CLAIM_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["nonce"])')
unset CLAIM_JSON
```

## 6. Record one bounded direct worker

`limits-json` is a safety envelope, not an instruction prompt:

- `max_depth: 0` means this direct worker cannot create child delegations;
- `wall_time_seconds: 900` allows at most 15 minutes for this attempt;
- `max_attempts: 1` and `max_concurrency: 1` allow one process, once;
- `provider_units: 1` bounds provider-process attempts for this parent and
  provider. It is not a token count or dollar cap.

```bash
DELEGATION_JSON=$("$AGENT_COMMONS_PY" -m agent_commons --json delegation create \
  --target-ref "task:$TASK_ID" \
  --target-revision "$TASK_REVISION" \
  --target-profile claude-builder \
  --purpose implementation \
  --limits-json '{"max_depth":0,"wall_time_seconds":900,"max_attempts":1,"max_concurrency":1,"budget":{"unit":"provider_units","limit":1}}' \
  --idempotency-key first-site-delegation-create-v1)
DELEGATION_ID=$(printf '%s' "$DELEGATION_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["entity_ref"]["id"])')
DELEGATION_REVISION=$(printf '%s' "$DELEGATION_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])')
printf 'Delegation: %s @ %s\n' "$DELEGATION_ID" "$DELEGATION_REVISION"
```

The task revision is the fixed work target. The delegation revision is the new
request's own revision and is the value `broker run` expects.

## 7. Launch from the same coordinator session

Verify the selected requester once more, then launch. Creation and launch use
different idempotency keys because they are different operations.

```bash
"$AGENT_COMMONS_PY" -m agent_commons session current
test "$AGENT_COMMONS_SESSION_ID" = "$COORDINATOR_SESSION_ID"
"$AGENT_COMMONS_PY" -m agent_commons --json broker run \
  "$DELEGATION_ID" "$DELEGATION_REVISION" \
  --idempotency-key first-site-delegation-launch-v1 \
  --telemetry local \
  --profile-config "$RUNTIME_CONFIG"
```

The broker registers a distinct child session and runs synchronously. Do not
blindly retry after canonical start. If the provider exits after requesting
input, the current headless runtime cannot resume or reattach it; inspect the
recorded `needs_operator` state and keep any files it safely produced.

## 8. Verify the result honestly

Check canonical state and the actual workspace separately:

```bash
"$AGENT_COMMONS_PY" -m agent_commons --json delegation show "$DELEGATION_ID"
"$AGENT_COMMONS_PY" -m agent_commons task list
git status --short
test -s index.html
command grep -q '<html' index.html
command grep -q 'Moonbeam Coffee' index.html
git diff --check
```

The delegation should be `succeeded` and contain typed result references; the
file checks should also pass. Either signal alone is insufficient. A succeeded
delegation is a result report, not review approval or task acceptance. Inspect
the page and the task state before deciding what to do next.

Release the claim, then end this session only after no requested, active, or
input-needed delegation remains:

```bash
"$AGENT_COMMONS_PY" -m agent_commons claim release \
  "$CLAIM_ID" --nonce "$CLAIM_OWNERSHIP"
unset CLAIM_OWNERSHIP
"$AGENT_COMMONS_PY" -m agent_commons session end \
  --nonce "$AGENT_COMMONS_SESSION_NONCE"
unset AGENT_COMMONS_SESSION_ID AGENT_COMMONS_SESSION_NONCE
```

Never paste either ownership value into a prompt, repository file, or shared
log.

## Manual two-window fallback

Use this branch when the core CLI works but real provider execution is not
ready. Do not run `delegation create`; the second interactive window is the
direct worker.

1. Keep the coordinator window open. Create the task with the commands in
   [step 5](#5-create-the-site-task), but let the worker own the file claim.
2. Open a second terminal in the same customer repository, export the same
   `AGENT_COMMONS_STATE_BASE`, and start a **distinct** implementation session:

   ```bash
   cd /absolute/path/to/first-delegation-site
   unset AGENT_COMMONS_STATE_ROOT
   export AGENT_COMMONS_STATE_BASE=/absolute/path/to/agent-commons-state
   export AGENT_COMMONS_PY=/absolute/path/to/agent-commons/.venv/bin/python
   export PATH=/absolute/path/to/agent-commons/.venv/bin:$PATH
   eval "$("$AGENT_COMMONS_PY" -m agent_commons session start \
     --stable-instance-id first-site-worker-01 \
     --principal local-operator \
     --client claude \
     --software claude-code \
     --role implementation-worker \
     --shell-export zsh)"
   "$AGENT_COMMONS_PY" -m agent_commons session current
   "$AGENT_COMMONS_PY" -m agent_commons doctor
   ```

3. Copy the non-secret task ID and revision from the coordinator window, then
   ask the interactive worker to follow `.agent-commons/ONBOARDING.md`, take
   that exact task, claim `path:index.html`, build and check the page, complete
   the task with an honest summary, release its claim, and report the current
   task revision. The coordinator must not edit `index.html` while this happens.
4. Run the workspace checks from [step 8](#8-verify-the-result-honestly) in the
   coordinator window. Manual completion is still not review approval or
   acceptance.

This fallback gives up headless launch, not the task, claims, distinct-session,
or evidence boundaries.
