<!-- agent-commons:managed:start -->
## Agent Commons

This project uses the shared Agent Commons workspace. Before starting or
resuming work, read `.agent-commons/ONBOARDING.md` and follow its session,
orientation, coordination, review, truth-promotion, and handoff contract.

Use the installed `commons-start` skill at the beginning of each window. The
paired `commons-coordinate`, `commons-share`, `commons-review`, `commons-record`,
and `commons-handoff` skills provide the supported workflows.

That onboarding document is the single canonical guidance contract for every
agent client. This managed block adds no client-specific workflow policy.
<!-- agent-commons:managed:end -->

## Engineering standards

Read `AGENTS.md` — it carries the engineering standards this repository holds
every agent to: the `make check` green contract, version files (`uv.lock`,
`.python-version`, `.node-version`), push-until-CI-green discipline, commit
style, the single-writer rule for the UI asset, and the tool caveats that have
burned sessions before. The frontend's own laws live in
`docs/FRONTEND_CONTRACT.md`. The standards are written once, there — this file
deliberately does not duplicate them.
