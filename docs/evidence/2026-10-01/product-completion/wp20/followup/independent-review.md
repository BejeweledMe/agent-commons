# Независимое ревью ограниченного WP20 follow-up

Дата: 2026-10-01. Вердикт: **approved для четырёхфайлового increment**. Существенных дефектов в изменении не найдено. Это экспертное scoped review конкретных bytes; не full merged/release approval и не подтверждение latency target.

Объект: `/private/tmp/commons-wp20-next-impl-20261001`, `incremental.patch`, `integration-manifest.json`, реальный source и tests. Независимо пересчитаны все четыре result file hashes, patch SHA, а также полные before/after package fingerprints функцией `agent_commons_source_sha256` на `baseline-src/agent_commons` и `src/agent_commons`.

- Before: `fe98a42e588e29d79198d109627814f22d496272d1447732605b5dba44169fe4`.
- After: `9b3368df09bceb1a5196663937886fbc0c7705ee3b814562f771463763868196`.
- Patch: `f54476b2fc96dfe71e5aca792ef7b6b74e3c737e598987f3fd0978174ad6b102`.

Frozen source/tests и основной checkout/ledger не изменялись. Интерпретатор основного `.venv` использован read-only, PYTHONPATH — frozen snapshot; Commons env удалены, bytecode/cacheprovider выключены. Все synthetic fixtures, scripts/logs и pytest basetemp находятся в собственном `/private/tmp`. Provider calls, installed-MCP changes и benchmarks не выполнялись.

## Проверка изменения

`services/manager.py:159–177`: при structural rebuild/cache miss предварительная `_identity` действительно не могла разрешить cache reuse. Теперь сразу вызывается неизменённая `_verify`, которая сохраняет before identity → validating reader → after identity, до трёх попыток и refusal при продолжающемся изменении. Reader продолжает schema/policy/path validation. На cache-hit пути каждый файл по-прежнему перечитывается/хэшируется; digest, size и resolved path сравниваются со старой verified записью. Изменение path set сохраняет полную перевалидацию коллекции. Own-append, lock scope и authoritative snapshot rules этим patch не меняются.

`services/tasks.py:246–252`: shortcut применяется только если `artifact_refs is not None` и явно пуст. `None` сохраняет прежний путь snapshot/evidence retention с отказом для stale/unbound evidence. Непустые refs проходят `_bind_evidence_refs`. Пустой результат всё равно превращается в обычный `record_event`: schema, policy, целостность canonical ledger, receipt/idempotency, lifecycle/CAS и append защищены canonical write lock. Удалён внешний redundant snapshot, не locked validation.

## Независимая верификация

**76 focused tests passed за 37.17s**:

- `tests/services/test_verified_ledger_view.py`;
- `tests/services/test_task_evidence_snapshot.py`;
- `tests/services/test_manager.py`;
- `tests/services/test_receipts.py`.

Покрыты unchanged cache reuse, изменённые байты/parent path, structural collection changes, три before/read/after races, lock reacquisition, nested trusted hook, own-append crash и identical retry, same-manager threads и competing processes, receipt recovery/idempotency, `None` retention, explicit-empty replacement, nonempty evidence binding и stale evidence refusal. Новые count regressions прошли; это свидетельство устранения конкретного лишнего read, не измерение продуктовой latency.

Дополнительный adversarial probe `/private/tmp/commons-wp20-followup-gap-probe.py`:

1. После возврата shortcut для пустого tuple, но до `record_event`, canonical manifest заменён некорректными bytes. Locked validation отказала; event count не увеличился.
2. То же для пустого list — отказ, новых events нет.
3. Непустой ref на несуществующий artifact по-прежнему получает ValidationError.
4. Explicit-empty completion со stale expected revision получает LifecycleConflictError.

Corrupt manifest с `{}` отказал на schema validation (`ValidationError: manifest schema must be a string`); это корректный fail-closed исход. Probe не требует ошибочно конкретный IntegrityError для каждой формы некорректных bytes.

Runner `/private/tmp/commons-wp20-followup-review-run.py`; suite log `/private/tmp/commons-wp20-followup-review-tests.log`. После проверок result bytes/fingerprint повторно проверены.

## Ограничения

Full integrated `make check` выполняет root. Browser, wheel reinstall и exact next profile canary не входят в это ревью. Не утверждаю speedup, regression или ≤1s: замеры worker выполнялись на concurrent host, и этот аудит не запускал сравнительный benchmark. Остаточная стоимость full canonical validation/replay и receipt processing этим узким correctness verdict не оценивается. Одобрение не отменяет предыдущие ограничения архитектуры/безопасности, не изменённые diff.

## Exact file hashes

| Файл | SHA256 |
|---|---|
| `incremental.patch` | `f54476b2fc96dfe71e5aca792ef7b6b74e3c737e598987f3fd0978174ad6b102` |
| `integration-manifest.json` | `8b3ac7eb84eeca0485435706ef1588a115077bf73ee45e190eff1915c038aef4` |
| `src/agent_commons/services/manager.py` | `4ca10ba6e7f31c2b90d153ce21675eacc75c5e8758a6cbb524fb91d84c9a64bb` |
| `src/agent_commons/services/tasks.py` | `7b044bef6bdc7fc154ff80d10e4236daf9de1e9ea236407bf94df37e6add1d75` |
| `tests/services/test_verified_ledger_view.py` | `367fc2bdead16f74bd2239e07a62966a71a132a632f5c82d18e90d377999c756` |
| `tests/services/test_task_evidence_snapshot.py` | `782c328644b8fd19ef6ef954e2dae0351d6f1fe17bf1c2a2ba27eaac916b4e31` |
