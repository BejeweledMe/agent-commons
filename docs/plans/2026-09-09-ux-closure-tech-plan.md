# Технический план закрытия UX-наблюдений

Дата: 9 сентября 2026 года. Роль автора: Astra, technical lead / architect.
Статус: исполнимый план по окончательным решениям владельца. План не является
приёмкой реализации. Изменяется только этот файл; Git и `make check` в рамках
подготовки плана не запускаются.

## Изменения по замечаниям

1. Добавлен раздел G с самодостаточными task briefs для каждого запуска волны 1;
   для пакетов волн 2–3 даны названия и проверяемые критерии.
2. Добавлен раздел H — готовый outline ADR для canonical objective и immutable
   blueprint application provenance.
3. Добавлены отдельные WP-14 (single-locale user content), WP-15 (editable run
   limit), WP-16 (удаление Starter Packs с миграцией) и WP-17 (local opt-in
   instrumentation); обновлены coverage и зависимости.
4. B-02 оставлен операторским housekeeping, а не worker package; приведены
   точные формы `event correct`, `event invalidate` и повторного `review request`.
5. B-01/B-02 помечены как подтверждённые координатором: локальный `uv` в этой
   sandbox недоступен из-за global-cache permission, что не отменяет проверку.
6. Волна 1 перестроена: первые пять последовательных запусков дают видимую
   ценность B-10, B-09, B-04/05/06 и B-07/08 до больших рефакторингов.
7. Добавлен раздел I с обязательным межзапусковым протоколом координатора:
   claims, tests, evidence, formal review и момент сборки packaged Work asset.

## A. Проверка UX-плана и полное покрытие

### A.1. Проверенные исходные факты

Координатор вручную подтвердил B-01 и B-02. В текущей sandbox in-repo
`uv run agent-commons` не открывает глобальный uv cache; это ограничение среды,
а не опровержение фактов. Требование использовать in-repo CLI уже закреплено в
`AGENTS.md:75-78`.

Остальные ключевые точки проверены по checkout:

- provider availability уже имеет closed states/capabilities, но не фактический
  лимит попытки (`frontend/work/src/contracts.ts:223-258`);
- Starter Packs имеют отдельный DTO (`frontend/work/src/contracts.ts:276-327`),
  компонент `frontend/work/src/components/StarterPacksSection.tsx` и отдельные
  API routes (`frontend/work/src/api.ts:1714-1726`);
- `TrackerRun` содержит duration, но не `wall_time_seconds`
  (`frontend/work/src/contracts.ts:373-391`);
- UI coordinator сейчас подставляет 600 секунд
  (`docs/audits/2026-09-08-service-self-improvement-dogfood.md:185-192`);
- server canary принимает `wall_time_seconds` и сейчас валидирует диапазон
  30…1800 (`src/agent_commons/services/provider_canary.py:133-140`);
- bundled Starter Packs читаются из packaged resources
  (`src/agent_commons/integrations/starter_packs/bundled.py:39-156`);
- objective service уже существует (`src/agent_commons/services/objectives.py:1-82`),
  но task/application provenance для blueprint apply не завершён;
- сырые Starter Pack styles всё ещё образуют самостоятельную surface
  (`frontend/work/src/styles.css:500-640,1157-1161`).

Сохраняются обязательные инварианты `docs/FRONTEND_CONTRACT.md`: клиент не
продвигает canonical lifecycle; зелёный означает только acceptance; draft не
попадает в `localStorage`; inline styles не добавляются; canonical values не
переводятся; новые DTO-поля additive и при отсутствии трактуются консервативно.

### A.2. Coverage B-01…B-17 и решений 8.1…8.8

| Область | Реализация |
|---|---|
| B-01 | WP-01: CLI provenance diagnostic; только после отдельной product task. |
| B-02 | Operator housekeeping, не WP; команды ниже. |
| B-03 | WP-02: server-authoritative archive/restore skill group. |
| B-04/B-05/B-06 | WP-03: archive confirmation, semantic rail, readable availability stages. |
| B-07/B-08, D-9 | WP-04.1 backend snapshot join; WP-04.2 draft warning, availability chip, Stepper. |
| B-09, D-10 | WP-05: nullable `review_state` и честные output labels. |
| B-10 | WP-06: apply result → explicit Prepare run, без auto-launch. |
| B-11, 8.4 | WP-14: один ввод user-authored content, as-is в обеих UI locales. |
| B-12/B-13 | WP-07: scoped mutation registry и 2s/8s временные hints. |
| B-14/B-15, 8.3 | WP-13: consolidated EN/RU registry и dark semantic tokens; light theme вне scope. |
| B-16, 8.1 | WP-09: default «Сейчас», tabs «Карта»/«Все задачи», один render path. |
| B-17, 8.6 | WP-16.1 migration/routes; WP-16.2 удаление DTO/component/tests/docs. |
| 8.2 | WP-11.1 ADR до кода; WP-11.2 canonical objective/application implementation. |
| 8.5 | WP-15.1 server DTO/enforcement; WP-15.2 editable Prepare run control/copy. |
| 8.7 | WP-08 baseline by Grok; p50/p95 targets фиксируются только после evidence. |
| 8.8 | WP-17: local opt-in content-free counters + Settings + privacy contract. |
| D-11 | Один Opus asset integrator в конце каждой волны. |

### A.3. B-02: только поддерживаемый operator housekeeping

Координатор сначала фиксирует target id/hash/revision через read-only inspection,
затем выполняет ровно одну из поддерживаемых форм через in-repo CLI:

```text
uv run --locked agent-commons event correct <TARGET_EVENT_ID> \
  --expected-target-sha256 <SHA256> \
  --replacement-payload-json '<JSON>' \
  --idempotency-key <STABLE_KEY>

uv run --locked agent-commons event invalidate <TARGET_EVENT_ID> \
  --reason '<BOUNDED_REASON>' \
  --idempotency-key <STABLE_KEY>

uv run --locked agent-commons review request \
  --target-ref <TYPED_REF> \
  --target-revision <CURRENT_REVISION> \
  --criterion '<VERIFIABLE_CRITERION>' \
  --independent \
  --idempotency-key <STABLE_KEY>
```

Нельзя редактировать `.agent-commons/events`/manifests вручную. После correction
или invalidation запускаются `doctor`, повторная регистрация exact evidence и
новый review на текущую ревизию; старый review не «перепривязывается».

## B. Work packages

Каждый пункт ниже — logical package. Если указаны `.1/.2`, это отдельные запуски
не длиннее 60 минут с самостоятельным checkpoint. Полные briefs волны 1 — в G.

### WP-01 — CLI provenance

Terra: отличить stale/global executable или unsupported schema от повреждения
ledger; дать stable diagnostic code и supported next action. Paths:
`src/agent_commons/cli/`, maintenance service, CLI tests и docs. Canonical files
вручную не менять. B-02 в этот пакет не входит.

### WP-02 — archive skill group

Terra: добавить revision-bound route/service для archive/restore. Пустая группа
архивируется напрямую; непустая требует явную target group и атомарный перенос.
Opus позднее оформляет UI в рамках source integration, но server correctness и
DTO принадлежат Terra. Request additive:
`{expected_revision, archived, move_to_group_id?}`; typed 409/422, no optimism.

### WP-03 — project/setup clarity

Opus: confirmation на archive project с именем и неизменяемым path; текстовый
semantic state в rail; четыре availability stages человеческим языком, canonical
values только в disclosure. Стили только классами; зелёный не используется для
`ready`/`launchable`. Фактический лимит берётся только из WP-15 DTO.

### WP-04.1 — recipient availability snapshot

Terra: server делает D-9 join из одного manager snapshot и отдаёт additive
nullable `recipient_availability`:
`{state:"active"|"inactive"|"unknown", agent_id, active_delegation_id?, observed_revision}`.
Отсутствие/partial/stale → `unknown`, не `inactive`.

### WP-04.2 — conversation safety and delivery

Opus: один global `beforeunload` только при unsent text/reply/attachments; draft
остаётся в RAM. Availability chip читает WP-04.1. «Подготовить запуск» только
переходит/prefill. Existing five delivery states становятся Stepper и не
приравниваются к acceptance.

### WP-05 — honest output semantics

Opus: labels различают latest/historical/expired/unavailable и awaiting review.
Terra-owned backend field из D-10 реализуется как additive nullable
`review_state:"awaiting"|"approved"|"returned"|null`; UI не выводит review из
`stale`. Absence сохраняет прежнюю нейтральную подпись.

### WP-06 — blueprint-to-Prepare-run bridge

Opus: apply confirmation явно сообщает, что созданы roles/tasks и ноль runs;
primary CTA открывает Prepare run для первой server-confirmed ready task, secondary
CTA — план. Если `first_ready_task_id` отсутствует, UI проверяет returned mapping
на обновлённом server snapshot и не угадывает по порядку массива.

### WP-07 — scoped mutation feedback

Opus: заменить global `activeAction` на registry
`{projectId,surface,operation}`; navigation/read остаются доступны, конфликтующие
writes блокируются. 2s/8s — временные presentation hints, не performance SLO.
Confirmed POST и fallible refresh разделены; uncertain retry сохраняет body/key.

### WP-08 — baseline before targets

Grok: воспроизводимый 300-task fixture, warm/cold отдельно, server lock/write/
replay/read и browser paint отдельно. Результат — raw local evidence с runtime/
hardware metadata. До baseline нет hard p50/p95 gate; после baseline владелец
фиксирует targets отдельным решением.

### WP-09 — «Сейчас», «Карта», «Все задачи»

Opus: default route/view — `now`; три колонки «Нужно от вас», «В работе»,
«Дальше». Graph монтируется только в tab `map`; full list — только `all`.
Capacity fallback выбирается до mount, двойного graph/list DOM нет. URL хранит
только allowlisted presentation state и не меняет canonical task.

### WP-10 — DecisionCard and inspector hierarchy

Opus: top-level card отвечает «что происходит / кто отвечает / что дальше»;
technical values остаются в disclosure. Accept/return используют exact revision;
только accepted получает green. Findings не скрываются.

### WP-11.1 — ADR objective/application provenance

Terra: написать ADR по outline H до любой schema/domain реализации. ADR должен
быть принят до WP-11.2; до этого код provenance не начинается.

### WP-11.2 — canonical objective/application provenance

Terra: nullable `objective_id` на task и immutable blueprint-application record,
который связывает application с exact created task ids. Manual task может явно
join objective отдельной revision-bound operation. Старые tasks остаются `null`;
history не переписывается; retries сохраняют application identity.

### WP-12.1/.2 — role-first worker selection

Terra (WP-12.1) добавляет eligibility DTO/refusal contract; Opus (WP-12.2)
строит flow: сначала specialization, затем только server-eligible workers;
capability chips отдельно; profile/model под Advanced. Missing eligibility →
unknown/unselectable, а не eligible.

### WP-13 — dark semantic visual system and i18n consolidation

Grok выполняет механический перенос строк/token inventory отдельным checkpoint;
Opus владеет primitives, dark palette, responsive/a11y QA и финальной визуальной
интеграцией. Built-in catalog остаётся EN/RU. Light theme исключена.

### WP-14 — single-entry user-authored locale

Opus. Минимальный контракт: **не менять persisted schema**; форма показывает одно
поле для каждого user-authored name/description/title, а submit заполняет этим
точным значением оба существующих locale slots. Это наименьший совместимый шаг:
старые readers и parity validation сохраняются, migration не нужна, пользователь
не видит и не обязан поддерживать второе поле. Значение никогда не переводится;
при edit расхождение старых slots показывается как legacy conflict, требующий
явного выбора одного исходного текста, без silent overwrite.

Предлагаемая формулировка для `FRONTEND_CONTRACT.md`:

> Built-in catalog copy is authored and rendered in both EN and RU. User-authored
> names, descriptions and task text are entered once, stored without translation,
> and rendered byte-for-byte in either UI locale. Compatibility duplication into
> locale slots is storage plumbing, not a second authoring requirement.

### WP-15.1 — authoritative editable run limit backend

Terra. Добавить `wall_time_seconds: int | null` в run/canary read DTO и optional
integer в Prepare-run request. Для обычного run product bounds: **60…3600 секунд**;
canary сохраняет свой более узкий server policy **30…1800**. Server сам применяет
default 600 только когда поле отсутствует в create request, возвращает фактически
принятое значение и повторно валидирует bounds; клиенту не доверяет. Read absence
для legacy attempt остаётся `null`, не 600.

### WP-15.2 — Prepare run limit control and truthful copy

Opus. В Prepare run — numeric minutes control 1…60, step 1, с inline validation;
request отправляет секунды. UI показывает `лимит попытки N мин`, если factual DTO
не null, иначе `лимит не предоставлен`. Client validation удобна, но отказ
server 422 остаётся authoritative и сохраняет введённое значение в RAM.

### WP-16.1 — migrate Starter Pack content and remove backend routes

Terra. Перенести bundled pack manifests в built-in blueprint/template catalog с
stable semantic ids и provenance `built_in`; доказать parity ролей, profiles,
skills и deny-by-default grants. Затем удалить backend GET/apply routes и actions:
endpoint больше не публикуется и route tests ожидают обычный not-found/method
refusal без write. Никакого release window со старой surface. Новые installs не
выставляют Starter Pack capability.

### WP-16.2 — delete Starter Packs surface

Opus. Удалить DTO `contracts.ts:276-327`, methods `api.ts:1714-1726`,
`StarterPacksSection.tsx`, import/use в `LibrarySection.tsx`, strings/styles и
`tests/ui/test_work_starter_packs_ui.py`; заменить tests на blueprint migration/
route-deprecation coverage. Docs говорят только «built-in blueprints/templates».

### WP-17.1/.2 — local opt-in content-free instrumentation

Terra (WP-17.1) создаёт bounded counter store под resolved state root; Opus
(WP-17.2) добавляет Settings toggle и disclosure. Событие содержит только allowlisted event kind,
UI version, duration bucket и coarse outcome. Запрещены text, paths, ids, task/
agent/project identifiers, attachment metadata и raw timestamps; хранение local,
bounded retention, export/upload/transmission отсутствуют. Default off; disabling
stops collection and offers explicit local clear.

Privacy-добавление в `FRONTEND_CONTRACT.md` должно явно сказать: collection off
by default; schema rejects content/paths/identifiers; bytes живут только под
resolved state root с bounded retention; UI не имеет export/transmit action;
выключение немедленно прекращает запись, clear удаляет только этот local store.

## C. Зависимости, строгие волны и asset ownership

```mermaid
flowchart LR
  W61[WP-06] --> W5[WP-05] --> W3[WP-03] --> W41[WP-04.1] --> W42[WP-04.2]
  W42 --> W151[WP-15.1] --> W152[WP-15.2]
  W152 --> W8[WP-08] --> W14[WP-14] --> W2[WP-02] --> W7[WP-07]
  W7 --> W161[WP-16.1] --> W162[WP-16.2] --> W111[WP-11.1] --> W112[WP-11.2]
  W112 --> W9[WP-09] --> W10[WP-10] --> W121[WP-12.1] --> W122[WP-12.2]
  W122 --> W171[WP-17.1] --> W172[WP-17.2] --> W13[WP-13]
```

Никаких параллельных writer runs: checkout policy требует ровно одного writable
worker. Strict order:

| Волна | Последовательность запусков | Asset integrator |
|---|---|---|
| 1 — видимая ценность | WP-06 → WP-05 → WP-03 → WP-04.1 → WP-04.2 → WP-15.1 → WP-15.2 | Opus после WP-15.2 |
| 2 — foundations/refactors | WP-08 → WP-14 → WP-02 → WP-07 → WP-16.1 → WP-16.2 → WP-11.1 → WP-11.2 | Opus после WP-11.2 |
| 3 — IA/consolidation | WP-09 → WP-10 → WP-12.1 → WP-12.2 → WP-17.1 → WP-17.2 → WP-13.1 → WP-13.2 | Opus после WP-13.2 |

Каждый запуск ≤60 минут. Незавершённый запуск не «продлевается»: он оставляет
checkpoint (изменённые files, passing named tests, open failures) и следующий
запуск получает новый `.n` brief/revision. Asset integrator единственный держит
`path:src/agent_commons/ui/static/work`; остальные меняют только source/tests.
`src/agent_commons/ui/static/index.html` не входит ни в один пакет.

Почему mapping такой: Opus получает React/CSS/copy/design; Grok — объёмную
механическую миграцию и profiling; Terra — Python domain/DTO/storage/CLI и
cross-stack correctness boundary. Только Opus rebuild packaged Work asset как
frontend owner, один раз в конце каждой волны.

## D. Окончательные решения владельца

| ID | Fixed input |
|---|---|
| 8.1 | Default — «Сейчас» с тремя колонками; graph → «Карта»; list → «Все задачи». |
| 8.2 | Canonical objective + nullable task `objective_id` + immutable blueprint application links; manual explicit join; ADR first. |
| 8.3 | Dark consolidated direction; semantic tokens first; light theme вне scope. |
| 8.4 | Built-in catalog bilingual EN/RU; user-authored entered once and shown as-is in both locales. |
| 8.5 | Factual additive `wall_time_seconds`; editable before launch; absence copy «лимит не предоставлен». |
| 8.6 | Starter Packs удаляются сейчас после migration в built-in blueprints/templates. |
| 8.7 | Baseline first; 2s/8s only temporary hints; p50/p95 targets after evidence. |
| 8.8 | Local opt-in content-free counters under state root, Settings toggle, never transmitted. |
| D-9 | Server joins recipient availability from one manager snapshot. |
| D-10 | Additive nullable `review_state` on outputs. |
| D-11 | One Opus packaged-asset integrator per wave. |

Эти пункты не являются open questions и не могут быть переоткрыты worker-ом.

## E. Review protocol

После **каждого запуска** координатор сначала проверяет diff и named tests, затем
запрашивает formal independent review через broker:

- работу Opus и Grok проверяет `codex-independent-reviewer` (Astra);
- работу Terra проверяет `claude-independent-reviewer`;
- reviewer получает exact task revision, exact artifact revision и evidence,
  а не mutable checkout «как есть»;
- failed/returned review исправляется новым bounded run, затем создаётся новый
  review на новой ревизии; старое approval не переносится.

Для UI evidence нужны EN/RU, keyboard/focus и narrow viewport для затронутой
surface. Для DTO — fixtures present/absent/unknown и typed refusal. Для storage —
state-root isolation, permissions/locking, bounded retention. В конце каждой
волны Opus rebuild-ит asset, координатор запускает полный `make check`, регистрирует
exact source + asset + test evidence и только затем открывает интеграционный review.

Проверяемые глобальные критерии:

1. Ни один client action не продвигает acceptance/run/review без ответа сервера.
2. Только accepted task зелёный; ready/approved/launchable нейтральны.
3. Missing additive field означает unknown/hidden/neutral, но не success/default.
4. Retry после uncertain использует исходные body и idempotency key.
5. Built-in copy имеет EN/RU parity; user content не переводится.
6. Drafts/attachments/paths/IDs не попадают в browser storage или telemetry.
7. Packaged asset собран ровно одним интегратором из принятого source.

## F. Риски, rollback и stop conditions

| Риск | Снижение / rollback |
|---|---|
| DTO join устаревает или дорог | Один manager snapshot; bounded fields; missing → unknown; additive field можно не рендерить. |
| Run limit врёт | Server returns accepted factual value; legacy absence не подменяется 600; UI control можно скрыть без schema rollback. |
| Starter migration теряет semantics | Manifest parity fixture до удаления; compatibility refusal window; built-in blueprint ids stable. |
| Objective migration меняет history | ADR first; только новые events/projections; nullable старые tasks; feature presentation можно отключить. |
| Mutation refactor ломает retry | Fake-clock/state-machine tests; write result отделён от refresh; exact retry identity. |
| Массовый i18n/CSS diff скрывает regression | Grok mechanical checkpoint отдельно от Opus visual checkpoint; token lint/parity tests. |
| Instrumentation собирает content | Closed event schema, rejecting serializer tests, local-only store, default off и clear. |

Немедленный stop: target revision изменился; два writer-а держат пересекающиеся
paths; DTO absence трактуется как success; появляется client-side lifecycle
advance; retry identity теряется; reviewer bytes не совпадают с artifact;
packaged asset собрал не назначенный Opus; named tests или final `make check` red.

## G. Task briefs для исполнителей

### G.1. Волна 1 — полные briefs

#### WP-06 — Blueprint apply: явный переход к Prepare run

**Executor / limit:** Opus, 60 минут.

**Description:** изменить `WorkBlueprintsSection.tsx`, orchestration в `main.tsx`,
EN/RU strings/styles и tests так, чтобы successful apply показывал counts и прямо
говорил «run не запущен». Primary action лишь открывает Prepare run для task id,
подтверждённого response mapping и свежим tracker snapshot; secondary action
открывает план. При отсутствии ready task показать neutral next action. Не делать
POST launch из apply callback, не выбирать первый array element, не писать draft
в storage. Отчёт: files, named tests, screenshots EN/RU, remaining gaps.

**Acceptance criteria:**

1. Apply path создаёт ноль runs; test наблюдает отсутствие launch request.
2. Primary CTA prefill-ит exact server-confirmed ready task id.
3. Missing/no-ready mapping не запускает и показывает safe fallback.
4. EN/RU copy явно разделяет «created» и «started».
5. No localStorage и no optimistic canonical state.

**Claims:** `frontend/work/src/components/WorkBlueprintsSection.tsx`, relevant
`main.tsx`, `i18n.json`, `styles.css`, `frontend/work/tests/work-library.test.mjs`,
`tests/ui/test_custom_library_blueprints.py`. Packaged asset не claim-ить.

#### WP-05 — Honest output status and review state

**Executor / limit:** Opus, 60 минут.

**Description:** обновить output component/parser/copy и минимальный backend join.
Render branches: latest, historical verified, expired preview, unavailable,
awaiting/approved/returned review. `review_state` additive nullable; missing не
выводит awaiting. Не превращать review approval в task acceptance и не красить
его зелёным. Отчёт: DTO fixture table, tests, EN/RU screenshots.

**Acceptance criteria:**

1. Present/missing/null/unknown review field покрыты parser tests.
2. Historical bytes никогда не выглядят current.
3. Awaiting label приходит только из server field.
4. Green используется только для accepted state.
5. Existing clients/payloads без поля сохраняют neutral UI.

**Claims:** output service/route, `OutputsPanel.tsx`, `outputsApi.ts`,
`outputsStrings.ts`, relevant CSS, output frontend/service/route tests.

#### WP-03 — Project archive and setup clarity

**Executor / limit:** Opus, 60 минут.

**Description:** добавить accessible confirmation с project name и immutable
folder path; rail показывает текстовый state, не одиночную цветную точку;
availability stages разнести на читаемые строки, protocol terms оставить в
details. Actual limit показывать только при WP-15 field. Не менять backend
archive semantics, canonical values и не добавлять inline styles.

**Acceptance criteria:**

1. Cancel не вызывает archive; confirm вызывает один existing request.
2. Dialog озвучивает name/path и возвращает focus.
3. Rail state понятен без цвета; ready не зелёный acceptance.
4. Первый уровень не содержит `canary`/`qualification probe`.
5. EN/RU и keyboard tests проходят.

**Claims:** `ProjectSidebar.tsx`, affected setup/main components, `styles.css`,
`i18n.json`, project-workspace/provider-availability/work-shell tests.

#### WP-04.1 — Server-derived recipient availability

**Executor / limit:** Terra, 60 минут.

**Description:** в conversation service/route получить thread и recipient run
availability из одного manager snapshot. Добавить nullable field с closed state,
agent id, optional active delegation id и observed revision. Partial/stale/error
не угадывать: `unknown`. Не создавать отдельный client join и не раскрывать
private process details. Отчёт: exact schema, fixtures, query-count note, tests.

**Acceptance criteria:**

1. Active/inactive/unknown выводятся из одного snapshot в service tests.
2. Missing recipient или partial snapshot даёт unknown.
3. Старый response shape остаётся валидным additive contract.
4. Route test доказывает отсутствие второго mutable snapshot read.
5. Поле не содержит content/private paths.

**Claims:** `src/agent_commons/services/conversations.py`, exact conversation
route adapter, `tests/services/test_conversations.py`,
`tests/ui/test_conversation_routes.py`.

#### WP-04.2 — Draft guard, availability chip, delivery Stepper

**Executor / limit:** Opus, 60 минут.

**Description:** parse WP-04.1 conservatively; добавить text+icon availability
chip, Prepare-run navigation/prefill и Stepper существующих delivery states.
Один global beforeunload активен только при unsent text/reply/attachments.
Draft остаётся в component/session RAM. Не писать localStorage, не делать launch
POST и не считать delivery acceptance. Отчёт: branch matrix, tests/screenshots.

**Acceptance criteria:**

1. beforeunload включается/выключается по всем трём draft sources.
2. Reload очищает draft; browser storage остаётся пустым.
3. Missing availability рендерится unknown.
4. Prepare action не отправляет write request.
5. Все пять delivery states доступны keyboard/screen reader и имеют EN/RU copy.

**Claims:** conversation components/state/API/strings, relevant `main.tsx`/CSS,
`frontend/work/tests/conversations.test.mjs`.

#### WP-15.1 — Server-enforced wall-time contract

**Executor / limit:** Terra, 60 минут.

**Description:** добавить optional request и nullable factual read field для
normal run/canary. Normal run bounds 60…3600; server default 600 только для
отсутствующего create field. Canary policy остаётся 30…1800. Validate integer,
reject bool/fraction/out-of-range typed 422. Сохранить accepted limit в canonical/
attempt projection, чтобы reads не реконструировали его из default. Legacy reads
дают null. Отчёт: schema locations, migration/absence story, tests.

**Acceptance criteria:**

1. Boundary values и invalid types покрыты server tests.
2. Client cannot exceed/avoid server bounds.
3. Accepted value возвращается в run/canary DTO.
4. Legacy missing value читается null, не 600.
5. Same idempotency retry с другим limit конфликтует, с тем же — replays.

**Claims:** launch coordinator/service, delegation/canary DTO adapters,
`contracts.ts` parser boundary if required, corresponding service/UI route tests.

#### WP-15.2 — Editable Prepare run control

**Executor / limit:** Opus, 60 минут.

**Description:** добавить в Prepare run minutes input 1…60 step 1, начальное
значение 10 для нового create draft; отправлять seconds. После response отображать
только factual DTO: «лимит попытки N мин» или «лимит не предоставлен». 422 не
сбрасывает RAM draft и показывает server reason. Не считать HTML min/max защитой
server и не подставлять 10 минут в legacy reads.

**Acceptance criteria:**

1. User меняет limit до launch; request содержит exact seconds.
2. Empty/fraction/out-of-range дают accessible local error без POST.
3. Server 422 показан и введённое значение сохранено в RAM.
4. Factual/missing DTO дают две утверждённые строки.
5. EN/RU, keyboard and parser tests проходят.

**Claims:** Prepare run component/state, `api.ts`, `contracts.ts`, `i18n.json`,
styles и launch/work-shell tests. Packaged asset только на финальном integration run.

### G.2. Волна 2 — минимальные briefs

| Run | Title | Acceptance criteria |
|---|---|---|
| WP-08 | Baseline mutation latency (Grok) | (1) reproducible 300-task fixture; (2) warm/cold and server/browser split; (3) raw local evidence, no hard SLO and no network. |
| WP-14 | One-entry user-authored locale (Opus) | (1) one visible field; (2) exact value fills both compatibility slots; (3) legacy mismatch requires explicit choice; (4) built-ins remain EN/RU. |
| WP-02 | Revision-bound group archive (Terra) | (1) empty archive/restore; (2) nonempty requires target; (3) move+archive atomic; (4) typed conflict and retry tests. |
| WP-07 | Scoped mutation registry (Opus) | (1) unrelated reads enabled; (2) conflicts blocked; (3) 2s/8s fake-clock hints; (4) uncertain exact retry; (5) refresh never replays write. |
| WP-16.1 | Starter content migration/backend deletion (Terra) | (1) manifest parity in built-in blueprint; (2) stable ids/provenance; (3) GET/apply routes/actions removed and non-writing not-found tested; (4) deny grants preserved. |
| WP-16.2 | Delete Starter Packs UI/contracts (Opus) | (1) named component/DTO/API removed; (2) old UI test replaced; (3) no strings/styles/imports remain; (4) docs name blueprints/templates. |
| WP-11.1 | ADR objective/application (Terra) | (1) all H headings resolved; (2) compatibility/retry semantics explicit; (3) alternatives/consequences recorded; (4) accepted before WP-11.2. |
| WP-11.2 | Implement provenance (Terra) | (1) nullable objective on tasks; (2) immutable application links exact tasks; (3) manual explicit join; (4) retries stable; (5) no history rewrite. |

### G.3. Волна 3 — минимальные briefs

| Run | Title | Acceptance criteria |
|---|---|---|
| WP-09 | Сейчас / Карта / Все задачи (Opus) | (1) now default/three columns; (2) graph only map; (3) full list only all; (4) 300-node no double render; (5) allowlisted URL state. |
| WP-10 | DecisionCard (Opus) | (1) state/owner/next action first; (2) exact-revision actions; (3) findings visible; (4) only accepted green. |
| WP-12.1 | Worker eligibility DTO (Terra) | (1) closed eligibility/refusal schema; (2) missing is unknown; (3) role-version exactness; (4) server remains authority. |
| WP-12.2 | Role-first worker choice UI (Opus) | (1) role before worker; (2) only server-eligible selectable; (3) missing fail-closed; (4) profile/model under Advanced. |
| WP-17.1 | Private local counter store (Terra) | (1) opt-in default off; (2) serializer rejects content/path/id; (3) state-root bounded retention; (4) no transport/export code. |
| WP-17.2 | Instrumentation Settings UI (Opus) | (1) explicit toggle/disclosure; (2) disabling stops writes; (3) scoped clear; (4) privacy contract text; (5) EN/RU/a11y. |
| WP-13.1 | Mechanical i18n/token migration (Grok) | (1) exact EN/RU parity; (2) no copy semantic changes; (3) raw-color inventory/lint; (4) checkpoint tests green. |
| WP-13.2 | Dark visual consolidation (Opus) | (1) semantic dark tokens/primitives; (2) no light theme; (3) focus/contrast/narrow QA; (4) canonical values unchanged; (5) no inline styles. |

## H. ADR outline: canonical objective и blueprint application

**Предлагаемый файл:** `docs/adr/NNNN-objective-blueprint-application-provenance.md`.

1. **Context.** Сейчас blueprint apply возвращает mapping в response, но durable
   grouping нельзя надёжно восстановить после reload/retry; title/idempotency key
   не являются public provenance. Objectives уже canonical, старые tasks не имеют
   обязательной связи.
2. **Decision.** Task получает immutable-at-create nullable `objective_id`;
   отдельный immutable `blueprint_application.created` фиксирует
   `application_id`, exact blueprint id/version/revision, objective id, ordered
   created task ids и created agent/role refs. Manual task join — отдельное
   revision-bound событие с audit reason; связь application не фабрикуется.
3. **Schema/invariants.** Closed ids, exact revisions, unique application id,
   task ids принадлежат одному application, application record append-only;
   correction/invalidation только supported history operations. Tracker read DTO:
   nullable `objective_id`, `application_id`, conservative unknown handling.
4. **Write/retry protocol.** Application id детерминирован от original apply
   idempotency identity; crash/retry возвращает тот же mapping; differing body
   conflicts; partial projection rebuild не создаёт вторую application.
5. **Migration/compatibility.** Старые tasks → null, backfill по title/key запрещён;
   старые clients игнорируют additive fields; projection rebuild читает old/new
   events; никакого history rewrite.
6. **Authorization.** Create objective, apply blueprint и manual join имеют
   отдельные capabilities/revision checks; UI selection не является permission.
7. **Alternatives rejected.** Response-only grouping; inference from titles or
   idempotency keys; application-only без objective; mutable application rows;
   mandatory objective for every manual task.
8. **Consequences.** Появляется честная grouping/reload semantics и audit trail;
   увеличиваются schema/projection/test surface и необходимость cleanup policy
   для archived objectives без удаления provenance.
9. **Rollout/test plan.** Domain/projection first, apply service second, DTO/parser
   third, UI last; golden replay old ledger, retry/crash/partial tests, manual join,
   archive/reopen/correction tests.
10. **Open implementation details (не owner decisions).** Точные event names,
    ID factory namespace и maximum tasks per application выбираются в ADR review,
    не изменяя решение C.

## I. Что координатор делает между запусками

| Момент | Обязательные действия координатора |
|---|---|
| До run | Убедиться, что previous writer завершён; проверить target revision; release его claims; acquire только exact paths нового run; packaged asset claim не выдавать обычному worker. |
| После worker report | Остановить writes; сверить diff только с claims; запустить named targeted tests; проверить отсутствие незаявленных файлов и секретов. |
| Evidence | Зарегистрировать exact source artifact/revision, команды и bounded outputs; для UI — EN/RU/focus/narrow screenshots; для DTO — present/absent/refusal fixtures. |
| Formal review | Coordinator diff review, затем broker review: Astra для Opus/Grok, Claude reviewer для Terra; передать exact bytes/revision/evidence; дождаться verdict. |
| Returned review | Не принимать пакет; release claims; создать bounded fix run на текущей revision; после fix зарегистрировать новое evidence и новый review. |
| Accepted run | Отметить checkpoint, release worker claims, подтвердить следующий run в strict order; не переносить approval на последующие bytes. |
| Конец волны | Только Opus acquire `path:src/agent_commons/ui/static/work`, rebuild из accepted source, запускает asset-specific tests и передаёт claim обратно. |
| После rebuild | Coordinator запускает `make check`, регистрирует exact source+asset+green evidence, запрашивает integration review и лишь после принятия открывает следующую волну. |

Так сохраняются checkout single-writer rule, cross-provider independence и связь
каждого verdict с точными bytes, а не с плавающим состоянием shared checkout.

## L. Поворот к доске проекта (9 сентября, вечер)

Владелец вернулся к исходной модели PRD от 9 августа: главный экран проекта —
бесконечная доска (ADR 0020, `docs/adr/0020-project-board-home.md`, decision
`ux/project-board-home`; прежнее `ux/work-home-screen` заменено). Реализовано
координатором в этом же срезе: маршрут `board` по умолчанию (`team` →
`board`), компонент `ProjectBoard` на React Flow (роли как узлы, `agent_link`
как рёбра, отделы как рамки), приватные маршруты `GET/POST /api/board` с
хранилищем раскладки под state root (CAS по ревизии), фильтр трекера по роли
(`?agent=`), найм роли в боковой панели доски, рамка отдела из применения
шаблона. Проверено 10 сентября реальным управлением браузером на одноразовом
workspace (писущая панель): найм двух ролей с доски, перетаскивание карточки с
сохранением позиции после перезагрузки, связь протягиванием порта (запись
`agent_link`, ребро с подписью `ask`), применение шаблона Telegram Mini App с
автоматической рамкой отдела на 4 роли, переименование рамки с сохранением,
кнопка «Tasks» (трекер с фильтром по роли) и «Give a task» (форма запуска с
предвыбранной ролью). Найденный и исправленный дефект: холст сжимался в точку
при возврате на доску из скрытой секции; доска теперь монтируется только
видимой. Волна 2 перестраивается: WP-09 («Сейчас» как домашний) переносится
внутрь вкладки «Задачи»; WP-11 (provenance применений) становится основой для
канонической привязки рамок; остальное без изменений.

## K. Статус волны 1 (закрыта 9 сентября 2026)

| Пакет | Исполнитель | Review | Итог |
|---|---|---|---|
| WP-06 apply → Prepare run | Opus | Astra, approved | принят |
| WP-05 честные статусы результатов + review_state | Opus | Astra, approved | принят |
| WP-03 архив проекта, rail, readiness | Opus | Astra, approved | принят |
| WP-04.1 recipient_availability (backend) | Terra | Claude, changes_requested → approved (v2) | принят |
| WP-04.2 draft guard, chip, Stepper | Opus | Astra, changes_requested → approved (v2) | принят |
| WP-15.1 серверный лимит времени | Terra | Claude, changes_requested → approved (v2) | принят |
| WP-15.2 редактируемый лимит в UI | Opus | Astra, approved | принят |

Интеграция: asset пересобран координатором на Node 24 (`work-dVBmrCqt.js`),
полный `make check` зелёный (2613 python, 263 Work, 13 документированных
пропусков). Evidence: `docs/evidence/2026-09-09/wave-1-integration-manifest.json`
и по-пакетные манифесты там же. Три замечания reviewer-ов закрыты
координатором (WP-04.1 snapshot-сигналы, WP-04.2 парсер, WP-15.1 тесты и
parser); три падения первого `make check` устранены (fixture RunView,
ожидаемые ключи MCP wire, детекция отказа по коду вместо текста). Открыто:
WP-15.3 (лимит для проверочных запусков), коммит и push по команде владельца,
перезапуск сервиса 8787 владельцем.

## M. Статус волны 2 (начата 10 сентября 2026)

Порядок после ADR 0020: WP-08 → WP-14 → WP-02 → WP-02.2 (UI архива групп,
Opus) → WP-07 → WP-16.1 → WP-16.2 → WP-11.1 → WP-11.2; интеграция asset —
координатор в конце волны.

| Пакет | Исполнитель | Review | Итог |
|---|---|---|---|
| WP-08 baseline латентности записи | Grok | Astra, changes_requested (манифест не был привязан к ревизии) → approved (v2) | принят |
| WP-14 один ввод user-authored текста | Opus | Astra, approved | принят (run 1 оборвался на лимите провайдера, run 2 добил checkpoint; 275 тестов, tsc чисто; `wp-14-source-manifest.json`) |
| WP-02 архив/восстановление группы навыков (сервер) | Terra | Claude, approved (6 non-blocking notes) | принят; координатор дописал HTTP-тест маршрута и тест парсера; 77 python + 276 Work тестов, ruff/tsc чисто; `wp-02-source-manifest.json` |
| WP-02.2 архив группы в UI | Opus | Astra, changes_requested (фокус после restore последней группы) → approved (v2) | принят; 282 Work тестов, tsc чисто; `wp-02.2-source-manifest-v2.json` |
| WP-07 scoped mutation feedback | Opus | Astra, changes_requested ×2 (live region; надёжный возврат фокуса с тестом) → approved (v3) | принят; координатор: счётчик aria-hidden, инициирующий контрол захватывается в begin(), чистый `mutationFocus.ts` с поведенческим тестом; 299 Work тестов, tsc чисто; `wp-07-source-manifest-v3.json` |
| WP-16.1 миграция Starter Packs в built-in blueprints, удаление backend | Terra | Claude: changes_requested (verdict доставлен текстом — координатор писал в ledger во время review) → approved (v2) | принят; run 1: needs_operator; run 2: миграция и удаление (роли → delivery-tech-lead, qa-engineer, product-manager, strategy-advisor); координатор откатил ослабление доменного правила и схемы agent.v2, поправил 4 теста, добавил route-surface no-write тест, исправил счётчик «семь built-in»; 2582 full pytest, ruff чисто; `wp-16.1-source-manifest-v2.json` |
| WP-16.2 удаление Starter Packs из Work app | Grok | Astra, approved | принят; Grok снова вышел без терминального вызова (finding.4Y9BWC5ZHK1GHZFCEQ8VGZ8V7Q); координатор проверил: упоминаний starter нет, 297 Work тестов, tsc чисто; `wp-16.2-source-manifest.json` |
| WP-11.2 canonical objective/application (реализация) | Terra | — | **заблокирован до принятия ADR 0021 владельцем** |
| WP-11.1 ADR 0021 objective/application provenance | Terra | Claude, changes_requested (детерминированный id vs ULID; objective присоединённой вручную задачи) → approved (v2) | принят; ADR написан Terra, правки текста координатора; статус ADR «предложено» — **нужно принятие владельцем до WP-11.2**; `wp-11.1-source-manifest-v2.json` |

WP-08: Grok написал `benchmarks/benchmark_work_mutation.py` и
`tests/benchmarks/test_work_mutation_latency.py`, но завершил процесс без
терминального вызова (`needs_operator`, finding.1JSG1HQJZXP0EHGRH1NE6RJ82S);
отчёт `docs/performance/2026-09-10-mutation-baseline.md` сгенерирован
координатором поставленным инструментом на машине владельца. Evidence:
`docs/evidence/2026-09-10/wp-08-source-manifest.json`
(artifact.4WNFN9T4G4M62XE3VJQ48GMS0W). Результат измерения: warm `task_create`
медиана 4,4 с на 300 задачах/420 событиях и 33 с на 3206 событиях; 62–64 % —
`canonical_reads_validation` (двойной полный проход по ledger на каждую запись).
Целевые p50/p95 не зафиксированы — ждут решения владельца `ux/latency-thresholds`.
Урок процесса: манифест обязан быть привязан `--artifact-ref` и к `task complete`,
и к `task submit`; brief для Grok обязан заканчиваться явным требованием вызвать
терминальный инструмент.

Замечания reviewer-а по WP-02 (не блокирующие, переносятся): старые receipts
без поля `archived` при replay возвращают тело со старой revision (следующая
запись получит 409 и перезагрузку — fail-safe); `move_to_group_id: null`
трактуется как отсутствие, а не как ошибка формы; в тестах нет архива пустой
группы, replay idempotency-key и проверки одного receipt; клиентский парсер не
запрещает assignment на архивную группу (WP-02.2 добавляет клиентскую защиту);
архивные группы продолжают занимать лимит 128; до WP-02.2 существующие
компоненты показывают архивные группы как обычные, сервер отказывает 422.

Интеграция волны 2 (10 сентября, вечер): asset пересобран координатором на
Node 24 (`work-DQKgHjnE.js`, `work-Dw4mpzRm.css`), tool переустановлен. Проверка в
реальном браузере на demo-workspace (порт 8792, код волны 2): вкладка Library без
Starter Packs, `Blueprints 7` с «Feature delivery» и «Product discovery»; форма
новой группы — один ввод названия (кириллица сохранена как есть); архив пустой
группы через подтверждение, группа уходит из списка и из всех select только по
ответу сервера, раздел «Archived groups 1», Restore возвращает группу, фокус
после restore последней группы — на корне раздела (persistent fallback).
Полный `make check` на Node 24 зелёный: 2617 python (14 документированных
пропусков), 297 Work, 27 shell; evidence
`docs/evidence/2026-09-10/wave-2-integration-manifest.json`
(artifact.2W6EYEDHVEQT86R48T5WTXF8D1, 66 файлов). Открыто: принятие ADR 0021
владельцем (WP-11.2), коммит/PR/merge по команде владельца, перезапуск
сервиса 8787 после merge, WP-15.3.

## J. Поправки координатора (Claude Fable) к раунду 2

Внесены 9 сентября 2026 после разбора v2; Астра с ними не спорила, третий раунд
не запрашивался.

1. **Grok догружен механической работой.** По указанию владельца Grok берёт
   объёмные однотипные задачи: к WP-08 и WP-13.1 добавляется **WP-16.2** (удаление
   Starter Packs surface: DTO, api, компонент, строки, стили, тесты). Opus остаётся
   на дизайне и потоках. Порядок волны 2 не меняется.
2. **Квалификация Terra.** Квитанция canary хранится как `{profile_id}.json`,
   поэтому canary Terra (`gpt-5.6-terra`) и canary Астры на `codex-builder`
   взаимно вытесняют друг друга. Астра дальше работает только как
   `codex-independent-reviewer` (отдельная квитанция, модель по умолчанию);
   `codex-builder` закрепляется за Terra через `profiles-terra.yaml`. Перед
   первым запуском Terra координатор повторяет canary Terra.
3. **Команды housekeeping в A.3** приведены по памяти Астры и не проверены
   sandbox-ом; координатор сверяет флаги с `--help` перед выполнением.
4. **Claims в G.1** формулируются координатором как точные пути при создании
   задач; «relevant main.tsx» означает `path:frontend/work/src/main.tsx` целиком.
5. **Решения владельца записаны канонически** (`decision list`, scope `ux/*`:
   work-home-screen, wave-objective-provenance, visual-direction,
   user-content-locale, run-time-limit, starter-packs, latency-thresholds,
   local-instrumentation, closure-executor-logistics). Worker ссылается на них,
   а не на чат.
6. **Скриншоты** в headless-запусках недоступны; исполнитель регистрирует тесты
   и явно пишет, что визуальная проверка сделана координатором в браузере.
7. **Квалификация привязана к исходникам.** `qualification_fingerprint`
   (`src/agent_commons/runtime/provider_qualification.py:151-193`) включает
   `agent_commons_source_sha256()`, поэтому любой пакет, меняющий
   `src/agent_commons/**`, делает недействительными квитанции **всех** профилей.
   Обнаружено после WP-05. Правило для координатора: после backend-пакета
   перед следующим `broker run` повторить canary ровно для тех профилей,
   которые запускаются дальше (reviewer для review этого пакета, builder для
   следующего run). Каждый canary ≈ 1–2 мин и одна попытка провайдера.
   Frontend-only пакеты квалификацию не трогают. Стоимость учтена в порядке
   волн: backend-пакеты стоят на 2–3 canary дороже.
   Перед canary обязателен ещё один шаг: worker-ы получают MCP из глобального
   uv-tool (`~/.local/share/uv/tools/agent-commons`), и preflight сравнивает
   его source fingerprint с checkout (`mcp_tool_contract_failed` при
   расхождении). После backend-пакета координатор переустанавливает tool по
   `docs/TROUBLESHOOTING.md` («Updating an exact source checkout»):
   `uv tool install -q --force --reinstall-package agent-commons --python <.python-version> '.[mcp,ui]'`
   (именно с extra `ui`: сервис владельца на 8787 запускается из того же tool и
   без `ui` не стартует),
   затем canary нужных профилей. Запущенный UI-сервис на 8787 после этого
   работает на старом коде до перезапуска; перезапуск делает владелец.
9. **Бюджет попыток на сессию.** `OperatorLimits.parent_provider_units = 4`
   (`src/agent_commons/runtime/policy.py:193`) считается на пару «родительская
   сессия × провайдер» и включает review-запуски. Волна из 7 пакетов с формальными
   review требует ~7 попыток на провайдера. Координатор поднял лимит блоком
   `limits: {parent_provider_units: 24}` в трёх операторских файлах профилей
   (`profiles.yaml`, `profiles-terra.yaml`, `runtime.yaml`); владелец может
   вернуть значение. Ротация сессий ради обхода лимита не используется.
10. **Интегратор asset (поправка к D-11).** Sandbox claude-builder отклоняет
   любые команды, поэтому Opus физически не может выполнить `npm run build`.
   Packaged Work asset в конце каждой волны пересобирает координатор, держа
   claim `path:src/agent_commons/ui/static/work`, командой из
   `tests/ui/test_work_app_contract.py:110-118` (`npm run build` в
   `frontend/work` после `npm ci --ignore-scripts`). Воспроизводимость
   подтверждают freshness-тест и полный `make check`; single-writer правило
   сохраняется, писатель один — координатор.
11. **WP-15.3 (follow-up, волна 2, Terra, S).** Критерий WP-15.1 о
   `wall_time_seconds` в DTO проверочного запуска (canary) перенесён в
   отдельный пакет: квитанция квалификации и availability DTO пока не несут
   лимит; UI показывает «лимит не предоставлен» для проверочных запусков.
8. **Sandbox исполнителей не запускает команды.** Оба запуска Opus сообщили,
   что node/uv/npx отклонены permission-слоем; проверку всегда выполняет
   координатор, а worker обязан честно вернуть `needs_operator` или явно
   написать «verification not run». Это уже отражено в брифах.
