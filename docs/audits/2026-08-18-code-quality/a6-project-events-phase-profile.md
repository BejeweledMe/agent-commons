# A6.4 — фазы `project_events()` на неизменяемом snapshot

**Измеренный код:** `95a9de788bc3c0d68c6d005f8f56c4c5a4ba8698`
(`Isolate Replay Benchmark Instrumentation`).

**Среда:** Python 3.14.5, macOS 26.5.2 arm64. Замер снят 2026-08-26 на
локальной машине. Это evidence для выбора следующей отдельной работы A6, а не
пользовательский SLO, CI-budget или сравнение с другим железом.

## Граница и неизменяемая выборка

После corrective commit с context-local benchmark-инструментом был создан
detached worktree ровно на указанном SHA. В его `.agent-commons/` один раз
скопированы только canonical `events/`, `manifests/` и `blobs/`; затем эти три
дерева сделаны read-only. SQLite index, lock- и receipt-файлы находились в
отдельном disposable state root. Живой checkout и его operational state не
участвовали ни в prewarm, ни в timed samples.

| Snapshot-составляющая | Число файлов | SHA-256 дерева до и после замера |
|---|---:|---|
| canonical events | 1 599 | `a5b6e3cc9cd3a6c4f2dc7f65cf9a92357a0321504ce249a2efe1bb89b764eb73` |
| artifact manifests | 144 | `424e2facfad086b6c5a971d1a34af285f551c02ccd75082011b0a828f6291a16` |
| blobs | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Хэш дерева — SHA-256 от отсортированных строк `shasum -a 256` для каждого
файла с его путём относительно snapshot. Raw ledger и disposable state в
репозиторий не добавлялись.

`agent-commons doctor` в snapshot увидел 22 schema, 1 599 event и 144
manifest, а projection завершился с `events_replayed=1599` и
`fixed_point_passes=2`. Его итоговый `ok: false` ожидаем в этой изоляции: в
новом disposable receipt scope нет 1 599 derived idempotency receipts, а
doctor также просит v2 receipt reconciliation. Это не доказательство зелёного
doctor и не было скрыто. `SQLiteIndex.sync()` и `read_projection()` ниже
прошли свой verified-reader путь с hash/workspace/source-coverage проверками,
но не заменяют receipt-check doctor-а.

## Воспроизведение

Из корня detached worktree, где `<snapshot-root>` содержит read-only canonical
деревья, а `<state-root>` — новый disposable каталог:

```text
env -u AGENT_COMMONS_STATE_BASE -u AGENT_COMMONS_SESSION_ID \
  -u AGENT_COMMONS_SESSION_NONCE AGENT_COMMONS_STATE_ROOT=<state-root> \
  uv run --locked python benchmarks/benchmark_a6_read_paths.py \
  --components-only --repo <snapshot-root> --state-root <state-root> --repeats 3
```

Перед timed samples benchmark один раз выполняет `SQLiteIndex.sync()` и
`read_projection()` над snapshot. Для каждого sample он вызывает
`gc.collect()`; `tracemalloc` измеряет только whole-run peak. Normal baseline
по-прежнему напрямую вызывает `project_events()` и не получает обёрток.
Instrumented run временно оборачивает только module globals в памяти и
возвращает их в `finally`, в том числе при исключении. Collector привязан к
active execution context: concurrent call из другого потока немедленно идёт в
оригинальный helper и не изменяет его timing stack. Он проверяет свой snapshot
against uninstrumented baseline по canonical digest, множеству known event ids,
порядку applied event ids и числу fixed-point passes.

## Baseline и стоимость измерения

| Компонент | Медиана | Samples, с | Max additional traced allocation |
|---|---:|---|---:|
| warm `SQLiteIndex.sync()` | 0.566413 с | 0.573587 / 0.564368 / 0.566413 | 3 317 204 B (3.16 MiB) |
| `SQLiteIndex.read_projection()` | 0.238162 с | 0.236635 / 0.238875 / 0.238162 | 11 936 604 B (11.38 MiB) |
| normal `project_events()` по verified tuple | 2.097866 с | 2.099401 / 2.069669 / 2.097866 | 9 774 734 B (9.32 MiB) |
| instrumented `project_events()` по тому же tuple | 2.223321 с | 2.244295 / 2.223321 / 2.206608 | 9 776 826 B (9.32 MiB) |

Wrapper overhead посчитан только как разность каждой парной normal и
instrumented sample: 0.144895 / 0.153652 / 0.108742 с; медиана парных
разностей — **0.144895 с**. Это стоимость benchmark-local instrumentation,
не часть production latency. Разные медианы фаз ниже нельзя складывать друг с
другом или с median root: они получены из разных samples.

## Exclusive-фазы replay

`fixed_point_planning` — exclusive время тела `project_events()` вне
`_project_events_once`. Каждая строка `_project_events_once.*` — exclusive
время соответствующего fixed-point pass вне инструментированных дочерних
helpers. Все остальные строки также exclusive; `residual` явно оставлен как
root minus учтённые фазы. В каждом из трёх samples сумма exclusive-фаз вместе
с residual не превышала measured root.

| Фаза | Вызовов на sample | Медиана exclusive времени |
|---|---:|---:|
| `fixed_point_planning` | 1 | 0.005304 с |
| `_project_events_once.probe` | 1 | 0.116175 с |
| `_project_events_once.normal` | 1 | 0.115367 с |
| `_project_events_once.final` | 0 | 0.000000 с |
| invalidation | 2 | 0.034283 с |
| revision resolution | 3 190 | 0.134158 с |
| acceptance staleness | 2 | 0.001170 с |
| CAS conflict detection | 2 | 0.023212 с |
| payload validation | 6 380 | 0.087188 с |
| event-envelope parsing | 3 190 | 0.758224 с |
| transition validation | 3 190 | 0.505823 с |
| effective-event application | 3 190 | 0.368465 с |
| bound-evidence staleness | 2 | 0.071970 с |
| decision conflict detection | 2 | 0.001174 с |

Residual у трёх samples составил 38.208 / 22.583 / 25.917 мкс. Его наличие
преднамеренно: он не выдаёт время setup, clock bookkeeping и неименованных
участков за отдельную семантическую фазу. Phase-level allocations не
измерялись — только whole-run `tracemalloc` peak из таблицы выше.

## Инварианты результата

Uninstrumented baseline и каждый из трёх instrumented samples получили один
и тот же canonical snapshot digest:

```text
9ac2fc64daed6112e74161cd89bd929df5622739de3997c4a2a669dc5ade9b10
```

В каждом случае было 1 599 known event ids, 1 595 applied event ids в том же
порядке и `fixed_point_passes=2`. Этот snapshot использует probe + normal
pass; final pass в нём не нужен, поэтому его явная строка имеет ноль вызовов.
Benchmark tests отдельно проверяют synthetic three-pass scenario, где
probe/normal/final получают по одному вызову, а также проверяют возврат всех
patched globals при ошибке.

## Вывод и следующий отдельный кандидат

На этом snapshot самые крупные именованные exclusive участки —
event-envelope parsing (0.758224 с), transition validation (0.505823 с) и
effective-event application (0.368465 с). Это объясняет, куда смотреть, но
не доказывает, что любой из этих шагов избыточен: оба fixed-point passes
намеренно повторяют lifecycle semantics, а profile не измеряет histories, где
нужен final pass.

Единственный допустимый следующий кандидат — **отдельно** исследовать
возможность безопасно переиспользовать неизменяемый typed envelope для одного
effective event между fixed-point passes. До реализации нужны доказательство
чистоты/ключа cache для correction-revision и characterization fixtures для
probe, normal и final; порядок событий, validation, correction semantics,
fixed-point result и persisted JSON менять нельзя. Эта страница не предлагает
production-оптимизацию, новый SLO или вывод о пользовательской задержке.
