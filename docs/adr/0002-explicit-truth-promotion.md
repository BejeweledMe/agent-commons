# ADR 0002: Explicit project-truth promotion

- Status: accepted
- Date: 2026-07-14

## Context

Multiple agents can repeat the same unsupported claim, review their own work,
or converge on a convenient answer. A shared chat transcript therefore cannot
serve as project truth. At the same time, preserving every draft as authoritative
would overwhelm future sessions and hide uncertainty.

## Decision

The protocol separates policy, working discussion, evidence, and effective
truth. Threads and task completion remain provisional. Reviews are scoped expert
judgments; verifications are reproducible factual records. Findings enter
effective truth only through an explicit promotion with revision-bound evidence.
Decisions enter it through an explicit acceptance with an expected revision and
rationale; evidence and dissent are revision-bound and preserved when supplied.
Any additional decision evidence, review, or authority gate belongs to the local
operator workflow in MVP-0, which records but does not authenticate the actor.

Agent count and model-family diversity never substitute for evidence. Conflicting
accepted decisions in one scope fail closed. A changed artifact revision makes
prior review results stale.

## Consequences

- New sessions can distinguish what was suggested, observed, reviewed, and
  actually adopted.
- Independent review is enforceable at the session boundary.
- Small projects can use only the lightweight orientation, coordination,
  discussion, review, and handoff workflows needed for the change. Named policy
  presets remain roadmap work.
- Local identities remain coordination metadata, not cryptographic proof; remote
  authorization is deferred to a service deployment.
