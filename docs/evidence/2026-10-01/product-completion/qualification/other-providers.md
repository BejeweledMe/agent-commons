# Final existing-provider qualification

The four pre-existing Claude/Grok profiles keep their original model setting
(null, provider default), credentials and billing configuration. These checks
therefore do not re-label them as the council’s explicitly chosen models.
All use final source `bcb3d978…` and the matching installed MCP.

| Profile | Result | Provider time | Terminal calls/completions/rejections |
| --- | --- | --- | --- |
| Claude builder, initial 180 s | timed out, child closed | 180.66 s | 1/0/0 |
| Claude builder, diagnostic 300 s budget | succeeded | 47.38 s | 1/1/0 |
| Claude independent reviewer | succeeded | 29.76 s | 1/1/0 |
| Grok builder | succeeded | 90.39 s | 1/1/0 |
| Grok independent reviewer | succeeded | 73.53 s | 1/1/0 |

The Claude diagnostic changed the time budget and recorded only tool names,
result-error booleans and presence of explicit context flags. Both Bash attempts
contained repo/state/session/read-only flags but returned errors. Their messages
were not retained, so no cause is asserted. Scoped MCP orientation, repository
read and terminal success then completed. This qualifies the configured small
broker workflow, not unrestricted native-shell onboarding. The first timeout’s
cause remains unresolved; increasing the limit alone is not established as its
cause. No raw provider output, private reasoning or credentials are preserved.

[Claude initial](final-canary-claude-builder.json),
[Claude diagnostic](final-canary-claude-builder-300.json),
[Claude reviewer](final-canary-claude-independent-reviewer.json),
[Grok builder](final-canary-grok-builder.json),
[Grok reviewer](final-canary-grok-independent-reviewer.json).
