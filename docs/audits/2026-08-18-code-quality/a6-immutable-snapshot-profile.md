# A6.3 — профиль verified-projection на неизменяемом snapshot

**Измеренный код:** `882c367351edfc3c948ce8adac3e82d781b1f003`
(`Preserve Mixed-Precision Replay Order`).

**Среда:** Python 3.14.5, macOS 26.5.2 arm64. Замер снят 2026-08-26 на
локальной машине. Это evidence для выбора следующей работы A6, а не
пользовательский SLO, CI-budget или сравнение с профилями на другом железе.

## Неизменяемая выборка и изоляция

Перед прогоном был создан отдельный detached worktree ровно на указанном SHA.
В него один раз скопированы только canonical `events/`, `manifests/` и
`blobs/` из `.agent-commons`; затем source-деревья повторно прохэшированы после
замера. Операционный SQLite index и все его lock/receipt-файлы находились в
отдельном disposable state root. Живой checkout и его operational state не
участвовали ни в prewarm, ни в timed samples.

| Snapshot-составляющая | Число файлов | SHA-256 дерева |
|---|---:|---|
| canonical events | 1 581 | `413032e36272a47f389005fa58c612a84ec4e8650b8c2355091ea1c7076f900c` |
| artifact manifests | 142 | `11097d06cf383e6886a62c7cc7f7343a1cc2ed1bda5e05c6030e609747c7e18a` |
| blobs | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Хэш дерева — SHA-256 от отсортированных строк `shasum -a 256` каждого файла с
путём относительно snapshot. Он одинаков до и после прогона. Raw ledger и
state в репозиторий не добавлялись.

`agent-commons doctor` был запущен в snapshot до профиля. Он прочитал 22
schema, 1 581 event и 142 manifest, а projection завершился с
`events_replayed=1581` и `fixed_point_passes=2`. Его итоговый `ok: false`
ожидаем: новый detached checkout получает новый receipt scope, а пустой
disposable state ещё не содержит 1 581 derived idempotency receipt. Это не
ошибка canonical ledger, но и не «зелёный doctor»: проверка receipts в таком
scope сознательно не пройдена. Их реконструкция потребовала бы actor/session,
который для изолированного read-only измерения не создавался. Успешный
`SQLiteIndex.sync()` и `read_projection()` ниже дополнительно исполнили
verified-reader путь с его hash/workspace/source-coverage проверками, но не
заменяют receipt-check doctor-а.

## Воспроизведение

Из корня detached worktree, с путями snapshot и disposable state, был выполнен:

```text
env -u AGENT_COMMONS_STATE_ROOT -u AGENT_COMMONS_STATE_BASE \
  -u AGENT_COMMONS_SESSION_ID -u AGENT_COMMONS_SESSION_NONCE \
  uv run --locked python benchmarks/benchmark_a6_read_paths.py \
  --components-only --repo <immutable-snapshot> \
  --state-root <disposable-state> --repeats 3
```

`--components-only` намеренно не включает composite `snapshot()` или
`CommonsManager._read_snapshot()`. Профайлер один раз index-ирует snapshot до
таймера; перед каждым sample выполняет `gc.collect()` и `tracemalloc`, а warm
`sync()` проверяет отсутствие новых/удалённых canonical файлов. Каждый replay
подтвердил два fixed-point прохода.

## Результат

| Компонент | Медиана | Samples, с | Max additional traced allocation |
|---|---:|---|---:|
| warm `SQLiteIndex.sync()` | 0.628094 с | 0.628094 / 0.627550 / 0.629141 | 3 376 862 B (3.22 MiB) |
| `SQLiteIndex.read_projection()` | 0.238828 с | 0.238828 / 0.238376 / 0.238979 | 11 802 008 B (11.25 MiB) |
| `project_events()` по verified tuple в памяти | 2.033753 с | 2.021872 / 2.033753 / 2.051581 | 9 693 280 B (9.25 MiB) |

Samples описывают независимые операции, поэтому их нельзя складывать в latency
одного manager-path. Peak — только дополнительная Python allocation под
`tracemalloc`, не process RSS.

## Вывод и следующий узкий шаг

На этом exact snapshot `project_events()` — измеримо доминирующая отдельная
операция: его медиана в 3.24 раза выше следующего компонента `sync()` и в 8.52
раза выше verified `read_projection()`. Это подтверждает лишь приоритет для
исследования replay, не объясняет причину и не оправдывает оптимизацию.

Следующей допустимой задачей A6.4 может быть **отдельный characterization и
profile `project_events()`** на таком же immutable snapshot: разложить время и
allocations по именованным фазам replay и проверить кандидаты на повторную
materialization/сортировку. Её acceptance criteria должны сохранять exact
event order, schema/hash/workspace проверки и `fixed_point_passes=2`; никакой
production-правки, нового SLO или вывода о пользовательской задержке из этого
профиля пока не следует.
