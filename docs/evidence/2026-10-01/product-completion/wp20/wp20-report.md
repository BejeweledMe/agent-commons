# WP-20: собственный append и владение lock одним потоком

Изменения подготовлены только в `/private/tmp/commons-product-wp20-20261001`. Основной checkout, его ledger и operational state не изменялись. Интегратор применяет `implementation.patch` по двум файлам; исходные и конечные SHA-256 приведены в `integration-manifest.json`. Перед интеграцией проверить исходные bytes и объединить независимые изменения `manager.py` по delta. Исходный Python source hash совпал с зафиксированным `fab6f03a9ca61162e4495cc8758c662cf4720a7588d7cbee169b8e2c4dcc274e`.

`_VerifiedLedgerView.accept_own_append` принимает только созданный самим store новый event, без repair, maintenance и совпадения с уже известным path/event ID. Новый файл проходит существующий `_verify` с identity до/после полноценного `read_path`, включая canonical JSON, schema, policy и storage containment. Результат связывается с возвращённым append record по path, event ID, semantic-document hash и полному event body. Любое несовпадение оставляет обычный refresh. Он по-прежнему читает и хеширует все старые files; неожиданные additions/deletions вызывают полный rebuild. Refresh перед append, после trusted hook, сохранён. Receipt recovery не изменён.

Дополнительно исправлен воспроизведённый concurrency defect: второй поток того же `CommonsManager` видел чужой `_write_lock_depth` и проходил как nested writer. Per-manager `threading.RLock` теперь охватывает canonical lock и доступы к shared verified view из snapshot, identity lookup и manifest checks. File lock между managers/processes сохранён, nested acquisition одного потока работает. Исправлена также неточная прежняя фраза docstring о «весь cascade либо ничего»: lock сериализует операции, но не обеспечивает rollback нескольких immutable writes.

## Измерение

Запущен неизменённый `benchmark_work_mutation.py`, SHA-256 `2fb9eddb8e779abf1d9cf554a414e012522d48e4d3cd1002db218a4068253917`, `build_mutation_fixture(profile='large')`, только исходный `task_create` workload, три пары cold/warm для каждой версии. Fixture начинается с 3206 events и 350 manifests; каждая пара добавляет два events. Cold означает новый manager в уже импортированном процессе; warm — второй вызов того же manager. Build исключён. Версии запускались последовательно. Другие builders могли работать на хосте; это не сертификация idle-host latency. Полные фазовые таблицы и samples находятся в `benchmark-before.json` и `benchmark-after.json`; wrapper — `benchmark-comparison.py`.

| Метрика | До | После |
|---|---:|---:|
| Cold median | 12.857 s | 8.553 s |
| Cold p95 (3 samples) | 14.031 s | 8.655 s |
| Warm median | 12.816 s | 8.615 s |
| Warm p95 (3 samples) | 12.852 s | 8.900 s |
| Первый sample, event reads | 6413 | 3207 |
| Первый sample, manifest reads | 350 | 350 |
| Cold canonical validation median | 7.923 s | 4.050 s |
| Warm canonical validation median | 7.894 s | 4.050 s |

Устранён ровно повторный полный проход по 3206 прежним events в первом sample. Median ускорился примерно на треть. Цель ≤1 s **не достигнута**. Cold scan, hashing, projection и receipt prepare/reconcile остаются; receipt snapshot не кешировался. Эти данные нельзя смешивать с иной нагрузкой, process-cold startup или более ранним профилированным 24.140 s запуском.

## Проверки и границы

Регрессии охватывают own-append read count, неожиданный extra path, изменение прежнего файла при сохранённых размере/mtime, удаление, symlink, fallback для retry/repair/maintenance/mismatched hash, crash после immutable append с identical retry, nested hook, TOCTOU, parent symlink, competing dependency cycle и same-manager thread readers/writers. Базовый source воспроизводит провал нового теста cross-thread hold (`thread-baseline-reproduction.txt`).

Выполнен focused baseline pass: 97 tests, 32.50 s. После защиты read-side shared view расширенный набор прошёл: **384 passed in 44.67 s** (`final-focused-tests.txt`): verified view, весь storage/domain/security, manager, task edits и checkout recovery contract. Ruff по двум изменённым файлам чистый. Полный `make check` здесь не запускался: он обязателен после интеграции с параллельными изменениями, эта работа не объявляет весь tree green. Installed MCP reinstall, canary, assets и commits не выполнялись; ими управляет интегратор после объединения.
