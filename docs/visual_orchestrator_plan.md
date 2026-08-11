# План: Agent Commons → Visual Multi-Agent Orchestrator

Синтез четырёх независимых разборов (product, design, LLM-engineering, architecture)
поверх `docs/visual_multi_agent_orchestrator_prd.md`. Все выводы привязаны к
фактическому коду, а не к идеализированной модели PRD.

---

## 0. Решения одним списком

| Вопрос | Решение |
|---|---|
| Canvas — source of truth? | **Нет.** Org DSL — артефакт с immutable revision в ledger. Canvas — редактор и проекция. Явный `Publish`. Run биндит `org_revision`. |
| Главный экран | **Design → canvas. Run → timeline.** Не один экран в двух режимах — разные первичные поверхности. |
| Бэкенд | **Python 3.12 + FastAPI + asyncio.** Без второго языка, без гибрида. |
| Хранилище операционных событий | **SQLite WAL**, disposable, под `.git/agent-commons-state/`. Не Postgres. |
| Транспорт в UI | **SSE + `Last-Event-ID` = монотонный `seq`.** Команды — обычные POST. |
| Canvas-библиотека | **React Flow (xyflow) v12** + elkjs + zustand. |
| Дизайн-система | **Tailwind + shadcn/ui (Radix) + Lucide.** |
| Запуск UI | `agent-commons ui` — один процесс, SPA вкомпилирован в wheel, loopback + токен. |
| Мульти-фреймворк адаптеры | **Вырезать полностью.** Только Codex CLI и Claude CLI. |
| Параллельные writable-агенты | V1, через worktree-per-agent с явным подтверждением. Не MVP. |

---

## 1. Шесть фактов из кода, ломающих наивное прочтение PRD

Принять до начала проектирования. Иначе UI будет врать пользователю.

| Допущение PRD | Реальность | Файл |
|---|---|---|
| Агент — долгоживущий процесс с памятью | Агент = **delegation**: одна ограниченная попытка, отдельная child-session, resume невозможен | `services/delegation_runtime.py`, `PROTOCOL.md §9` |
| Tools/MCP настраиваются галочками | Наборы tools **зафиксированы кодом** по `profile_id` + `purpose`. Reviewer получает `--tools ""` + жёсткий disallow-list | `runtime/model.py::_worker_tools` |
| Десятки агентов работают параллельно | `global_concurrency: 2`, `profile_concurrency: 1`, один writable worker на checkout | `runtime/policy.py` |
| Трёхуровневая оргструктура запускается | `remaining_depth` по умолчанию **1** | `runtime/policy.py` |
| Inspector показывает тела сообщений и tool-аргументы | Телеметрия **metadata-only by design**; промпты, транскрипты, аргументы исключены | `runtime/telemetry.py`, `THREAT_MODEL.md` |
| Есть Tokens/Cost | Учёта токенов **нет вообще** — только `stdout_bytes_seen`. У Codex `supports_budget = False` | `runtime/telemetry.py`, `runtime/model.py` |

Седьмой факт, которого в PRD нет вовсе, но он важнее половины его содержимого:
**каскад staleness.** Ревизия артефакта → биндинги таска протухают → ревью
протухает → acceptance перестаёт быть эффективным → таск откатывается в `review`.
На графе при этом всё выглядит зелёным. Это единственное, что в этом продукте
**обязано** быть визуальным.

---

## 2. Разрешение противоречий

### 2.1 «Canvas является source of truth» (PRD §3, §10)

Прямо противоречит `PROTOCOL.md §8`: графы и борды — rebuildable projections,
никогда не независимый источник правды. И `ARCHITECTURE.md`: MCP и CLI —
адаптеры к одному менеджеру, второго write path не существует.

**Разрешение — три домена состояния с разными владельцами:**

| Домен | Что | Source of truth | Где живёт |
|---|---|---|---|
| Design-time | Org DSL: агенты, команды, рёбра, permissions, workspaces | **Ledger** — DSL это артефакт с immutable revisions | `org/*.yaml` + manifests |
| Canonical runtime | Runs, делегации, задачи, threads, decisions | **Ledger** | `.agent-commons/events/` |
| Operational | Статусы, tool-calls, токены, spans, координаты нод | Rebuildable, неавторитетно | `orchestrator.sqlite3` (WAL) |

Формулировка для PRD: **canvas — редактор DSL и проекция ledger'а.** Координаты,
зум, цвета — UI-state, теряются без последствий. Run при старте биндит
`org_revision` ровно как делегация биндит `target_revision`. Правка canvas во
время run не меняет топологию запущенного run — идёт в draft, применяется к
следующему.

Перед `Publish` — диалог влияния: «это протухнет 2 ревью и 1 acceptance».

### 2.2 Workspace: переиспользовать threads или строить новый store?

Расхождение между агентами. Архитектор: workspace = существующие typed threads,
отдельного message store быть не должно. LLM-инженер: нужен
`runtime/workspaces.py` с новыми событиями `workspace.*` и content-addressed
телами.

**Разрешение — архитектор прав по инварианту, LLM-инженер прав по механике:**

- Workspace — это **именованный scope поверх существующих threads**, а не новый
  store. Никаких параллельных write path.
- Тела сообщений — в существующем content-addressed store (`storage/manifests.py`
  это уже умеет), в ledger только `body_sha256` + `body_tokens`.
- Новое — только `ContextPolicy` (`summary + last N`) и membership/ACL как поля
  scope'а.
- `body_tokens` считается на записи: даёт честную цену канала в Inspector без
  повторной токенизации при каждой сборке контекста.

**Коллизия имён, которую надо развести до MVP:** существующие MCP-тулы
`commons_workspace_files/read/search` работают с **файлами репозитория**, а не с
Workspace из PRD. Переименовать в `commons_repo_*` либо зафиксировать различие в
документации явно.

### 2.3 Communication Inspector с полными трассами (PRD §5)

Невозможен: канонический ledger и телеметрия намеренно не хранят промпты,
reasoning, tool payloads. Реализовать «как в PRD» — либо нарушить privacy-периметр
(риск утечки credentials/PII на canvas), либо держать контент эфемерно вне ledger
и врать пользователю про durability.

**Разрешение — трёхуровневая визуальная иерархия достоверности, постоянно
видимая в UI:**

```
CANONICAL     (ledger, immutable)      полный текст thread-сообщений, verdicts,
                                       evidence-ссылки, ревизии
COORDINATION  (communication store)    request/progress/blocker/guidance/checkpoint,
                                       bounded metadata ≤4KB, состояния, дедлайны
OPERATIONAL   (runtime journal + otel) метаданные: старт/финиш процесса, attempt,
                                       reason_code, queue, счётчики терминальных тулов
                                       ⓘ No prompts, transcripts, or tool arguments
                                         are retained by design.
```

Плашка обязана быть в интерфейсе, не в справке. Иначе первый разбор инцидента
заканчивается фрустрацией «где логи».

### 2.4 Параллелизм из PRD §8 (Backend и Frontend одновременно)

Невозможен в одном checkout: инвариант «один writable worker на checkout scope».

**Разрешение:** worktree-per-writable-agent с явным подтверждением в UI. Инвариант
«broker never creates worktrees» сохраняется — провижининг делает
OrchestratorService, git-операции остаются explicit. Merge результатов — руками
пользователя. **Это V1, не MVP.** В MVP ограничение показывается, а не прячется.

---

## 3. Фичи

### MUST

1. **Agent как персистентная конфигурационная сущность** — обёртка над broker
   profile: name, role, model ref, allowlisted MCP. Без первоклассного «агента»
   нечего рисовать.
2. **Read-only canvas поверх существующего ledger** — session/task/delegation →
   nodes/edges. Дешёвая проверка главной гипотезы продукта.
3. **Live-статусы из существующего delegation lifecycle** — данные уже есть,
   нужен транспорт.
4. **Task launch из UI** через существующие `task create` + `delegation
   create/start`. Без этого canvas — картинка.
5. **Streaming seam в `subprocess_runner.py`** — построчный колбэк. Блокер №1:
   сейчас вывод виден только после exit, live-canvas физически невозможен.
6. **Capacity/queue UI** — виджет ёмкости, статус `queued` с позицией, дорожка
   queue depth. Без этого пользователь решит, что продукт сломан.
7. **Санитизированная timeline** на основе thread/handoff/progress-blocker событий.

### HIGH

- Team-контейнер (плоский, без глубокой вложенности).
- Permissions/MCP на уровне агента — **выбор из уже allowlisted профилей**, не
  свободный ввод.
- Учёт токенов и стоимости (новый `runtime/usage.py` + per-provider декодеры).
- Staleness как визуальный канал + Truth board с lineage-графом.
- Multi-level hierarchy (depth > 1) — требует отдельного ADR.
- Повышение concurrency сверх 2 — отдельная инженерная работа над брокером.

### LOW

Сложная аналитика; визуальный prompt builder; marketplace; mobile UI;
собственная inference-инфраструктура. Первые три PRD исключает сам.

### ANTI — что вредно

1. **Canvas как прямой мутатор runtime topology.** Второй write path, который
   архитектура целенаправленно запрещает. См. §2.1.
2. **Communication Inspector с полными телами и tool-аргументами.** См. §2.3.
3. **Продажа «организации из десятков агентов»** поверх рантайма, который сам
   себя называет experimental и ограничивает одним writable worker. Потеря
   доверия на первой реальной задаче.
4. **Мульти-фреймворк адаптеры (AutoGen / Agents SDK / LangGraph).** Каждый — свой
   versioned runner contract, свой стриминговый формат, свой security-анализ.
   Утраивает работу при нулевой проверке ключевой гипотезы. И это ровно то, что
   LangGraph Studio делает лучше нас.
5. **Autonomous agent creation.** Противоречит governance-модели, где агент
   никогда сам не расширяет полномочия. Не переносить даже в V1 без отдельного
   протокольного решения.
6. **Богатый RBAC/billing.** Преждевременно для проекта, где MVP-0 даже не
   аутентифицирует роль сессии.
7. **Живая перестройка топологии во время run.** Конфликтует с revision-binding.
8. **Чекбоксы tools/MCP.** Профили фиксированы кодом — интерфейс будет обещать
   то, чего нет.

---

## 4. UI/UX

### 4.1 Информационная архитектура

Орг-граф и граф причинности — **разные графы**. Попытка нарисовать второй поверх
первого убивает большинство визуальных оркестраторов: canvas великолепен на демо
с шестью нодами и бесполезен при разборе инцидента.

```
ВОПРОС ПОЛЬЗОВАТЕЛЯ                  ЛУЧШАЯ ФОРМА
кто кому подчиняется                 граф
кто с кем может говорить             граф
какой контекст видит Backend         граф + overlay достижимости
что сейчас происходит                timeline
почему упал агент                    timeline + tree
почему acceptance слетел             lineage-граф (другой граф!)
что требует меня прямо сейчас        список
```

При `global_concurrency: 2` анимированный orgchart показывает две светящиеся ноды
из тридцати — информационная плотность около нуля.

**Пять экранов, ничего больше в MVP:**

1. **Org Canvas** (Design) — авторинг топологии.
2. **Run Console** (Run) — timeline снизу (главное), attention queue справа,
   topology minimap.
3. **Workspace view** — thread-reader со своим URL.
4. **Truth board** — tasks/reviews/findings/decisions + staleness-каскад.
5. **Runtime settings** — read-only зеркало operator-owned `runtime.yaml`.

Inspector — **один полиморфный компонент** на все типы сущностей.

### 4.2 Canvas: ноды и рёбра

Четыре типа нод, различаемых **формой**, а не цветом (colorblind-safe, читаемо на
zoom 0.4): agent — скруглённый прямоугольник с левым provider-бордюром; team —
двойная рамка с заголовком-полкой; workspace — капсула; resource/MCP —
шестиугольник в правой «resource rail».

Три независимых визуальных канала: **форма** = тип, **цвет-акцент** = provider
(claude / codex), **глиф** = статус. Четвёртый, ортогональный — **штриховка** =
staleness.

Ресурсы не разбросаны по canvas (иначе спагетти из N×M рёбер), а живут вертикальной
полосой справа; связь агент→ресурс — тонкий пунктир только при hover/select. Снимает
60–70% визуального мусора без потери информации.

**Два типа рёбер, различаемых тремя признаками:**

```
CONTROL (delegation)                  COMMUNICATION (workspace access)
сплошная, стрелка-треугольник         двойная линия, без стрелки
ортогональная маршрутизация           безье
порты: bottom → top (вертикаль)       порты: left/right (горизонталь)
                                      всегда лейбл-капсула с именем workspace
```

Разделение портов по осям — самый дешёвый приём: **невозможно случайно нарисовать
не то ребро**, и граф автоматически читается как orgchart с горизонтальными
каналами.

**Создание нод — четыре пути в одно действие:** ⌘K (первичный для dev-аудитории);
клавиатура (`Tab` = дочерняя нода + control-edge сразу, `Enter` = сиблинг —
организация строится за 30 секунд); палитра drag-and-drop; drag из порта в пустоту.

**Валидация — inline, не модалка.** При старте drag весь canvas переходит в
connect mode: валидные цели полностью непрозрачны, невалидные — opacity 0.35.
Ошибка предотвращается, а не сообщается.

Ключевое правило: **canvas не может выразить то, чего рантайм не может
исполнить.** Порт для связи, которую профиль не поддерживает, физически не
рендерится.

**Depth ruler** — горизонтальная шкала `D0 / D1 / D2` слева; всё ниже текущего
лимита уходит в приглушённую зону с плашкой «beyond delegation depth».
Пользователь видит ограничение **до** запуска.

**Auto-layout — гибрид:** `⇧L` тидит текущий контейнер (elk.layered, только внутри
границ), `⇧⌥L` — всю организацию, флаг `pinned` защищает ноду. Полный автолейаут
отбирает пространственную память, полностью ручной превращается в кашу после 20 нод.

### 4.3 Design vs Run

|  | Design | Run |
|---|---|---|
| топология | редактируемая | заморожена, правки → draft |
| статусы | нет | ○◐●✓✕⚠ + чипы попыток |
| правая панель | Validation | Attention queue |
| нижняя панель | — | Timeline (главная, 240px) |

**Attention queue** заменяет 90% сценариев «смотрю на граф и жду, когда
замигает»: отсортированный список того, что требует человека —
`needs_operator` > `blocker` > `input_needed` > `changes_requested` > `stale
acceptance`.

**Бюджет анимации: не более одного анимированного элемента на экран.**
Анимируется только текущая активная lineage. Свёрнутый team показывает activity
ring (сегменты = доли статусов) — ноль анимации, полная информация. Толщина
communication-ребра = число операций за окно, насыщенность = свежесть: «где
горячо» видно при полностью статичном canvas.

**LOD по zoom:** `>0.8` full, `0.4–0.8` compact (имя + глиф), `<0.4` chip (точка +
имя контейнера, рёбра агрегируются).

### 4.4 Inspector: Claude vs Codex

Первый вопрос при создании агента — **провайдер**, потому что от него зависит вся
форма. `profile_id` — производное поле, не редактируемое.

```
PROVIDER   [ Claude ] [ Codex ]
PURPOSE    [ Builder ] [ Independent reviewer ]
           → claude-builder | claude-independent-reviewer
             codex-builder  | codex-independent-reviewer
```

| Только Claude | Только Codex |
|---|---|
| Permission mode (`acceptEdits`/`dontAsk`/`plan`); для reviewer **принудительно** `dontAsk` | Sandbox (`read-only`/`workspace-write`); для reviewer **принудительно** `read-only` |
| Budget USD — `supports_budget = True`, `--max-budget-usd` | Budget **скрыт**, плашка: «Codex CLI cannot enforce a monetary budget» |
| | Provider units — **required** |
| | Approval policy — read-only чип `never` |
| | Trusted workspace — required даже для reviewer |

Разница решается **одной формой с адаптивной секцией Execution**, не двумя
разными формами — иначе пользователь не увидит, что именно отличается.

**Capabilities — read-only с замком.** Важнее списка разрешённого — явный список
**запрещённого**: пользователь ищет ответ на «может ли он испортить репозиторий».

```
▾ CAPABILITIES                              🔒 fixed by profile
  Commons tools    12    Outcome tools   8    Review tools   2
  Native tools     ✕  Bash Read Glob Grep Edit Write Agent Web*
  ⓘ Change requires the operator runtime profile config.
```

**Прогрессивное раскрытие:** сразу пять полей (Name, Provider, Purpose, Model,
Reports to) — этого достаточно для валидной ноды. Один клик — instructions и
лимиты. Два — capabilities и context. Advanced — пути к бинарям, трогают раз в
жизни.

### 4.5 Дизайн-система

**Tailwind + shadcn/ui (Radix) + Lucide + cmdk.**

Обоснование под canvas-heavy инструмент:

1. **Нулевая рантайм-стоимость стилей.** Mantine/MUI используют runtime CSS-in-JS
   (emotion) — при 60fps пане/зуме с сотней DOM-нод это заметный overhead.
   Tailwind — статический CSS.
2. **Токены как CSS-переменные.** React Flow-ноды, SVG-рёбра, minimap и обычный UI
   читают одни и те же `--color-*`. Смена темы = смена переменных на `:root`, без
   React-рендера.
3. **Copy-in source.** Дефолтные высоты shadcn слишком воздушные для инспектора с
   40 полями — нужен свой compact-scale. С Mantine/MUI это борьба с библиотекой.
4. **Radix закрывает самое дорогое** — фокус-менеджмент, порталы, ARIA,
   клавиатурная навигация. Делает keyboard-first флоу реальным.

**Токены — цвет в OKLCH** (стабильная воспринимаемая светлота):

```
                LIGHT                   DARK
--bg-canvas     oklch(98% 0.002 250)    oklch(17% 0.008 250)
--bg-surface    oklch(100% 0 0)         oklch(21% 0.008 250)
--border        oklch(91% 0.004 250)    oklch(31% 0.010 250)
--text-primary  oklch(24% 0.010 250)    oklch(95% 0.004 250)
--accent        oklch(56% 0.180 258)    oklch(70% 0.160 258)
```

Панели «всплывают» над полотном: `bg-canvas` темнее `bg-surface` в тёмной теме,
инвертировано в светлой.

**Статусная палитра** — каждый статус имеет глиф, цвет никогда не единственный
носитель:

```
idle ○  active ◑  input_needed ◐(пульс)  succeeded ✓  failed ✕
timed_out ⏱  needs_operator ⚠(пульс)  cancelled ⊘  stale ▨(штриховка)
provider:claude — левый бордюр 295°   provider:codex — 60°
```

**Типографика:** Inter var + JetBrains Mono, базовый размер **13px** (не 14/16) —
инспектор с 40 полями и плотный timeline; dev-аудитория к этому привычна.
`tabular-nums` везде, где числа.

**Spacing** — шаг 4px, разрешены только `4 8 12 16 24 32 48`. Высота контрола 28px.

**Тени — только две**: панель над canvas и выделенная нода. Никаких теней на
обычных нодах: 50 теней = 50 композитных слоёв.

### 4.6 Canvas-библиотека

**React Flow (xyflow) v12 + elkjs + zustand.**

| Требование | React Flow | tldraw | Rete | Своё на Pixi |
|---|---|---|---|---|
| Вложенные контейнеры | ✅ `parentId` + `extent` | ⚠ freeform-модель | ✗ | ✗ |
| Ноды как DOM (формы, a11y) | ✅ | ⚠ ограничено | ✅ | ✗ |
| Точечный live-update | ✅ `updateNodeData` | ⚠ | ⚠ | ручное |
| Автолейаут вложенности | ✅ elkjs | — | — | — |
| Лицензия | MIT | ⚠ watermark/коммерческая | MIT | — |
| Время до MVP | низкое | среднее | среднее | очень высокое |

Решающее: ноды — DOM, значит настоящие контролы, фокус и ARIA внутри нод.
`parentId`/`extent` покрывают team-контейнеры без изобретательства.

Осознанно принимаемое ограничение: React Flow не для тысяч нод с непрерывной
анимацией. Наш бюджет анимации (§4.3) это соблюдает by design. Если появятся
сценарии >800 нод — заменяется слой рендера рёбер (canvas-оверлей под DOM-нодами),
не вся библиотека. Решение обратимо.

---

## 5. Бэкенд и стек

### 5.1 Python, без гибрида

**Нагрузка:** локальный однопользовательский инструмент, 10–20 одновременных
агентов, 5–50 JSONL-событий/с на агента → пик ~1000 событий/с, типично 50–200/с.

**Где Python не узкое место:** asyncio на одном ядре — 50–100k мелких сообщений/с;
`json.dumps` события 1–5 µs; SSE fan-out на 5 вкладок при 1k событий/с — <2% ядра;
SQLite WAL батчами — >50k insert/с. Оркестрация субпроцессов — чистый I/O-wait.

**Где станет:** полнотекстовый поиск (решается FTS5 внутри SQLite) и реплей 10⁶+
событий (решается snapshot+tail). CPU-bound частей в задаче нет — LLM считает в
чужих процессах.

**Гибрид Go/Rust отвергнут:** выигрыш ~2–3% одного ядра против второго toolchain,
второго релизного артефакта, IPC-границы и дублирования схем. Доменная логика
(`CommonsManager`, 2477 строк) уже на Python.

### 5.2 Хранилище: SQLite WAL

Postgres — серверная зависимость в локальном инструменте, убивает «запуск одной
командой»; конкурентный писатель всего один. DuckDB — OLAP, слаб на мелкий
конкурентный append. LMDB — нет SQL/FTS, а нужны запросы «события run между seq X
и Y типа tool_call».

`synchronous=NORMAL`: потеря хвоста операционных событий при power-loss приемлема
— канонические вехи защищены fsync ledger'а.

### 5.3 Архитектура

```
                Browser (React + React Flow)
                  │ REST (команды)   ▲ SSE (Last-Event-ID = seq)
                  ▼                   │
┌──────── agent-commons ui (один процесс, FastAPI/uvicorn) ──────────┐
│  HTTP API ──▶ OrchestratorSvc ──▶ RunEventStore (SQLite WAL) ──▶ SSE│
│  (токен)      (asyncio)          append-only, seq per run           │
│                   │                                                 │
│         thread-pool bridge (sync broker в executor)                 │
│                   ▼                                                 │
│  ┌────────── СУЩЕСТВУЮЩЕЕ ЯДРО (не форкается) ──────────┐          │
│  │ CommonsManager ──▶ Event/Manifest Ledger  ◀ TRUTH     │          │
│  │ LocalBroker ──▶ exec gate ──▶ provider CLI            │          │
│  │ AttemptStore (fsync journal, reconcile)               │          │
│  └───────────────────────────────────────────────────────┘          │
│                   │ stdout JSONL (streaming tap — новое)            │
│                   ▼                                                 │
│    Codex / Claude CLI субпроцессы (process groups, worktrees)       │
└─────────────────────────────────────────────────────────────────────┘
```

**RunEventStore — не второй ledger.** Operational-проекция уровня attempt journal,
живёт под `.git/agent-commons-state/`, игнорируется git, может быть удалена.
Канонические вехи пишутся в ledger через `CommonsManager` как сейчас; RunEventStore
хранит высокочастотный шум и **ссылается** на канонические `event_id`.

**UI никогда не пишет в ledger напрямую** — только через API → `CommonsManager`.
Ни одного нового write path.

### 5.4 Процессная модель

- **Мост sync→async:** `LocalBroker.run()` блокирующий. MVP — bounded
  `ThreadPoolExecutor` (размер = лимит concurrency). Канонические записи
  сериализуются через одну asyncio-очередь к `CommonsManager` (write-lock и так
  глобальный).
- **Streaming tap** — новый `StreamingSubprocessRunner`: тот же контракт (gate,
  bounded output, process-group termination) плюс колбэк на каждую JSONL-строку.
  ~30 строк, но без него live-canvas невозможен.
- **Graceful shutdown:** SIGTERM → отменить `requested` → SIGTERM группам, grace
  **5–10 с** (сейчас 2 с — мало для CLI-провайдеров) → SIGKILL → reconcile.
  Незавершённое → `needs_operator`, не `cancelled`.
- **Восстановление после краха:** `AttemptStore.reconcile()` уже fail-closed
  сверяет launch token / fingerprint / PID и никогда не перезапускает потенциально
  живой процесс. При старте: reconcile → перечитать open runs → осиротевшие
  показать как `needs_operator` с PID и кнопкой operator-stop. **Не** пытаться
  auto-resume: headless-провайдеров нельзя reattach.

### 5.5 Схема данных

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
    org_ref TEXT NOT NULL, org_revision TEXT NOT NULL,   -- immutable binding
    root_target TEXT NOT NULL, canonical_event_id TEXT,
    state TEXT NOT NULL CHECK(state IN
      ('created','running','stopping','completed','failed','needs_operator')),
    created_at TEXT NOT NULL, finished_at TEXT
);

CREATE TABLE run_events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL,   -- монотонный, единственный писатель
    ts TEXT NOT NULL, node_id TEXT NOT NULL,
    kind TEXT NOT NULL,      -- status|tool_call|token_usage|span_start|span_end|error
    payload TEXT NOT NULL,   -- для вех: {event_id}, содержимое в ledger
    PRIMARY KEY (run_id, seq)
) WITHOUT ROWID;

CREATE TABLE spans (
    run_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_span_id TEXT,
    node_id TEXT NOT NULL, kind TEXT NOT NULL,   -- delegation|tool|wait|review
    started_seq INTEGER NOT NULL, ended_seq INTEGER, attrs TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, span_id)
);

CREATE TABLE run_snapshots (
    run_id TEXT NOT NULL, upto_seq INTEGER NOT NULL,
    state_json TEXT NOT NULL, PRIMARY KEY (run_id, upto_seq)
);

CREATE TABLE canvas_layout (   -- чистый UI-state, неавторитетно
    org_ref TEXT NOT NULL, node_id TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL, meta TEXT, PRIMARY KEY (org_ref, node_id)
);
```

**Реплей:** состояние графа = чистая left-fold `reduce(state, run_event)`. Те же
редьюсеры на бэке для снапшота и на фронте для live. Снапшот каждые 1000 событий;
подключение UI = `snapshot(upto_seq)` + SSE с `Last-Event-ID`.

### 5.6 Org DSL

```yaml
schema: commons.org.v1
org_id: org.engineering
agents:
  - id: agent.backend
    profile: codex_builder            # ТОЛЬКО имя профиля из operator allowlist
    model_hint: gpt-5.x               # hint, не argv
    instructions_ref: {artifact: art.backend_prompt, revision: rev.7}
    reports_to: agent.tech_lead
    policy: {timeout_seconds: 3600, provider_units: 3, max_depth: 1}
    workspaces: [ws.eng]
teams:
  - id: team.eng
    supervisor: agent.tech_lead
    members: [agent.backend, agent.frontend, agent.reviewer]
edges:
  - {kind: control, from: agent.tech_lead, to: agent.backend}
  - {kind: communication, from: team.product, to: team.eng, workspace: ws.pe}
workspaces:
  - {id: ws.pe, thread_kind: proposal, context_policy: {summary: true, tail: 30}}
```

Валидация через существующий `SchemaRegistry`. Permissions в DSL — **выбор профиля
+ narrowing полиси**, не произвольные гранты.

### 5.7 API-контракт: REST + SSE

Поток строго асимметричен: вниз льётся стрим, вверх идут редкие команды. У SSE
`Last-Event-ID` — часть стандарта и **изоморфен нашему seq**, реплей получается из
коробки. У WebSocket пришлось бы писать resume-протокол самому — и это ровно то
место, где теряются события. Polling исключён: tool call длительностью 1.8 с при
1-секундном поллинге визуально не виден.

```
POST /api/orgs                       → регистрирует artifact revision
PUT  /api/orgs/{ref}/layout          только координаты (неавторитетно)
POST /api/orgs/{ref}/validate        schema + графовая валидация (циклы, orphan)
POST /api/runs                       {org_ref, org_revision, target, task_text}
GET  /api/runs/{id}/events?after_seq= страничная история
GET  /api/runs/{id}/stream           SSE; Last-Event-ID = seq
POST /api/runs/{id}/stop             честный stop (см. §7)
POST /api/runs/{id}/input/{op_id}    ответ на input_needed
GET  /api/nodes/{run}/{node}/trace   spans + события ноды
GET  /api/profiles                   operator allowlist — READ ONLY
```

**Реконнект без потери событий:**

```python
if last_event_id is None:
    yield sse("snapshot", store.snapshot(run_id))
elif store.can_replay_from(run_id, last_event_id):
    cursor = last_event_id                    # точный догон
else:
    yield sse("snapshot", store.snapshot(run_id))
    yield sse("resume_gap", {"from": last_event_id, "to": store.head_seq(run_id)})
```

Три свойства: снапшот идемпотентен и самодостаточен; `resume_gap` **явный**
(молчаливая потеря событий покажет агента навсегда застрявшим в `working`); все
события идемпотентны по `(run_id, seq)`.

**Backpressure:** `node.state` коалесится по `node_id`; usage-дельты
аккумулируются и шлются раз в 250 мс; `text_delta` стримится только для
«сфокусированной» ноды (`POST /focus`); при медленном клиенте дропаются дельты, но
никогда структурные события.

### 5.8 Локальный запуск

**`agent-commons ui` — FastAPI + SPA, вкомпилированный в wheel.** Не Electron
(+150 МБ, второй pipeline, нулевая польза при локальном state), не Tauri (тянет
Rust-toolchain в проект, где решено его не иметь). Оба можно добавить позже как
оболочку над тем же HTTP API.

- Порт `127.0.0.1:0` (эфемерный), bind строго на loopback, никогда `0.0.0.0`.
  Флага `--host` не существует в принципе.
- **Токен обязателен.** `THREAT_MODEL.md` прямо: loopback reachability alone is not
  authentication. 256-битный токен при старте, URL с фрагментом, авто-открытие
  браузера, токен в заголовке (не cookie — закрывает CSRF).
- Анти-DNS-rebinding: проверка `Host` ∈ {127.0.0.1, localhost}, иначе 403.
- CORS-заголовки не выдаются вовсе (same-origin only).
- CSP `default-src 'self'`; агентский текст в DOM **только** как textContent —
  это untrusted input по threat model; ссылки из агентского текста не кликабельны
  без подтверждения.

---

## 6. Оркестрация и контекст

### 6.1 Паттерн: hierarchical plan-then-delegate

```
Supervisor (в оркестраторе, НЕ отдельный процесс)
  wave N:  read blackboard → PLAN (structured output) →
           ВАЛИДАЦИЯ ПЛАНА (детерминированная) → dispatch →
           barrier → reduce → done | wave N+1 | escalate
Worker (одноразовый процесс) — внутри ReAct до terminal MCP-тула
```

**Почему не ReAct-supervisor:** реактивная генерация делегаций по одной — ровно тот
режим, где рождаются бесконечные циклы и нечем ограничить fanout заранее. Явный
план позволяет проверить рёбра org-графа, `policy.assert_launch_allowed()` и
конфликты claims через `resources_overlap()` **до** траты provider units.

Бонус: план — это ровно те рёбра, которые UI должен подсветить **до** начала
работы. Реактивный supervisor рисовал бы граф задним числом.

**Ложится на существующий broker** через `idempotency_key` с `wave` в составе:
повтор волны не порождает дубль-процесса, `AttemptStore.reserve()` вернёт
`reused=True`. Встроенный exactly-once на уровне волны.

Осторожно: осознанный ретрай **обязан** менять ключ (`:retry=N`), идемпотентный
повтор — не менять. Смешать эти два случая = молчаливый no-op, который UI покажет
как «мгновенно завершилось». Ключ строить централизованно, никогда ad-hoc.

### 6.2 Agent↔agent: каждый ход = новая делегация

Сценарий PRD §8 (Reviewer → Backend → Reviewer) в один процесс не ложится:
`--no-session-persistence`, resume невозможен. Каждый ход ревью — отдельная
делегация, привязанная к точной ревизии. Это не хак, а прямое следствие
`PROTOCOL.md §5`: изменённая ревизия делает предыдущие суждения stale.

Для не-terminal коммуникации переиспользуем существующий
`runtime/communication.py` как есть: `PROGRESS` → «агент жив», `BLOCKER` → «◐
Waiting», `GUIDANCE` → уточнение в живой процесс, `CHECKPOINT` → wave-барьер.
`OperationLimits.max_chain_depth=4` уже ограничивает ping-pong.

**Обязательный handoff packet** (валидируется схемой до старта): `artifacts` с
revision-биндингом, `assumptions`, `open_questions`, `next_actions`, `not_done`.
Отсутствие artifacts → делегация не стартует.

### 6.3 Context isolation

Три независимых слоя, каждый закрывает свой класс утечки:

1. **Транспортный** (есть): `SafeEnvironment` — 15 переменных, `--strict-mcp-config`,
   `--setting-sources ""`, `--ignore-user-config`.
2. **Инструментальный** (есть): reviewer получает `--tools ""` — физически не может
   читать ФС нативно, только через `commons_workspace_read`.
3. **Контекстный** (новый): единственная точка сборки промпта.

**Порядок слоёв диктуется prompt caching, а не читаемостью:**

```
[ IDENTITY | POLICY | TOOLS | ORG ]   стабильно → cache hit
[ PRIVATE  | WORKSPACE            ]   append-only → частичный hit
[ HANDOFF  | TASK                 ]   уникально → всегда miss
```

Никаких timestamp, `wave=N` или ID прогона в первых четырёх слоях — это убивает
кэш префикса. Workspace рендерится только как append; ресуммаризация — **редко и
по порогу**, потому что инвалидация кэша на 40k токенов дороже одной суммаризации.

Целевой `cache_read / (cache_read + input)` для builder-агента с большим
workspace — **> 0.6**. У Codex этих цифр нет — отдельный аргумент держать «толстые»
роли на Claude.

**Порядок вытеснения жёстко зафиксирован:** workspace → private → handoff
(`artifacts`/`next_actions` **никогда**) → `TASK`/`POLICY`/`TOOLS`/`IDENTITY` **не
усекаются**. Если не влезло — это ошибка планирования, supervisor обязан
декомпозировать задачу, а не отправить агента с обрезанным ТЗ.

**Детерминированный гейт против утечки:** каждый канал получает неугадываемый
маркер (`<!--wsp:ch_a1b2c3-->`); проверка — точное вхождение подстроки на
собранном контексте до запуска, O(n), без LLM. Тестируемый инвариант:
`context_leak_rate` строго 0.

---

## 7. Observability

### 7.1 Два потока, не один

`telemetry.py` намеренно закрыт (`no content-bearing fields`, `assert_safe` на
каждом emit, fsync под flock). Ломать нельзя и не нужно.

|  | Поток A: canonical telemetry | Поток B: run observability |
|---|---|---|
| Файл | `runtime/telemetry.py` (как есть) | новый `runtime/observability.py` |
| Содержимое | metadata-only | metadata + контент под явным флагом |
| Хранение | JSONL + fsync per event | SQLite WAL, батч-коммит, TTL |
| Частота | ~10 событий на делегацию | сотни–тысячи |
| Кто читает | оператор, SLO | UI-canvas |

Поток B **никогда** не источник истины для lifecycle — только для отрисовки.

### 7.2 Схема событий (поток B)

Конверт содержит `seq`, `run_id`, `trace_id`, `span_id`, `parent_span_id`,
**`org_node_id` / `org_edge_id`** (без них UI вынужден джойнить на клиенте),
`delegation_id`, `attempt_id`, `kind`, `payload`.

```jsonc
{"kind":"run.started",   "payload":{"org_revision":"evt_...","budget_microusd":5000000}}
{"kind":"wave.started",  "payload":{"wave":2,"planned_steps":3}}
{"kind":"node.state",    "payload":{"state":"working","previous":"idle","reason":"process_started"}}
{"kind":"delegation.planned","payload":{"to":"agent:backend","purpose":"implementation","est_cost_microusd":120000}}
{"kind":"llm.turn","payload":{"turn":3,"model":"...","input_tokens":18421,"output_tokens":892,
   "cache_read_tokens":16100,"cost_microusd":34120,"cost_is_estimated":false,"ttft_ms":740}}
{"kind":"tool.started", "payload":{"call_id":"tc_1","server":"github","tool":"get_pull_request",
   "args_sha256":"...","args_bytes":64}}
{"kind":"guardrail.tripped","payload":{"guard":"cost_ceiling","threshold":5000000,"action":"halt_run"}}
```

`args_sha256` вместо аргументов — no-content по умолчанию. Реальные аргументы
только при явном `capture_content=true` оператором, с отдельным retention.

### 7.3 Метрики

```
duration p50/p95 per (org_node_id, profile_id)  → HDR-histogram, не хранение точек
tokens_per_agent    Σ llm.turn.* group by org_node_id
cache_hit_ratio     Σ cache_read / Σ(cache_read + input)      целевой > 0.6
cost_per_task       Σ cost_microusd + estimated(codex_turns)
cost_per_success    cost_per_task / succeeded_runs
retry_rate          (distinct attempt_id / distinct delegation_id) − 1
tool_error_rate     group by (server, tool)   — разделять сервер и тул!
finalization_lag    SLO p95 ≤ 5s, p99 ≤ 15s (из BROKER_OPERATIONS.md)
redundant_work_rate делегации с одинаковым task_fingerprint / total
```

**Кардинальность:** `org_node_id` и `(server, tool)` ограничены — безопасны как
лейблы. `delegation_id`/`attempt_id` — **только поля событий**, никогда не лейблы
метрик.

### 7.4 OpenTelemetry GenAI: да, но только на экспорте

**За:** `OpenTelemetrySink` уже есть, `CorrelationIds.trace_id` уже валидируется как
32-hex (W3C-совместим по конструкции), готовые бэкенды (Langfuse, Phoenix, Grafana)
понимают `gen_ai.*` из коробки.

**Против как внутренней схемы:** semconv нестабильна и уже несколько раз
переименовывала атрибуты; она контент-ориентирована
(`gen_ai.input.messages`) — прямой конфликт с `assert_safe`; в ней нет понятий
делегации, attempt, claim, workspace, org-edge — то есть ровно того, что нужно
этому UI.

**Решение:** внутренняя схема `agent_commons.run_events.v1` — канон, маппинг на
`gen_ai.*` на экспорте. `gen_ai.input.messages` / `output.messages` не эмитим
никогда. Handoff моделируется как **span link**, а не атрибут — тогда любой
OTel-бэкенд рисует граф передач бесплатно.

### 7.5 Чего нет в `telemetry.py`

Токенов и денег (только `stdout_bytes_seen`); событий tool call (только счётчики
трёх terminal-тулов); уровня run/graph; streaming seam (emit синхронный с fsync
под flock — на 1000 событий/с заблокирует брокер); поля `model` (одна роль на
разных моделях неразличима); TTFT.

Всё это — в поток B. `telemetry.py` не трогаем.

### 7.6 Что показывать честно

- **Codex: денег не рисовать вообще** — `supports_budget = False`. Вместо этого
  «units 2/4». Фейковый счётчик долларов там, где рантайм их не считает, — ложь.
- **`task.completed` ≠ правда.** UI обязан визуально различать «делегация
  succeeded» и «принято review». Зелёная галка ✓ — только для `accepted`, иначе
  визуализация станет каналом false-consensus, против которого построен весь
  протокол.
- **Capacity-виджет постоянно в шапке**: `▓▓░ 2/2 · queue 3 · wait ~40s`. Самый
  недооценённый элемент — объясняет отсутствие активности лучше любой анимации.

---

## 8. Guardrails

| Отказ | Детекция | Порог | Действие |
|---|---|---|---|
| Зависший агент | нет `llm.turn`/`tool.*` за окно | >180 с или >0.25×timeout | `waiting` + guardrail; при >0.5×timeout — SIGTERM группе, затем SIGKILL. Итог `timed_out`, **не** авто-ретрай |
| Процесс кончился, финализации нет | `process_finished` без canonical | 10 с | alert + reconcile; недоказуемо → `needs_operator` |
| Взрыв стоимости | скользящая сумма | 60% warn / 90% стоп новых / 100% halt | Claude режет сам; **для Codex единственная защита — наш счётчик + `provider_units`** |
| Циклическое делегирование | `(from, to, task_fingerprint)` | повтор >2; всего >3×\|agents\|; волн >8 | halt + `needs_operator`. Никогда не «пропустить шаг» — цикл значит, supervisor не сходится |
| Конфликт claims | acquire после валидации плана | ≥1 | Баг планировщика. Перенос в следующую волну **один раз**, повтор → halt. Никаких retry-циклов |
| Недоступный MCP | `MCP_*` коды (уже есть) | handshake >10 с, 3 подряд | required → делегация не стартует; опциональный → degrade + запись в handoff |
| Terminal-тул не вызван | `TERMINAL_TOOL_NOT_CALLED` (уже есть) | exit 0 при 0 completions | `invalid_result`. **Exit 0 ≠ успех** |
| Context leak | `_assert_no_leak` | ≥1 | Делегация не стартует. Инвариант безопасности |

**Глобальный kill-switch:** снять pre-start делегации через `delegation cancel`
(единственный разрешённый путь) → для active SIGTERM/SIGKILL группе → **затем**
reconcile. Нельзя помечать active-работу cancelled до доказанного завершения
процесса: это запрещено протоколом и создаст orphan-процессы.

**Честная кнопка Stop** — двухрежимная: `Cancel (queued only)` активна только до
старта; после старта основное действие — `Operator stop…` с пошаговым runbook.

---

## 9. Тестирование

**Unit:** редьюсер run-состояния (fold чистый — тривиально), org-DSL валидатор,
компиляция DSL→BrokerRequest, `decode()` адаптеров как чистая функция.

**Property-based (hypothesis)** — инварианты:
- идемпотентность: повтор того же события → тот же `event_id`, файл не переписан;
  другой контент под тем же ключом → `IdempotencyConflictError`;
- **replay-детерминизм:** `fold(events) == snapshot(k) + fold(tail)` для любого k;
  произвольные префиксы дают монотонное состояние без откатов статусов;
- immutability: запись поверх canonical-файла другими байтами → `ImmutableCollisionError`;
- narrowing: случайные пары parent/child policy — расширяющий child отвергается;
- crash-injection на `atomic_write_replace`/journal (kill между tmp-write и rename)
  → reconcile не теряет и не дублирует attempt.

**Contract:** JSON Schema всех SSE-событий и org DSL в `SchemaRegistry`;
OpenAPI-снапшот в git, diff ревьюится; **golden-фикстуры провайдерских JSONL** с
версионированием — при drift формата CLI падает до релиза.

**Детерминированная оркестрация:** существующий `evals/fake_provider.py` слишком
узкий. Нужен **ScriptedAgentProvider** — исполняемый фейковый CLI, читающий
сценарий (emit tool_call → sleep → emit usage → request_input → exit N) и пишущий
JSONL тем же контрактом, что настоящие CLI. Прообразы уже есть в
`tests/fixtures/fake_{codex,claude}_mcp_provider.py`. На нём — полный цикл: run →
делегации → статусы → терминал → реплей byte-identical.

**Недетерминированная часть:** record/replay сырых provider-JSONL (после
SecurityPolicy-редакции); golden traces сравнивать **не по тексту**, а по
структурной проекции (последовательность kind'ов, терминальные состояния,
инварианты бюджета). Живые провайдеры — только opt-in canary.

**E2E (Playwright), 3–5 сценариев, не больше** — они дорогие: собрать org →
запустить → дождаться статусов → открыть Inspector → **убить бэкенд посреди run** →
рестарт → проверить `needs_operator` и корректный реплей.

**Нагрузочные (счётчики, не wall-clock):** вставка 50k событий батчами <5 с;
реплей 50k через snapshot+tail <1 с; SSE 1k событий/с без роста очереди.

**CI-гейты:** ruff + mypy → unit/property/contract → integration с
ScriptedProvider → OpenAPI/schema-diff → Playwright smoke (1 на PR, полный набор
на main) → benchmark-счётчики.

### Evals оркестрации (три уровня)

**L1 — топология и планирование, без LLM, на каждый PR.** 20–30 зафиксированных
org-графов (плоский, 3-уровневый, с циклом, с пересекающимися claims, с
недоступным MCP) × задачи с gold routing.

```
routing_precision/recall vs gold      cycle_detection_rate     target 100%
plan_validity_rate                    context_leak_rate        target СТРОГО 0
handoff_completeness                  policy_violation_rate    target 0
```

**L2 — поведение на fake provider, nightly.** Сценарный fake с инъекцией отказов
(reviewer возвращает changes_requested дважды; builder падает по timeout; MCP
отваливается на третьем туле; terminal-тул не вызван).

```
recovery_rate       loop_containment       wasted_delegation_rate
finalization_lag_p95 ≤ 5s
determinism         один вход → один граф делегаций, target 100%
```

`determinism` — самый ценный сигнал: ловит регрессии планировщика, невидимые в
success rate.

**L3 — качество результата, LLM-judge + человек, на релиз.** 30–50 реальных задач
с эталонами. **Обязательный baseline: один агент с тем же бюджетом.** Если
multi-agent не бьёт single-agent по `cost_per_success`, вся конструкция экономически
не оправдана — это главный вопрос, на который должны отвечать evals.

```
task_success@1, pass^k (k=3)     cost_per_success vs baseline
wall_clock_speedup vs baseline   review_catch_rate (внедрённые баги)
false_approval_rate              ЕДИНСТВЕННАЯ метрика, где допустим только 0
```

LLM-судья — **только** для L3 и только для качества финального артефакта. Всё, что
касается процесса (маршрутизация, циклы, утечки, бюджеты, claims), судится
детерминированно по потоку событий. Тратить недетерминированного судью на факты,
проверяемые точным сравнением, — методологическая ошибка. Калибровка судьи: 30
размеченных человеком примеров, целевой Cohen's κ ≥ 0.7.

**Feedback loop:** каждый прод-инцидент → новый детерминированный L1/L2-кейс.
`EvalCase.failure_tags` для этого уже есть.

---

## 10. План инкрементов

Каждый шаг — работающее состояние, без «большого взрыва».

| # | Что | DoD (проверяемый) |
|---|---|---|
| **0. Walking Skeleton**<br>1–2 нед | Read-only страница, рендерящая существующий ledger как граф. Цвет = lifecycle state. Клик → сырой JSON. Ручной refresh. Ноль записи. | Статус ноды совпадает с `delegation show --json` на 100% фикстуры. UI не создаёт **ни одного** канонического события — верифицируется diff ledger-файлов до/после. |
| **1. Run Viewer** | FastAPI + токен-auth + SPA-скелет. Граф делегаций из ledger-проекции + attempt journal. Ещё polling. | Наблюдаемость существующих делегаций работает. Риск ~0. |
| **2. Streaming** | `StreamingSubprocessRunner` + RunEventStore + SSE с seq/Last-Event-ID + снапшоты. | Canvas оживает для одиночных делегаций, запущенных из CLI. Реплей byte-identical. |
| **3. Org DSL v1 + запуск из UI**<br>→ MVP (4–6 нед) | Только agents + control edges, один уровень. DSL как artifact revision. Canvas-редактор + layout-store. Capacity/queue UI. Панель бюджета. | Пользователь создаёт агента на allowlisted профиле, запускает делегацию **из UI**, видит requested→active→succeeded/failed вживую. Существующие broker canary/contract тесты продолжают проходить **без изменений**. Ни один сырой prompt/tool payload не появляется в UI — грепом по отображаемым полям. |
| **4. Мульти-агентный run** | OrchestratorService: supervisor-делегация depth 2, параллельные ветки через thread-pool, worktree-провижининг с подтверждением, честный stop. | Организация из ≥2 команд и ≥4 агентов исполняет задачу с фан-аутом ≥3 одновременно, в рамках явных operator-лимитов. |
| **5. Workspaces** | Маппинг на typed threads + context policy. Communication Inspector из ledger. | Полный цикл submit → independent review → accept без обхода инварианта «ревью не может закрыть автор». |
| **6. Полировка → V1** | Team-контейнеры, token/cost counters, staleness/Truth board, упаковка SPA в wheel, e2e-набор. | Ни одна live-правка графа не обходит `CommonsManager` — статический аудит write-путей. |

---

## 11. Риски

| # | Риск | Митигация |
|---|---|---|
| 1 | **Canvas обещает параллелизм, которого рантайм не даёт.** 12 агентов, работают двое → «продукт сломан» | Capacity-виджет; статус `queued` с позицией; дорожка queue depth; превью плана на переходе Design→Run («12 агентов, ёмкость 2, оценка 6 волн»); depth ruler **до** запуска |
| 2 | **UI обещает настраиваемые tools, профили фиксированы кодом** | Никаких чекбоксов. Read-only с замком + явный список запрещённого. Порт для неподдерживаемой связи физически не рендерится |
| 3 | **Каскад staleness невидим** — человек действует по устаревшему approve | `stale` как четвёртый визуальный канал (штриховка) на нодах, рёбрах, карточках, timeline; Truth board с lineage; автопопадание в Attention queue |
| 4 | **Пользователь ждёт логи, телеметрия metadata-only** | Трёхуровневая иерархия достоверности постоянно в UI; максимизировать то, что есть; для реального вывода — «Open in terminal», а не имитация логов |
| 5 | **Sync-брокер × async-оркестратор** | Bounded executor = лимит concurrency; канонические записи через одну очередь; streaming-runner инкрементом, не переписыванием брокера |
| 6 | **Chatty runs раздувают ledger** (10⁴–10⁵ событий на run) | Жёсткий шлюз: в ledger только вехи (десятки на run), поток — в disposable SQLite; retention-политика |
| 7 | **UI как эскалация привилегий** | Permissions = выбор operator-профиля + narrowing-only; профили создаются вне UI; loopback + токен; агентский текст всегда untrusted в DOM |
| 8 | **Скоуп-крип** — PRD описывает крупный MVP целиком | Жёсткое разделение по §10, каждая стадия независимо полезна или удаляема |
| 9 | **Смена ICP.** Текущая аудитория — разработчики, координирующие Codex+Claude на одном чекауте; визуальный оркестратор целится в более конкурентный рынок | Визуальный слой — **опциональная надстройка**; ценность coordination-ядра от неё не зависит; не переписывать core под UI-требования |

---

## 12. Позиционирование

Все сравнимые продукты (LangGraph Studio, AutoGen Studio, n8n, Flowise, Dify,
CrewAI-экосистема) **уже имеют работающий визуальный слой**; у agent-commons его
нет вообще — ноль строк UI-кода.

Реальное отличие — **не canvas UX** (там старт с нуля против зрелых продуктов), а
**governance-модель поверх immutable ledger**: review ≠ verification ≠ acceptance,
revision-bound evidence, explicit truth promotion. Этого как первоклассной
концепции у перечисленных, по доступным источникам, нет.

Продавать «canvas лучше, чем у LangGraph Studio» — проигрышная позиция на годы.
Продавать «governance и durable evidence для команд coding-агентов, теперь с
визуальным слоем» — защитимая ниша, согласованная с `VISION.md`.

Сравнение конкурентов основано на внешних источниках и не верифицировано на самих
продуктах. Детали по CrewAI Studio и статусу AutoGen Studio — **низкая
уверенность**, требуют отдельной проверки перед публичным использованием.

---

## 13. Что вырезать из PRD

- **«Canvas является source of truth»** — принимается только в переформулировке
  §2.1. Иначе вилка проекта.
- **Мульти-фреймворк адаптеры** — из MVP полностью.
- **Свободный agent↔agent messaging в реальном времени** — ядро построено вокруг
  delegation tree с depth/budget lineage. Реализуемая форма — асинхронные
  workspace-threads, а это другой UX, чем «чат агентов» из §8.
- **Точные Tokens/Cost** — реалистично best-effort с пометкой «reported by
  provider»; enforcement — только `provider_units`/`micro_usd`.
- **Живая перестройка топологии во время run** — run фиксирует ревизию, изменения
  идут к следующему.
- **Communication Inspector с полными телами** — заменяется §2.3.
- **Вложенные команды** — один уровень в MVP.

---

## 14. Решения по открытым вопросам

### 14.1 Depth > 1 и concurrency > 2 — принято

Уточнение, меняющее постановку: **механика уже есть и уже operator-owned.**
`RuntimePolicy.remaining_depth`, `OperatorLimits.global_concurrency`,
`provider_concurrency`, `profile_concurrency` — всё читается из конфигурации
через `from_mapping`. Это значения по умолчанию, а не потолок. Поднять их —
изменение конфига, а не кода.

Проблема в другом: **гарантии, которые держатся на depth 1 / concurrency 2, выше
не держатся.** Три дыры:

1. **Depth ограничивает только вертикаль.** Supervisor с `remaining_depth=1` может
   открыть неограниченное число последовательных depth-1 делегаций — каждая по
   отдельности легальна. Плюс ping-pong `A→B→A` по одной и той же работе: две
   легальные делегации на одном уровне, и ничто не замечает, что пара не сходится.
2. **Concurrency сталкивается с инвариантом одного writable worker.** Поднять
   `global_concurrency` — не создать второй безопасный writable-слот, а получить
   два процесса, редактирующих одно рабочее дерево.
3. **Учёт попыток — на делегацию, не на поддерево.** Глубокое дерево жжёт бюджет
   геометрически, оставаясь в лимитах в каждом узле.

Оформлено в [ADR 0007](adr/0007-multi-level-delegation-and-raised-concurrency.md).
Ключевое из него:

- Три новых монотонных поля `RuntimePolicy` (`max_delegations_total`,
  `max_wave_count`, `max_context_tokens`) — автоматически покрываются
  `assert_reduction_of`, потому что входят в набор полей.
- Repeat-pair guard по `(from, to, task_fingerprint)`, где fingerprint считается
  по каноническому target и purpose, **не по тексту инструкции** — иначе
  перефразирование моделью сбрасывает счётчик.
- **Read-only concurrency поднимается первой и дёшево** — независимым ревьюерам
  не нужен worktree, хватает существующего queue-учёта.
- **Writable concurrency — последней**, за worktree-изоляцией. Самый эффектный
  сценарий PRD (Backend и Frontend параллельно) поставляется последним осознанно.
- Конфигурация, поднимающая лимит без выполненных предусловий, падает **на
  загрузке**, а не на запуске — оператор узнаёт до старта, а не после частичного
  фан-аута.

### 14.2 Коллизия имён — разведена

Сделано в коде. `commons_workspace_files/read/search` → **`commons_repo_files/
read/search`**; внутренний `ScopedWorkspaceReader` → `ScopedRepoReader`.
Затронуты `mcp/server.py`, `runtime/model.py` (allowlist
`mcp__agent-commons__*`), текст инструкции в `services/delegation_runtime.py` и
пять тестовых файлов. События ledger'а с прежним именем **не трогались** — они
immutable.

Имя `commons_workspace_*` освобождено под будущие канальные тулы
`commons_channel_post/read/list` (§2.2). Обратная совместимость не нужна: тулы
`worker_only`, и сервер с allowlist'ом едут в одном пакете — внешних
потребителей, пиннящих имена, нет.

### 14.3 Retention для RunEventStore

Оценка объёма: 10⁴–10⁵ событий на run × 200–500 байт ≈ **2–50 МБ на run**. Двадцать
ранов — до 1 ГБ в худшем случае. Значит размерный кап обязателен, одного лимита по
числу ранов мало.

**Трёхуровневая политика, три независимых триггера, что сработает первым:**

| Уровень | Что хранится | Порог по умолчанию | Ключ настройки |
|---|---|---|---|
| Full | все события + снапшоты | последние **20** завершённых ранов | `full_run_limit` |
| Digest | снапшот + терминальные события + вехи, поток событий выброшен | до **30 дней** | `digest_age_days` |
| Purged | ничего (канонические вехи остаются в ledger навсегда) | старше 30 дней или свыше **500 МБ** суммарно | `max_total_bytes` |

Все пороги — operator-конфигурация в том же файле, что и `OperatorLimits`,
читаются через `from_mapping` с отклонением неизвестных ключей. Значения по
умолчанию выше; менять можно любой из трёх независимо. Срабатывает тот, что
наступит первым.

Три правила, без которых политика вредна:

1. **Раны в `needs_operator` не подчищаются автоматически никогда.** Это ровно те
   раны, ради которых forensics и нужен. Убираются только явным действием
   оператора.
2. **Активные раны не подчищаются** независимо от возраста и размера.
3. **Экспорт до удаления.** `agent-commons run export <run_id>` → один JSONL-файл.
   Ран можно вынести из-под retention руками, не отключая политику целиком.

Триггер — **на завершении рана и при старте процесса**, а не фоновым таймером:
меньше движущихся частей, чистка происходит там, где и так есть write-lock.
Физически — `DELETE` + `PRAGMA auto_vacuum=INCREMENTAL` с инкрементальным
вакуумом, чтобы файл не рос монотонно.

Все три порога — operator-конфигурация, как `OperatorLimits`. Ни один не выводится
из содержимого.

### 14.4 `capture_content` — да, но разделённый на два разных механизма

Их путать нельзя, потому что у них радикально разный профиль риска.

**(а) Live-контент сфокусированной ноды — делаем, риск низкий.**
`text_delta`/`thinking_delta` только для одной ноды, выбранной через
`POST /runs/{id}/focus`, **только в памяти**, на диск не попадает никогда, в SSE
уходит и умирает вместе с вкладкой. Это уже заложено в backpressure-модели (§5.7).
Рендер строго как `textContent` — агентский вывод untrusted по threat model.
Закрывает сценарий «смотрю, что агент делает прямо сейчас».

**(б) Durable-контент для post-mortem — отдельный opt-in, отдельный ADR.**
Это единственное место, где продукт сознательно ослабляет privacy-периметр, и
поэтому решение должно быть **на один ран, видимым и истекающим**, а не галочкой в
конфиге, которую поставили один раз и забыли.

- включается **на конкретный ран** при запуске, не глобально;
- в UI — постоянный баннер на всё время рана, а не иконка;
- отдельный файл `orchestrator-content.sqlite3`, права `0600`, **никогда** не в
  ledger, не в телеметрии, не в экспорте по умолчанию;
- прогоняется через тот же `SecurityPolicy.assert_safe` — секреты и PII
  **отвергаются, а не редактируются**, как уже сделано в communication store;
- своя, гораздо более короткая retention: **7 дней или 5 ранов**, что раньше;
- удаление файла оставляет всё остальное работающим — то же свойство отката, что
  у communication store в ADR 0006.

Почему (а) недостаточно и (б) всё-таки нужен: живой просмотр помогает, только если
ты смотришь в момент падения. Разбор инцидента через час требует durability. Но
(а) покрывает ~80% случаев при ~5% риска, поэтому делается в MVP, а (б) — после,
отдельным решением.

---

## 15. Агенты как первоклассная сущность — что сделано и что отложено

Полный разбор решений — в
[ADR 0009](adr/0009-agents-as-first-class-roles.md). Здесь только состояние
работы и границы.

Это меняет пункт §3 MUST-1 («Agent как персистентная конфигурационная сущность»)
с «обёртка над broker profile» на настоящую доменную сущность в ledger, и
частично отменяет §3 ANTI-5 («Autonomous agent creation — не переносить даже в
V1 без отдельного протокольного решения»): протокольное решение принято.

> **Правка 2026-08-11.** Прошлая редакция §15 утверждала, что уровень `auto`
> «поставляется **вместе** со всеми семью ограничителями». Это оказалось неверно:
> обзор (`docs/audits/2026-08-10-standing-roles-review.md`) показал, что четыре
> независимых пути обходят ограничители. Пути закрыты, ограничители доказаны под
> состязательным исполнением двумя ревью — но **уровень `auto` сейчас удержан**:
> `effective_grants` ограничивает потолок до `ask`, любое структурное действие
> подтверждает человек. Автоматический путь спроектирован и заброкирован; он
> вернётся, когда поработает дольше за проверенными тормозами. Живой
> человек-подтверждаемый путь ограничен бюджетом оборота, а не строгим убыванием
> (оно связывает только `approval: automatic`).

### Инкремент A — роль как каноническая сущность (сделано)

Каждый шаг ниже — работающее состояние; ни один не оставляет механизм без
вызывающего.

| Что | Где проверяется |
|---|---|
| `agent.created` / `reconfigured` / `retired`, `agent.link_opened` / `link_closed`; сущности `agent` и `agent_link` | `tests/schemas/test_universal_contracts.py` — минимальный образец каждого типа события проходит схему, домен и жизненный цикл |
| Три права × три уровня, эффективный уровень выводится из всей цепочки создателей | `tests/cli/test_agent_cli.py` — понижение у предка немедленно блокирует уже запущенного потомка |
| Потолок оборота: создания и выводы считаются вместе | там же — цикл create/retire упирается в бюджет (тест пути `auto`, сейчас `skip`) |
| Уровень строго убывает при автоматическом создании | там же — третье поколение отказано (тест пути `auto`, сейчас `skip`) |
| Каскадный вывод одним действием, отказ целиком при живой работе, порядок «листья первыми», атомарно под одним локом | там же |
| Коррекция не меняет полномочия/личность/изоляцию роли; реконфигурация перепроверяет бюджет и строгое убывание | `tests/services/test_manager.py`, `tests/cli/test_agent_cli.py` (round-1 C2/H1) |
| Эфемерная роль выводится при приёмке/отмене задачи (вывод, а не событие) | там же |
| Роль не выводит роль, созданную человеком | там же |
| Независимость ревью — по принципалам, а не по сессиям | `tests/domain/test_role_independence.py` |
| Сужение набора tools доходит до argv запущенного процесса | `tests/runtime/test_orchestration.py`, `tests/runtime/test_profiles_policy.py` |

### Инкремент B — UI: панель, шестерёнка, жёлтый контур (сделано)

- Роли — узлы на канвасе; ребро `reports_to` — постоянная структура, `acts_for`
  — временная привязка запуска к роли.
- `awaits_human` на узле: делегация в `input_needed` и открытый
  decision-request тред. Оба источника — существующие продюсеры; третьего,
  который бы не зажигался, добавлено не было.
- `agent-commons ui --enable-writes`: перечисленный набор POST-маршрутов, каждый
  — тонкий адаптер над `CommonsManager`. Тест удаляет `record_event` и требует,
  чтобы **все** маршруты упали.
- Шестерёнка правит только то, что сужает; модель показывается как свойство
  профиля. Каталог скиллов и туллов редактируется формой из панели за отдельным
  гейтом `--enable-catalog-editing`; профили не редактируются ни при каком.

### Инкремент C — главный чат и разблокировка (сделано)

| Что | Где проверяется |
|---|---|
| Главный чат: тред `engagement`, адресованный ролям верхней полосы, привязанный к objective | `tests/mcp/test_main_chat.py` — два архитектора в одном треде, роль читает и отвечает своим MCP-туллом |
| Роль, созданная после открытия чата, помечена как неадресованная | там же — адресаты канонические, проекция их не переписывает |
| Воркер отвечает только там, где адресован | там же — попытка ответить в чужой тред отказана |
| Ответ на `input_needed` из панели, с возобновлением запуска | `tests/ui/test_blockers.py` — по HTTP, включая «отвечает другая сессия» |
| Уровень `ask`: предложение + подтверждение, привязанное к предложению | `tests/mcp/test_role_proposals.py` |
| Каталог как форма, отдельный гейт, атомарная запись | `tests/ui/test_catalog_editing.py`, `tests/cli/test_ui_command.py` |
| Скиллы доходят до инструкции запущенного процесса | `tests/runtime/test_orchestration.py` |
| Поиск по истории, белый список полей, read-only не создаёт проекцию | `tests/index/test_search.py` |
| `handoff_work` расширяет укомплектование, `ask` — нет, закрытие отзывает | `tests/domain/test_role_independence.py` |

### Инкремент D — запуск из UI (сделано, 2026-08-11)

Переход от «реестра с чатом» к «работающей оргструктуре»: MUST-4 (запуск
делегации из UI) и MUST-5 (live-состояние запуска в панель), которые прошлая
редакция §15 держала отложенными.

| Что | Где проверяется |
|---|---|
| Действие «запустить роль на задаче»: `POST /api/delegations` записывает делегацию `on_behalf_of` роли и запускает её тем же `DelegationRuntimeService`, что и CLI-брокер — не второй путь запуска | `tests/ui/test_launch.py` — по HTTP, с подставным раннером; и живой прогон с echo-провайдером |
| Провайдер и модель фиксируются профилем роли; purpose следует профилю, предусловия ревьюверского профиля — отказом домена | там же |
| Отдельный гейт `--enable-launch` + `--profile-config` (валидируется на старте), отдельный allowlist маршрутов, чтобы тест поверхности записи оставался честным | `tests/ui/test_launch.py`, `tests/ui/test_readonly_invariant.py` |
| Live-состояние: отпечаток изменений включает каталог runtime-попыток, панель обновляется по мере launching → running → терминал; поверхность Runs показывает фазу/цель/роль — только метаданные, без промптов и транскриптов | `tests/ui/test_launch.py` |

### Что отложено, и почему

| Отложено | Причина |
|---|---|
| **Пресеты как отдельная сущность** | Пресет — это роль с `template: true`: не запускается, не делегируется, ничего не авторит. Четвёртый вид записи ради того, что во всём остальном является ролью, не заводился |
| **Расширение MCP-серверов из UI** | Роль может только сужать набор туллов профиля. Добавить третий MCP-сервер — значит поменять то, что запускает дочерний процесс; это отдельная работа с отдельным анализом, и профили не редактируются из UI ни при каком гейте |
| **Ответ на чужой `input_needed`** | Канал авторизует по участнику. Панель отвечает за свою сессию; чужие показаны с указанием, из какого окна отвечать. Операторский оверрайд по capability возможен, но расширяет авторизацию канала и требует отдельного решения |
| **Дедлайн связи в replay** | У replay нет часов, а сверяться с ними значило бы проецировать одни и те же события по-разному в разное время. Связь закрывается явно; истечение показывается там, где часы есть |
| **Автоматический уровень `auto`** | Удержан до тех пор, пока не поработает дольше за проверенными тормозами. Стоит `AUTOMATIC_LEVEL_WITHHELD`; `effective_grants` ограничивает потолок до `ask`. CLI предупреждает при `--create-roles auto`, панель называет удержание. Тесты пути `auto` помечены `skip` с той же причиной. Восстановление — отдельный осознанный шаг (round-2, все линзы) |
| **Полноценная нить человек↔роль** | Вкладка Message открывает новый тред на каждую отправку, ответ роли читается только в очереди внимания. Острый тупик закрыт (в карточке внимания есть поле ответа через канонический thread-message маршрут), но связная двусторонняя нить — бо́льшая поверхность (round-2, design) |
| **Доступность канваса с клавиатуры** | Узлы графа — SVG-группы с click-обработчиком без `tabindex`/`role`, выбор роли только мышью. Настоящий a11y-проход — отдельное изменение (round-2, оба дизайн-ревьювера) |
