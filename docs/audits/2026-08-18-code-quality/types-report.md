# Аудит модели данных и типов

**Замороженный коммит:** `4e0ec8f`
**Линза:** модель данных и типы
**Объём:** всё дерево `src/` (Python, JSON Schema, встроенные skill/template/static-ресурсы)

## Итог

Главная проблема не в отсутствии проверки входных JSON: на границе диска события
проверяются JSON Schema и доменными валидаторами. Проблема начинается сразу после
проверки: данные снова становятся `Mapping[str, Any]`/`dict[str, Any]`, а состояния,
типы событий и идентификаторы теряют уже доказанные ограничения. Поэтому mypy или
редактор не видит ни неверного ключа, ни перепутанного ID, ни нового состояния,
которое забыли добавить в один из параллельных словарей.

Замер линзы подтверждён: наиболее плотные скопления `dict[str, Any]` —
`services/manager.py` (112), `mcp/server.py` (69), `ui/context.py` (41), затем
`services/delegation_runtime.py` (18), `services/communication.py` (16) и
`domain/projection.py` (15). Это не шесть отдельных косметических задач, а один
непрерывный неименованный транспорт от хранилища до CLI/MCP/HTTP.

## Карта движения данных

1. **JSON на диске → проверенный документ.** `storage/events.py` и
   `storage/manifests.py` читают строгий canonical JSON; `core/schema_registry.py`
   проверяет envelope и payload по packaged JSON Schema. Здесь внешний формат надо
   сохранить без изменений.
2. **Проверенный документ → домен.** `EventRecord.event` остаётся
   `Mapping[str, Any]`; `domain/validation.py` повторно описывает обязательные поля,
   а `domain/lifecycle.py` извлекает их строковыми ключами.
3. **Домен → проекция.** `domain/projection.py` складывает payload, actor и
   вычисленное состояние в один `dict[str, Any]`; `ProjectSnapshot` хранит по такому
   словарю для каждого вида сущности.
4. **Проекция → сервисы.** `CommonsManager`, communication/delegation services и
   runtime читают те же словари через `.get()` и создают новые словари payload.
5. **Сервисы → публичные границы.** CLI, MCP и HTTP/UI снова передают
   `dict[str, Any]`; `views.py` и `ui/graph.py` дополнительно формируют собственные
   неименованные представления.
6. **Отдельный operational state.** sessions, claims, attempts и communication
   имеют хорошие frozen dataclass-модели, но часть парсеров принимает исходный
   `Mapping[str, Any]`, преобразует значения через `str(...)` и оставляет состояния
   строками.

Целевая граница: JSON Schema остаётся источником истины для байтов на диске;
сразу после успешной проверки документ разбирается в именованные неизменяемые
in-memory типы, домен работает только с ними, а явный serializer собирает ровно
прежний JSON. `TypedDict` уместен для публичных JSON-shaped результатов; frozen
dataclass — для сущностей и инвариантов домена.

## Находки

### P1 — `TypedRef` не связывает `kind` с префиксом `id` (`major`)

- **Где:** `src/agent_commons/core/refs.py:15-35`.
- **Что:** тип, названный `TypedRef`, принимает, например,
  `{"kind": "task", "id": "session.…"}`, потому что проверяет поля независимо.
- **Цена:** перепутанный идентификатор проходит общую нормализацию и может найти
  неверный объект, дать вводящее в заблуждение «не существует» либо записать
  семантически ложную связь; статическая типизация все ID как `str` этого не ловит.
- **Правка:** добавить общие branded-типы (`TaskId`, `SessionId`, `EventId`, …) на
  основе `NewType` для статических сигнатур и одну runtime-фабрику `TypedRef`, которая
  проверяет соответствие `kind` префиксу через `is_typed_id`. Открытые extension-kind
  хранить отдельной веткой `ExtensionRef`, не ослабляя core kinds. JSON serializer
  по-прежнему выдаёт две строки `kind`/`id`.
- **Размер правки:** M.
- **Что сломается:** вызовы `normalize_ref`/`parse_ref`, схемы не меняются; нужны
  негативные тесты на cross-kind ID и тесты разрешённых extension refs.

### P2 — валидированный event envelope теряет тип на входе в домен (`major`)

- **Где:** `src/agent_commons/storage/events.py:25,38-49,71-90,170-203`;
  `src/agent_commons/domain/validation.py:20-229,613-715`.
- **Что:** после двух runtime-проверок `EventRecord.event`, payload, actor,
  provenance и relations всё равно имеют `Mapping[str, Any]`, а допустимые поля
  одновременно описаны JSON Schema и ручным `EVENT_SPECS`/ветвями
  `validate_payload`.
- **Цена:** новое или переименованное поле может быть добавлено в схему, но забыто
  в ручном разборе (или наоборот); downstream-код компилируется с любым ключом и
  узнаёт о расхождении только на конкретном replay/write пути.
- **Правка:** ввести `EventEnvelope` и именованные payload-типы по семействам
  событий; после SchemaRegistry выполнять единственный `parse_event`, возвращающий
  discriminated union по `event_type`. `EventSpec` должен ссылаться на parser и
  transition metadata, а serializer — собирать существующий envelope byte-for-byte.
  Сначала покрыть maintenance и delegation events, где ошибка влияет на восстановление
  и дочерние процессы, затем task/review, остальные truth-сущности и extensions.
- **Размер правки:** L.
- **Что сломается:** внутренние сигнатуры storage/domain/services и тестовые
  fixtures; persisted schema, имена событий и JSON не должны измениться.

### P3 — `ProjectSnapshot` стирает форму всех сущностей (`major`)

- **Где:** `src/agent_commons/domain/projection.py:114-139,141-169,213-239`.
- **Что:** двенадцать разных коллекций объявлены одинаково как
  `dict[str, dict[str, Any]]`, а `_apply` смешивает payload, вычисленные поля, actor
  и revision через dictionary spread.
- **Цена:** изменение одной сущности требует искать строковый ключ по всему дереву;
  опечатки и чтение поля не той сущности не ловятся, а merge допускает случайное
  затенение вычисленного или payload-поля и повышает риск неверной проекции.
- **Правка:** завести frozen record-типы `TaskRecord`, `ReviewRecord`,
  `DelegationRecord` и т. д. с общим `ProjectedRecord`-ядром; заменить универсальный
  `_apply` тематическими чистыми reducers, а JSON-shaped `SnapshotView` собирать
  только на выходе. Не менять порядок replay и persisted event format.
- **Размер правки:** L.
- **Что сломается:** domain lifecycle, manager/views/UI и большинство projection
  fixtures; рефакторинг должен идти отдельными behaviour-preserving коммитами по
  одному семейству сущностей с `make check` после каждого.

### P4 — жизненные циклы описаны несколькими несвязанными наборами строк (`major`)

- **Где:** `src/agent_commons/domain/projection.py:15-71`;
  `src/agent_commons/domain/lifecycle.py:37-84`;
  `src/agent_commons/domain/validation.py:20-229,663-715`;
  `src/agent_commons/services/delegation_runtime.py:69-78`.
- **Что:** результирующие состояния, разрешённые исходные состояния, event specs,
  verdicts/reason codes и runtime terminal states поддерживаются отдельно.
- **Цена:** добавление перехода может успешно пройти валидацию, но проецироваться
  не в то состояние или остаться non-terminal для broker; такая ошибка способна
  оставить delegation занятой, разрешить/запретить неверный переход или неверно
  показать состояние пользователю.
- **Правка:** ввести `StrEnum`/`Literal` для каждого lifecycle и единый typed
  `TransitionSpec(event_type, from_states, to_state, entity_kind, payload_parser)`;
  projection, validation и runtime должны получать множества из него. Открытые
  extension event types остаются строками только на extension boundary.
- **Размер правки:** L.
- **Что сломается:** lifecycle/projection/runtime tests и импорты констант; JSON
  продолжает сериализовать те же строковые значения.

### P5 — operational parsers молча принимают неизвестный `status` (`major`)

- **Где:** `src/agent_commons/coordination/claims.py:109-127,178-204`;
  `src/agent_commons/coordination/sessions.py:232-257,322-352`.
- **Что:** `Claim.status` и `Session.status` — произвольные строки; парсеры делают
  `str(value.get("status", "active"))`, причём claim проверяет mode, но не status,
  а session не проверяет ни одно допустимое значение состояния.
- **Цена:** опечатка в audit record не вызывает integrity error: объект просто
  считается неактивным; пропущенный status, наоборот, считается active. Это может
  ошибочно освободить claim либо признать сессию доступной/недоступной.
- **Правка:** ввести `ClaimStatus`/`SessionStatus` как `StrEnum`, строго разбирать
  присутствующее значение и явно отделить legacy default (если он нужен для уже
  записанной истории) от текущей схемы. Не менять существующие записи на диске.
- **Размер правки:** S.
- **Что сломается:** malformed-record tests; возможно, исторические fixtures без
  `status` — их совместимость надо зафиксировать отдельным legacy parser test.

### P6 — публичные границы повторно стирают уже известную форму (`major`)

- **Где:** `src/agent_commons/services/manager.py:95-134,229-376,1130-1203`;
  `src/agent_commons/mcp/server.py:507-1500`; `src/agent_commons/ui/context.py:239-386`;
  `src/agent_commons/views.py:145-234`.
- **Что:** сервисный фасад, MCP, UI и views обмениваются универсальными словарями,
  поэтому контракт каждого результата существует только в реализации и тестах.
- **Цена:** изменение ключа в manager не требует согласованной правки потребителей;
  дефект проявляется поздно как пустое поле/неверный JSON, а IDE не может показать
  полный публичный контракт.
- **Правка:** после типизации projection ввести узкие `TypedDict` DTO для каждого
  public response/request и использовать их в manager protocols, MCP tools и UI.
  Старые поля сохранять на migration window: новое имя рядом, предупреждение на
  старом, удаление отдельным третьим шагом.
- **Размер правки:** L.
- **Что сломается:** аннотации и contract tests CLI/MCP/HTTP; наблюдаемое поведение
  не должно меняться в структурных коммитах.

## Порядок полной типизации

1. **Первые 20%: защита от неверного поведения.** P5 (strict operational status),
   затем P1 (kind-aware refs), затем typed delegation/maintenance envelope из P2.
   Это небольшие швы, которые защищают блокировки, восстановление и canonical
   outcomes до большого переезда.
2. **Единый словарь lifecycle.** Реализовать P4 для delegation и task/review,
   сохранив все строки на диске; только после этого расширять на остальные сущности.
3. **Typed event boundary.** Завершить P2 семейство за семейством. На каждом шаге
   parser + serializer обязаны round-trip существующие fixtures без изменения JSON.
4. **Typed projection.** Выполнять P3 вертикальными срезами: delegation,
   task/review, thread/handoff, truth/evidence, agents. Каждый срез переводит reducer,
   lifecycle и service consumer вместе, но не меняет поведение.
5. **DTO наружу.** P6: manager → MCP/CLI, затем manager → UI/views. Публичные
   переименования только через трёхшаговое окно совместимости.
6. **Хвост `Any`.** После вертикальных срезов запретить новые broad mappings в
   domain/services проверкой lint/type-check и разбирать оставшиеся diagnostics,
   extension payload и genuinely arbitrary JSON по отдельности. `Any` допустим лишь
   до parser или внутри явно названного extension value.

## Что проверено и признано здоровым

- `core/schema_registry.py` проверяет envelope и payload до записи и после чтения;
  связь `payload_schema` с `event_type` проверяется явно. Это правильная runtime
  граница, её не надо заменять pydantic/attrs.
- `storage/events.py` сохраняет canonical bytes, повторно валидирует перед записью
  и после чтения и держит `EventRecord` frozen. Persisted-формат менять не нужно.
- `runtime/model.py`, `runtime/attempts.py` и `runtime/communication.py` показывают
  подходящий локальный образец: frozen dataclass + `StrEnum` + строгий
  `from_mapping`; его стоит распространить, а не вводить новую dependency.
- `core/refs.py` правильно запрещает вывод dependency edges из имён полей и имеет
  явный `{kind, id}` carrier; исправить нужно только связь kind↔prefix.
- Современный стиль `| None`, immutable dataclass и структурные Protocol соблюдены.
  Проверенные Protocol-ы имеют реальные границы подмены (filesystem resources,
  runner/provider, telemetry/tracing); кандидатов на удаление только ради
  «упрощения типов» не найдено.
- JSON Schema ресурсов отделены от in-memory модели. Предлагаемый план не меняет
  ни schema names, ни event semantics, ни уже записанные документы.
- Встроенные skill/template/YAML/static-ресурсы не содержат альтернативной
  Python-модели доменных сущностей; типовой разъезд сосредоточен в Python ↔ JSON
  Schema ↔ public DTO, а не в этих ресурсах.

## Решения человека

Миграция данных не требуется и не предлагается. Единственный вопрос, который
следует явно закрепить перед P5: отсутствие `status` в старых session/claim records
является поддерживаемым legacy-форматом или integrity error. В обоих вариантах новые
записи должны иметь обязательный enum status; различается только read-path для
старой истории.
