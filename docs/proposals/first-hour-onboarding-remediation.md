# First-hour onboarding remediation

**Status:** proposal; it does not authorize product, CLI, event, or persisted-schema changes.
**Date:** 2026-08-27.
**Scope:** local first-use UI: safe browser handoff, bound-workspace clarity, setup/runtime guidance, and an honest /work → legacy review/acceptance handoff. It contains no user-project names, task text, provider output, or auth codes.

## 1. Outcome, evidence, and control

**Outcome.** A first-time operator should reach either the first meaningful UI action (workspace initialization) or an actionable explanation of why it is blocked, without guessing the browser, repository, or terminal mode. The single-use browser handoff must remain secure.

**Evidence boundary.** One exploratory user run is strong qualitative evidence, not a population baseline. It showed repeated terminal returns before first UI action, then successfully completed role → task → run → independent review → human acceptance. The implementation facts below are independently confirmed in source/tests; duration, cost, restart counts, and project details are not treated as product facts.

**Positive control.** The core loop is semantically guarded: direct acceptance is refused and reviewed work can be accepted ([tests/ui/test_acceptance_chain.py](../../tests/ui/test_acceptance_chain.py)). Today it is a hybrid flow: /work performs Cold Start; the legacy panel performs run monitoring, review, and acceptance. This proposal must not weaken review/acceptance semantics or automate task acceptance.

## 2. P0 AUTH-01 — verified handoff and recovery contradiction

### Confirmed fact chain

1. agent-commons ui auto-opens a browser unless --no-browser is supplied: [cli command](../../src/agent_commons/cli/__init__.py#L161-L170) and [serve](../../src/agent_commons/ui/server.py#L1103-L1130).
2. Today terminal output and auto-open receive the same `/#c=<code>` link ([CLI emit](../../src/agent_commons/cli/__init__.py#L290-L300)). S1 must migrate the emitted plain-text URL, emitted JSON URL, and an explicit opt-in browser open together to the same Work route: `/work#c=<code>`.
3. The code is intentionally one-use and short-lived; the first tab consumes it under lock ([LocalBrowserSession](../../src/agent_commons/ui/security.py#L67-L101)). A repeat correctly gets a non-oracular 401 unauthorized ([auth test](../../tests/ui/test_auth.py#L56-L82)).
4. /work is a separate public React shell ([work routes](../../src/agent_commons/ui/work_routes.py#L13-L35)). If the system browser consumed the code at /, taking that same fragment to /work must fail.
5. Work gives a fragment priority over a stored API base and clears the stored base on failed exchange ([Work API](../../frontend/work/src/api.ts#L181-L208)). Its current “open the printed URL again” recovery copy is therefore false ([Work locales](../../frontend/work/src/i18n.json#L56-L67)).

**Verdict:** a proven P0 activation/recovery defect, not user error.

### Security invariants

- retain one-use, 60-second, process-memory-only codes;
- never put a code in query, history, referrer, browser storage, API output, or canonical history;
- continue returning the same server refusal for wrong, expired, or consumed code;
- retain exact loopback Host + same-origin checks, opaque process API base, and HttpOnly SameSite Strict cookies ([security](../../src/agent_commons/ui/security.py#L19-L45), [exchange route](../../src/agent_commons/ui/server.py#L359-L435));
- add no UIContext, CommonsManager, MCP, event, or schema capability.

### Safe alternatives

| Option | Benefit | Cost / risk | Recommendation |
| --- | --- | --- | --- |
| A. Auto-open `/work#c=<code>` instead of the legacy root route | Removes legacy-first route mismatch. | The system browser still consumes the code before the user can choose another browser. | Insufficient alone. |
| B. Manual Work handoff by default; explicit --open-browser opt-in | User selects browser; simplest reversible fix; security model unchanged. | One deliberate terminal-to-browser transfer; CLI default change needs owner approval and migration note. | **Recommended immediate P0 path.** |
| C. Authenticated replacement-code flow | Later allows switching browser without restarting. | New security-sensitive auth surface: invalidation, rate limit and threat-model review required. | Defer. |

Every option also needs this exact client sequence. First read `c` into a local variable and immediately remove the fragment with `history.replaceState` **before** either stored-base restoration, exchange, or any API request. Next, if a validated stored opaque API base exists, probe/restore that live cookie-bound session first. Only when that restoration is not live may Work exchange the captured fresh fragment. A stale or failed fragment may not clear or replace a successfully restored session; only when neither restoration nor exchange yields a live session may the client clear its stored base and show recovery. This keeps the code out of history/referrer while preserving the one-use rule; it does not make an old code reusable.

**Candidate paired copy**

- EN terminal: “This sign-in link is single-use. Open it once in the browser you want. Use --open-browser only for your default browser.”
- RU terminal: “Ссылка для входа одноразовая. Откройте её один раз в нужном браузере. Используйте --open-browser, только если нужен браузер по умолчанию.”
- EN consumed state: “This sign-in link has already been used or expired. Stop the local UI, start it again, then open the newly printed Work URL once in this browser.”
- RU consumed state: “Эта ссылка для входа уже использована или устарела. Остановите локальную панель, запустите её снова и один раз откройте новый напечатанный адрес Work в этом браузере.”

The client may say “used or expired”; it must not claim to know which indistinguishable server-side cause occurred.

## 3. Confirmed P1 gaps

### P1-SETUP — Work drops actionable diagnostics

The existing setup read already provides found/missing providers, missing support binaries, config location, and a blocking refusal ([UIReads](../../src/agent_commons/ui/reads.py#L72-L102)); legacy has first-run guide/rescan coverage ([first-run tests](../../tests/ui/test_first_run_screen.py#L177-L203)). Work keeps only state, writes, and launch enabled ([contracts](../../frontend/work/src/contracts.ts#L9-L13), [parser](../../frontend/work/src/api.ts#L63-L72)), making a blocking runtime failure generic.

**Slice S2:** use a typed `SetupGuidanceDTO`, not a pass-through setup/read error. Its only displayable fields are: (a) a closed `blocker_code` enum; (b) closed-allowlist canonical tool names (`Claude`, `Codex`, `git`, `agent-commons-mcp`) required for that code; (c) a paired locale `next_action_key`; and (d) an optional, explicitly user-revealed *location label* from a closed vocabulary such as “workspace configuration” — never a raw path. The adapter must redact everything else: raw loader/parser/provider error, rejection reason, config contents, absolute or home path, secret/token, and stderr. Work names the missing executable before a click, disables Configure while blocked, and offers **Check again**. It does not invent an install button, raw-YAML editor, or browser file picker.

Candidate RU: “Прогон пока не запустится: в этой среде не найден ни Claude, ни Codex. Установите любой из CLI, затем нажмите «Проверить снова». Рабочее пространство сохранено.” For a support dependency, name agent-commons-mcp or git, never “Install it”.

### P1-SCOPE — the target project is invisible before mutation

--repo defaults to . and resolves once when the UI process starts ([CLI state](../../src/agent_commons/cli/__init__.py#L40-L116)). There is no project picker. The authenticated meta handler already knows repo and workspace ID, but Work does not load it. In `serve()` mode, browser APIs are bound at an opaque process path `/api/<random>`; raw `/api/meta` is intentionally unavailable (the latter is only direct-constructor test compatibility). A browser-side switch would also change process-bound state, config, and panel ownership; it is not a small frontend feature.

**Slice S3:** after auth and before Initialize, Work calls the existing meta handler only through its authenticated opaque Work API base (`${apiBase}/meta`), never raw `/api/meta`; it shows repo basename, an explicit “show full path” control, and what initialization writes. Wrong project fallback: stop the panel and restart with --repo <path>; nothing has been written yet. Do not add a picker.

### P1-HANDOFF — Cold Start ends at launch

Work explicitly directs the user to legacy monitoring after launch ([launch card](../../frontend/work/src/main.tsx#L357-L379)). That is honest but too easy to miss. **Slice S4:** launch success must say “run recorded; work is not accepted”, identify the next review step, and link to the existing panel. A native Work Runs/review/accept surface is a separate product vertical.

## 4. Observed but unproven — no delivery claim yet

| Item | Known | Needed before source change |
| --- | --- | --- |
| P1-OPS needs_operator after a run | It is a real fail-closed state; its cause in this run is unknown. | Synthetic fake-provider trace, typed reason, and comprehension test. Never auto-finalize/retry. |
| P2-VIS clipped legacy drawer / …[truncated] | A user observed it; legacy intentionally constrains dock width and abbreviates some summaries. | Screenshots/accessibility tree at 1440/1024/900/768px, 200% zoom, long EN/RU review. Prove layout defect versus intended shortening; only then take the legacy single-writer claim. |

## 5. Component graph, ownership, and boundaries

~~~text
CLI launch adapter ──────┐
ui.server::serve         ├─ S1 handoff route / browser policy
ui.security              ┘  (security invariants unchanged)
                              │
                              ▼
frontend/work API + i18n ── recovery and live-session restoration
                              │
typed setup read DTO ──────── Work setup explanation
existing `${apiBase}/meta` ─── bound-repo confirmation
legacy panel (unchanged) ◄─── Work launch-success handoff
~~~

| Slice | Owners | Modules | Explicit guard |
| --- | --- | --- | --- |
| S1 P0 handoff | product owner; Python UI/security; Work frontend; security QA | cli init, UI server, optional Work route constant, Work API + i18n, focused tests | no reusable code, event/schema, manager/UIContext, or legacy-static growth |
| S2 setup clarity | Python UI read-model; Work frontend; UX; QA | read DTO, existing setup serialization if required, Work contracts/API/UI/i18n/tests | reuse current GET setup; no setup-write or config-semantics change |
| S3 repo clarity | Work frontend/UX; privacy review | Work source/tests, existing GET meta through opaque `apiBase` | show, never switch, project; never call raw `/api/meta` in serve mode |
| S4 launch handoff | Work frontend/UX; QA | Work source/tests | no Runs/review/accept API or legacy-static modification |
| later Runs/review/accept | product, frontend, backend, security, QA | separate plan | follows work-state/review-loop gates |

New boundaries are typed DTOs/frozen records, not untyped dictionaries. Paired RU/EN locale keys and CSP-safe DOM apply. The UI server remains a thin adapter; UIContext, CommonsManager, and legacy index.html do not grow.

## 6. Verification and evaluation

### Deterministic CI

Use temporary Git repos, state roots, XDG config, and fake executable stubs only: no real provider, credentials, user path, task, or output.

1. CLI/UI integration: plain output, JSON output, and opt-in `webbrowser.open` emit `/work#c=<code>`; /work assets public, APIs private; one exchange succeeds and the repeat remains non-oracular 401.
2. Client contract: it strips `#c` with `history.replaceState` before any restoration, exchange, or request; valid stored opaque base restores before stale-fragment exchange; a stale fragment cannot clear or replace a live cookie-plus-base session; no code in history, referrer, or storage; paired honest copy.
3. Setup fixtures: no provider, partial provider, missing MCP, missing Git, and rejected config yield only the `SetupGuidanceDTO` allowlist; sentinel raw parser/provider reason, config body, absolute/home path, fake secret, and fake stderr are absent from the serialized DTO and rendered DOM. Blocked Configure and post-refresh recovery are visible.
4. Scope/handoff: in a real `serve()` fixture with a non-`/api` opaque API base, Work identity succeeds only through `${apiBase}/meta`, while raw `/api/meta` remains unavailable; Work shows that authenticated bound repo before Initialize, has no switch control, and post-launch copy distinguishes run from accepted work.

Start from [Work contract tests](../../tests/ui/test_work_app_contract.py) and [auth tests](../../tests/ui/test_auth.py). Source/HTTP tests do **not** prove the browser journey.

### Browser/usability gate

No Playwright/Selenium capability exists today. A separate frontend-toolchain decision may add browser E2E for manual handoff → first Work action, no-provider/fresh-handoff recovery, Cold Start to launch, and legacy drawer reachability. Before broad delivery, run at least 10 moderated clean-machine sessions. “First meaningful action” is **not** synonymous with Initialize: stratify by starting environment and report the two legitimate outcomes separately. For a ready environment it is successful Initialize; for a truthfully blocked environment it is correctly naming the displayed requirement and safe next action. Do not count a correct blocked outcome as abandonment, and do not combine the two rates into an activation target.

| Measure | Target | Safety guardrail |
| --- | --- | --- |
| First meaningful action — ready environment | ≥85% successful Initialize in ≤10 min without restart | one-use auth tests pass |
| First meaningful action — blocked environment | ≥90% name the displayed requirement and next action in ≤30 sec; no Initialize required | typed allowlist/redaction tests pass |
| Intended-browser auth | ≥95% first attempt | no cause oracle |
| Terminal crossings before first writable UI action | median ≤1; p90 ≤2 | no silent config/setup write |
| Target repo comprehension | 100% before Initialize | no project picker |
| Core loop | ≥80% launch → review request → human acceptance in ≤15 min | review/accept guard 100% |

Do not add telemetry now. Any later approved measurement may store only aggregate categories, never fragments, API bases, full paths, IDs, task text, raw refusal, or provider output.

## 7. Owner decisions and execution order

| Decision | Options | Recommendation |
| --- | --- | --- |
| DC-P0-1 browser default | auto-open root; auto-open Work; manual Work + opt-in --open-browser; future reissue | **Manual Work + opt-in**; harden same-tab restoration. |
| DC-P1-1 setup detail | exact missing executables; OS-specific install commands; generic docs | **Exact executables + Check again** now. |
| DC-P1-2 local path | basename + reveal full path; always full path; no identity | **Basename + reveal**. |
| DC-MEASURE-1 persistence | moderated study only; new browser telemetry/events | **Moderated study only** now. |

After decisions: implement S1, S2, S3, and S4 in separate behaviour commits and claims; run clean-environment make check before each; seek exact independent review and green CI. Rollback is UI/adapter reversion only: no ledger migration or canonical-state repair is involved, and the legacy panel/CLI fallback remains.
