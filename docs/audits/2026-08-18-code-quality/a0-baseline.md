# A0 — базовая линия поведения и replay

**База production-кода:** `49c7b0d` (после неё до замера менялись только
аудитный план, benchmark и tests).

## Characterization coverage

Перед структурными переносами проверена не численная coverage, а наличие
наблюдаемого контракта на каждой требуемой границе:

| Граница | Зафиксированный контракт |
|---|---|
| Event payload и replay | `tests/domain/test_projection.py` проверяет payload-driven projection, порядок, corrections, stale acceptance и счётчики fixed-point; `tests/core/test_storage.py` — byte-stable append/read и canonical event bytes. |
| Lifecycle transitions | `tests/domain/test_validation_lifecycle.py` и replay-сценарии проверяют разрешённые/запрещённые переходы, CAS revision и независимый qualifying review. |
| MCP tool catalog | `tests/mcp/test_server.py::test_bounded_tools_delegate_to_the_manager` сравнивает полный набор tool names; worker-вариант отдельно сравнивается с `INDEPENDENT_REVIEW_WORKER_TOOL_NAMES`. |
| UI JSON shapes | `tests/ui/test_readonly_invariant.py::test_read_endpoints_preserve_their_top_level_json_shapes` фиксирует верхние wire shapes meta/graph/entity/attention/runs. |
| Sessions/claims private storage | Session и claim tests фиксируют canonical audit bytes, `0700` directories, concurrent identity/acquisition и межпроцессный claim lock. |
| Attempts private storage | `test_attempt_reservation_is_private_atomic_and_idempotent` и tamper/concurrency tests фиксируют `0700`/`0600`, atomic/idempotent write и fail-closed read. |
| Communication private storage | Lifecycle, restart, HMAC, `0700`/`0600` и concurrent first-use tests фиксируют третий вариант private store. |

Уже существующие точные проверки не дублировались. Добавлены только отсутствующий
top-level UI wire-shape contract и проверки, оправданные выжившими мутациями.

## Replay benchmark

Команда:

```text
uv run --locked python benchmarks/benchmark_projection.py --event-count 20000 --repeats 3
```

Среда: Python 3.14.5, macOS 26.5.2 arm64. Workload строится до включения
`tracemalloc`, поэтому peak ниже — дополнительные Python allocations самого
replay, а не размер исходного списка событий.

| Сценарий | Событий | Полных проходов | Медиана | Max peak allocation |
|---|---:|---:|---:|---:|
| Applied successor после acceptance | 20 000 | 2 | 5.236654 s | 64 594 292 bytes (61.60 MiB) |
| Тот же probe плюс post-replay stale artifact acceptance | 20 000 | 3 | 7.751015 s | 83 711 052 bytes (79.83 MiB) |

Сырые samples: two-pass — 5.201929 / 5.248895 / 5.236654 s; three-pass —
7.751015 / 7.777116 / 7.651246 s. Benchmark сам падает, если workload перестал
достигать ожидаемых двух или трёх проходов.

## ReceiptRecovery.status mutation checks

`mutmut` не входит в locked dependencies, поэтому каждая названная мутация
применялась изолированно к production-файлу, после неё запускался полный pytest,
а затем исходник восстанавливался до следующей мутации.

| Мутация | Результат исходного набора | Добавленная защита | Повторная мутация |
|---|---|---|---|
| Scoped conflict: `elif not _receipt_matches(...)` → `elif False` | Выжила: 805 passed, 13 skipped | `test_status_reports_a_scoped_receipt_that_conflicts_with_its_event` | Убита |
| Legacy conflict в `status()`: условие → `False` | Выжила: 805 passed, 13 skipped | `test_status_reports_a_legacy_receipt_that_conflicts_with_its_event` | Убита |
| Reconciled tombstone warning: `if reconciled` → `if False` | Выжила: 805 passed, 13 skipped | warning assertion в `test_exact_git_arrival_after_abandonment_is_audited_and_reconciled` | Убита |

Все три кандидата потребовали теста: отсев по мутациям здесь равен нулю, но
каждый новый assertion доказан повторным запуском соответствующего mutant.
