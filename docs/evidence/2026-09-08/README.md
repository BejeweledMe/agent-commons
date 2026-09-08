# Project workspace evidence

Current evidence is the [final source manifest](project-workspaces-final-source-manifest.json),
[browser verification](project-workspaces-browser-verification.json) and
[test/wheel/review verification](project-workspaces-release-verification.json).
The [audit](../../audits/2026-09-08-project-workspaces-verification.md) records
verdicts and limits. The three PNGs contain synthetic Alpha/Beta project data.

Registry v1–v4, host v1–v2 and frontend v1–v2 are superseded review inputs. Registry
v5, host v3 and frontend v3/v4 identify approved component scopes. They are kept
as compact hash provenance for findings, not parallel current plans or raw logs.
Any `pending` text inside a frozen input describes its creation time; current
review verdicts live in the audit and canonical review.

No provider transcript, operator configuration, authentication code, state-root
contents or private source snapshot is included. This evidence does not close L1.
