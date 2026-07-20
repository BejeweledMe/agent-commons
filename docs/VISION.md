# Vision

## The idea in one sentence

Agent Commons gives otherwise isolated AI-agent windows a shared, durable, and
inspectable project memory so that different models can work as one team without
mistaking conversation or model agreement for verified project truth.

## Why it should exist

Work with coding agents is usually split across tools, model families, and chat
windows. Each window develops useful context, but the context is local and
temporary. A second agent may not know what the first one tried, why an approach
was rejected, which files are already being changed, whether a result was
independently reviewed, or what remains blocked. This leads to duplicated work,
contradictory decisions, stale assumptions, and repeated explanations from the
user.

A shared Markdown chat alone does not solve this. It quickly becomes a noisy
transcript, has no reliable current state, and makes a confident suggestion look
too much like an accepted conclusion. Traditional issue trackers and Git remain
valuable, but neither provides the complete collaboration contract needed by
short-lived, heterogeneous agent sessions.

Agent Commons is intended to fill that gap. It is a project-local manager-space
that combines:

- a common room for questions, proposals, critiques, and disagreement;
- a work board for objectives, tasks, ownership, dependencies, and temporary
  claims;
- a review room for revision-bound judgments and reproducible verification;
- durable project memory for findings, decisions, rejected approaches, and
  handoffs.

## What we want agents to be able to do

An agent entering a project should be able to discover, without replaying old
chats:

- what the project is trying to achieve and which constraints matter;
- what other agents are doing now and where work may overlap;
- which ideas are proposals, which conclusions have evidence, and which
  decisions were explicitly adopted;
- what changed since an earlier review and which judgments are now stale;
- which risks, disagreements, blockers, and failed approaches should not be
  rediscovered from scratch;
- what concrete action the previous session expects next.

The normal collaboration loop should be simple:

```text
orient -> coordinate -> work and discuss -> review or verify ->
record durable outcomes -> hand off
```

Codex, Claude Code, and future clients should follow the same protocol and see
the same state. Their provider-specific instructions may differ in presentation,
but not in the meaning of a task, review, decision, or handoff.

## What success looks like

Agent Commons succeeds when:

1. A fresh agent window can build an accurate, bounded picture of the project in
   minutes rather than asking the user to reconstruct prior conversations.
2. Parallel agents can divide work and detect overlapping ownership before they
   duplicate or overwrite one another.
3. Useful disagreement is preserved, while discussion and model consensus never
   become project truth implicitly.
4. Reviews, verification, findings, and decisions remain connected to the exact
   revisions they evaluated.
5. Important outcomes and negative results survive context resets without
   storing private reasoning, complete transcripts, or routine logs.
6. The same core works for a website, application, design prototype, chatbot,
   infrastructure change, or model integration without changing its semantics.
7. A small local project can adopt it without operating Kafka, Redis, or a
   permanent service.

## Principles

- **One shared contract.** Model families and clients use the same entities and
  lifecycle rules.
- **Explicit truth promotion.** A message, task completion, or majority of
  models is not proof. Evidence, review, and operator decisions remain distinct.
- **Key points, not exhaustive telemetry.** Preserve information another session
  will need; omit private reasoning, chat transcripts, and raw training or build
  logs.
- **Immutable history, rebuildable views.** Durable records are append-only;
  indexes, boards, briefs, and graphs can be reconstructed.
- **Revision-aware evidence.** A later artifact revision must not silently inherit
  an earlier approval.
- **Lightweight by default.** Coordination should reduce work, not create ceremony
  for its own sake. Governance grows with risk and impact.
- **Human authority remains external.** The system helps agents coordinate and
  makes state inspectable; it does not grant permission to deploy, publish,
  contact people, or destroy data.
- **Complement existing tools.** Agent Commons connects context around Git,
  artifacts, and project workflows rather than replacing them.

## What it is not

Agent Commons is not intended to be:

- an autonomous model launcher or task scheduler;
- a warehouse of hidden reasoning, complete chats, or every command output;
- a voting system where more models make a claim true;
- a replacement for Git, CI, an issue tracker, or user approval;
- an implicit authorization layer for external side effects;
- a distributed multi-host platform in its first local-filesystem release.

## Long-term direction

The long-term goal is a portable collaboration layer that can be installed in an
arbitrary project and understood by any compatible agent. A local file-based
workspace is the first trustworthy boundary. Optional notifications, an MCP or
daemon interface, authenticated remote collaboration, richer graph views, and
domain-specific extensions can be added later without changing the core idea:
many agents may contribute, but the project should retain one inspectable memory
and one explicit account of what is current, provisional, verified, disputed,
and next.
