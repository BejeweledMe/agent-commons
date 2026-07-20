# User workflows

The examples describe the shared operating flow rather than client-specific
prompt syntax. Each participant begins by reading the generated onboarding
contract, registering a distinct session, and running a bounded orientation.
For a copyable two-terminal walkthrough, see
[Build Snake with Codex and Claude Code](../README.md#worked-example-build-snake-with-codex-and-claude-code).

## 1. Build a web application

The user records the product objective, non-negotiable constraints, and release
acceptance criteria. An architecture session proposes the service boundary and
opens a decision request. Other sessions critique the proposal before an
authorized decision is recorded.

The work is decomposed into API, interface, persistence, and integration-test
tasks with explicit dependencies. Implementers take different tasks and claim
their component or path scopes. A design-oriented participant registers an
interface artifact and asks for usability review without blocking unrelated API
work.

When implementation is ready, authors submit exact revisions for review.
Reviewers record judgments and reproducible checks separately. A changed
revision makes earlier approval stale. Accepted tasks and concrete release work
appear in the next orientation; reported risks and rejected approaches remain
available through the bounded list commands and generated views. Pausing
sessions leave targeted handoffs and release inactive claims.

Evidence flags in the CLI still take concise `kind:id` values. Agent Commons
binds each one to the current effective revision before writing, so a later
artifact revision cannot silently leave an earlier finding or decision in the
effective-truth view.

Outcome: after orientation and the relevant bounded views, a new window sees
current architecture, completed and blocked work, pending reviews, and rejected
approaches without replaying earlier chats.

## 2. Prototype a product design

A designer session creates a task with measurable usability and accessibility
criteria, then registers several immutable design revisions. It opens one
proposal thread linked to the variants rather than separate unconnected chats.

An accessibility reviewer identifies contrast and navigation risks. A product
reviewer challenges the information hierarchy and supplies a competing
proposal. The designer responds with a new revision; old review results remain
visible but stale for that revision.

The authorized decision records the selected variant, evidence, alternatives,
and reasons the other variants were rejected. Those negative conclusions remain
available to later orientation, preventing another session from unknowingly
recreating a discarded direction.

Outcome: critique and dissent are preserved, while only the accepted decision
and verified findings enter effective project truth.

## 3. Create a chatbot service

The project starts with separate conversation, external-tool, user-interface,
privacy, and security workstreams. One session defines the service contracts,
another implements the tool boundary, and another creates adversarial and
integration checks. Each participant claims only the relevant task and path
scope.

A security review reports an instruction-injection weakness and returns
`changes_requested`. The implementation task cannot be accepted under the
MVP protocol invariant. After a new artifact revision is registered, the reviewer
repeats the scoped verification and records new evidence.

The final decision documents the accepted safeguards and residual limitations.
A handoff to an operations session includes typed artifact references,
revision-bound evidence where exact content matters, open monitoring work, and
prohibited deployment assumptions. It does not grant permission to deploy.

Outcome: implementation, adversarial review, governance, and operational
handoff remain connected without treating discussion or model agreement as
proof.
