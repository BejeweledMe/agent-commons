# Independent frontend re-review, v2

Verdict: **approved within the exact source-review scope**. Both previous P2 findings are resolved; no new material findings in the reviewed delta. This is not task acceptance or confirmation of the still-running final release gate.

Target: `task.3NX9WHMPVZ279J8V827QQBNR5H` at `evt.01M4FYT73XK0J0KF2P89GA1ABE`.
Requested review: `review.2ZTX4G1192YQK1YMWT5FZKESMR` at `evt.01M4FYTTZETD6P77XEMXRYX6A7`.
Evidence: `artifact.0YEXDHTZNHRVC2RE38NPXBN7V4` at `evt.01M4FYQ8CCHQDAK4SCN8GEMSA2`.
Source manifest SHA-256: `84d9a588d1e42fe25477e896ed2be9d433b964ba982649efae4a86cba54559f5`.
Independent reviewer session: `session.abe2bb248ac947cb9630dfb263fd41d0`.

## Findings closure

1. **Unrelated draft loss — resolved.** Actual `agentSettings.ts` now acknowledges only fields included in the confirmed settings mutation whose current draft still equals the submitted value. The operation is rebased once. Link mutations acknowledge no settings fields. Method bindings retain their own content baseline and preserve unrelated general drafts. Confirmed refresh retries issue reads rather than replaying the mutation. The saved-with-unsaved-drafts message accurately distinguishes operation success from remaining edits.

2. **Project-switch state loss — resolved.** Actual `agentBoardState.ts` stores project-specific viewport, agent editor registry and pending canvas connection/key. `WorkApp` owns this registry outside the project-keyed providers; `ProjectBoard` receives the matching session, restores the viewport without an automatic Fit, and updates the same session as the user moves. A/B/A keeps projects separate and retains the original uncertain request body and idempotency identity.

Reviewed the full v1-to-v2 delta: editor state transitions, board session ownership/integration, JSX, dropdown CSS, EN/RU feedback, regression tests and asset index. All backend Python remains byte-identical to the independently approved v1 boundary. Earlier accepted October 9 implementation remains included in the manifest, not newly authored by this reviewer.

## Independent validation

- All **141 manifest entries** verified against actual checkout and frozen-source-v2, including deleted assets; final recheck performed before canonical completion.
- Ran `node --test frontend/work/tests/agent-settings.test.mjs`: **13 passed**.
- Ran two extra checks against compiled production modules: partial method-content refresh failure with subsequent edits/retry, and confirmed settings mutation with failed GET followed by newer draft/retry. Drafts remained; each scenario issued exactly one POST.
- Read root browser evidence `project-restoration.json` and `cross-tab-draft.json`. Reported A/B/A transform is identical and unsaved agent name survives both project switching and a confirmed connection write. Browser measurements were not independently repeated by this reviewer.
- Existing exact-source real Codex/Astra canary remains successful with matching result refs and closed child; Python fingerprint `285f9c773f9e650057a9ba244e1bc9a4533f43bfff6647a81fd576a683a9d109` is unchanged. Current HTML points to the v2 assets covered by the checked manifest.

## Limits

Final frozen-source `make check` in `oct09-codex-style-make-check-final4.log` was still running during review (last observed about 44%). The earlier 3034-Python green result is baseline evidence and is not substituted for the current gate. Owner acceptance remains conditional on that final full gate succeeding. No physical trackpad, Linux, light-theme browser, broad performance or general user acceptance is implied. No source edits, provider launches, whole-suite reruns or task acceptance were performed.
