# WP-20: независимый статический review и изолированное воспроизведение

Граница: immutable snapshot `source`, исходники и канонические записи основного проекта не менялись. Это не formal approval и не дополнительный голос консилиума. Прочитаны diff и ADR 0023, выполнялись только синтетические воспроизведения в автоматически удаляемых подпапках `wp20-review` через основной `.venv/bin/python`, `PYTHONPATH` указывал на snapshot `src`.

## P1 — legacy abandonments импортируются мимо рабочего receipt view

`src/agent_commons/storage/receipt_recovery.py:421–422`: `copy_legacy_abandonment()` пишет tombstone на диск, но `receipt_view.abandonments` остаётся прежним. Далее `_derive_receipts_and_tombstones` (`:320–336`) и финальный `status` (`:447`) читают прежний view. Поэтому migration сообщает успех без reconciliation, а конфликтующий tombstone не вызывает прежний отказ.

Подтверждены два сценария. Для точного tombstone + вернувшегося canonical event: reconcile возвращает `ok=true`, `reconciled_tombstones_count=0`, reconciliation отсутствует; новый `receipt_status()` возвращает `ok=false`, один `tombstone_matches`. При изменённом `semantic_sha256` legacy tombstone reconcile тоже возвращает `ok=true` и пустые conflicts; новый status видит один конфликт. Это нарушает ADR 0023: прежние проверки/миграция должны сохраниться.

Минимальная правка: после успешного `copy_legacy_abandonment(abandonment)` добавить `receipt_view.abandonments[str(abandonment['key_digest'])] = dict(abandonment)`. Повторная `_preflight_event_identities(event_map, receipt_view=receipt_view)` после объединения legacy abandonments и до anchor позволит выявлять конфликт раньше, чем `_derive_receipts_and_tombstones`; это узкое улучшение порядка, не широкая миграция.

Два регрессионных теста в `test_receipt_bulk_load.py` должны переиспользовать setup из `reproduce.py::legacy_tombstone`:

1. Exact: один вызов reconcile создаёт reconciliation, возвращает `reconciled_tombstones_count == 1`, `ok == True`; свежий status тоже healthy и содержит этот digest в `reconciled_tombstones`, не в `tombstone_matches`. Повтор reconcile не создаёт второй документ.
2. Conflicting: first reconcile вызывает `IntegrityError` с `abandonment conflicts` (либо соответствующим нынешним preflight текстом); не объявляет migration healthy; reconciliation отсутствует. Для усиленного preflight варианта проверить отсутствие нового migration marker.

Нынешний migration test (`tests/storage/test_receipt_bulk_load.py:371–396`) покрывает только legacy receipt, без tombstone, поэтому дефекта не обнаруживает.

## P2 — cached reuse не перепроверяет resolved storage boundary

`services/manager.py:140–161`: `_identity()` делает lstat конечного файла и digest bytes, но не проверяет resolved canonical-relative path, который входит в `EventStore.read_path()` (`storage/events.py:236–258`). В одной lock hold после первого чтения заменить каталог дня событий symlink на каталог с теми же bytes вне canonical storage. Путь glob и digest сохраняются, cached snapshot принимает запись; непосредственный `read_path` отказывает `event path is outside canonical storage`. Это воспроизведено `reproduce.py::cached_parent_symlink`.

Условие узкое: out-of-band изменение файловой структуры во время одной write-lock hold; обычный сотрудничающий writer через lock так сделать не должен. Тем не менее ADR 0023 обещает неизменное покрытие проверки path и invalidation на structural surprises. Digest и конечный lstat не доказывают identity родительской цепочки. Исправление: перед reuse проверить те же resolved-relative layout/root invariants либо включить валидированную resolved identity в cache key с полной повторной `read_path` при её изменении. Не вводить более широкую новую symlink policy без необходимости.

Минимальный тест: вариант из repro должен вызвать тот же IntegrityError на втором `_records_and_snapshot()`; аналогичный случай manifests полезен, поскольку у него такая же граница `resolve().relative_to()`.

## Остальная оценка и проверки

Обычная reentrancy/reacquisition: view создаётся после outer lock и сбрасывается в finally (`manager.py:805–828`); nested write делает refresh перед index/append и после append. Receipt view заново грузится для post-append reconcile: это консервативно безопаснее одного долгоживущего view. Не нашёл самостоятельного нового concurrent-writer обхода при соблюдении существующего lock. Per-manager depth без thread identity и fork invalidation заслуживает отдельного теста, но thread-reentrancy ограничение существовало до patch; не выдаю его за новый доказанный дефект.

Before/read/after digest фиксирует обычную гонку изменения bytes, но это не атомарное чтение и не математическое доказательство против специально организованной ABA-гонки; модель угроз уже ограничивает writers, игнорирующих lock. Не считаю теоретическую ABA отдельным блокером этого patch без дополнительного воспроизведения/требования.

Текущие parity-тесты сравнивают два view через один новый `status`, поэтому они не заменяют независимую проверку старого поведения на переходах. Начальный gate: два legacy-теста выше и parent-symlink; затем tests/services/test_verified_ledger_view.py, tests/storage/test_receipt_bulk_load.py, tests/contract/test_h2_checkout_recovery_contract.py, существующие crash/retry/concurrent-write и golden replay. После этого полный make check и benchmark small/large с phase table. Первая полная проверка каждого lock и полная перевалидация после нового path остаются; цели ≤1 с нельзя считать достигнутыми по сокращению receipt accessors.

Запуск воспроизведения из основного checkout:

```sh
PYTHONPATH=/private/tmp/commons-council-20261001/source/src .venv/bin/python /private/tmp/commons-council-20261001/wp20-review/reproduce.py
```

Полные тесты и benchmark этим review не запускались. Сохранённый `reproduction-result.json` содержит вывод изолированного воспроизведения.
