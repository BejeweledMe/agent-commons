# Независимое ревью G5/G7: retained outputs

Дата: 2026-10-01. Вердикт: **changes_requested** для замороженного инкремента G5/G7. Это экспертное ревью конкретного артефакта, не одобрение объединённого checkout, релиза, задачи или продуктовая приёмка.

Объект: `/private/tmp/commons-product-outputs-20261001`. Проверены реальный код, `G5_G7.patch`, `G5_G7_REPORT.md`, manifest интеграции, ADR 0025 и соответствующий раздел PROTOCOL. Все 27 файлов совпали с SHA256 из integration manifest до проверки и повторно после неё. Main checkout, canonical ledger, reviewed snapshot и установленный MCP не изменялись. Сессия/записи ревью в Commons не создавались согласно явному запрету любых ledger writes; результат находится только в этом файле.

## Требуемые исправления

### [P2] Чтение короткого хвоста build-файла повторно сканирует весь файл без вычислительного ограничения

Место: `src/agent_commons/mcp/server.py:1070` (контекст 1059–1073). Новый `commons_read_build_file` сначала читает/декодирует весь разрешённый файл до 10 MiB, затем вызывает `workspace._review_content(text, ...)` для всего текста и лишь потом применяет `offset`/`limit`. Это происходит заново при каждом чанке.

Воспроизведение: опубликован обычный static build с `index.html` размером **65 557 UTF-8 байт**: 65 535 символов `a`, затем `😀Жe\u0301終` и десять `b`. Вызов `commons_read_build_file(artifact_id, "index.html", offset=65536)` должен вернуть только небольшой хвост, но не завершился за **10.01 секунды**; SIGALRM остановил именно этот вызов. Стек: `server.py:1070` → `scoped_repo.py:225` → `security/policy.py:370`, `_QUOTED_ASSIGNMENT.finditer(text)`. Первичный более длинный эксперимент остановлен Ctrl-C; окончательное доказательство получено ограниченным 10-секундным прогоном.

Это не ошибка Unicode-разбиения. При тех же границах, но с разделёнными пробелами короткими токенами, текст точно восстанавливается и финализируется. Причина — старые assignment-регулярные выражения, повторяющие поиск с каждой позиции длинного токена, плюс новый whole-entry scan при каждом bounded build read. Такие данные допустимы контрактом (включая текст/inline assets); лимит длины ответа не ограничивает стоимость обработки. Ревью разрешённого build может расходовать весь worker wall-time до получения доказательств чтения.

Исправление должно сохранить security policy: линейный/ограниченный сканер с корректными границами токенов и cross-line правилами; отдельно стоит кешировать проверенный, отредактированный текст по замороженному entry digest на время server session, чтобы не повторять сканирование при каждом чанке. Простое удаление проверки или независимое сканирование чанков без обработки секретов на границах небезопасно. Добавить timeout-bounded regression с длинным токеном и multibyte boundary.

Доказательство: `/private/tmp/commons-g57-review-timed-probe.py`, `/private/tmp/commons-g57-review-timed-probe.log`.

### [P2] Неизменённый retry исторической image-публикации стал idempotency conflict

Место: `src/agent_commons/mcp/design_output_tools.py:184` (контекст 172–200).

Предыдущая версия публиковала image artifact с пятью metadata полями без `retained_content`. Новый код вычисляет тот же `generated-image-<sha256(delegation:key)>` и находит существующий artifact, но требует `old.get("retained_content") == retained`. Поэтому после обновления той же worker session неизменённый повтор того же source/title/key ошибочно сообщает `IdempotencyConflictError: Generated image retry differs from its publication.`

Воспроизведение использует собственный временный synthetic workspace и поддержанный `register_artifact`: оригинальная metadata форма, оригинальная derivation ключа, тот же автор/делегация/task, неизменённые PNG байты. Новый `commons_publish_design_image("outputs/screen.png", "Homepage", "homepage-001")` воспроизводимо конфликтует. Документация сохраняет legacy metadata-only чтение и обещает неизменённый retry с тем же ключом; изменение внутренней схемы не является изменением пользовательского intent. Сценарий важен после потерянного ответа/перезапуска MCP при обновлении.

Нужно распознавать точную legacy форму отдельно: вернуть её прежний artifact/revision и честный metadata-only результат, без переписывания истории и без ложного `content_copied: true`. Новая retained версия требует нового ключа. Изменённые байты/title/source/producer должны по-прежнему конфликтовать. Добавить регрессию upgrade retry.

Доказательство: `/private/tmp/commons-g57-review-fast-probes.py` (ветка `legacy`).

## Что независимо проверено

- **76 Python тестов passed**, `tests/mcp/test_design_output_tools.py`, `tests/ui/test_output_routes.py`, `tests/mcp/test_worker_scope.py`; один существующий Starlette/httpx deprecation warning. Python из main `.venv` использован только как интерпретатор, PYTHONPATH указывал на frozen src, Commons env удалены, bytecode/cache writes отключены, pytest basetemp — собственный `/private/tmp`.
- **33 Work outputs Node tests passed**, Node 24; harness компилировал только во временную директорию.
- Дополнительный Unicode probe: граница 65 536 Python characters, emoji/кириллица/combining character; хвост без первого чанка не удовлетворяет approval, два диапазона точно восстанавливают текст и позволяют `delegation.succeeded`.
- По коду и focused tests: store limits 512 objects/256 MiB без eviction; descriptor/no-follow путь, immutable hash verification, thread RLock и process flock, одинаковые concurrent puts, atomic-link recovery, quota/refusal, source deletion/restart, deterministic ZIP, scoped authenticated attachment с `nosniff`/`no-store`, отсутствует HTML-serving endpoint.
- Artifact-target approval имеет отдельный DTO от task review, связан с точной event revision, self-review запрещён manager lifecycle. Незавершённая delegated approval не отображается; scoped image read возвращает реальные MCP pixels; manifest-only и пропуски build text не проходят finalizer. Замороженная artifact binding проверяется при чтении.
- EN/RU, Builds filter, explicit download и hash/freshness проверки покрыты UI harness. Browser visual interaction не проводилась.
- Ограничение stdout само по себе не удаляет terminal факты: `mcp/server.py` guarded terminal calls пишут отдельный `TerminalToolAuditStore`; `services/delegation_runtime.py:750` читает этот store независимо от stdout. Provider/model запусков в этом ревью не было; новое поле/allowlist всё равно требует отдельной следующей профильной canary.

## Ограничения

`make check`, сборка wheel/assets, installed-MCP reinstall, provider canary, внешний reviewer через реальный provider и браузерная визуальная проверка не запускались. Binary assets внутри build проверяются хэшами; это не их визуальное или runtime ревью. Redaction остаётся отдельным ограничением доказательств; этот аудит не заявляет чтение скрытого policy текста. Проверка filesystem race опирается на код и существующие focused regressions; исчерпывающий adversarial scheduling не выполнялся. Полная объединённая версия с G6/WP20 этим результатом не одобряется.

## SHA256 границы ревью

| Файл | SHA256 |
|---|---|
| `G5_G7.patch` | `08f079a9a6bf1286ef19209325fb593c4de038740ba8c560496f72e21d2f3f0f` |
| `G5_G7_INTEGRATION.json` | `fe0ceccbd5d856427809496ad02547dd9cdf30ba309e8299dcbbd3201c7760f9` |
| `G5_G7_REPORT.md` | `7e00d4b656dc07117d8510a76cfef1d667d2bbd5e2d1eb4ad2fc04be55556ee7` |
| `src/agent_commons/ui/output_routes.py` | `b1c3306b0dc156b39edd1caa09126065ac0ac0f6f1ef96557f191f436155d963` |
| `src/agent_commons/runtime/model.py` | `994360dbc68368f2d73abcf4ac9e1a392433b56ca8f247684033b606841874e1` |
| `src/agent_commons/mcp/server.py` | `d406df885f7ad158eacd972958d96c0c867a6bef4cb1024d1ad2b50a1ad5c57e` |
| `src/agent_commons/mcp/design_output_tools.py` | `a446df1ebfc925afd1ef749b52ba58af9794dbb96df1fb4b0d3e9861d9bfab04` |
| `src/agent_commons/services/delegation_instruction.py` | `25117db5e183ff2c99a98a9230eee3811a6bb251859b1e75cd0c17cc6ed8390f` |
| `src/agent_commons/services/output_content.py` | `8c0a0b673c7d5166ffea4ab83abd745298a25ed16c4af55e6b377b7e21c2c8dd` |
| `src/agent_commons/services/artifact_content.py` | `7068eabe4b55f5e2fe33a717ccc52782af9ea9c4c82441421374b54aa137bd20` |
| `src/agent_commons/services/generated_outputs.py` | `a23aec17289b93ec23b7c4d0b56bd18308c7e8029591abb8e41206adfd80d433` |
| `src/agent_commons/services/outputs.py` | `57e97276363c51b644382d2e882644bc5a1be86c6e41798107f4bedd62a2df0d` |
| `frontend/work/tests/outputs.test.mjs` | `08ff9c512b8b4ae05baf6fcd4afb79ed2cfe4aa4a29c58dc71673fbc61e34ce6` |
| `frontend/work/src/outputsTypes.ts` | `5f160a17ec30dc2c33e3e3988fbe74c1c565061e01e6331d41e0f9a9cec71de3` |
| `frontend/work/src/i18n.json` | `ec065a1e2c692ff9429b7293453cb4c6879a681d4d0ede9253764c69b18e9be3` |
| `frontend/work/src/api.ts` | `66bc13f84a0e55f82399b7287e4b063bdb2fbbc232ba221e032aa779f334f014` |
| `frontend/work/src/outputsStrings.ts` | `2343a04c86819d75fc291ab38d009984cdc22a0290109f7ec32173965b32681d` |
| `frontend/work/src/outputsApi.ts` | `3e32ce1a7ab54f719e1d0f0938e5ffcbd4d6a8d45c3754a91b86930e919911aa` |
| `frontend/work/src/components/OutputsPanel.tsx` | `219ead74830db36341c99ed2b682bb6a6ca866a92a02e1a6c4cd7438ac0c1582` |
| `tests/ui/test_output_routes.py` | `24221acbf6240b8b12b1a490e30585dd6b6790cc853163c9383a4d4a4678eafa` |
| `tests/runtime/test_profiles_policy.py` | `5e3173a714dd928e8b940518904732ff4bb2ed03e4e05dcdeafe1cc505c58a70` |
| `tests/runtime/test_delegation_instruction.py` | `cf21e1bcc6403124c72cdedad24b23da35cb981c4ad5005032cd844cd5c01276` |
| `tests/mcp/test_design_output_tools.py` | `a62d96b4908b0c5d3c30be7bb912dac77a31138cecf4cc58b1a92be59c70f8b3` |
| `tests/mcp/test_worker_scope.py` | `f5c98c4a45113e5b2c95c4afd1a19c8cc233a669df4b68580639e7e29aeee5f2` |
| `docs/FRONTEND_CONTRACT.md` | `32c0b6a002e9cd567c39039989c541cdd27b21a01e68d3067ab4d8699529ea7d` |
| `docs/PROTOCOL.md` | `9584f4cb528ce07a523740e090404f54e3b0b9b70beeaef13ffc72932452fa31` |
| `docs/adr/README.md` | `3a9e84293ddf25095eef3e3bd9874aebcf2c638c8306dc59723055184b3386bc` |
| `docs/adr/0025-retained-worker-results.md` | `811acf7bf5b90cadb1ac285f18c0516b89d8385e97a1273571f38db981a0e7f0` |
| `docs/user/ru/README.md` | `eb2b8cbed901d8a5d54a99cce7a2c0f747bb59f0aa588bf0f14cd111f5880e33` |
| `docs/user/en/README.md` | `cda06217951130ed379322a35ec3c9c9f3caea6179c5b41494694a010194b9d7` |
