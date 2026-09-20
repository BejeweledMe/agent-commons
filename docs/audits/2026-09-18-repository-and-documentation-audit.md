# Аудит репозитория и документации Agent Commons

Дата: 18 сентября 2026. Автор: координатор (Claude Fable), сессия
`session.8f29684ae9824337a0f29a9ea73fdae9`. Граница: ветка `wave-3-ux-closure`
на `43b7d8f` плюс незакоммиченная работа волны 3 (WP-11.2, WP-13.1 приняты,
WP-09 выполняется). Метод: четыре независимых read-only прохода (карта кода,
гигиена файлов, противоречия документов, покрытие UX-фидбэка по леджеру) и
ручная проверка ключевых утверждений по исходникам.

Это отчёт-инвентарь, а не план. Что делать дальше — в
[плане консолидации и волны 4](../plans/2026-09-18-consolidation-and-wave-4-plan.md).
Ничего из перечисленного здесь не удалялось и не перемещалось; в рабочей копии
удалены только два игнорируемых Git локальных кэша (`.playwright-mcp/*.log`,
`benchmarks/__pycache__`).

## 1. Карта проекта

### 1.1. Верхний уровень

| Путь | В Git | Размер | Назначение |
| --- | --- | --- | --- |
| `src/agent_commons/` | 677 файлов | 16M | Python-пакет: CLI, домен, сервисы, storage, runtime, UI-сервер, MCP |
| `frontend/work/` | ~100 файлов | 147M на диске (node_modules) | React 19 + Vite 8 + TS: приложение Work (Доска · Задачи · Агенты · Библиотека · Настройки) |
| `frontend/gallery/` | ~18 файлов | — | React-галерея дизайн-пакетов (`/gallery`) |
| `tests/` | 212 файлов | 16M | 202 pytest-модуля в 19 каталогах |
| `docs/` | 220 файлов | 4.3M | 21 ADR, 13 планов верхнего уровня, 14 аудитов, 10 review, 109 evidence-файлов, руководства |
| `.agent-commons/` | 3 файла | 17M локально | Леджер (3725 событий, 390 манифестов) — игнорируется Git, кроме `ONBOARDING.md`, `workspace.yaml`, `.gitignore` |
| `.claude/`, `.agents/` | 16 + 14 | — | Побайтово одинаковые зеркала семи `commons-*` навыков (Claude Code и OpenAI-клиенты) |
| `.claude/worktrees/` | исключено через `.git/info/exclude` | 90M | Два живых worktree других сессий — не трогать |
| `benchmarks/`, `tools/`, `.github/` | 3 + 2 + 1 | — | Бенчмарки записи/проекции, проверка Node, `clean_generated.py`, один CI workflow |
| `assets/agent-commons-hero.png` | 1 | 1.8M | Hero-изображение, используется CSP и vite-конфигами |
| `claude_architecture_improvement_review.md`, `codex_architecture_improvement_review.md` | 2 | 140K + 48K | Ревью августа, лежат в корне; хэши записаны в `docs/reviews/README.md` |
| `CHANGELOG.md` | 1 | 31K | Последняя запись — август 2026; нет волн 1–3, доски, проектов, результатов |
| `build/`, `artifacts/`, `.pytest_cache/`, `.ruff_cache/`, `.venv/` | игнорируются | 7.8M / 136K / 0.7M / 0.6M / 87M | Локальные кэши; `make clean` чистит первые четыре (кроме `artifacts/`) |

### 1.2. Backend `src/agent_commons/`

| Подпакет | Файлов | Строк | Роль |
| --- | --- | --- | --- |
| `runtime/` | 27 | 14 110 | Провайдеры, попытки, вложения, live previews, quali-fication |
| `services/` | 34 | 13 332 | Менеджер леджера, задачи, делегирования, review, outputs, conversations |
| `ui/` | 32 | 12 468 | FastAPI-сервер, маршруты Work/Gallery/Board, DTO трекера, сессия панели |
| `domain/` | 42 | 10 314 | Проекции, lifecycle, валидация, схемы; не импортирует services/ui |
| `cli/` | 3 | 3 345 | `cli/__init__.py` — 3 025 строк, один модуль на всё дерево команд |
| `mcp/` | 7 | 2 615 | MCP-сервер для воркеров |
| `storage/`, `coordination/`, `index/`, `core/`, `security/`, `integrations/`, `evals/`, `presentation/` | 2–7 | 500–1 900 | Фундамент |

Слои: `cli, mcp → ui → services → domain / runtime → core, storage, errors`.
Одно обратное ребро `runtime → services`. Точки входа: `agent-commons`
(`cli:cli`), `agent-commons-mcp` (`mcp.server:main`), `ui/server.py:create_app`.

Десять крупнейших файлов: `cli/__init__.py` 3 025, `services/delegation_runtime.py`
1 972, `mcp/server.py` 1 583, `ui/server.py` 1 572, `services/manager.py` 1 404,
`runtime/model.py` 1 392, `domain/projection.py` 1 268,
`runtime/message_attachments.py` 1 213, `runtime/attempts.py` 1 175,
`services/work_metrics.py` 1 059.

### 1.3. Две UI-поверхности и одна legacy

| Поверхность | Источник | Куда собирается | Кто открывает |
| --- | --- | --- | --- |
| Work (продукт) | `frontend/work/src` (29 компонентов, 27 модулей, 25+ node-тестов) | `src/agent_commons/ui/static/work/assets/work-<hash>.js` — один хэш, коммитится | `agent-commons ui` печатает `/work#c=…` (`cli/__init__.py:364`) |
| Gallery | `frontend/gallery/src` | `ui/static/gallery/` | `/gallery` |
| Legacy-панель | `src/agent_commons/ui/static/index.html` — 8 696 строк, 540K, рукописный монолит | — | Только `/` при `hosted=False`; проектный host создаётся с `hosted=True` (`ui/project_host.py:398`), то есть внутри проекта не отдаётся вовсе. 16 тестовых файлов читают её через `read_spa()` |

Следствие: правило `AGENTS.md` «UI-asset — один файл `index.html` ~7000 строк,
один писатель» описывает legacy-панель, а не продукт. Новый агент, прочитав
его, будет править мёртвый файл.

### 1.4. Состояние программы закрытия UX (по леджеру)

24 work package в плане, 18 в леджере: 17 `accepted`, 1 `ready`/в работе
(WP-09, `task.0DAF8ZNJF8FN11GXC0ATJGTBV6`). Без задач: WP-01, WP-10, WP-12.1,
WP-12.2, WP-13.2, WP-15.3, WP-17.1, WP-17.2. Волны 1–2 влиты (`64ff1c4`,
`43b7d8f`); волна 3 — 1,5 пакета из 9, незакоммичена (39 изменённых, 14 новых
файлов), `make check` для этого дерева не регистрировался.

Леджер в целом: 43 задачи в `review` (медиана 12 дней, максимум 57, ни одна —
не WP), 12 `active` и 9 `assigned` задач — все до программы (6–8 сентября),
несколько дублируют WP-09/WP-10; 104 открытых handoff; ~60 stale review в
предупреждениях `doctor`; девять задач R0–R7 с одобренными review так и не
приняты — та самая путаница «готово» из UX-11, воспроизведённая командой на себе.

## 2. Покрытие UX-фидбэка от 9 сентября

| UX | Что просили | Чем закрыто | Статус | Что осталось |
| --- | --- | --- | --- | --- |
| 01 | Вход в проект, папка, смысл архива | WP-03, ADR 0020 | частично | Native chooser не проверен; нет кадров V01–V03; первый запуск сервиса без проектов не описан |
| 02 | Роль vs исполнитель vs провайдер | WP-12.1/12.2 (без задач) | не начато | Весь вопрос: сначала специализация, затем eligible-исполнители, чипы возможностей |
| 03 | Управляемая библиотека навыков | WP-02, WP-02.2 | почти закрыто | Встроенное vs своё, счётчики, перенос/копия навыка |
| 04 | Шаблон → реально начатая работа | WP-06, WP-14, WP-16.x | закрыто по названному непониманию | Нет мастера с обзором плана; копирование шаблона |
| 05 | Готовность = готовность к нужному действию | WP-03, WP-15.1/15.2 | частично | Чипы возможностей (WP-12.2); WP-15.3; различие «не настроено / проверка устарела / ошибка провайдера» |
| 06 | Успех ≠ ошибка запроса ≠ ошибка обновления | fix в `39459cc`, WP-07 | закрыто | Строка «сохраняется в другом проекте» из матрицы состояний не реализована |
| 07 | Карта текущей работы, а не истории | WP-09 (в работе), WP-11.x, ADR 0020 | в работе | Группировка по цели/волне в UI; сохранение выбора при live-обновлениях |
| 08 | Инспектор: результат, потом детали | WP-10 (без задачи) | не начато | Вся иерархия карточки |
| 09 | Чат: адресат, доставка, нужен ли запуск | WP-04.1/04.2 | закрыто по названным пробелам | Позднее уточнение, частично загруженные файлы, путь file chooser |
| 10 | Результаты: последний/прежний/истёк/не проверен | WP-05 | частично | Превью на карточке, комментарий к версии, возврат из превью |
| 11 | «Готово» имеет много смыслов | WP-10 (без задачи); данные из WP-05 | открыто | Карточка решения человека |
| 12 | Ожидание записи на большом проекте | WP-07, WP-08 | частично | Только половина «обратная связь»; ускорение записи не запланировано (62–64 % времени — `canonical_reads_validation`) |
| 13 | Визуальная система, клавиатура, узкий экран | WP-13.1 | в основном открыто | WP-13.2; независимый a11y-аудит; наполненные экраны |

Ожидаемые результаты экспертизы (раздел 9 фидбэка): диагноз, IA/глоссарий,
пути, матрица состояний и backlog существуют как документы
(`docs/handoffs/2026-09-09-ux-improvement-plan.md`); глоссарий не дошёл до UI;
wireframes V01–V12 — 0 из 12; сравнение двух визуальных направлений не делалось
(решение 8.3 принято без него); ни один из десяти сценариев с пользователем не
проводился; метрики раздела 9 неизмеримы без WP-17.

## 3. Противоречия в документации

Пронумерованы для ссылок из плана (WP-C1).

| # | Противоречие | Где | Истина по коду |
| --- | --- | --- | --- |
| K1 | UI-asset = `index.html` ~7000 строк, один писатель | `AGENTS.md` §The UI asset; `docs/FRONTEND_CONTRACT.md` §One file, one writer | Продукт — `frontend/work` (Vite); `index.html` — legacy-панель на 8 696 строк, не отдаётся внутри проекта |
| K2 | «no npm, no bundler, do not introduce a build step» рядом с «`npm ci && npm run build`» | `docs/FRONTEND_CONTRACT.md` строки 56–71 | Оба фронтенда собираются Vite; собранные бандлы коммитятся |
| K3 | «Пять blueprints» | `README.md`, `docs/ROADMAP.md`, `docs/current-product-and-architecture.md`, `docs/agent-platform-implementation-program.md`, `docs/user/*/service-library.md` (по именам) | Семь: `web-app`, `mobile-app`, `telegram-mini-app`, `grounded-ai-assistant`, `improve-service`, `feature-delivery`, `product-discovery` (`ui/library_blueprints.py`) |
| K4 | Вкладки «Work, Team, Library, Settings» | `docs/ROADMAP.md`, `docs/user/README.md`, `docs/user/en/README.md`, программа | `Board/Доска · Tasks/Задачи · Agents/Агенты · Library · Settings`; домашний экран — доска (ADR 0020) |
| K5 | ADR 0016 «Proposed» в индексе | `docs/adr/README.md` | Файл ADR: «Implemented»; та же дрейфующая пара для 0014/0015/0017. Индекс утверждает, что его проверяет тест — тест проверяет только наличие ссылок |
| K6 | «Единый текущий список работ» = `agent-platform-implementation-program.md` | `docs/README.md`, `docs/ROADMAP.md` | Живой backlog — `docs/plans/2026-09-09-ux-closure-tech-plan.md`, на который `docs/README.md` не ссылается |
| K7 | Семь разных формулировок «что такое продукт» (human-first vs agent-first) | `README.md`, `VISION.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `user/README.md`, `current-product-…`, PRD | Решение владельца `product/primary-surface`: UI-first, человек управляет командой агентов |
| K8 | Восемь разных чисел тестов без даты | планы и снимки | Число тестов — не факт документации; должно жить только в evidence-манифестах |
| K9 | Первый запуск ищет только `claude`/`codex` | `CHANGELOG.md` Unreleased | Grok поддержан (`README.md`, `user/en/README.md`) |
| K10 | Grok передаёт инструкции через argv | `docs/provider-adapter-architecture-plan.md` | Через stdin endpoint (программа, ADR 0004) |
| K11 | Starter Packs — живое предложение | `docs/proposals/starter-packs-integration.md` | Удалены WP-16.1/16.2 |
| K12 | «Historical» — метка в прозе, а не место | `docs/README.md` §Historical plans vs `docs/archive/` | 11 завершённых планов лежат рядом с активным |
| K13 | Четыре глоссария с разными определениями role/agent/run/delegation/skill/приёмка | `README.md`, `docs/user/*`, `PROTOCOL.md`, фидбэк | Единого нет; см. §5 |
| K14 | ADR 0001–0019 на английском, 0020–0021 на русском | `docs/adr/` | Языковая политика не определена |

### 3.1. Один факт в трёх и более местах

| Факт | Где повторяется | Единственный дом |
| --- | --- | --- |
| Определение продукта | 7 документов | `docs/PRODUCT.md` (новый) |
| Цепочка Роль → Задача → Прогон → Review → Приёмка | 6 | глоссарий `docs/PRODUCT.md` |
| 45 навыков / 30 специализаций / N blueprints | 9 | `docs/user/*/service-library.md`, число — из кода |
| alpha, macOS/Linux, CPython 3.11–3.14 | 5 | `SUPPORT.md` |
| Один писатель UI-asset | 5 | `docs/FRONTEND_CONTRACT.md` |
| Брокер экспериментальный, не планировщик | 7 | `docs/BROKER_OPERATIONS.md` |
| Прогон ≠ приёмка; зелёный — только приёмка | 6 | ADR 0002, одна цитата в `PRODUCT.md` |
| L1 не доказан / R2 открыт | 10 | один раздел «Release gates» активного плана |
| Зелёный контракт `make check` | 4 | `CONTRIBUTING.md` |

## 4. Гигиена файлов: манифест очистки

Верхняя политика меняется по запросу владельца: репозиторий держит **актуальные
файлы + структурированный changelog**; история хранится в Git (тег
`archive/2026-09-pre-consolidation` до первого удаления) и в леджере. Текущее
правило `docs/README.md` «never prune registered evidence» переписывается в
явную политику `docs/evidence/README.md` (см. решение D-13 в плане).

Ограничения, которые нужно учесть при перемещении:
`tests/contract/test_documentation_map.py` пинит пути `docs/README.md`,
`pivot-ui-only-plan.md`, `architecture-improvement-implementation-plan.md`,
`visual_multi_agent_orchestrator_prd.md`, `visual_orchestrator_plan.md` и их
категории; `tests/test_tutorial_contract.py` — `docs/tutorials/*.md`,
`QUICKSTART.md`, `USER_WORKFLOWS.md`; `docs/reviews/README.md` записывает пути
двух корневых review. Тест «индекс ADR покрывает каждый файл» проверяет только
ссылки, не статусы.

### A. Удалить сейчас (нет риска)

| Элемент | Размер | Примечание |
| --- | --- | --- |
| `.playwright-mcp/console-*.log` | 32K | игнорировался Git; **удалено 18.09** |
| `benchmarks/__pycache__/` | 165K | игнорировался Git; **удалено 18.09** |

### B. Переместить в `docs/archive/` (после merge волны 3, одним коммитом с правкой ссылок)

| Элемент | Размер | Куда | Что править |
| --- | --- | --- | --- |
| `claude_architecture_improvement_review.md`, `codex_architecture_improvement_review.md` | 188K | `docs/archive/reviews/` | пути в `docs/reviews/README.md` (хэши не меняются), 6 входящих ссылок |
| `docs/pivot-ui-only-plan.md`, `architecture-improvement-implementation-plan.md`, `architecture-improvement-agent-team-plan.md`, `task-first-workspace-implementation-plan.md`, `project-native-collaboration-implementation-plan.md`, `project-workspace-ux-plan.md`, `context-pack-gallery-implementation-plan.md`, `feedback-remediation-plan-2026-08-28.md`, `cli-migration-inventory.md`, `handoffs/2026-09-09-ux-improvement-plan.md` | ~500K | `docs/archive/plans/` со штампом статуса | `docs/README.md`, `ROADMAP.md`, `test_documentation_map.py`, входящие ссылки (5–10 на документ) |
| `docs/visual_orchestrator_plan.md`, `docs/visual_multi_agent_orchestrator_prd.md`, `docs/VISION.md`, прозаическая часть `docs/current-product-and-architecture.md` | ~120K | слить в `docs/PRODUCT.md`, оригиналы — в архив | `test_documentation_map.py` (категории), ссылки |
| `docs/agent-platform-implementation-program.md` (1 364 строки), `docs/provider-adapter-architecture-plan.md` | ~110K | живая таблица «Текущая работа» и открытые gates → активный план; остаток — в архив | `docs/README.md`, `ROADMAP.md`, `test_documentation_map.py` (ACTIVE_PROGRAMME) |
| `docs/tutorials/*` (CLI-эра, `BUILD_THE_PRODUCT.md` — placeholder) | — | переписать под UI или архив | `test_tutorial_contract.py` |
| `docs/proposals/starter-packs-integration.md`, `c1-ui-surface-contract.md` | — | заголовок «superseded», затем архив | — |
| `docs/audits/2026-08-16-operator-round4.md`, `real-world-usage-audit.md`, `docs/reviews/2026-08-25-*.md` (5), `2026-08-29-*.md` (4), `docs/audits/2026-08-18-code-quality/a6-*.md` (4) | ~180K | без входящих ссылок; итог — одной строкой в CHANGELOG, файлы — в архив | — |

### C. Нужно решение владельца

| Элемент | Размер | Варианты |
| --- | --- | --- |
| `docs/evidence/**` — 109 файлов, 2.0M, ноль ссылок из кода и тестов; цепочки версий манифестов (`project-registry-source-manifest` ×5, `project-frontend` ×4, `project-host` ×3, `wp-04.1/04.2/15.1/07/11.2` ×2) | 2.0M | (а) оставить только последнюю версию каждого манифеста и интеграционные манифесты волн; (б) вынести всё evidence из основного дерева, оставив ссылку на тег; (в) сохранить как есть. Артефакты зарегистрированы в леджере по хэшу, `doctor` наличие файлов не проверяет |
| `src/agent_commons/ui/static/index.html` (540K) + 16 тестовых файлов + правило одного писателя | ~600K + тесты | Вывести legacy-панель из продукта (ADR 0022, `/` → redirect на `/work`) или оставить как admin-fallback с явной пометкой «legacy» в контракте |
| `.agents/` = `.claude/skills/` | 108K | Единый источник + генерация, или тест на дрейф |
| `.pytest_cache`, `.ruff_cache`, `build/`, `artifacts/` | 9M локально | Уже игнорируются; `artifacts/` добавить в `make clean` или оставить |
| Леджер: 43 задачи в `review`, 21 pre-programme `active/assigned`, ~60 stale review, 104 handoff | — | Разовая приёмка/отмена под авторством владельца (WP-C7) |

### D. Оставить

`assets/agent-commons-hero.png`, `.grok/`, `benchmarks/*.py`, единственные
хэшированные бандлы `ui/static/{work,gallery}`, `uv.lock`, `SUPPORT.md`,
`CLAUDE.md`, `docs/PROTOCOL.md`, `docs/THREAT_MODEL.md`, `docs/BROKER_OPERATIONS.md`,
`docs/archive/*`, `docs/performance/*`, `docs/adr/*`. Мёртвых Python-модулей
грубым поиском не найдено (`gallery_upload` — ложное срабатывание). Tracked
`__pycache__`, `.egg-info`, `.orig/.bak/.DS_Store` отсутствуют.

## 5. Целевой глоссарий (первый уровень UI и документов)

| Термин | Единственное определение | Что убрать |
| --- | --- | --- |
| Проект / Project | Подключённая папка-репозиторий и граница её доски, агентов, задач, разговоров и результатов | «workspace» как пользовательское слово (только для каталога леджера) |
| Навык / Skill | Версионированный набор инструкций в библиотеке сервиса; прав и инструментов не даёт | Коллизия с `commons-*` client skills — называть их «клиентская интеграция» |
| Специализация / Specialization | Провайдеро-независимое определение ответственности и набора навыков; шаблон, из которого нанимают агента | «Role» как синоним |
| Агент / Agent | Именованный экземпляр специализации в проекте: имя, провайдер, профиль, модель, закреплённые версии навыков | «агент» в смысле «окно клиента» — говорить «сессия» |
| Роль / Role | Целевое правило: слово леджера для записи агента (`agent`); в первом уровне UI заменяется на «агент». Сегодня UI ещё говорит «роль» в форме найма, подсказке о модели и вступлении доски (`i18n.json`: `Role name`, `role_model_help`, `board_intro`); переход на «агент» входит в WP-12.2/WP-23, до него оба слова сосуществуют | Заменять в строках UI по мере пакетов волны 4; поля леджера не переименовывать |
| Профиль / Profile | Операторская настройка запуска провайдера (CLI, модель, права), заданная вне проекта | — |
| Blueprint / Шаблон проекта | Версионированное определение команды, задач и зависимостей; применение создаёт агентов и задачи и ничего не запускает | «процесс», голое «шаблон» |
| Задача / Task | Ожидаемый результат, критерии результата и зависимости | «критерии приёмки» → «критерии результата», чтобы развести с приёмкой |
| Прогон / Run | Одна ограниченная попытка агента выполнить задачу | «delegation» — только для механизма брокера, с пояснением при первом употреблении |
| Результат / Output | Файл, изображение или превью, привязанные к точной задаче и агенту-производителю | «artifact/revision» — только для леджера |
| Review / Проверка | Независимая оценка одной точной ревизии; не приёмка | — |
| Приёмка / Acceptance | Записанное решение человека, что проверенная работа принята; единственный зелёный цвет | — |
| Цель / Objective | Каноническая запись «ради чего» набора задач; задача наследует цель применения blueprint, если своей нет | «объектив» |
| Волна / Wave | Упорядоченная группа work packages одного плана, закрываемая одной интеграцией и одним `make check`; только планирование | как UI/леджер-понятие |
| Handoff | Запись завершающейся сессии: состояние, блокеры, следующее действие | `docs/handoffs/` хранит фидбэк и план — переименовать |

## 6. Что этот аудит не утверждает

Не проводился AST-анализ мёртвого кода, не измерялась стоимость CI, не
проверялись сами утверждения планов о прошедших проверках. Числа тестов взяты
из документов и не перепроверялись прогоном. Аудит не является приёмкой волны 3
и не даёт разрешения на удаление файлов: каждое перемещение из раздела 4.B и
каждый пункт 4.C выполняются только по решениям владельца из плана.
