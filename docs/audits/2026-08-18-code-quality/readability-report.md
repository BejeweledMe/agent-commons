# Линза 5 — читаемость человеком

Аудит выполнен по `4e0ec8f` (`git rev-parse --short HEAD`). Прочитаны все 353
файла под `src/` (29 952 строки; включая схемы, шаблоны и статический UI).
`src/agent_commons/ui/static/index.html` не изменялся. Этот документ — только
аудит; поведение и формат данных не меняются.

## Короткий вывод

Главный маршрут системы распознаётся: CLI/MCP/UI вызывают `CommonsManager`,
домен валидирует переход, `EventStore` атомарно пишет событие, а projection
строит снимок. Читать его целиком приходится потому, что границы между
оркестрацией, преобразованием данных и регистрацией событий проходят внутри
нескольких больших функций, а не по отдельным именованным операциям.

## Три сквозных маршрута

### 1. Запись в реестр

Маршрут: `cli.py` → `CommonsManager` в `services/manager.py` → доменные
`lifecycle.py`/`registry.py` → `storage/events.py` и `storage/idempotency.py` →
`domain/projection.py` → `index/sqlite.py`/`views.py`.

При чтении пришлось открыть 8 основных файлов (и ещё общие конфигурацию,
схемы и atomic storage). Нить теряется в менеджере: публичная команда выглядит
как одна операция, но её смысл распределён между сборкой payload, проверкой
ревизии, выбором idempotency namespace и записью. В `EventStore.append` хорошо
виден инвариант, но чтобы понять, почему сначала резервируется idempotency и
только затем выбирается путь файла, нужно читать `idempotency.py` и
`atomic.py` вместе. В projection `_project_events_once` сначала сортирует весь
поток, собирает отношения, вычисляет инвалидации, разрешает corrections и
только затем применяет события; имя функции не сообщает об этих пяти фазах.

Пример удачного места: `EventStore.append_event` отделяет публичные поля
события от внутреннего `append`, поэтому точка записи остаётся проверяемой.

### 2. Делегация

Маршрут: `cli.py::delegation_create` → `CommonsManager.create_delegation` →
`domain/lifecycle.py::validate_transition` → `runtime/broker.py` и
`runtime/attempts.py` → `runtime/subprocess_runner.py` →
`services/delegation_runtime.py` → терминальное событие менеджера.

Пришлось открыть 9 файлов. Начальная команда читается легко и явно печатает
следующие безопасные шаги. Нить теряется в том, что `delegation_create`
формирует подсказки брокеру, а фактические проверки профиля, budget,
child-session и terminal state живут в других слоях. `AttemptStore.reserve` и
`transition` также смешивают чтение документов, блокировку, budget и
жизненный цикл попытки; имена методов не показывают, что это файловый журнал
попыток, а не только объектный state machine.

Удачный контраст — `build_server` явно проверяет binding delegated MCP до
регистрации worker-инструментов, а `AttemptState.terminal` делает правило
терминальности читаемым в одном месте.

### 3. Представление

Маршрут: `storage/events.py` → `domain/projection.py` → `services/manager.py` →
`ui/context.py` → `ui/graph.py`/`ui/server.py` → статический клиент.

Пришлось открыть 7 основных файлов. Главная потеря нити — `UIContext` является
одновременно загрузчиком snapshot, адаптером представлений, каталогом,
поиском, командным фасадом и launch coordinator (1406 строк). Например,
`runs`, `attention`, `engagements` и `catalog` возвращают разные
неименованные словари, поэтому контракт виден только внутри каждой функции.
Хорошо, что `refresh_if_changed` использует fingerprint append-only файлов и
что `snapshot_frame` фиксирует последовательность вместе с данными: эти
решения объяснены докстрингами и помогают понять кэширование.

## Находки

### R-1 — крупный менеджер скрывает смысл публичных операций

- **Где:** `src/agent_commons/services/manager.py:1-3199`, публичная
  поверхность `CommonsManager`.
- **Тяжесть:** `major`.
- **Что:** один фасад объединяет lifecycle сущностей, event append,
  projections, sessions/claims, reviews, delegations, artifacts и runtime,
  поэтому имя метода не показывает, в каком инварианте находится операция.
- **Цена:** для изменения одной команды приходится читать соседние домены и
  искать общий порядок действий; это повышает риск нарушить expected revision,
  idempotency или authorisation при поддержке.
- **Правка:** вынести тематические command services (например
  `TaskCommands`, `DelegationCommands`, `ReviewCommands`, `CoordinationCommands`)
  с узким интерфейсом, оставив `CommonsManager` совместимым фасадом. Сначала
  перенести одну read-only группу, затем write-группы; старые методы оставить
  как предупреждающие прокси до третьего шага. Каждый перенос — отдельный
  поведенчески нейтральный коммит с `make check`.
- **Размер:** `L`.
- **Что сломается:** внутренние импорты, CLI/MCP/UI вызовы и тестовые doubles;
  persisted events не должны измениться. Миграция: новые сервисы рядом,
  старые методы-прокси и тесты на одинаковый payload/revision, удаление прокси
  отдельным решением.

### R-2 — `build_server` читает как каталог инструментов, но содержит весь wiring

- **Где:** `src/agent_commons/mcp/server.py:487-1644`, `build_server`.
- **Тяжесть:** `major`.
- **Что:** функция на 1013 строк одновременно разрешает binding, строит
  scoped reader, вычисляет grants, определяет visibility инструментов и
  регистрирует обработчики.
- **Цена:** изменение одного MCP инструмента требует держать в голове
  безопасность worker binding и порядок регистрации; ошибка может либо
  раскрыть инструмент не тому worker, либо убрать его из каталога.
- **Правка:** выделить без изменения поведения `resolve_worker_binding`,
  `build_scoped_services`, `register_read_tools`, `register_worker_tools` и
  `register_control_tools`; сохранить тот же `build_server` как тонкий
  композиционный корень. Добавить тест каждого набора регистрации и тест
  каталога до/после.
- **Размер:** `L`.
- **Что сломается:** MCP tool names/schema and catalog-only handshake;
  сохранить старые имена инструментов и проверить их snapshot/contract tests.

### R-3 — UI-контекст скрывает несколько разных словарей контрактов

- **Где:** `src/agent_commons/ui/context.py:112-1406`, особенно
  `meta`, `graph`, `catalog`, `attention`, `runs`, `engagements` и command
  methods.
- **Тяжесть:** `major`.
- **Что:** один класс одновременно читает, форматирует, мутирует каталог,
  создаёт tasks/delegations и запускает provider threads.
- **Цена:** изменение представления может затронуть запись или запуск; разные
  `dict[str, Any]` дают читателю мало подсказок о полях и делают сквозной маршрут
  зависимым от неявных соглашений фронтенда.
- **Правка:** разделить read model adapters, catalog commands и launch
  coordinator; в памяти ввести именованные dataclass/TypedDict для публичных
  frame/meta/run/attention результатов. Оставить `UIContext` совместимым
  переходным фасадом и не менять JSON wire shape в структурном коммите.
- **Размер:** `L`.
- **Что сломается:** HTTP handlers, статический клиент и UI behavior harnesses;
  мигрировать по endpoint-группам, сохраняя поля и порядок перехода.

### R-4 — длинные domain-функции не называют свои фазы

- **Где:** `src/agent_commons/domain/projection.py:844-1070`
  (`_project_events_once`) и `src/agent_commons/domain/lifecycle.py:118-383`
  (`validate_transition`).
- **Тяжесть:** `major`.
- **Что:** функции содержат последовательные фазы (normalization,
  invalidation/correction resolution, application; либо event-family dispatch,
  revision/state checks и special authorisation), но читаются как единый
  условный блок.
- **Цена:** читателю трудно понять, какая проверка является prerequisite и где
  проходит смысловой шов; исправление порядка может менять projection или
  принимать запрещённый transition.
- **Правка:** выделить чистые именованные helpers по фазам, сохранив текущий
  порядок и тесты; сначала зафиксировать phase-level characterization tests,
  затем переносить код.
- **Размер:** `M` для одной функции, `L` для обеих.
- **Что сломается:** projection replay, correction semantics и lifecycle event
  acceptance; persisted format не менять.

### R-5 — повторяющиеся инфраструктурные имена затрудняют поиск инварианта

- **Где:** `runtime/attempts.py:132-172`, `runtime/communication.py`,
  `runtime/tool_audit.py` и `coordination/sessions.py`.
- **Тяжесть:** `minor`.
- **Что:** одинаковые приватные helpers (`_iso`, private-directory checks,
  exclusive lock) реализованы в нескольких местах, а не названы общей
  инфраструктурной концепцией.
- **Цена:** читатель не знает, одинаковы ли symlink/O_NOFOLLOW guarantees;
  исправление одного варианта может оставить другое место с иным поведением.
- **Правка:** после поведенческих characterization tests собрать общий
  private-storage/locking helper и заменить реализации по одной; не менять
  формат файлов.
- **Размер:** `M`.
- **Что сломается:** file safety and concurrent writers; проверить POSIX/Windows
  branches и storage/runtime tests.

## Что проверено и признано здоровым

- `EventStore` явно запрещает caller-у назначать `event_id` и `recorded_at`,
  проверяет canonical bytes и изолирует idempotency conflict; это хорошая
  последовательность чтения.
- `domain/lifecycle.py` использует таблицы спецификаций событий и отдельные
  helpers для creation/transition вместо копирования всех правил по CLI.
- Имена и докстринги в `cli.py` на границе команд, в `AttemptState`,
  `ScopedRepoReader` и `UIContext.refresh_if_changed` объясняют внешний
  контракт или важный инвариант, а не пересказывают каждую строку.
- `storage/atomic.py`, схемы ресурсов и небольшие `core`/`security` модули
  имеют узкую причину меняться и читаются целиком.
- Статический UI не оценивался как место для рефакторинга: он намеренно
  исключён из изменений согласно frontend contract.

## Порядок безопасного улучшения

1. Зафиксировать characterization/contract tests для event payloads,
   projection snapshots, lifecycle transitions и MCP tool catalog.
2. Вынести read-only phase helpers из projection/lifecycle и MCP registration;
   после каждого переноса запускать `make check`.
3. Разделить UI read models, затем вынести `CommonsManager` command groups,
   оставляя старые фасады и имена в transition window.
4. Собрать повторяющиеся locking helpers и только после этого удалить
   переходные прокси. Отдельными коммитами менять поведение, если оно вообще
   потребуется.
