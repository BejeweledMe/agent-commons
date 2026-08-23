# A6.1 — профиль replay и verified-read пути

**Измеренный production-код:** `9a1beaef4b4dbe1e5e60f6f2ab2e98758cb236d2`
(`Type Agent and Role Link Envelopes`).

**Среда:** Python 3.14.5, macOS 26.5.2 arm64. Замер снят 2026-08-23 на
локальной машине, поэтому это baseline для сравнения путей, а не универсальный
SLO или CI-budget.

## Воспроизведение

```text
env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_SESSION_ID \
  -u AGENT_COMMONS_SESSION_NONCE \
  uv run --locked python benchmarks/benchmark_a6_read_paths.py \
  --event-count 20000 --repeats 3
```

Для тихого существующего workspace тот же profiler принимает явные root и
операционное состояние:

```text
env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_SESSION_ID \
  -u AGENT_COMMONS_SESSION_NONCE \
  uv run --locked python benchmarks/benchmark_a6_read_paths.py \
  --repo /absolute/repository/path --state-root /absolute/state/path --repeats 3
```

Профайлер создаёт во временном каталоге валидный 20 000-event canonical ledger
из уже зафиксированного A0 two-pass workload. SQLite index прогревается до
начала замеров. Конструктор manager, создание fixture и первый index sync вне
таймера; перед каждым sample выполняются `gc.collect()` и `tracemalloc`.
Поэтому числа показывают дополнительные Python allocations операции, но не
полный RSS процесса и не стоимость стартового открытия приложения.

## Результат

| Изолированный путь | Что входит | Медиана | Samples, c | Max additional allocation |
|---|---|---:|---|---:|
| `CommonsManager.snapshot()` | canonical file read/validation + replay | 112.662756 s | 112.657192 / 112.662756 / 112.813211 | 196 021 340 B (186.94 MiB) |
| verified SQLite read | warm `SQLiteIndex.sync()` + `read_projection()` + replay | 24.799615 s | 24.766737 / 24.832145 / 24.799615 | 162 416 858 B (154.89 MiB) |
| in-memory replay | `project_events()` по уже verified event tuple | 7.353497 s | 7.372395 / 7.333957 / 7.353497 | 77 912 790 B (74.30 MiB) |

Каждый sample подтвердил два fixed-point прохода. Никакие hash, workspace,
schema или source-coverage проверки не отключались.

## Реальный workspace этого репозитория

Тот же command был запущен по тихому root этого checkout с его exact
операционным state root. На момент prewarm ledger содержал 1 048 событий и
также потребовал два fixed-point прохода.

| Изолированный путь | Медиана | Samples, c | Max additional allocation |
|---|---:|---|---:|
| `CommonsManager.snapshot()` | 9.242791 s | 9.260009 / 9.230407 / 9.242791 | 14 500 044 B (13.83 MiB) |
| verified SQLite read | 1.499166 s | 1.503757 / 1.494410 / 1.499166 | 12 695 969 B (12.11 MiB) |
| in-memory replay | 0.599407 s | 0.596375 / 0.607586 / 0.599407 | 5 052 700 B (4.82 MiB) |

Здесь вне готового in-memory replay на verified SQLite пути остаётся 0.90 s и
до 7.29 MiB additional traced allocations. Синтетический и реальный профили
согласованно указывают на тот же boundary, но их абсолютные времена не следует
сопоставлять с более ранним замером `snapshot()` без `tracemalloc`.

## Что это доказывает и чего не доказывает

1. Предыдущая арифметика не смешивается: `snapshot()` (112.66 s), verified
   SQLite read (24.80 s) и чистый replay (7.35 s) — три разных пути с разной
   стоимостью. В частности, SQLite-путь в этом controlled workload не равен
   `snapshot()` и не должен вычитаться из него как один и тот же fixed floor.
2. На warm verified SQLite пути вне уже готового in-memory replay остаётся
   17.45 s / 84.50 MiB для 20k synthetic и 0.90 s / 7.29 MiB для реального
   1 048-event ledger. Это доказывает, что целевой bottleneck находится на
   verified-read boundary; текущий замер намеренно не приписывает эту цену
   только JSON decoding, `sync()` или одному SQL-запросу.
3. `UIContext.rebuild_graph()` сейчас создаёт `CommonsManager(...,
   read_only=True)` и потому вызывает canonical `snapshot()`, а не
   `_read_snapshot()` (см. `src/agent_commons/ui/context.py`). Термин
   «verified UI read» в A6 относится к интерактивному manager-path,
   используемому `orient()`/`inbox()`; этот профиль не утверждает, что текущий
   read-only HTTP graph уже использует SQLite.

## Подтверждённый кандидат следующей задачи (без оптимизации в A6.1)

Точный следующий seam: `SQLiteIndex.read_projection()` в
`src/agent_commons/index/sqlite.py`. Он одновременно держит `event_rows`,
`manifest_rows`, decoded `events`, `manifest_ids` и `head_rows`, а затем
возвращает `ProjectionReadResult.events` как tuple, который
`project_events()` снова materialize-ит в list.

Следующая задача должна сначала раздельно измерить `sync()`,
`read_projection()` и `project_events()` на этом же fixture; затем может
вводить один immutable ordered normalized sequence от verified reader к replay.
Она обязана сохранить hash/workspace/source-coverage checks, ordering,
fixed-point semantics, `ProjectionReadResult` compatibility и persisted JSON.
Оптимизация числа fixed-point проходов не является кандидатом этого шага.
