# Lane B — интеграции и agent runtime

## 1. Диагноз

Проверено по коду. Наш «трекер» — это не трекер. Таблица переходов (`src/agent_commons/domain/transitions.py`) держит 40+ типов событий, из которых задачи — лишь 11; остальное это review, finding, decision, handoff, artifact, delegation, agent. Ядро ценности лежит именно в нерабочих-для-Jira местах:

- **Ревью, привязанное к ревизии.** `lifecycle.py:49` `require_revision` — оптимистичная блокировка по `expected_revision`; `task_projection.py` выставляет `revision = event_id` и `effective_revision = _effective_correction_id or event_id`. Ревью «свежее» только при совпадении `review.target_revision` с текущей эффективной ревизией задачи (`review_projection.py: with_stale`, `acceptance.py: select_qualifying_review`).
- **Приёмка как записанное человеческое решение.** `lifecycle.py:310–370` для `task.accepted` требует одновременно: ссылку на approved-ревью, `independent is True`, совпадение `target_ref` с задачей, попадание `target_revision` в {effective_revision, revision} задачи, терминальный результат делегации с `purpose == "independent_review"` и актора ревью **вне** `work_author_session_ids`. Это семь инвариантов на одно событие.
- **Провенанс исполнителя.** `review_projection.py: with_producer_annotation` пишет `producer_agent_ids`, `producer_context_mode`, `producer_prior_verdict_count` — вычисляется реплеем истории, а не полем в форме.
- **Клеймы — это вообще не задачи.** `coordination/claims.py:113` — лизы на ресурсы (пути, exclusive/shared, `nonce`, `expires_at`, продление, all-or-none бандлы, детект пересечения по префиксу пути). В Jira/Linear такого примитива нет ни в каком виде.
- **Снимок читается атомарно.** `ui/tracker_reads.py: ObservedTrackerSource` сравнивает fingerprint до и после чтения и отдаёт `gap="source_revision_unavailable"` вместо рваного состояния. Граф зависимостей — `TrackerEdgeDTO` (prerequisite→dependent + `prerequisite_missing`), готовности и пробелы — `domain/execution_plan.py` (`DEPENDENCY_MISSING`, `TERMINAL_DEPENDENCY_FAILURE`, `DEPENDENCY_POLICY_UNKNOWN`).

Владелец прав в главном: то, что видно в UI как «задачи со статусами», **действительно** дублирует Linear/Jira. Но приёмка, независимость ревью, привязка к ревизии, клеймы и провенанс — нет. Не проверял: фронтенд (`frontend/work/src`) и качество UI-слоя — это lane A.

## 2. Варианты

**(a) Linear/Jira — источник истины, у нас только provenance/review/acceptance по external issue id.**
Синхронизация: вебхуки внутрь, наши события ссылаются на `{provider, issue_id, issue_updated_at}`. Конфликты: внешний трекер всегда прав по полям задачи, мы — по evidence.
Что ломается: `require_revision`. У задачи больше нет неизменяемой ревизии, которую можно связать с ревью — Linear отдаёт `updatedAt`, который меняется от смены метки или добавления комментария. Значит «ревью привязано к ревизии кода/задачи» деградирует до «ревью привязано к моменту времени», и `select_qualifying_review` теряет смысл: любая правка описания в Linear сделает approved-ревью stale, либо мы перестанем считать stale вообще. Второе — обещание «вывод провайдера никогда не истина»: агент с Linear-токеном может сам перевести issue в Done, и внешний трекер это примет как факт. Наша приёмка станет необязательной надстройкой, которую можно обойти через UI Linear.
API-факты: Linear GraphQL, API-ключ 2 500 запросов/час и 3 млн complexity-баллов/час, OAuth — 5 000/час и 2 млн баллов, один запрос ≤ 10 000 баллов; документация прямо не рекомендует polling. Вебхуки: HMAC-SHA256 (`Linear-Signature`), таймаут ответа 5 с, ретраи через 1 мин / 1 ч / 6 ч, затем вебхук может быть **автоматически отключён**, гарантии порядка не заявлены. Для связей есть `issueRelationCreate` (тип `IssueRelationType`, включая `blocks`), но по багрепорту он *конвертирует* существующую связь вместо добавления новой — read-before-write обязателен (источник неофициальный, отметить как непроверенное). Jira Cloud: с 2 марта 2026 включены points-квоты и burst-лимиты для Forge/Connect/OAuth 3LO (пул по умолчанию 65 000 баллов/час, GET ~100 rps, отдельный лимит на запись в один issue — 20/2 с и 100/30 с), 429 с `Retry-After` и `RateLimit-Reason`. Визуальный граф зависимостей у Jira — это Advanced Roadmaps, то есть **Premium/Enterprise**.
Стоимость: 4–6 недель. Локальность теряется полностью (офлайн работать нельзя), lock-in высокий.

**(b) Наш ledger — источник истины, зеркалим наружу.**
Синхронизация: одностороння, ledger → провайдер, через outbox (у нас уже есть `storage/idempotency.py` и `receipt_recovery.py`). Конфликты: правки в Linear игнорируются или помечаются как «внешний шум»; в крайнем случае reconcile-событие в ledger от имени человека.
Что ломается: почти ничего из обещаний — append-only цел, приёмка цела. Ломается ожидание владельца «задачи живут и обновляются там»: люди будут править Linear, а мы — откатывать. Это раздражает быстрее, чем помогает.
Стоимость: 2–3 недели на Linear (одно API), ~4 на Jira. Lock-in низкий, отключается за день.

**(c) Свой трекер остаётся; внешний — только view/export.**
Синхронизация: read-model → внешний формат. Дешёвые варианты: экспорт в Linear через периодический upsert, или вовсе не трекер, а Mermaid/`.dot`/CSV из уже существующего `TrackerEdgeDTO`.
Что ломается: ничего. Цена: 1–1.5 недели, lock-in нулевой. Теряем: «Jira с её графиком» как готовый продукт — граф всё равно рисуем сами (и в Jira он под Premium).

**(d) Гибрид, узкий.** Linear — источник истины **только для входящего беклога и обсуждений с людьми**; как только задача взята агентом, она импортируется в ledger по `external_ref` и дальше живёт у нас; в Linear уходит только статус-комментарий. 3–4 недели, но требует явного «переход границы» события.

## 3. Рекомендация

**(c) сейчас, (d) как опция позже.** Пивот в (a) стоит дороже, чем UI-проблема, которую он якобы решает: жалоба владельца — «выглядит как Streamlit», а не «не хватает полей задачи». Внешний трекер не нарисует за нас ни граф (Jira — Premium), ни приёмку.

Первые три шага: (1) экспорт графа из существующего `build_tracker_snapshot` в Mermaid/DOT/CSV — один read-only endpoint, ноль изменений в домене; (2) добавить в payload задачи необязательное поле `external_ref {provider, id, url}` и показать его как ссылку — это делает (d) возможным без переписывания истории; (3) прототип Linear-импорта одной задачи за час работы, чтобы измерить, сколько реально стоит (a), прежде чем обсуждать его.

## 4. Что переиспользовать

Уточнение владельца: переиспользуем **идеи и практики**, а не тянем библиотеки в зависимости. Ниже для каждого кандидата — конкретный паттерн и вердикт: *перенять паттерн* / *обернуть библиотеку* / *написать своё*.

**LangGraph — перенять три паттерна, библиотеку не тянуть.**
1. *Interrupt как первоклассное состояние, а не флаг.* В LangGraph `interrupt` останавливает граф, сохраняет состояние через persistence layer и ждёт `Command(resume=...)` неограниченно долго. У нас `awaits_human` живёт в read-model (`tracker_dtos.TrackerTaskDTO`), а в ledger есть только `delegation.input_needed` → `delegation.resumed` (`transitions.py`). Перенять: то же «ожидание» для задач и решений, отдельным событием, чтобы «ждём человека» было фактом истории, а не производной вычисления. Своё, ~3 дня.
2. *Checkpoint на каждом суперстепе с ключом треда.* LangGraph пишет состояние по `thread_id` на каждом шаге; `InMemorySaver` рестарт не выживает. У нас durability уже строже — append-only ledger + `runtime/attempts.py` (`AttemptState`: reserved → launching → running → succeeded/failed/cancelled/timed_out/**needs_operator**) + `checkout_fingerprint`. Здесь заимствовать нечего: наш инвариант сильнее.
3. *Сериализуемый resume-пакет.* То, что LangGraph кладёт в прерванное состояние (approvals, usage, tool input, вложенные resume, trace metadata) — это готовый чек-лист полей для наших handoff-envelope'ов (`domain/thread_handoff_envelopes.py`). Перенять как список полей, не как формат.

**OpenClaw — перенять один паттерн, брокер не заменять.**
Это self-hosted gateway, мультиплексирующий WebSocket/HTTP на одном порту, с 20+ мессенджер-каналами и heartbeat-демоном; вокруг него уже накопился корпус security-разборов (arXiv 2603.10387, 2603.27517, 2605.23330) — сам факт такого корпуса и есть аргумент не брать его внутрь. Наш `runtime/broker.py` (273 строки) специально узкий: `idempotency_key`, `launch_key_sha256`, `role_tools` (может только *сужать* набор инструментов профиля), `role_grants`, `purpose ∈ {implementation, independent_review, verification}` и `instruction`, который «never written by the runtime». Перенять стоит **разделение «канал доставки» / «агентный цикл»**: у нас уведомления о `needs_operator` живут только в UI, и при закрытом ноутбуке человек их не видит. Написать свой тонкий notifier (Telegram/почта) на 100–150 строк — да; ставить gateway с 20 каналами — нет. Идею heartbeat-демона (проактивный тик вместо ожидания запроса) взять для реконсиляции «повисших» attempt'ов.

**Hermes Agent — перенять идею профиля-как-файла; остальное уже есть.**
По описанию профиль = отдельный агент со своим `config.yaml`, идентичностью `SOUL.md`, SQLite-памятью, gateway-процессом и cron. **Источники — блоги, не первичная документация; отмечаю как непроверенное.** Полезны две практики: (i) *идентичность агента отдельным версионируемым документом* — прямо ложится на пункт 4 брифа (`.claude/agents` пресеты) и на `domain/roles.py`; (ii) *lineage-based context compression* и «сессия как инфраструктура» — как дисциплина для `domain/context_pack.py`, где компакция должна оставаться проекцией контекста, а не подменять чекпойнт. Заменять он мог бы только `runtime/model.py: ProfileRegistry`, что у нас и так тривиально; проверки независимости и привязки к ревизии в `services/delegation_runtime.py` (1 972 строки) у Hermes отсутствуют как класс. Вердикт: перенять практику, ничего не оборачивать.

**Где уместно именно обернуть библиотеку** (не домен, только периферия): ReactFlow/ELK для layout графа вместо ручной раскладки; готовый SSE/resume-клиент вместо самописного в `tracker_reads.py`; Pydantic-модели для внешних API. Все три не трогают ledger и снимаются за день.

## 5. Чего не делать

Не делать двусторонний sync с Linear/Jira — это dual write без стратегии реконсиляции, при вебхуках без гарантии порядка и с авто-отключением. Не давать агентам провайдерский токен на запись в трекер: это одной строкой убивает «вывод провайдера никогда не истина». Не переносить приёмку и независимость ревью в кастомные поля Jira — там нет ни ревизии, ни проверки «не автор». Не переписывать брокер на OpenClaw/Hermes: мы заменим 273 аудируемые строки на чужой gateway и всё равно допишем свои проверки. И обратное: не писать своё там, где обёртка дешевле — layout графа и SSE-resume мы уже написали руками зря. Не строить граф на Jira Premium, пока не проверено, что владелец готов за Premium платить.

## 6. Открытые вопросы владельцу

1. **Кто должен закрывать задачу — человек в UI или трекер?** (a) только наша приёмка, трекер read-only; (b) трекер может закрыть, приёмка становится рекомендацией; (c) двойной статус (внешний «Done» + наш «accepted»).
2. **Есть ли в проекте люди, кроме вас, которым нужен Linear/Jira UI?** (a) нет — тогда вариант (c) закрывает вопрос; (b) да, 2–5 — (d); (c) да, внешняя команда — только тогда (a) оправдан.
3. **Jira или Linear, если интегрировать?** (a) Linear — одно GraphQL API, вебхуки с HMAC, дешевле в 2 раза по работе; (b) Jira — граф «из коробки», но Premium и три независимых механизма лимитов; (c) ни то ни другое, экспорт файлом.

## Источники

- Код: `src/agent_commons/domain/{transitions,lifecycle,acceptance,task_projection,review_projection,delegation_projection,execution_plan}.py`, `src/agent_commons/ui/{tracker_reads,tracker_dtos}.py`, `src/agent_commons/coordination/claims.py`, `src/agent_commons/runtime/{broker,attempts}.py`.
- [Linear — Rate limiting](https://linear.app/developers/rate-limiting), [Linear — Webhooks](https://linear.app/developers/webhooks), [Linear — GraphQL getting started](https://linear.app/developers/graphql)
- [issueRelationCreate конвертирует существующую связь (неофициально)](https://github.com/CodySwannGT/lisa/issues/3605)
- [Jira Cloud — Rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/), [Jira Cloud REST v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/), [Dependencies report in Advanced Roadmaps](https://support.atlassian.com/jira-software-cloud/docs/refine-the-dependencies-report-in-advanced-roadmaps/)
- [LangGraph — Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [LangGraph durable runtime (ZenML)](https://www.zenml.io/blog/langgraph-durable-runtime)
- [OpenClaw security analysis (arXiv 2603.27517)](https://arxiv.org/pdf/2603.27517), [Defense framework for OpenClaw (arXiv 2603.10387)](https://arxiv.org/pdf/2603.10387), [OpenClaw production guide (блог)](https://www.contextstudios.ai/blog/the-complete-openclaw-guide-how-we-run-an-ai-agent-in-production-2026)
- [Hermes Agent (сайт проекта)](https://hermes-agent.org/), [Hermes agent harness architecture (Arize)](https://arize.com/blog/how-hermes-implements-open-source-agent-harness-architecture/) — низкая авторитетность, отмечено как непроверенное
