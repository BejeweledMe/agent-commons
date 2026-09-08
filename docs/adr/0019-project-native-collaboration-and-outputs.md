# ADR 0019: Project-native collaboration, reusable library and task outputs

Date: 2026-09-08. Status: accepted direction; implementation and qualification open.
Authority: operator request in the self-improvement session. This decision changes
the product direction; it does not approve an implementation or a release.

## Problem and evidence

The operator cannot reliably distinguish the project, team, reusable library and
produced results. The current sidebar exposes duplicate search labels because
`ProjectSidebar.tsx` uses `sr-only` without its CSS definition. Project actions
are separated from the project name. Library offers Design and Context beside
reusable methods, but generated outputs belong to particular tasks and agents.
Skills are a flat catalog; the five built-in blueprints have no creation API.

The React Work application lacks the existing legacy chat UI. Canonical threads
accept text only; saving a message does not prove a running provider read it.
Gallery screens have exact artifact/task references, but the project catalog
does not filter individual screens by producing task. A task revision actor is
not sufficient evidence of the agent that produced an artifact. Grok currently
places its composed instruction in process arguments. These are source findings,
not speculative aesthetic preferences.

## Decision

### Project shell and navigation

The project is the working directory and the boundary for tasks, hired agents,
conversations, context and results. Use a compact sidebar with one search field,
one create/connect action and an accessible ellipsis beside each project.
Actions stay attached to their subject. Archive or remove from the registry never
means delete its folder. Show only supported actions, with explicit unavailable
states where the host cannot perform an action.

Create/connect accepts a typed path and, on supported local hosts, a native
folder chooser. Infer the display name from the directory and allow editing it.
Retain inspect/create identity checks, owner/symlink restrictions, idempotency
and filesystem revalidation. A browser directory upload is not a trusted local
path. Headless/unsupported platforms keep the manual-path flow.

Use plain labels and one short explanation for hired agents. Remove protocol
language from primary actions. Keep accessible labels visually hidden rather
than dropping labels, and preserve keyboard navigation, focus, reduced motion,
responsive layouts, empty states and the two-language contract.

### Reusable library

Library contains skills, specializations and blueprints. Built-in skills appear
in collapsible named groups with counts and grouped search. Users can create
groups, including empty groups, and create/move/edit skills. Catalog organization
must not invalidate pinned skill content hashes or alter instructions silently.

Custom blueprints are persistent, versioned definitions with slots, exact
specialization references, task descriptions, acceptance criteria and a validated
acyclic dependency graph. Creation, editing and copying are visible UI actions.
Apply uses an exact definition version and preserves existing preview,
idempotency and partial-failure recovery contracts. Built-ins remain immutable.

Context is presented as project background, constraints and decisions, with an
explicit explanation of what is included in a launch. It is not a transcript or
an implicit promise of provider memory.

### Task and agent results

Remove Design from the general Library and the unexplained global Gallery
shortcut. Show a Results/Gallery action on a task or producing agent only when
the server reports accessible outputs. A task scope filters individual screens,
including screens inside mixed-task design packages. Agent scope requires
recorded producer provenance; never infer ownership from a later task assignee
or a task revision actor.

Show the latest relevant output per producer while retaining access to versions
and visible stale/unavailable states. Image designs and running frontend previews
are different output kinds. A live preview requires a task/run owner, readiness,
origin and lifetime descriptor. Serve untrusted previews on a separate origin;
never forward Commons authentication or capabilities into them.

### Human collaboration and attachments

Provide a main project/task conversation and a conversation with a selected
agent. The operator can compose and send while work runs. Preserve drafts on
errors and concurrent updates. Delivery is explicit: recorded/queued, made
available to the run, acknowledged/read, answered. Do not derive a read receipt
from saving an event, process exit or prose claiming success.

Each message permits up to 10 images and up to 10 other files, at most 20
attachments total. Each file is at most **15,000,000 bytes (15 MB)**. Use streamed
uploads with an aggregate 300 MB message ceiling and bounded temporary retention.
Attachment-only messages are valid. The UI reports per-file validation and keeps
successful uploads when another file fails.

Store bytes in private service-owned storage outside the repository, immutable
ledger and public webroot. Canonical references contain an opaque identity,
digest, verified media type, size and safe display name; no operator path, raw
provider transcript or credentials. Enforce project/conversation/recipient scope
on upload binding, read and download. Validate size, image decoding/dimensions,
filenames and type; never execute attachments. Active document formats are not
rendered inline as trusted application content.

MCP/provider delivery must expose actual image content or a supported typed file
reader. Mentioning a filename in text is not image delivery. Advertise provider
attachment and active-run interaction capabilities only after behavioral tests.
Use durable queues and explicit checkpoints/wakeup policy where supported;
unavailable continuation stays visible instead of silently losing human input.

### Provider transport and execution

Use Codex, Claude and Grok through the service's broker. The operator authorizes
additional live runs without a total run-count cap for this program. Every
attempt still has finite time, concurrency and budget limits; 300 seconds is a
canary timeout, not a universal five-minute worker limit. Do not recursively
launch model councils without bounded tasks.

Grok instructions must leave argv and must not be persisted to a prompt file.
First implementation uses its documented `--prompt-file` option with the fixed
`/dev/stdin` pipe endpoint, exact gate validation and no alternate prompt flag.
Synthetic tests prove gate behavior; a real Grok run must prove CLI support.
Failure must not silently restore argv or write the prompt to disk.

## Alternatives and consequences

A generic global gallery was simpler but obscured provenance and mixed unrelated
results. A full provider transcript stream would suggest live visibility while
violating retention boundaries. We choose typed progress, bounded intentional
messages and scoped outputs. Native browser folder handles alone cannot supply
the server's trusted local project identity, so a host picker is optional and
manual paths remain supported.

The added metadata and thread attachment contract require schema/replay tests,
old-event compatibility, scoped DTO review and recovery checks. Output indexing
must avoid multiplying current snapshot projection costs. Security and actual
provider delivery take precedence over displaying a misleading green state.

## Verification and rollout

Implement on `codex/service-self-improvement`, preserving existing uncommitted
repairs. Use bounded task/path ownership; one writer owns the Work frontend.
Independent review in a shared checkout requires a write freeze and exact bound
artifacts. Provider exit, implementation completion, exact review, acceptance,
installation and release remain distinct.

Required evidence: focused regressions; full `make check`; populated two-project
browser journeys; upload boundary/isolation tests; all three providers actually
used with the changed source fingerprint; honest delivery states; reviewed wheel
installation. Linux six-profile L1 and release R2 remain open until their own
exact evidence exists. Historical successful canaries do not qualify new bytes.

See [implementation graph](../project-native-collaboration-implementation-plan.md).

Primary design constraints: [MDN folder picker](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker),
[OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

### Conversation audience and image-decoder implementation clarification

A new project conversation explicitly addresses all current and future project
agents (`*`); UI copy must say so. A task conversation uses its immutable typed
task address, which a worker can reach only through its bound task delegation.
An agent conversation addresses exactly that role identity. Historical engagement
threads retain their original fixed audiences. Introducing these semantics needs
the explicit thread payload v2 and ledger reader floor; no history rewrite is
permitted.

Image upload uses a format-restricted PNG/JPEG decoder after bounded signature,
dimension and byte validation. Full decoding precedes successful upload; merely
valid chunk headers are insufficient. Pillow is a locked core dependency because
both the UI and the provider MCP runtime must verify images. See the upstream
[Pillow 12.3.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html).
This decision does not establish actual media support in every provider profile.

### Integrated storage, read caching and provisional outputs

When ordinary operational state is inside the checkout, collaboration bytes and
preview descriptors resolve to an external per-workspace private user-state
location. An explicit external operational state root remains authoritative.
The resolver performs no migration or hidden copy of existing private content.

A per-UI-context read cache verifies all canonical workspace/event/manifest bytes
before reuse, includes workspace/config identity, and returns owned copies.
Changed safe canonical bytes require fresh replay before a new cache entry can
be published. Unsafe traversal, invalid content or a change during verification
refuses the read and clears cached data; no stale result is returned. Exceeding
the cache budget uses the ordinary uncached read path. Writes and explicit fresh
reads never use this cache.

Conversation history pages newest-first in batches of 50 with explicit older
history access and a visible 2,000-row client memory limit. A server refusal may
permit draft recovery only after proving that the expired draft is unbound and
no message exists; ambiguous commits retain the original immutable send identity.

The worker `commons_publish_design_image` records a bounded PNG/JPEG artifact
with exact child/task/delegation provenance. It appears as provisional
`artifact_image` Results, independently of formally composed Design Packages.
Source identity, digest, size and producer are checked; publishing is neither
task completion nor design acceptance. Existing Design Package rules remain.


### Provider media and native command boundaries

Image/file consumption, publication of an existing image, creation of a new
image and execution of native build commands are separate capabilities. A
terminal canary does not qualify all four. The [validation record](../audits/2026-09-08-project-native-collaboration-validation.md)
records actual per-provider outcomes at their exact source boundary, including
unsuccessful attempts. Claude native Bash still requires approval and Grok
terminal execution remains denied in the configured builder profiles; no optional
native-command capability change is part of this implementation.

Repository search must report bounded coverage and redaction. An empty result
from an incomplete or redacted search cannot establish that a feature is absent.
Generated-asset correspondence is verified through a scratch rebuild and exact
byte comparison, independently of whether a scoped reader can expose minified
content. Review corrections require fresh source-bound verification before
acceptance; they do not rewrite the earlier review outcome.

### Viewing results after work finishes

A provisional live preview survives only the producing child's direct, uncorrected
transition to succeeded, with the task and producer bindings unchanged. The original
expiry is not renewed. Readiness remains reported and reachability unknown; the
preview server is externally owned and must still run. Failure, cancellation,
timeout, changed bindings or expiry revoke navigation.

A generated artifact image from an earlier task revision remains explicitly stale.
It can be viewed as recorded history only after exact bytes, digest, image format,
classification and producer are verified again. This separate viewing permission
does not promote task readiness, design acceptance or launch eligibility. Formal
Design Package policy and latest-version selection remain unchanged.
