# Evidence — 2026-09-08

## Project workspace verification

The final evidence for that wave is the [final source manifest](project-workspaces-final-source-manifest.json),
[browser verification](project-workspaces-browser-verification.json) and
[test/wheel/review verification](project-workspaces-release-verification.json).
The [audit](../../audits/2026-09-08-project-workspaces-verification.md) records
verdicts and limits. The three PNGs contain synthetic Alpha/Beta project data.
The later [CI PID-fixture regression](project-workspaces-ci-pid-regression.json)
records the isolated test correction after PR #8; application and wheel bytes
are unchanged from the project-workspace evidence above.

Registry v1–v4, host v1–v2 and frontend v1–v2 are superseded review inputs. Registry
v5, host v3 and frontend v3/v4 identify approved component scopes. They are kept
as compact hash provenance for findings, not parallel current plans or raw logs.
Any `pending` text inside a frozen input describes its creation time; current
review verdicts live in the audit and canonical review.

## Self-improvement checkpoint

The [dogfood audit](../../audits/2026-09-08-service-self-improvement-dogfood.md)
binds [confirmed refresh](self-improvement-confirmed-refresh.json),
[initial provider canaries](self-improvement-provider-canaries.json) and the
[review-finalizer repair](self-improvement-review-finalizer.json). These retain
their original fingerprints, failures and observation times. They are historical
evidence, not the current qualification or permission state.

## Project-native collaboration

Use the [current validation record](../../audits/2026-09-08-project-native-collaboration-validation.md)
for results and remaining gates. [Provider outcomes](project-native-provider-canaries.json)
contain separate initial and final-installed checkpoints; only the latter binds
the six-profile `f35c5fe0…` macOS qualification. The
[review source manifest](project-native-review-source-manifest.json) identifies
its exact frozen input; a manifest alone is not an independent review verdict.
Later source manifests must explicitly supersede earlier review inputs while
preserving their registered evidence.

No provider transcript, operator configuration, authentication code, state-root
contents or private source snapshot is included. These records do not close L1.

- [Final project-native checkpoint](project-native-final-validation.json): 2026-09-09 source-bound full gate, installation, six providers profiles, and scoped Results browser outcomes.
