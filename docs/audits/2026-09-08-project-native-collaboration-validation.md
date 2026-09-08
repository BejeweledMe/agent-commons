# Project-native collaboration validation — 2026-09-08

Status at earlier checkpoint: implementation integrated; the first exact review requested changes.
Review corrections, final verification and independent re-review remain open. This record supersedes current-status statements in the earlier
self-improvement audit while preserving their historical outcomes.

## Exact boundary and scope

Base HEAD and observed origin/main: `e08488f392936c43d35f1013d6468fe45fa89b00`.
Branch: `codex/service-self-improvement`. The tree was already dirty at the start
of this project-native wave and remains dirty. No commit, push or merge yet.

Reviewed areas: project shell/folder creation; service-owned skills/groups and
versioned Blueprints; task/agent conversations and private attachments; exact
producer Results and live previews; provider transport and terminal finalization;
old ledger compatibility, task freshness and populated read performance.

## Historical pre-review checkpoint

| Evidence | Observed result | Limit |
| --- | --- | --- |
| In-repository doctor and bounded orientation | Zero integrity issues; 3183 events, 355 manifests, 136 advisory warnings at checkpoint | Historical stale reviews/findings remain; not all backlog accepted |
| `git diff --check` | Passed | Whitespace check only |
| `make check` | 214 Work, 27 Gallery; Python 2541 passed, 13 skipped, one failure | Exact ArtifactCommands signature fixture did not include the new source-path guard; corrected; 29 focused tests pass; full repeat pending |
| Fresh installed source | Python fingerprint `f35c5fe05ff340989be417a70e73daa102a81795c69a88e504ebecb4c0bc552e` equals checkout | Final frontend build is work-CEMJyPpv.js; reinstall pending |
| [Six actual provider canaries](../evidence/2026-09-08/project-native-provider-canaries.json) | Codex/Claude/Grok builder and independent-reviewer each succeeded, one terminal completion, zero rejections, child closed | Synthetic macOS execution; not Linux L1, product media qualification or release review |
| Actual service implementation | Codex R3A and Grok R1 succeeded; Grok R7 exited without required terminal call and remains needs_operator | Process success is not canonical completion or acceptance |
| Installed browser, separate new project | Manual folder creation, custom bilingual Blueprint create/apply/archive, Claude-profile role and task, recorded task conversation | No provider started by applying the Blueprint; native picker selection not proven |
| Populated readonly sample | 10.67 s cold; 0.32 s warm; 1.36 s graph rebuild | Local sample, not cross-platform SLO; changed canonical bytes still replay |
| Default collaboration storage tests | New default-state project text, PNG upload/download, MCP image, acknowledgement and Results pass | Actual provider media check still running |

## Later f35 checkpoint and review corrections

The [bounded post-review evidence](../evidence/2026-09-08/project-native-post-review-checkpoint.json)
records the subsequent full green gate at Python fingerprint `f35c5fe0…`:
2542 Python tests passed, 13 documented skips, two existing warnings; Work and
27 Gallery tests passed. This supersedes the pending repeat above only for those
bytes. The 158-file manifest remains unchanged historical proof.

An independent Node 24.20.0 scratch build reproduced `work-CEMJyPpv.js`,
`work-BuAI-tN9.css` and their HTML byte-for-byte. Positive feature markers were
present. The first Claude review's missing-bundle finding was therefore not
supported: minified lines were redacted by the scoped reader, search omitted
that coverage limitation, and the bounded global file listing omitted the
bundle. Search-coverage reporting has been corrected in source; the original
`changes_requested` judgment is retained. The decoder finding was actionable:
PNG/JPEG restrictions are now explicit at Pillow open calls, awaiting the final
gate and review of the changed source.

| Actual f35 media run | Result and retained limitation |
| --- | --- |
| Codex, generated image | Addressed image/file fetched and answered correctly; acknowledged; newly created image published; exact producer and cross-task/agent isolation checks passed. |
| Claude, existing image | Image/file fetched and answered correctly; acknowledged; existing image published with matching producer and isolation checks. This did not demonstrate new image creation. |
| Claude, generated image | Consumption passed, but generation produced no output and ended `needs_operator` with `process_canonical_mismatch`; five terminal rejections remain recorded. |
| Grok, existing image | File answer correct; image answer semantically matched golden yellow as gold. Strict label match and aggregate `ok` remain false. Publication, acknowledgement and scoped output checks passed. |

The Codex preview probe published a harness-created frontend descriptor. Root
browser observation loaded its separate-origin page through a new-tab link with
`noopener noreferrer` and no query/fragment. Header capture was unavailable;
no claim of observed authentication-header isolation follows. The first scorer
failed with a harness type error, and its later observation found the preview
stale after successful termination. Preview lifetime and historical image-output
corrections need their own final-source checks.

Claude native shell execution still needs approval; Grok terminal execution is
explicitly denied. Neither is qualified here for unattended build commands.
These media observations do not qualify a later Python fingerprint, Linux L1,
release R2 or every installed UI journey. Final code, installed assets, full
`make check`, exact independent re-review and task-bound reviews remain pending.

## Risk register

| Priority | Risk / observation | Disposition and closure |
| --- | --- | --- |
| P1 | Current integrated code has no exact independent approval | Freeze source manifest; actual Claude independent review; lead resolves findings before merge |
| P1 | Actual image/file use must not be inferred from a filename or hermetic ImageContent test | Real isolated provider fetch/ack/answer and scoped output verification; keep unsupported capabilities unavailable |
| P1 | Cross-task/project result or attachment disclosure | Exact context checks, external private storage, closed metadata, decoded image limits, producer-bound publisher and regression tests integrated; independent review required |
| P1 | Grok instruction in argv | Replaced with fixed stdin endpoint, no prompt file; actual Grok startup/terminal success observed; no fallback to argv |
| P1 | Canonical replay cost delays populated workflows | Read coalescing with full-byte scope verification and uncached writers implemented; cold mutations remain a latency limit |
| P2 | New project appeared to have incomplete task evidence solely because optional capacity telemetry is absent | Browser-reproduced; task evidence itself complete; presentation corrected with strict capacity-only predicate |
| P2 | Authored task goal is collapsed; empty summary occupies inspector | Browser-reproduced; corrected: open goal, omit absent summary, keep technical codes in disclosure |
| P2 | Native folder selection not verified on this host | Manual path works; real supported-host chooser and Linux behavior remain qualification work |
| P2 | Library custom authoring requires both languages and exact specialization | Validated refusal/recovery and successful save/apply; consider guided defaults without changing exact binding |
| P3 | Old status documents contradict later checkpoints | New plan/audit explicitly supersede current claims; preserve historical evidence and immutable ledger |

No confirmed credential or raw-provider-output leak was observed in the new safe
canary evidence. This statement is scoped to the retained evidence and reviewed
new payload contracts, not a claim of exhaustive repository security certification.

## Actionable checklist

Must fix before this branch is delivered:

- [x] Pass the full f35/CEM checkpoint; retain its exact historical evidence.
- [ ] Repeat full `make check` after review corrections.
- [ ] Freeze an exact source manifest and receive independent Claude review.
- [x] Record actual f35 image/file consumption and provider-specific publication outcomes.
- [ ] Complete installed attachment/Results and corrected preview journeys.
- [ ] Rebuild, install, compare source/assets and inspect the installed UI.
- [ ] Record task/review states accurately; do not normalize stale history.

Should fix soon:

- [ ] Measure cold canonical mutation latency after each ledger append.
- [ ] Verify native folder chooser and narrow/keyboard flows on supported hosts.
- [ ] Distinguish stale qualification from provider failure in all readiness copy.

Growth / needs decision:

- [ ] Add further project actions only with explicit host semantics (pinning,
  reveal folder, registry removal); current menu supports rename/archive/restore.
- [ ] Simplify bilingual Blueprint authoring with transparent defaults.
- [ ] Extend active-run wakeup/resume only when a provider offers an enforceable
  capability; polling/fetch acknowledgement is not process reattachment.
- [ ] Keep API/local vLLM/SGR plans in ADR 0018, outside current subscription runs.

External/live verification:

- [ ] Linux six-profile L1 and release R2 remain open; macOS results do not close them.
- [ ] Fresh source-bound media/preview verification after corrections and supported-host picker evidence.

## Plan deltas and ownership

[ADR 0019](../adr/0019-project-native-collaboration-and-outputs.md) records the
accepted product direction and new storage/cache/provisional-output contracts.
[Implementation graph](../project-native-collaboration-implementation-plan.md)
tracks R0–R7 and exact task IDs. The primary implementation program now reflects
the installed checkpoint and successful fresh Claude runs. EN/RU service-library
guides and FRONTEND_CONTRACT describe current reusable Library and scoped Results.

The lead owns integration, installed/browser proof and final task transitions.
Claude owns the independent exact review; real provider verification owns media
proof. Linux/operator evidence owns L1/R2 closure. The old Grok R7 terminal failure
must remain recorded even though its code passed later tests.

Cleanup is restricted to generated disposable files and clearly superseded
planning duplication. `.agent-commons/runtime/` is ignored; immutable events,
receipts and manifests are not manually edited or deleted. The five built-in
Blueprints remain unchanged; the synthetic custom Blueprint was archived after
UI verification. No user project files are removed.

## Final source checkpoint — 2026-09-09

[Final bounded evidence](../evidence/2026-09-08/project-native-final-validation.json)
supersedes pending test/install statements above. Python source SHA-256:
`a5ba8bf35e9c0241525e25e4b5c1a38de13186b1419e5fac7c143298167683db`.
Full `make check` passed: 2586 Python tests, 13 documented skips, two existing
warnings; both frontend suites and Ruff passed. A clean wheel contains only the
current Work assets. Installed UI/MCP source matches; browser loaded
`work-cJnL5_T-.js`. Independent scratch rebuild reproduced all three assets.

All six fresh provider terminal canaries succeeded on this source. Actual Codex
published the supplied image and separate-origin frontend descriptor, then
completed its delegation. Browser navigation still worked afterwards, without
Commons cookie, Authorization or navigation Referer. After the operator completed
the task, the image remained explicitly stale but its verified original pixels
opened through View recorded image. The live descriptor then became stale under
the unchanged exact-task-revision contract. Neither fixture was created by that
Codex run. The automated live-confirmation timer expired during slow browser
automation; its false result is preserved alongside separate actual browser proof.

Installed UI checks also passed custom-group create/rename in EN/RU and saving a
new version of an archived custom Blueprint. Native folder selection, browser
file-chooser upload and actual narrow/keyboard journeys remain unverified;
hermetic tests cover those contracts. Native Claude/Grok build-command capability,
Linux L1 and release R2 remain open. No broader capability or release claim is made.

The earlier Claude changes_requested outcome and frozen manifest remain intact.
The source now includes explicit search coverage, PNG/JPEG decoder selection,
direct-success preview retention and verified historical image viewing. A fresh
exact review and canonical task acceptance are pending at this checkpoint.
