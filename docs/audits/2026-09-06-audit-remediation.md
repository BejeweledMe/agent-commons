# Audit remediation — 2026-09-06

This is the implementation follow-up to the [independent audit](2026-09-06-total-plan-work-adr-prd-review.md). The audit remains a historical assessment of its exact baseline. The user subsequently authorized three parallel GPT-6 Astra implementation agents; that authorization permits code and regression changes, but does not authorize commits, pushes or live provider canaries.

Base `HEAD` and local `origin/main`: `329d2f3920753853ce265536d9d3ceb4e35f1f91`. The starting tree for remediation already contained the audit's eleven documentation changes. Uncommitted implementation bytes are identified by file SHA256 evidence, not by claiming that the base commit contains these fixes.

**Later checkpoint — 2026-09-07:** the [task-first pivot verification](2026-09-07-task-first-pivot-verification.md)
records the subsequent Astra/Opus UX work and current integrated source hashes.
Its Work changes supersede shared UI hashes below; the runtime remediation was
preserved. The historical wave results remain evidence for their exact bytes.

## Evidence and independent review

The immutable [wave 1 hash inventory](../evidence/2026-09-06/audit-remediation-wave1.json) is registered as `artifact.6YJ9ED2GV0TYV7T7NRA0FBNAFM`, revision `evt.01M1VY4JVSNKNEADBR2CPGVBEA`. Its file-content SHA256 is `3927b38c7ba64e14e02ae1985441fca23a24d3a5b0433ccc421f7fef4de759a3`. The artifact manifest's own hash is a different identifier. This inventory is retained unchanged when later source bytes supersede it.

| Wave 1 scope | Independent judgment | Reproducible checks | Limit |
| --- | --- | --- | --- |
| F01: evidence retention and review recovery | Approved by the privacy reviewer; three hashes matched | 25 independent tests passed | Scoped judgment; canonical task approval and acceptance are separate |
| F02: Context Pack source validation | **Changes requested** by the workflow reviewer; five hashes matched | 16 independent cases passed, but a synthetic probe-time restriction still reached invocation | A green test matrix did not cover the post-probe authorization gap |
| F03: truncated stderr | Approved by the context reviewer; four hashes matched | Included in 72 independent F03/F12 tests | Fixed metadata remains; historical truncated tail content is suppressed without rewriting storage |
| F12: Node major guard | Approved by the context reviewer; three hashes matched | Included in 72 independent tests; both frontend targets refused Node 23 before npm | This working-tree gate does not represent a new CI run |

Wave 1 full gate: `git diff --check` passed; `npm exec --yes --package=node@24 -- make check` passed using Node 24.20.0, locked Ruff and Python 3.14.5. Results: 1888 pytest tests passed, 13 skipped, two existing warnings; Work 29/29 and Gallery 22/22 passed. Pytest elapsed 453.09 seconds. Twelve skips concern the intentionally withheld automatic grant level; one is the local CI-only environment check. No frontend test was skipped.

The additional F02 reproduction changed a synthetic source artifact to restricted inside a fake initialization probe. The fake invocation received the previously compiled baseline, while normal compile then refused it as stale. It demonstrated an authorization freshness gap, without a live provider or real source-content disclosure. This required the additional fix and independent re-review recorded in the final boundary below.

## Verified final implementation boundary

The [wave 2 inventory](../evidence/2026-09-06/audit-remediation-wave2.json) is registered as `artifact.7SFJJF5MHW6Z2M3PJW1GF8305D` at `evt.01M1VZ63SWA2RH6ECNNQ5H7QJA`; content SHA256 `1a979904863e5e09ac611c7c1290bfb30fb3df7155c3ed69efe20642d59808b9`. It records the final source/test/assets boundary and removal of the replaced Work JavaScript bundle. Shared files occur in more than one explicitly scoped list.

Final `make check` passed under Node 24.20.0: **1945 pytest passed, 13 skipped, two existing warnings; Work 38/38, Gallery 22/22; Ruff check and format passed (463 files)**. Pytest elapsed 715.02 seconds. The [verification record](../evidence/2026-09-06/audit-remediation-verification.json) retains bounded outcomes, not command logs, browser snapshots, operator configuration or test ledger contents. It is registered as `artifact.6PHEQD6N2PWKNVPCRJM0Y1DEXN` at `evt.01M1W099NSB05EQCPF8GDHK6EB`, content SHA256 `4fce2bdfaa8945b013b190b87c902e7951f534f3622178eeb80591542506e907`.

| Finding | Final implemented behavior | Independent judgment and evidence |
| --- | --- | --- |
| F01 | UI review walk preserves exact artifact bindings and authorship; stale evidence refuses; a lost response resumes only the original bounded transition chain | Privacy reviewer approved; three wave 1 hashes unchanged; 25 independent tests |
| F02 | Common source checks on first bind/cache/retry, after probes, before build and after build; refusal prevents runner execution and closes a newly created child | Workflow reviewer approved; original independent probe-drift reproduction now has zero invocations/attempts/children |
| F03 | Any incomplete stderr becomes a fixed omission marker; legacy Runs suppresses historical truncated tails without rewriting disk | Workflow reviewer approved final shared-file bytes; typed diagnostics retained |
| F04 | Shared Gallery source inspection gates selection, binding, retry and launch choices; stale provenance refuses | Workflow reviewer approved; post-build source restriction refused before runner and the child was closed |
| F06 | Work retains serialized body, original CAS and key for create/review/accept/reopen recovery; changed intent under the same key refuses | Privacy reviewer approved; browser create response-loss replay produced two identical POST bodies and exactly one canonical task |
| F07 | Apply refreshes available presets; native labelled picker hires from the exact preset, preserves skills, locks inherited profile/context and keeps typed drafts across locale changes | Privacy reviewer approved; real browser Apply → Hire → fake Launch succeeded, actual stored skills matched, no task was accepted |
| F08, size only | Grok has a conservative UTF-8 instruction bound including skill/context composition and NUL allowance before child/reservation/runner | Workflow reviewer approved; argv privacy remains open |
| F12 | `make check` and standalone frontend dependency targets refuse a PATH Node major differing from `.node-version` | Context reviewer approved; negative real-make probes and file-driven version tests passed |

The workflow reviewer independently ran 154 final runtime/service/UI tests across F02/F03/F04/F08. The privacy reviewer ran 17 Work checks, including an exact temporary rebuild of the packaged asset. Browser checks used a temporary workspace and exclusively fake provider execution; keyboard typeahead selected a preset, EN/RU retained the draft, and response-loss recovery preserved the original request. This does not certify screen readers, all native keyboard behaviors or browser reload persistence. Review/accept/reopen response loss is covered by Node/API regressions; the browser fault injection covered task creation.

No application code changed after the wave 2 freeze. `implemented`, `tested`, scoped review approval, canonical task review, task acceptance and release remain separate. Task acceptance is deliberately withheld under the user's original boundary.

## Files and working-tree status

The wave 2 inventory names 37 unique implementation/test/asset files and one
removed prior bundle. The retained audit has eleven documentation files; this
follow-up adds the remediation report and three safe JSON evidence records,
updates the frontend contract and ADR 0004 diagnostics, and adds precise links
to the four primary planning documents. The primary-plan files overlap the
earlier audit's list. No lockfile or tool-version file changed.

The original audit began clean. This implementation follow-up began dirty with
the eleven audit documentation changes and ends deliberately dirty with the
reviewable code/test/docs diff. `HEAD` and local `origin/main` remain
`329d2f3920753853ce265536d9d3ceb4e35f1f91`. No commit or push was made.
Final source hashes, JSON parsing, relative documentation links and
`git diff --check` were verified after the full gate. One-off browser snapshots,
the synthetic server and its disposable workspace/configuration were removed;
only bounded metadata outcomes are retained.

The G6 default follows the audit's explicit fail-closed instruction: stale producer/artifact provenance should refuse launch. An optional historical-provenance mode remains a product decision. A Grok argument-size guard can prevent obviously oversized launch inputs; it does not resolve the privacy implications of passing instructions in argv or prove an operating system's complete argument/environment budget.

Source checks use fresh snapshots around synchronous launch hooks; they do not atomically exclude an external writer after the final snapshot. A stronger revocation guarantee requires an explicit locking/transaction contract. Historical Context Pack revisions remain permitted when their referenced sources are current and safe.

## Updated checklist and plan deltas

- [x] F01/F02/F03: implement the three P1 fixes, add negative regressions, pass the full green contract and independent scoped review.
- [x] F04: apply the already requested fail-closed default and align Gallery/launch source validation. Optional historical launch mode remains undecided.
- [x] F06/F07: implement stable Work recovery and preset hire; check the real browser journey and synthetic create response-loss recovery.
- [x] F12: enforce the repository's Node major before frontend checks.
- [x] F08 partial: bound oversized composed Grok input without persisting prompt files.
- [ ] Before release: resolve F05/L1 and R2, the F08 transport contract, and the intended provider/platform support matrix. These are not closed by the hermetic gate.
- [ ] Soon: perform case-by-case F10 coordination cleanup and decide F09 evidence supersession/retention. Do not manufacture acceptance for obsolete tasks.
- [ ] Growth: automate evidence freshness/export and browser failure-injection coverage; current metadata inventories are a manual evidence index.
- [x] UX follow-up, implemented in the September 7 pivot: distinguish builder/reviewer in the manual profile selector's fallback labels. The synthetic browser displayed duplicate `claude`/`codex` labels because `profileLabel` falls back to provider name (`frontend/work/src/api.ts`). Preset choices already include exact profile IDs; this was a P3 follow-up, now covered by the later pivot's accessible-name regressions.

Primary plans receive a dated link to these results. Their historical baselines and earlier audit findings are retained; an uncommitted repair must not be described as code already shipped in the base SHA. ADR 0004's diagnostic paragraph and the frontend contract now describe complete bounded stderr and omission of incomplete tails; the unresolved stdin/argv transport clause is not silently relaxed.

## Canonical task and review checkpoint

A read-only call to the project's `select_qualifying_review` predicate found a current independent approval for every task below. Each exact target matches the submitted task revision, `stale=false`, and the reviewer is outside the task's work-author set. All seven tasks remain **review**, with **no acceptance**.

| Scope | Task and exact submitted revision | Approved review and exact review revision |
| --- | --- | --- |
| F01 | `task.0X4S1F5FVG20CBQG89E6PB0SCV` / `evt.01M1VYVS4K894W4EC8VKB2VWV1` | `review.15W8MZXRX79PWCGHCP5QAKTFP0` / `evt.01M1VZE8ETN821EP2CT2F6DZP9` |
| F02 | `task.4CX3E9E8NK0GRVSXXMDQX78A5B` / `evt.01M1W02NWTWFY8S0PJJE8G56R1` | `review.31SH5EZG18PPHDTGP55F0NZ1RQ` / `evt.01M1W0D9F88QVXJQ22T0ASWR54` |
| F03 | `task.7VRYXYQPK9ABVPFCECA038XQW3` / `evt.01M1W05A80PHJ46YDEV7JPCHY4` | `review.453DACNJ71QVFJCSYSPH22Q0ZW` / `evt.01M1W0KXQNDNZ8NCXRK9ZXAN92` |
| F04 | `task.4E0XNEC38K3WSWNVDEWMZHJQ78` / `evt.01M1W0FY5DV0XDCAYQBZQCMCJW` | `review.00EE4J9DMZ758WP6F9TMJ3S0GR` / `evt.01M1W0R1DWDG39J49MADS2RFCC` |
| F06/F07 | `task.5NG0QCXKABA2NB3G6K1YFDMFHP` / `evt.01M1W03ZWWAS7GBC22WRK0V9EG` | `review.1ZRE53K4KCEPGJX4V46KVKGNZE` / `evt.01M1W0H8MQ4WV5KDQS44MEWTC8` |
| F08 size only | `task.0XRP47NK30X1DYM535K63582GV` / `evt.01M1W0JK4JJDNDPE9MVEEHPD8G` | `review.6XR8RQG6H03KT91Q5H74PAF47Z` / `evt.01M1W0VWWJ37XT9FKGG8S92C21` |
| F12 | `task.3WN8G6KAD0JVH2J8VV5HGZKVTG` / `evt.01M1VYMSMEJRCTAVFAA5ETEZ0P` | `review.7FHRD7BY4N2HQBHWCTK4JZHMMK` / `evt.01M1VZCYVKX7XYV9TJ5X1QD9QR` |

Final doctor after all canonical writes returned `ok=true`, **2957 events, 329 manifests, zero integrity issues and 136 existing warnings**. The warnings remain the audit's separate coordination/history backlog; they were not silently resolved by these fixes. Canonical evidence and handoffs were written only through the supported CLI. Read-only orientation completed and confirmed the operator handoff is visible.

The central operator handoff is `handoff.2R1C875XBZ2JMXDVHJY70K6XQA` at
`evt.01M1W12MWXDXQ2KTRQRJ1KTYKH`. The three scoped peer handoffs are
`handoff.0XN4T2NMFQPSY5VT05TMCBC4R0`, `handoff.23N49H2VJEBBNWJCZ0YDB5X2Z4`
and `handoff.3CSAN6WCJ389Y5CGJJ16D4CYFE`. All three implementation/reviewer
sessions ended through the CLI and released their claims. The integrator's
final closure uses the same supported claim-release and session-end workflow;
private session/claim nonce files are removed at closure. No canonical state
file was edited manually.

## Remaining external and governance gates

- **F05 / L1 remains not proven; R2 remains open.** The intended Linux release candidate needs supported receipt-scope bootstrap, green doctor, separately authorized provider qualification, and exact independent review. Historical successful canaries do not close later Claude/Grok failures.
- **F08 transport privacy remains open.** The runtime/security owner must resolve the stdin/argv contract and any prompt-file lifecycle proposal. No prompt files or live provider work are authorized in this remediation.
- **F09 retention remains open.** The earlier documentation redaction does not erase old published or immutable bytes. The data owner must decide supersession/export and retention treatment.
- **F10 stale coordination requires case-by-case disposition.** Cancelled prerequisites, stale reviews and handoffs must not be mass-accepted or silently rewritten. The coordinator needs current evidence and an explicit replacement/disposition for obsolete work.
- **Growth items remain distinct tasks:** a public evidence exporter/schema, browser journey coverage, evidence freshness index, operator status dashboard and performance remeasurement. Completion of a bounded bug fix does not complete that product programme.

No live qualification, release, commit or push is implied by this document.
