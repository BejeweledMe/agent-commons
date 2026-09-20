# Agent Commons

Agent Commons is a local web application for running a product through a team
of AI agents. You describe the outcome, hire agents from a shared library, watch
the work on a board and in a task tracker, talk to agents, open their results
and record the decision to accept finished work.

It is for people who understand what an AI agent is but do not need to know how
the product is implemented. You remain responsible for product decisions,
provider accounts and accepting completed work. What the product is, who it is
for and what its words mean is written once in [docs/PRODUCT.md](docs/PRODUCT.md).

## Start here

Choose a guide in your language:

- [English user guide](docs/user/en/README.md)
- [Руководство пользователя на русском](docs/user/ru/README.md)

The guides explain the interface, the normal workflow, statuses, review and
acceptance, and the most common setup problems. They describe what works in the
current alpha release; planned features are not presented as available.

## Open the local app

From this source checkout:

```bash
make sync
uv run agent-commons ui
```

The command serves the app on your computer only and prints a local address. It
may also open your default browser. The address contains a short-lived,
single-use sign-in code: open it only once. If you need to choose the browser
yourself, use `uv run agent-commons ui --no-browser`, then open the newly
printed address once in that browser.

A project opens on its **Board**; **Tasks**, **Agents**, **Library** and
**Settings** sit beside it. The UI can initialize an empty Git repository and
look for a locally installed Claude, Codex or Grok Build provider. Starting a
run uses your own provider CLI and its account or subscription; it is not a
simulation.

## The working model

```text
Project → Agents → Tasks → Run → independent Review → human Acceptance
```

- An **agent** is a hired instance of a specialization from the Library.
- A **task** describes the result and how to judge it.
- A **run** is one bounded attempt by an agent to work on a task.
- A completed run is not automatically accepted. Send the work for an
  independent review, then record acceptance only when you are satisfied.

Applying a blueprint creates agents and tasks and starts nothing. The Library
ships built-in skills, specializations and blueprints; their current contents
are described in the [library guide](docs/user/en/service-library.md) and
[руководстве по библиотеке](docs/user/ru/service-library.md).

## Current limits

Agent Commons is alpha software for macOS and Linux with CPython 3.11–3.14.
Windows is not supported. The optional provider runtime is local and
experimental: a provider can fail, time out, or require your decision. A run
that ends in `needs_operator` has not silently succeeded; inspect it before
creating replacement work. Live qualification on Linux (L1) and release evidence
(R2) remain open gates.

Agent Commons records coordination. It does not authorize committing, pushing,
deploying, publishing, contacting people, or destructive actions.

## Configure a provider before running work

The UI can use a locally installed Claude, Codex or Grok Build provider.
Configure and authenticate that provider under your own account before starting
a real run; Agent Commons does not supply credentials or turn provider output
into acceptance. See the [technical quickstart](docs/QUICKSTART.md) and
[troubleshooting guide](docs/TROUBLESHOOTING.md).

## Experimental local broker (manual opt-in)

The optional broker is alpha software. Run its static preflight before any
provider work and use the explicit canary only when you accept the provider
usage cost. Its current contract, caps, recovery rules and release gates are in
[Broker operations](docs/BROKER_OPERATIONS.md); it is not an autonomous
scheduler.

## Technical and operator documentation

For developers and operators who configure or extend the product:

- [Documentation map and source-of-truth rules](docs/README.md)
- [Product, positioning and glossary](docs/PRODUCT.md) ·
  [Roadmap](docs/ROADMAP.md) · [Active plans](docs/plans/)
- [Architecture](docs/ARCHITECTURE.md) · [Protocol](docs/PROTOCOL.md) ·
  [Threat model](docs/THREAT_MODEL.md) ·
  [Frontend contract](docs/FRONTEND_CONTRACT.md)
- [Technical quickstart](docs/QUICKSTART.md) ·
  [Technical workflows](docs/USER_WORKFLOWS.md) ·
  [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Contributing](CONTRIBUTING.md) — the green contract, versions, commits and
  documentation rules · [Changelog](CHANGELOG.md)
