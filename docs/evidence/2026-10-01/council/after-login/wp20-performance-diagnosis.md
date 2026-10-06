# WP-20: профиль одной штатной записи

Проверен неизменяемый `review-final/src/agent_commons`: source SHA-256 `fab6f03a9ca61162e4495cc8758c662cf4720a7588d7cbee169b8e2c4dcc274e`. Использован исходный `review-final/benchmarks/benchmark_work_mutation.py`, SHA-256 `2fb9eddb8e779abf1d9cf554a414e012522d48e4d3cd1002db218a4068253917`, его `build_mutation_fixture(profile='large')` и `_mutation_operation(label='task_create')`. Это тот же workload, что в исходном `wp20-benchmark-final.json`: 3206 событий, 350 manifests. Новый синтетический fixture построен только в `resumed-council/wp20-profile-fixture`; основной checkout/ledger не открывался для записи. Унаследованные state/session env удалены, pycache выключен.

Ровно одна измеренная операция, с cProfile: 24.140 s; fixture build 9.044 s исключён из профиля. Всего 65,160,863 вызова. Ограничитель процесса — 300 секунд. Другие процессы работали; cProfile добавляет накладные расходы. Это диагностика, не benchmark certification, не сравнение производительности до/после и не подтверждение цели ≤1 s. После окончания повторные операции не запускались.

## Доминирующая повторная работа

| Функция | Вызовы | cumulative s |
|---|---:|---:|
| `_VerifiedLedgerView.refresh` | 5 | 17.507 |
| `_verify` | 6763 | 16.030 |
| `EventStore.read_path` | 6413 | 14.756 |
| `ContentPolicy.assert_safe` | 6775 | 8.520 |
| `SchemaRegistry.validate` | 27818 | 6.453 |
| `IdempotencyStore.load_recovery_view` | 2 | 3.374 |
| `ReceiptRecovery._event_map` | 3 | 1.496 |
| `project_events` | 1 | 1.022 |
| `EventStore.append_event` | 1 | 0.0075 |

Времена inclusive: строки вложены друг в друга, суммировать их нельзя. Собственная стоимость `_scan_text` — 3.076 s; regex search вызывается 5,878,107 раз. Есть 31,307 вызовов `_identity`, 64,077 resolve и 767,625 lstat, но выключать проверки containment нельзя.

Причина повторной полной валидации точно видна в source и счётчиках. `manager.py:156` устанавливает `rebuild = set(previous) != set(paths)`. После собственного append `manager.py:1309` вызывает refresh; состав events закономерно увеличился на один, поэтому `_refresh` повторно вызывает `_verify` для **всех** событий. Получается 3206 первоначальных + 3207 после append = **6413** полных event reads. Manifests не изменились, и их полная валидация прошла только 350 раз. `_records_and_snapshot` вызвал refresh на 8.481 s; два вызова из record_event (до и после append) вместе заняли 8.443 s. Таким образом, повторный schema/policy scan старых событий — главная устранимая работа, а не дорогая запись или ожидание внешнего провайдера.

## Минимальный следующий патч

Добавить узкую операцию принятия **собственного подтверждённого нового append** в lock-scoped `_VerifiedLedgerView`, затем оставить нынешний refresh. Она должна использовать существующий `_verify(record.path, events.read_path)` с before/read/after identity, проверить соответствие возвращённому event ID/path и только после этого добавить новый entry в view. Применять лишь к действительно созданному ранее отсутствовавшему пути; retry/repair и любые неоднозначности должны оставлять текущий консервативный путь. Нельзя просто присвоить digest от чтения, не связанного с валидированными bytes.

Последующий refresh по-прежнему читает текущие bytes, hash и resolved path каждого старого файла. Неожиданное добавление/удаление пути, nested write или несовпадение множества остаётся основанием полного rebuild. Изменённый старый файл заново валидируется. Не вводить stat-only cache и не переносить view за пределы write lock. Сохранить pre-append refresh после `_before_append`: trusted hook способен сделать вложенную запись. Так обычная запись перестаёт повторять schema/policy всей коллекции исключительно из-за известного собственного append; никакой проверки новых/изменённых bytes/path или schema не отключается. Ожидаемый эффект — устранение примерно 3206 повторных полных reads в данном workload; время выигрыша требует отдельной проверки после реализации.

Первые проверки патча: счётчик полного чтения каждого неизменённого старого event не растёт после собственного append; неожиданные add/delete и изменение bytes при прежнем размере/mtime не скрываются; оба существующих TOCTOU/parent-path regressions остаются зелёными; nested hook append, retry, receipt migration/conflict сохраняют корректность. После них повторить тот же официальный workload при спокойном окружении, затем общий green contract. Здесь патч не реализовывался и новые тесты не запускались.

Второй по приоритету участок — два `load_recovery_view` (prepare и reconcile: 1.709/1.665 s), три построения `_event_map`/9620 разборов envelope. Его нельзя исправлять простым reuse receipt snapshot: новый receipt и изменённые/невалидные receipts должны оставаться видимыми. Для него потребуется собственная схема identity/update внутри lock, поэтому это отдельный шаг после узкого исправления собственного append. Даже устранение дублированной валидации не доказывает достижение ≤1 s: первый cold scan и recovery всё ещё существенны.

Полные агрегаты с caller edges: `wp20-profile-aggregate.json`; таблицы top cumulative/self: `wp20-profile-aggregate.txt`; воспроизводящий wrapper: `wp20-profile-one.py`. Программа не снимала profiler locals, receipt contents или provider data.
