# Agent Commons

Agent Commons is a local web application for organizing work done by a team of
AI agents. It helps you turn an objective into clear tasks, give work to roles,
follow runs, ask for an independent review, and record the work you accept.

It is for people who understand what an AI agent is but do not need to know how
the product is implemented. You remain responsible for product decisions,
provider accounts, and accepting completed work.

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

The UI can initialize an empty Git repository and look for a locally installed
Claude, Codex, or Grok Build provider. Starting a run uses your own provider
CLI and its account or subscription; it is not a simulation.

## The working model

```text
Role → Task → Run → independent Review → human Acceptance
```

- A **role** is a continuing responsibility, such as Backend developer or
  independent reviewer.
- A **task** describes the result and how to judge it.
- A **run** is one bounded attempt by a role to work on a task.
- A completed run is not automatically accepted. Send the work for an
  independent review, then record acceptance only when you are satisfied.

The new **Work** screen helps with setup and starting a run. The full (legacy)
panel currently remains the place to monitor runs, request review, accept work,
or return it for changes. This handoff is deliberate and visible in the UI.

## Current limits

Agent Commons is alpha software for macOS and Linux with CPython 3.11–3.14.
Windows is not supported. The optional provider runtime is local and
experimental: a provider can fail, time out, or require your decision. A run
that ends in `needs_operator` has not silently succeeded; inspect it in the
panel before creating replacement work.

Agent Commons records coordination. It does not authorize committing, pushing,
deploying, publishing, contacting people, or destructive actions.

## Technical and operator documentation

The material below is for developers and operators who configure or extend the
product, rather than for ordinary UI use:

- [Technical Quickstart](docs/QUICKSTART.md)
- [Technical workflow examples](docs/USER_WORKFLOWS.md)
- [Technical troubleshooting](docs/TROUBLESHOOTING.md)
- [Architecture](docs/ARCHITECTURE.md), [threat model](docs/THREAT_MODEL.md),
  [vision](docs/VISION.md), and [roadmap](docs/ROADMAP.md)
- [Documentation map and source-of-truth rules](docs/README.md)

## Configure a provider before running work

The UI can use a locally installed Claude, Codex, or Grok Build provider.
Configure and authenticate that provider under your own account before starting a real run;
Agent Commons does not supply credentials or turn provider output into
acceptance. See the [technical quickstart](docs/QUICKSTART.md) and
[troubleshooting guide](docs/TROUBLESHOOTING.md).

## Experimental local broker (manual opt-in)

The optional broker is alpha software. Run its static preflight before any
provider work and use the explicit canary only when you accept the provider
usage cost. Its current contract, caps, recovery rules, and release gates are
in [Broker operations](docs/BROKER_OPERATIONS.md); it is not an autonomous
scheduler.
