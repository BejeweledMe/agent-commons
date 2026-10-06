# WP-20: два ограниченных устранения повторной работы

Работа выполнена в новой копии `/private/tmp/commons-wp20-next-impl-20261001`. Это отдельный increment после ранее принятого own-append/RLock; прежние Grok/WP-20 evidence не заменяются. Основной checkout, ledger, assets, dependencies и provider state не изменялись. Исходный package SHA-256: `fe98a42e588e29d79198d109627814f22d496272d1447732605b5dba44169fe4`; конечный: `9b3368df09bceb1a5196663937886fbc0c7705ee3b814562f771463763868196`.

## Изменения и проверки

- `_task_evidence_bindings` возвращает пустой список до snapshot только для явного пустого `artifact_refs`. `None` по-прежнему сохраняет ранее связанные evidence и отказывает при stale bindings; непустые refs связываются прежним путём. `record_event` сохраняет весь integrity/lifecycle/security/receipt preflight внутри write lock. У complete/submit с пустыми refs исчезает второй полный внешний read/replay.
- `_VerifiedLedgerView._refresh` при rebuild/cache miss сразу вызывает неизменённый `_verify`. Удалён только лишний предварительный raw read/hash, который уже не мог привести к reuse. Before/read/after, retries при TOCTOU, path containment, schema и policy неизменны. Cache hit продолжает перечитывать и хешировать bytes.

422 tests passed in 68.18 s: новые task-evidence tests, verified view, manager/task edits, весь storage/domain/security, checkout recovery contract. Это включает corruption, retry, crash/recovery, nested hook, same-manager и process concurrency. Три новых performance regressions ожидаемо падают на точном unmodified baseline: initial identity count и single-pass complete/submit. Ruff check/format чистые; `git apply --reverse --check incremental.patch` успешен. Полный интегрированный `make check` остаётся gate интегратора.

`incremental.patch` изменяет только manager.py, tasks.py, test_verified_ledger_view.py и добавляет test_task_evidence_snapshot.py. Exact base/result hashes — `integration-manifest.json`; source/tests заморожены после проверок, последующая работа только измерительная.

## Протокол измерений

Один запуск неизменённого CLI `benchmarks/benchmark_work_mutation.py --profile large --repeats 2 --json`, SHA-256 `2fb9eddb8e779abf1d9cf554a414e012522d48e4d3cd1002db218a4068253917`. Затем отдельно один cProfile task_create на новом synthetic large fixture через тот же `_mutation_operation`. Каждый fixture начинается с 3206 events и 350 manifests. Fixture build исключён. Cold — новый manager в импортированном процессе; warm — второй вызов того же manager. CLI использует все шесть штатных mutations, fixture последовательно растёт. Profiling не выполняется одновременно с CLI.

Хост мог выполнять интегрированный root make check и другую работу. Тайминги диагностические, concurrent-host, не idle-host certification. Два samples дают слабую статистику; p95 — интерполяция. Benchmark baseline на `fe98...` отдельно не запускался, поэтому проценты ускорения этому increment не приписываются. Предыдущий Grok прогон имел другой source hash. cProfile wall-time включает overhead и не сравнивается с unprofiled latency.

## Результаты нового benchmark

| Mutation | Cold median / p95, s | Warm median / p95, s | Reads cold / warm |
|---|---:|---:|---:|
| task_create | 11.378 / 11.520 | 11.390 / 11.467 | 3558 / 3559 |
| task_take | 11.281 / 11.477 | 11.310 / 11.521 | 3562 / 3563 |
| task_complete | 11.265 / 11.327 | 11.421 / 11.562 | 3566 / 3567 |
| task_submit | 11.818 / 12.074 | 11.584 / 11.622 | 3570 / 3571 |
| decision_propose | 11.704 / 11.847 | 11.740 / 11.813 | 3574 / 3575 |
| panel_http_task_create | 11.985 / 12.091 | 11.850 / 11.961 | 3578 / 3579 |

Complete/submit теперь имеют один полный read и один replay, а не два: это подтверждается samples/counters, а не только ожиданием по diff. Цель ≤1 s не достигнута ни одной workload. Текущие seconds нельзя выдавать за регрессию или speedup по сравнению с прежним source/host load. Fixture build занял 12.614 s и исключён. Единственный stderr — предупреждение Starlette/httpx.

Create cold median exclusive phases:

- `append`: 0.005338 s.
- `canonical_reads_validation`: 4.284731 s.
- `lock_hold`: 2.197465 s.
- `lock_wait`: 0.000315 s.
- `projection_replay`: 0.534198 s.
- `receipt_preparation`: 1.960747 s.
- `reconcile`: 2.294465 s.

## Granular cProfile и остаток цели

Одна операция task_create, 22.675 s под profiler, 43,942,624 calls; build 11.958 s исключён. Source hash до/после совпал с frozen final. Полные выбранные функции и caller edges — `task-create-profile.json`, таблицы — `task-create-profile.txt`, воспроизводящий wrapper — `profile-task-create.py`. Времена ниже inclusive, вложенные строки **нельзя складывать** и нельзя переносить в unprofiled seconds.

| Участок | Calls | cumulative s |
|---|---:|---:|
| events.py:read_path | 3207 | 9.689 |
| schema_registry.py:validate | 18670 | 6.537 |
| policy.py:assert_safe | 3569 | 4.924 |
| idempotency.py:load_recovery_view | 2 | 5.046 |
| receipt_recovery.py:_event_map | 3 | 2.206 |
| config.py:canonical_relative | 9620 | 1.543 |
| manager.py:_identity | 21339 | 2.066 |
| projection.py:project_events | 1 | 1.571 |

Полное event read выполнено 3207 раз, manifests — 350. `_identity` вызван 21339 раз; это соответствует сохранённым повторным byte freshness checks и устранению лишь одного предварительного raw read для каждого из 3556 исходных files. `_event_map` по-прежнему строится трижды, `load_recovery_view` дважды. Никакого неизвестного fallback ко второму полному event read в task_create не видно.

## Ранжированный следующий шаг — без расширения этого patch

1. **Schema + policy validation остаётся крупнейшим canonical блоком.** Профиль показывает `SchemaRegistry.validate` 6.537 s inclusive, из них top-level создание validator из этого метода — 1.260 s (18670 calls; caller edge), а не все 6.537 s. Отдельный bounded эксперимент с переиспользованием validator/FormatChecker в рамках registry потенциально убирает construction, сохраняя каждую `iter_errors`, но требует точного invalidation при reload/schema mutation/extensions и thread-safety tests. Не обещать исчезновение schema traversal. Policy `assert_safe` — 4.924 s, из них 4.752 s на events; `_scan_text` 195491 calls, `classify_key` 112219. Повторная классификация ключей — следующий кандидат на отдельный профиль/ограниченную memoization только при доказанно стабильной policy; нельзя кешировать весь security verdict, пропускать fields или менять findings/locations/limits. Никаких новых оптимизаций validators/policy здесь не реализовано.

2. **Receipt preparation/reconcile почти равны canonical validation по unprofiled total.** Два `load_recovery_view` занимают 5.046 profiled s, из них 2.557 prepare и 2.489 reconcile. Простая передача старого dict через append недопустима: новые/исчезнувшие/изменённые receipts, scope/migration, tombstones и nested writes должны быть видны. Это отдельный verified receipt-view design с freshness и fallback, не ещё один локальный `if`.

   Более малый кандидат — повторная derived event info, но профиль уточняет прежнее предложение: `_event_map` 2.206 s включает 1.543 s `canonical_relative` с текущим `resolve`/containment. **Нельзя обещать безопасно убрать всю повторную карту, не сохранив эти проверки.** Memoization чистых semantic/key/hash расчётов внутри одного hold может оставить текущую path validation, однако это лишь часть 2.206 s; перенос готовой карты через append/refresh не предлагается. Три прохода — 9620 infos; один из них в reconcile и два status. Прежде чем менять это, добавить тест path replacement между recovery phases и явно определить freshness boundary. Эта оговорка уточняет более ранний read-only next-step memo.

3. **Repeated path/byte freshness work остаётся заметным.** 47697 resolve и 523368 lstat в profile; `_identity` cumulative 2.066 s. Это не повод убирать symlink/containment checks или вводить stat-only reuse. Оптимизация root/path handling или количества refresh должна иметь отдельную модель identity/TOCTOU, особенно после trusted hooks и nested writes. Первое безопасное удаление предварительного identity уже сделано.

4. **Projection ниже этих блоков, но не бесплатна.** В create один replay; complete/submit теперь тоже один. Инкрементальная projection или warm canonical cache через lock release — отдельный структурный дизайн с version/invalidation contract, а не расширение текущего patch. Process/manager-cold goal нельзя доказать одним warm in-memory cache.

Цель ≤1 s остаётся **не достигнутой**: observed workload medians 11.265–11.985 s cold / 11.310–11.850 s warm на этом shared host. Удалённые дубли доказаны counters/regression tests, но оставшийся первичный validator path и два recovery scans существенно превышают бюджет. Отдельное ускорение validator construction имеет ограниченный измеренный профилированный объём и само по себе не закрывает gap. Следующий gate: отдельно выбрать bounded validator experiment либо спроектировать verified recovery/cache lifetime, не расширять patch и не ослаблять контракт ради числа.
