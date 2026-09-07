# Независимый аудит плана, реализации и evidence — 2026-09-06

**Последующая реализация:** после этого review пользователь разрешил code fixes.
Их отдельные hashes, проверки и оставшиеся gates находятся в
[remediation report](2026-09-06-audit-remediation.md). Ниже сохранён verdict
исходного SHA; он не описывает исправленные незакоммиченные bytes.

**Verdict: changes requested.** J1/G6 реализованы и имеют exact canonical
acceptance, но текущая реализация содержит воспроизводимые P1. L1 live
qualification не доказана; R2 release evidence остаётся открытым. Green CI
подтверждает существующие проверки, а не отсутствие найденных дефектов.

## Граница и метод

- Reviewed Git SHA: **`329d2f3920753853ce265536d9d3ceb4e35f1f91`**.
  Исходные `HEAD` и локальный `origin/main` совпадали с ним; дерево было чистым.
  GitHub CI отдельно проверен для этого exact SHA, без fetch/commit/push.
- Аудит выполнен независимым reviewer window с тремя параллельными read-only
  проверками: docs/evidence, security/privacy, implementation/UX. Авторы аудита
  не меняли проверяемую реализацию. Это scoped judgment, не canonical approval
  новой версии и не acceptance задач.
- Прочитаны ONBOARDING, `commons-start`, `commons-review`, `security-review`,
  `qa-testing`, frontend contract; для завершения применён `commons-handoff`.
- Проверены все запрошенные основные планы, VISION, PRD, visual plan, provider
  plan, pivot plan, Context Pack/Gallery plan, ADR 0001–0013, reviews и JSON/MD
  evidence. Код трассирован по task/review/delegation, providers, receipt/state,
  Context/Design bindings, Starter Packs и Work/Gallery boundaries.
- Методы: `rg`, git metadata, read-only ledger projection, сопоставление hashes,
  полный `make check`, CI metadata и bounded synthetic reproductions. Scratch
  fixtures использовали только временные workspace. Provider не запускался;
  prompts, skill text, credentials и реальные provider outputs не сохранялись.
- Это не live pentest, OS sandbox certification, dependency CVE assessment или
  исчерпывающее доказательство всех interleavings. Проверки package freshness и
  review ниже — воспроизведённые контрпримеры, а не вывод из отсутствия тестов.

## Baseline, команды и результаты

| Команда / проверка | Результат и граница |
| --- | --- |
| `git status --short`; `git rev-parse HEAD`; `git rev-parse origin/main` | Clean; оба SHA равны reviewed SHA. |
| `git diff --check` | Passed до изменений и после docs patch. |
| `uv run agent-commons doctor` с inherited environment | `state_owner_mismatch`: унаследован exact state root чужого workspace. Fail-closed защита сработала. |
| Остальные пять mandatory orientation commands с inherited environment | Тот же отказ; это не отсутствие tasks/claims и не доказательство повреждения ledger. |
| `env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE uv run agent-commons doctor` | **ok=true**, 2901 events, 326 manifests, issues=[]; writable SQLite projection синхронизирована. Canonical history не менялась. |
| `session show`, `task list`, `claim list` в штатном namespace с `--read-only` | Прочитаны; active sessions/claims до audit session: 0/0. |
| `orient`, `inbox` с `--read-only` | Без session CLI отказал; после поддерживаемого создания отдельной operational audit session обе команды passed. Inbox: 8 broadcast handoffs, 1 broadcast thread. |
| `make check`, первый проход | Ruff passed; 456 files format-clean; Work 29/29, Gallery 22/22; pytest **1834 passed, 13 skipped, 2 warnings**, 409.60 s. Позже обнаружен Node 23.11.0 вместо `.node-version=24`; это дополнительное свидетельство, не финальный pinned-toolchain gate. |
| `npm exec --yes --package=node@24 -- node --version` | Node **24.20.0**. Python 3.14.5 из `.python-version=3.14`, Ruff 0.16.3 через locked uv. |
| `npm exec --yes --package=node@24 -- make check` | **Passed** на Node 24.20.0: Ruff/format passed, Work 29/29, Gallery 22/22, pytest **1834 passed, 13 skipped, 2 warnings**, 408.62 s. |
| `gh run list --limit 5 --json headSha,status,conclusion,name,url` | Exact-SHA CI completed/success: [run 33859243957](https://github.com/BejeweledMe/agent-commons/actions/runs/33859243957). Это evidence исходного commit, не uncommitted docs patch. |
| Hash comparison J1/G6 | G6: **18/18 match**. J1: **3 match, 7 changed, 1 replaced bundle missing**, всего 11; исторические bytes нельзя выдавать за текущие. |
| Synthetic diagnostics | Review bindings 1→0; artifact author approval bypass; restricted-source Context binding persisted; stale Design binding accepted; truncated PEM body retained; ambiguous-response acceptance retry conflict. Без live side effects. |
| Final doctor / orientation | Повторно ok=true, 2901 events, 326 manifests, issues=[], 136 warnings; task/delegation counts не изменились, active claims=0. |
| Docs checks | Relative links, JSON parse, fenced blocks passed; новые строки не содержат private path/session identity shapes. Изменений tracked source/tests/assets/locks/CI нет. |
| `session end` через in-process CLI | Audit session closed; private temporary nonce file удалён. |

Первые sandboxed `uv` вызовы не дошли до CLI: cache permission refusal, затем
macOS system-configuration panic с временным cache. Повтор через разрешённое
исполнение вне sandbox дал реальные результаты выше. Automatic approval review
не отклонял действия. Не менялись owner markers, receipts, manifests или events.
Safe action для обычного запуска: убрать ошибочный inherited exact-root override
либо явно выбрать проверенный namespace; не перемещать/удалять чужой state.

## Evidence и статусы delivery

| Flow | Verified truth | Не доказано / ограничение |
| --- | --- | --- |
| J1 Work journey | Task `task.6GGRYSZFTXTG4GYZ1Q0EVQVXNR` accepted; review `review.0W0BYT8S1F202VCAJZ7KY66R8B` approved, stale=false, subject `evt.01M1KDDRBVK2Q9D9SBJ0ZQ3ERD`; review revision `evt.01M1KG3V153H73WWTZBCVPHYWC`. `docs/evidence/2026-09-03/j1-work-operator-journey-verification.json`. | Review действителен для своего зарегистрированного subject; 8 из 11 старых file entries уже не совпадают с checkout. Прямой API hire в fixture не доказывает browser Apply→Hire. |
| G6 Design→Work | Task `task.2DY7ATZMFXC2P3GRARY65YS1Z1` accepted; review `review.0S9E2B8KQ38W9AZR25FHSC9SY4` approved, stale=false, subject `evt.01M1MJY0R8F9ZDKFSTEK5GMA8X`; review revision `evt.01M1NTJN1822Y31S8WMXPR6QMC`. Evidence `docs/evidence/2026-09-04/g6-design-package-work-launch-verification.json`, 18 matching hashes. | Exact approval не разрешает ambiguity F04. Runtime сохраняет bounded provenance metadata; provider instruction не получает design source/image bytes. |
| Context C1/C2/R1 | Canonical entity, bounded compiler/editor, historical pack lookup и per-launch binding существуют; соответствующие tests входят в gate. | Historical pack revision разрешена намеренно; stale/restricted **sources** обходят validation в runtime (F02). |
| Starter/blueprint Apply | Explicit Apply создаёт ordinary role templates, fresh, DENY_ALL; просмотр read-only. Backend hire supports `from_preset_id`. | Work form не передаёт preset ID (F07); внешние skill downloads и materialization не подтверждены. |
| Providers P1/P2/P4 | Adapter registry, validated invocation, provider-specific skills и шесть profiles реализованы. Preflight не потребляет model attempt. | Live current matrix не установлена. Grok prompt argv расходится с ADR (F08). |
| Receipt/doctor | Штатный local doctor green; ownership fail-closed; recovery derives receipts из validated ledger. | Local green не доказывает исправление Linux fresh-checkout bootstrap. Reconcile в этом проходе не выполнялся. |
| Independent review / acceptance | Exact targets, author provenance и явная acceptance реализованы; terminal success сам по себе не acceptance. | UI review walk стирает artifact provenance, ослабляя independence (F01). |
| Work/Gallery assets | Node suites и generated bundle parity выполняются внутри `make check`; CSP, EN/RU, closed DTO coverage присутствуют. | Static/source/API contracts не заменяют browser navigation, interaction и response-loss tests. |

## Risk register

Новые **P0 не установлены**. P1 ниже — release blockers для затронутых функций;
P2 — подтверждённые operational/product gaps либо явно ограниченные risks;
P3 — maintenance. Confidence и consequence разделены в описании.
Все pointers относятся к исходному reviewed SHA; в изменённых docs строки могут
сдвинуться. Значения private metadata намеренно не скопированы.

### F01 — P1: UI review теряет evidence и независимость

`ui/actions.py:481,554–558` вызывает `submit_task` без artifacts;
`services/tasks.py:158–179` записывает пустые bindings, а
`domain/task_projection.py:103–104` заменяет прежние значения. Все пути здесь
относительно `src/agent_commons/`. `domain/lifecycle.py:701–707,732–745`
вычисляет evidence authors по текущим bindings.

Временный completed task имел один artifact binding; после
`UIActions.request_task_review` осталось ноль. Отдельный artifact-only author
до операции получал `LifecycleConflictError` на independent approval, после
операции смог approve; manager затем принял task. Это не только потеря UI-поля.

**Owner:** workflow/domain + UI. Сохранить exact прежние bindings, отказать при
staleness; не переопределять revision по одному artifact ID. Regression:
completed→review сохраняет provenance, artifact author всё ещё не independent,
source change делает review stale, retry не меняет evidence.

### F02 — P1: Context launch обходит source validation

`services/context_packs.py:139–142,297–318` проверяет current revision,
classification и manifest. Runtime authorizer в
`services/delegation_runtime.py:1329–1348` проверяет лишь принадлежность pack;
`runtime/context_binding.py:475` вызывает pure compiler напрямую.
`delegation_runtime.py:1404–1418` сохраняет этот binding;
`runtime/launch.py:316–318,419–422` добавляет compiled context без нового lookup.

Repro: internal source → publish pack → source revised to restricted. Service
compile отказывает `context_pack_stale`, но runtime сохраняет binding и включает
compiled pack в конструируемую instruction. Проверены только boolean assertions,
provider не запускался; реальная передача restricted bytes не заявляется.

**Owner:** context/runtime + security. Один source authorization/freshness gate
для compile, bind и retry до побочных эффектов. Regression: revised/invalidated/
restricted source, superseded decision, missing manifest; никаких binding,
child или invocation при отказе. Правило относится к sources, а не к запрету
намеренно разрешённых исторических pack revisions.

### F03 — P1: усечение stderr уничтожает контекст secret scanner

`runtime/subprocess_runner.py:224–235` оставляет последние 4096 bytes до
сканирования; `runtime/diagnostics.py:485–512` проверяет уже обрезанный tail.
Сохранение: `runtime/attempts.py:1095`; отдача authenticated legacy Runs:
`ui/reads.py:549`. Work tracker closed DTO этот tail не включает.

Synthetic PEM-shaped input 4644 bytes распознаётся целиком; после вытеснения
BEGIN header body marker остаётся в sanitized tail, `truncated=true`,
`redacted=false`. Реального key leak в просмотренных tracked files не найдено.
Current tests сохраняют header (`tests/runtime/test_attempts.py:299`,
`tests/security/test_policy.py:158,191,197`) и этот случай не ловят.

**Owner:** runtime/security. Минимальная безопасная мера — не сохранять
provider-controlled tail при truncation, оставлять typed diagnostic code;
альтернатива — streaming redaction до усечения с состоянием secret block.
Regression должна проверять sanitized value, persisted attempt и Runs DTO:
chunked stderr, displaced header, missing footer, cut credential assignment.

### F04 — P2: Gallery/launch расходятся в freshness screen provenance

`runtime/design_package_binding.py:415–429` проверяет сам package;
`services/delegation_runtime.py:1453–1474` не проверяет его screen provenance;
`:1526–1534` сохраняет metadata. `services/design_gallery.py:259–275` в том же
случае правильно выставляет stale. `ui/reads.py:601–614` предлагает published
packages без эквивалентной freshness проверки.

Repro: published package → producer task revision меняется (также при submit с
сохранением artifact refs) → package revision та же → runtime accepts/persists
binding. Доказано расхождение freshness, **не утечка design bytes**. G6 проверяет
stale package id/revision; обязательность актуальности всех historical screen
bindings этим доказательством не установлена. Поэтому это semantic ambiguity,
а не безусловно нарушенный launch contract. До явного product решения сохранять
fail-closed release gate для этого спорного пути; согласовать смысл с Gallery.

**Owner:** design/runtime. Общий freshness result для Gallery и launch;
regression producer/artifact revised/restricted/missing перед selection и bind.

### F05 — P1: L1 не закрыт; qualification текущего release candidate не доказана

Последнее retained Linux evidence:
`docs/evidence/2026-09-03/linux-centos9-l1-dd9edab-fail-closed.md:7–45`, SHA
`dd9edab1fb9e6550b28be633ecdf57f2479b2faa`. Six preflights прошли, но doctor
отказал `receipt_scope_bootstrap_required`; canonical artifact/verification на
хосте не записаны. Codex builder/reviewer дали 1/1 terminal calls/completions;
Claude оба — provider error, 0/0; Grok оба — 0/0,
`grok_mcp_terminal_tool_not_called`, reviewer mismatch=true/needs_operator.

Claude quota exhaustion — plausible operator explanation, **не proven root
cause**. Grok fixes `f81e21bb`, `dd9edab`, `1386cb2` и прежние positive canaries
не являются доказательством успешного later live rerun. L1 task
`task.2R2FJEWZPD57MHV6GB2ENAEDRP` имеет `ready`, а не `blocked`; это canonical
статус очереди, не product qualification. R2 `task.4A15VT58XX1W6XE7J6GG4DAG3N`
также `ready` и зависит от L1.

**Owner:** Linux operator + runtime maintainer + independent reviewer; release
decision — product owner. На intended candidate/host: проверить ownership и
receipt status, inspect `receipt reconcile --help`, выполнить только допустимое
восстановление validated ledger, получить green doctor. Затем отдельное live
разрешение/бюджет, six-profile matrix с exact source/provider/skill contract,
terminal counts и mismatch=false, sanitized evidence, exact review и R2 decision.
Ни новый live run, ни quota purchase, ни receipt repair этим аудитом не разрешены.

### F06 — P2: Tracker retry меняет payload при прежнем ключе

`frontend/work/src/components/TrackerSection.tsx:234–241` удерживает key по
action/task/text, но `frontend/work/src/api.ts:1731,1749` заново получает revision.
После commit-success/response-loss retry отправляет старый key с новым CAS.
Реальный scratch manager вернул `IdempotencyConflictError`; повторная acceptance
не прошла, backend fail-closed. Owner: frontend/workflow. Хранить полный intent
payload и key вместе; fault-injection test на потерю response после commit.
Аналогичная create-task duplicate-after-response-loss возможность остаётся
гипотезой для отдельного retest, не подтверждённым defect этого отчёта.

### F07 — P2: Apply→Hire не замкнут в Work browser flow

`ui/starter_packs.py:108–120` создаёт presets; success text в
`frontend/work/src/i18n.json:216,561` направляет к normal hire form. Но
`frontend/work/src/main.tsx:697–730` имеет только profile selector, а
`frontend/work/src/api.ts:1614–1630` не отправляет `from_preset_id`/skills.
Backend `ui/actions.py:380–399` умеет это; J1
`tests/ui/test_work_operator_journey.py:511–522` вызывает API напрямую.
Owner: product/frontend. Добавить preset selection или точный доступный переход
в legacy; browser test Apply→Hire→Launch должен сохранить preset skills.

### F08 — P2: Grok transport расходится с ADR

ADR 0004:179–181 обещает instruction через stdin, never argv.
`runtime/model.py:1102–1109` передаёт compiled instruction через `-p`;
`runtime/adapters.py:157` честно объявляет `prompt_argument`.
**Доказан contract mismatch**; process-list/ARG_MAX exposure — ограниченный
локальной средой риск, live утечка не наблюдалась. Owner runtime/security должен
выбрать private prompt-file lifecycle либо явно пересмотреть trust/transport
contract. Проверять oversized invocation до reservation. Не исправлять ADR так,
чтобы молча принять новый privacy риск.

### F09 — P2: public evidence и local history минимизированы не полностью

В исходном SHA публичный macOS JSON
`docs/evidence/2026-09-02/macos-skill-aware-provider-canaries.json:4,26–27`
содержал operator session ID и command/profile-config path. Linux MD
`docs/evidence/2026-09-02/linux-centos9-l1-f81e21bb-fail-closed.md:9–10`
содержал absolute home/checkout path. Это не nonce, credential или config bytes.
**Эти поля удалены docs patch данного аудита**; measured outcomes сохранены,
redaction notice предупреждает о новом digest. Старое approval не переносится
на новые bytes. Git history не переписывалась; superseding evidence registration
и решение о treatment уже опубликованной истории остаются owner action.

Отдельно local ignored event
`.agent-commons/events/2026/08/26/evt.01M0YDTGYMMNVVAWGM7XFA17PP.json:1`
содержит 14020-char raw pytest log с 18 home-path occurrences. Пять local
task-created events 18 августа содержат целиком audit rubrics 13027–14270 chars.
Это не tracked/public доказательство и не установленный private skill leak.
Immutable files не менялись; correction не стирает исходные bytes.
Owner данных должен определить retention/export policy и поддерживаемый
correction/supersession путь. Нужен allowlisted public evidence exporter,
запрещающий identities, private paths, prompts/transcripts и source bytes.

Credential/key/signed-URL matches в inspected tracked source/tests/docs вручную
разобраны: подтверждённых реальных secrets нет. Synthetic test strings, packaged
project-owned skills, managed MCP templates и provider byte counts — не утечки.
Закрытые Work/Gallery DTO, preview digest/classification/path guards и loopback/
Origin/cookie checks присутствуют; это не гарантия отсутствия всех утечек.

### F10 — P2: backlog и handoffs содержат stale work

Срез ledger: **230 tasks** = 124 accepted, 52 completed, 20 review, 7 ready,
26 cancelled, 1 assigned; **blocked=0, active=0**. Нельзя превращать все completed
в accepted или отсутствие blocked states — в отсутствие реальных blockers.
113 delegations: 53 succeeded, 18 failed, 6 timed_out, 13 cancelled,
23 needs_operator; requested/active/input_needed=0. У 18 из 23 needs_operator
target revision уже отличается от current; blind retry недопустим.

34 findings: 26 reported, 8 resolved. 235 reviews: 159 approved,
48 changes_requested, 28 requested (17 requested stale). Doctor сообщает
75 stale reviews, 49 stale verifications, 7 stale task artifacts, 2 stale
decisions, 2 не применённых stale acceptance events и 1 orphan manifest.
Это warnings, не hard integrity failures и не автоматически новые баги.

Две ready задачи зависят от cancelled `task.7SP8T6FSCQXENRFTJ0T5CKC6RM`:
`task.7HY1A5FB3C1WR2R49V2XC3AG3K` (DAG/routing/council/governance) и
`task.07DZQY9AZYBK31F3T5ESSE6A9Z` (workflow evals/release evidence).
Единственная assigned `task.1GACGKQCD9EQ1XNZ5SW1FWRTQ5` — старый corrected
assessment. Остальные ready: compact orient/maintenance, programme independent
review, test breadth/CI cost, L1 и R2. Owner должен disposition obsolete work,
не переиспользовать старые scope/acceptance автоматически.

Active claims=0. Всего 669 stored claims: 602 released, 8 broken, 59 с raw
status=active, но **все 59 expired**. Это stale housekeeping, не 59 текущих
блокировок. В этом проходе claims не брались, задачи не принимались/не закрывались.

82 handoffs: 69 open, 13 acknowledged. Актуальный G6 handoff
`handoff.1ZFG5S7P635EAZ5MQ656HESJH2` от 4 сентября направляет к оставшемуся L1.
Предыдущий `handoff.3G9ES4X0VZA67DXXYDRCGSAB3N` всё ещё просит review G6,
который уже завершён. `handoff.2RW2MZBMXRPFXBTJM1V3TMAX7K` требует rerun на
`f81e21bb`, хотя позже есть `dd9edab` failure; это историческая инструкция.
Latest does not mean all older handoffs still prescribe current work.

Примеры reported findings, которые требуют disposition, а не доверия summary:

| Finding | О чём старый record | Оценка текущего аудита |
| --- | --- | --- |
| `finding.46GEH6QMSFEARM2DDQ4ARCC15C` | Green gate не включает frontend suites | Stale: Makefile и текущий запуск это опровергают. |
| `finding.6YC5X2JY59KPW4E86R9MTFXNAF` | Tracker не имеет accept/reopen/review actions | Actions реализованы; остаются F01/F06, нужен scoped disposition. |
| `finding.1TTJK8411BP4BATZKVYB10Y2Q4`, `finding.404886JS6ADSNPSZK9ETY68NTR` | Node argv / blanket PID rules | Historical remediations видны в accepted task evidence; не новые текущие failures. |
| `finding.2AY4FGZWEME2KSCPRWS7TPAR49` | Codex вообще не имеет MCP wiring | Historical claim; текущие adapters и terminal tests противоречат общей формулировке. |
| `finding.5YBDR8VWNA3J90JEZFB4M7EK23` | Linux qualification обязательна | Всё ещё relevant, F05. |
| `finding.0MAD58V699NP8WX4CM3QTTJEMC` | Старый Grok direct-CLI bypass | Later evidence говорит, что bypass не воспроизведён; terminal 0/0 blocker остаётся. Не закрывать без exact disposition. |
| `finding.09KR70BKW3NJHNNWKM7DREQ9HD` | Provider authentication/host availability | Current blanket failure не перепроверялся live; разделить implemented recovery и L1 proof. |
| Остальные reported records | CLI/version skew, review prerequisites, sandbox, replay/CAS, output budget, governance, historical performance/provenance/onboarding | Provisional/unresolved; этот проход не переутверждает их severity и не закрывает их по старым summaries. |

Полный оставшийся список `reported`, не перечисленный отдельными IDs выше:

| Finding | Краткая тема исторического record; без нового подтверждения |
| --- | --- |
| `finding.3S86BPZQTJMYEXJFVY1YGPA73R` | Установленный CLI отстаёт от ledger semantics. |
| `finding.4FY20ZV3YQT8VCZGTRCJHXVS4T` | Claude exit без terminal tool call. |
| `finding.6VE6KG60D2MBGTJ4BN3AZCSZ72` | Scope Codex help/preflight. |
| `finding.3XK0XP1RGRJSNFQJKKRT4TX9FF` | Open exact review prerequisite перед launch. |
| `finding.4ERR3PQ3XKGFRYZS9XZ3AQ8AM0` | Sandbox/state-root writes. |
| `finding.7BSHN3Z9YDSCJNCHJ4GFM0KM6F` | Старые Codex builds/MCP compatibility. |
| `finding.7B0CXG5QTQ5SCY2JMCTW7W2SVH` | Role-prefixed handoff recipients. |
| `finding.5EX9CDCDHXJFBAENGE6ME066WN` | Final-symlink regression coverage. |
| `finding.026GYJFW71EAK7QTWDA0E1T6PR` | Replay/superseded acceptance chain. |
| `finding.1J0VT9597NVS6SKRMFQTSQSH3E` | Stale-acceptance/CAS successor handling. |
| `finding.4EGV40Q20BHDSBP6V1ZG12CTNZ` | Общий stdout/stderr output budget. |
| `finding.6K8VGMK210V6V84EG92CF9JM6E` | Records без своевременного disposition. |
| `finding.6D4SW3BD147FJNZV5SZ2YSZAD5` | Worker lifecycle authority и отсутствующие tools. |
| `finding.6XGX8NMR6WJZMMHKBE08DA13J6` | Исторический warm-read performance profile. |
| `finding.1K1HY86749G4D7VKD75QN2EV4C` | Provenance collision исходных архитектурных reviews. |
| `finding.7FVJ0PBH59S4MRC3P5X4TEKFZW` | Исторические onboarding/auth handoff UX blockers. |
| `finding.0B214Y8B3739EC46SVDB6T5AEM` | Documentation authority/status drift. |
| `finding.66CNKNHY4C9XZHVEWQBMK9HJ81` | Provenance index и отсутствующая external capture copy. |

Эти две таблицы перечисляют все 26 `reported` IDs. Статус finding не означает,
что его старая формулировка воспроизводится на reviewed SHA; next owner должен
привязать disposition к exact evidence. Никакой auto-promotion не выполнено.

### F11 — P2/P3: current planning и historic evidence смешаны

Programme §1 всё ещё описывал отсутствующие adapters/packs/Gallery; PRD:95–105
сохранял ранний current boundary. Provider plan:253–259 говорил о четырёх profiles,
programme:1215 — Codex/Claude-only. Starter proposal:3–13 оставлял Apply future.
Добавлены dated checkpoints; historical scope не переписан как новый факт.
ADR 0001:25–29 говорил о future indexed reads; precise implementation note
обновлена по `services/manager.py:375–425`.

`current-product-and-architecture.md` уже явно historical f998e33;
architecture-improvement plan — historical 4844fdb; pivot plan — historical
analysis. Их старые значения не ложные current claims. Старые reviews связывают
своё approval с exact subject, а не с сегодняшним whole-tree. Нужен единый
evidence index с source/hash coverage, review subject, acceptance и supersession.

### F12 — P2: local Node major не проверяется green gate

Локальный PATH Node 23.11.0 прошёл initial gate при `.node-version=24`.
`Makefile:30–40` вызывает npm из PATH; `tests/test_ci_environment.py:18–24`
проверяет наличие Node в CI, не major. CI правильно читает `.node-version`.
Audit повторяет gate под Node 24; рекомендуется быстрый version assertion или
documented launcher. Это local reproducibility gap, не доказательство broken CI.

Два Starlette TestClient deprecation warnings — P3 maintenance: зависимость
обновлять отдельной locked change; не делать ad-hoc pip install в uv venv.

13 skips текущего прогона: 12 относятся к намеренно withheld automatic grant
level, ещё один — локальная CI-only Node-presence assertion. Work/Gallery Node
suites не имеют skips; optional MCP tests в этом окружении не пропущены.
Отсутствие Node в CI fail-closed, но major version локально не проверяется.
`make check` действительно включает обе frontend suites и Python bundle parity;
`make test-domain/runtime/ui/contracts` остаются advisory shards. CI использует
locked sync, Python 3.11–3.14 × Ubuntu/macOS и тот же full target; wheel smoke —
отдельная installed-artifact проверка, не замена gate. Новые tests нужны именно
для F01–F04/F06/F07 и version precondition, а не для имитации реализации.

## ADR / PRD consistency и obsolete documents

| Документы | Текущая оценка |
| --- | --- |
| ADR 0001–0003 | Ledger/projection, explicit truth и checkout-aware recovery соответствуют основному коду; 0001 indexed-read note уточнена. Bootstrap остаётся operational prerequisite, не integrity bypass. |
| ADR 0004–0006 | Core broker/state/communication boundaries реализованы в ограниченном scope; stdin claim 0004 нарушен Grok (F08). 0006 acceptance относится к communication core, не доказательству exited-provider resume. |
| ADR 0007 / 0011 | Proposed; deeper delegation/hierarchical closure нельзя объявлять shipped. |
| ADR 0008 | Withdrawn private RunEventStore; не возвращать как второй truth store. |
| ADR 0009 / 0010 | First-class roles и explicit partial supersession отражены; automatic grant level withheld. |
| ADR 0012 / 0013 | C1/G1 semantic contracts; later C2/Gallery/G6 уже существуют. Scope note не release dashboard. F02/F04 требуют общего source/provenance enforcement. |
| `docs/adr/README.md:32` | «Gallery API/UI subsequent work» означает subsequent to G1; follow-up уточнить навигационную формулировку. |
| `docs/reviews/2026-08-25-*`, `2026-08-29-*` | Historical assessments, полезны как scoped evidence; не approval current checkout. |
| `docs/reviews/2026-09-01-grok-runtime-canary-evidence.md`, старые provider JSON | Positive historical proof не supersede-ит later Linux negative evidence. |
| `docs/current-product-and-architecture.md`, architecture-improvement plan, pivot plan | Честно historical; сохранены, добавлены актуальные ссылки где требуется. |
| `docs/context-pack-gallery-implementation-plan.md`, `docs/visual_orchestrator_plan.md`, VISION | Intent/sequence остаются полезными; не основание называть недоказанный flow released. |
| Active programme / ROADMAP / PRD / provider plan / starter proposal | Dated implementation checkpoint добавлен; remaining future tense читается относительно явно сохранённого baseline. |

## Checklist и следующие работы

### Must fix before next release

- [ ] **F01 / workflow + UI:** сохранить exact evidence при review и доказать
  artifact-author refusal, staleness и retry; независимый review исправления.
- [ ] **F02 / context + security:** общий current/safe-source gate перед binding
  и invocation; negative tests на source/restriction/manifest/decision drift.
- [ ] **F03 / runtime + security:** fail-closed truncated stderr; marker отсутствует
  в attempt и Runs DTO при chunked/partial secret cases.
- [ ] **F04 / product + design + runtime:** решить historical screen provenance
  policy, согласовать Gallery/launch freshness; до решения fail closed для
  спорного пути, затем проверить producer/artifact drift по принятому контракту.
- [ ] **F05 / Linux operator + reviewer:** receipt bootstrap/doctor, отдельно
  разрешённая exact six-profile qualification и R2 release evidence. До этого
  broker остаётся experimental/manual.
- [ ] **F08 / runtime owner:** закрыть Grok transport contract decision и
  bounded oversized-input behavior до расширения advertised support.
- [x] **F09 / docs:** удалить явно лишние operator/session/path поля из двух
  public evidence files, сохранив outcomes и provenance notice.
- [ ] **F09 / evidence owner:** зарегистрировать redacted/superseding evidence
  при необходимости; решить treatment старых опубликованных bytes.

### Should fix soon

- [ ] **F06 / frontend:** полный immutable retry intent, response lost after
  commit для accept/reopen/create/review; exact replay без duplicate work.
- [ ] **F07 / frontend + product:** настоящий Work preset hire и browser journey
  с skill preservation, keyboard/locale/accessibility checks.
- [ ] **F10 / coordinator:** disposition cancelled prerequisites, assigned orphan,
  stale requested reviews, resolved-in-code findings и superseded handoffs через
  CLI. Сначала доказательства, затем transitions; не массовое «закрыть всё».
- [ ] **F11 / docs owner:** source/hash/review/acceptance/release index; автоматом
  показывать historical или mismatched evidence, не подменять старый digest.
- [ ] **F12 / tooling:** проверка Node major до gate; owner для deprecations.

### Growth / opportunity

- [ ] Allowlisted public evidence exporter/schema и regression на forbidden
  fields, oversized copied prose, private path shapes и raw outputs.
- [ ] Current operator dashboard: implemented/tested/live-qualified/accepted/
  released отдельно; stale handoffs/reviews видны без чтения 2901 events.
- [ ] Browser-level critical journey и failure injection дополняют Node/API
  contracts; measure time-to-first-valid-run и recovery success без raw transcripts.
- [ ] Общие source/provenance validators для compile, Gallery, binding, retry;
  поддерживать ясные ownership seams вместо дублирующих authorization callbacks.
- [ ] Versioned performance remeasurement, прежде чем использовать исторические
  20k-event timings как KPI или основание для архитектурной перестройки.

### Needs decision

- [ ] Product owner: какая platform/profile matrix заявляется поддерживаемой,
  требуется ли каждый из шести профилей для R2 либо утверждается меньший scope.
- [ ] Runtime/security: Grok argv transport, prompt-file lifecycle и host trust.
- [ ] Product/design: разрешён ли stale historical screen provenance при launch;
  до решения fail closed и единая семантика с Gallery.
- [ ] Governance owner: disposition ADR 0011 и review qualification roster;
  предложенные authority changes не вводятся документационным аудитом.
- [ ] Data owner: retention/export treatment local logs/rubric copies и прежних
  public metadata; immutable correction не равна erasure.

### Needs external / live verification

- [ ] Linux fresh-checkout receipt bootstrap на intended release revision,
  без смешивания state namespaces; green doctor после поддерживаемой процедуры.
- [ ] Claude failure: sanitized diagnosis с действующими operator credentials/
  quota; считать quota лишь гипотезой до независимого подтверждения.
- [ ] Grok builder/reviewer: terminal MCP calls/completions и mismatch=false на
  source после последнего transport/tool-contract fix.
- [ ] Cross-provider six-profile matrix и exact independent review. Никакие live
  provider canaries в этом audit не запускались.

Рекомендуемые следующие work items — F01, F02, F03 как отдельные bounded
fix/review batches, F04 как policy decision с последующим scoped retest,
затем F06/F07, F08 decision/implementation и F05/R2. Повторно
использовать L1/R2 tasks; новые задачи в этом проходе не создавались. Каждая code
change требует `make check` на pinned toolchain и exact independent review;
одного исправленного happy-path теста недостаточно для снятия gate.

## Изменённые файлы и handoff

Изменены только docs: этот audit; четыре primary документа (architecture plan,
platform programme, ROADMAP, current-product snapshot); ADR 0001; PRD;
provider plan; Starter Pack proposal; два redacted evidence файла. Код, tests,
lockfiles, generated UI assets и immutable Commons state не изменялись.

1. `docs/audits/2026-09-06-total-plan-work-adr-prd-review.md` — новый report/checklist.
2. `docs/architecture-improvement-implementation-plan.md` — current audit link.
3. `docs/agent-platform-implementation-program.md` — dated checkpoint и next gates.
4. `docs/ROADMAP.md` — J1/G6/L1 и release gates.
5. `docs/current-product-and-architecture.md` — addendum к historical snapshot.
6. `docs/adr/0001-file-ledger-with-sqlite-projection.md` — verified warm reads.
7. `docs/visual_multi_agent_orchestrator_prd.md` — current implementation boundary.
8. `docs/provider-adapter-architecture-plan.md` — six profiles / L1 / transport gap.
9. `docs/proposals/starter-packs-integration.md` — implemented Apply / missing hire step.
10. `docs/evidence/2026-09-02/macos-skill-aware-provider-canaries.json` — metadata redaction.
11. `docs/evidence/2026-09-02/linux-centos9-l1-f81e21bb-fail-closed.md` — path redaction.

Canonical artifact/finding/handoff не записывались: optional recording намеренно
оставлено за отдельным review окончательных docs bytes; это не integrity block
после успешной диагностики namespace. Исторические task acceptance/review records
не редактировались. Audit session использовалась только как operational identity
для orientation и закрыта через CLI; временный private nonce удалён.

Handoff владельцу: сохранить этот uncommitted docs diff, независимо проверить
F01–F04 reproductions, определить advertised release scope, реализовать bounded
fixes и обновить exact evidence. Только оператор может разрешить live matrix и
решить release/retention. Итоговое дерево намеренно dirty: десять изменённых
tracked docs и один новый audit, все перечислены выше. `HEAD` и локальный
`origin/main` остались на reviewed SHA; commit/push не выполнялись.
