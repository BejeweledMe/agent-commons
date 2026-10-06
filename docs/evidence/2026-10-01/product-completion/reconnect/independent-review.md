# Независимое ревью Work UI reconnect

Дата: 2026-10-01. Вердикт: **approved для данного двухфайлового исправления**. Существенных дефектов в проверенной границе не найдено. Это не одобрение всего merged checkout или релиза; реальный browser restart retest остаётся у root.

Объект: `/private/tmp/commons-ui-reconnect-20261001`, `UI_RECONNECT.patch`, integration manifest, report и реальный `frontend/work/src/api.ts`. Source и tests не изменялись. Main checkout/ledgers, runtime/profile/MCP, provider процессы не затрагивались. Компиляция и дополнительные probes писали только в собственный `/private/tmp`.

## Проверенная логика

`api.ts:1850–1884`: сохранённый opaque prefix устанавливается временно и проверяется через `/projects`. Успех сохраняет действующую host session, не потребляя одноразовый code. При 404 одного `/projects` теперь требуется успешный `/setup` на **том же prefix** с object/string-state/boolean-launch_enabled формой. Только 401 или второй 404 очищают stale prefix и позволяют `connect` использовать code, уже прочитанный и удалённый из URL до networking. Без code восстановление отказывается.

Legacy setup может иметь `launch_enabled: false`: это всё ещё действующая authenticated browser session, даже если provider запуск недоступен. Abort, network failure, 403/5xx и malformed successful setup не становятся признаком expiry и не вызывают exchange. Entity 404 после connect не очищает сохранённую browser session.

Изменение не ослабляет серверную authentication. Дополнительный запрос — обычный same-origin GET с текущими credentials; exchange по-прежнему идёт на фиксированный `/api/auth/exchange`, возвращённый prefix проходит существующую строгую относительную `/api/<opaque>` валидацию. Read-only inspection действующего server guard подтвердил: неверный process-bound prefix получает 404, правильный prefix без cookie — 401, `/setup` не является публичным auth bypass; Host/Origin и exchange-code checks не менялись patch.

## Верификация

- **91 focused Node 24 tests passed**: task-inspector, project-workspace, work-shell. Включены same-origin restart → old-prefix404/404 → fresh exchange, valid project/legacy reload с отсутствующим и уже использованным fragment, missing code, unauthorized legacy, abort/offline/503/malformed setup и entity404.
- Strict Work `tsc --noEmit -p frontend/work` прошёл.
- Независимый `/private/tmp/commons-ui-reconnect-adversarial.mjs`, с отдельно скомпилированным frozen API, прошёл четыре дополнительных сценария: `/projects`403 сохраняет prefix и не использует code; legacy setup с disabled launch восстанавливается; HTML/string вместо setup object вызывает502 и не потребляет code; cross-origin api_base в exchange response отклоняется и не становится следующей сетевой целью.
- Patch SHA и обе final SHA256 проверены; фактические значения ниже.

## Ограничения

Все дополнительные transport проверки используют mocked fetch, не cookie jar настоящего браузера. Actual same-port process restart, новая одноразовая ссылка, UI bundle cache и визуальный выход из expired screen требуют browser retest root после rebuild assets. Полный `make check`, assets/wheel и provider canary не запускались. Поведение захвата/удаления fragment при transient error существовало до patch и здесь не менялось: это исправление не обещает хранить одноразовый code между неудачными connect attempts.

## SHA256

| Файл | SHA256 |
|---|---|
| `UI_RECONNECT.patch` | `adfc052229fb4a3e25b67eff7f4c19c6f721d211e6ae3adf6e254661387862b6` |
| `UI_RECONNECT_INTEGRATION.json` | `6ec1e1ee9994e7c99b92b085ae75137ab90abfb60eacf900c9e0606f6d7bb825` |
| `UI_RECONNECT_REPORT.md` | `2497c84a1cb97f585a06d58383935939607bef109eb26a87165af9caa7f58988` |
| `frontend/work/src/api.ts` | `8212e987ab86222876dd782f89f67919f983eae24fd548b1fff159cf9ba82a66` |
| `frontend/work/tests/task-inspector.test.mjs` | `236ead91a5df53dd1af6d4fca0d00ae887d2b631d9146bacce8e20562a1d89f7` |
