# Независимая перепроверка двух исправлений G5/G7

Дата: 2026-10-01. Вердикт: **approved в пределах двух исправлений и их регрессионной границы**. Оба P2 из `/private/tmp/commons-review-outputs-20261001.md` закрыты проверенным кодом и повторными воспроизведениями. Новых существенных дефектов в инкременте не найдено. Это не full release approval, не task acceptance и не результат реального model/provider review.

Объект: `/private/tmp/commons-product-outputs-20261001`, исходный 27-file G5/G7 increment плюс `G5_G7_FIX.patch`. Проверены patch, report, integration manifest и реальный код. Все шесть final hashes совпали; 23 файла исходного 27-file manifest вне исправления остались неизменными. Main checkout, основной и reviewed ledgers, installed MCP и frozen source не изменялись. Все временные fixtures/logs — собственные `/private/tmp`. Provider/model не запускался.

## Закрытие замечаний

**Длительное чтение build текста.** Исходный 65 557-byte reproducer с 65 535 подряд идущими `a` теперь завершает read до прежнего 10-second alarm; весь процесс, включая synthetic workspace/publication/reviewer setup, занял 1.925s. Изменение bare-key regex добавляет границу токена и possessive quantifier; лишние попытки распознавания суффиксов длинного идентификатора устранены. Реальный scanner остаётся whole-entry до slicing, поэтому security policy не обходится.

**Legacy same-key retry.** Исходный legacy reproducer теперь успешно возвращает исходную публикацию. Существующие и новые tests подтверждают тот же artifact/event, неизменённый manifest, `content_copied: false`, отсутствие CAS put, одинаковое поведение после реконструкции manager/tool. Changed bytes/title/source/producer и malformed metadata/binding всё ещё отказываются. Новый ключ создаёт новую retained версию; старая история не переписывается.

## Независимая проверка

- **131 тест passed** за 92.77s: весь `tests/security`, `tests/mcp/test_design_output_tools.py`, `tests/mcp/test_worker_scope.py`, `tests/ui/test_output_routes.py`. Один существующий Starlette/httpx deprecation warning. Лог: `/private/tmp/commons-g57-recheck-tests.log`.
- Повторно использованы мои исходные `/private/tmp/commons-g57-review-timed-probe.py` и `commons-g57-review-fast-probes.py`, без изменения reviewed code. Fast probe завершился за 2.766s: legacy retry success, Unicode reconstruction exact, gapped first chunk refusal, затем canonical finalize success в synthetic fixture.
- Отдельный read-only old/new policy differential на **405 assignment случаях**: numeric/punctuation/Unicode/underscore prefixes, credential/noncredential keys, quoted/unquoted values, cross-line operators. `scan` и `scan_text_lines` сохранили detection outcomes во всех случаях. Это дополнительно к полному security suite.
- Отдельный task-scoped fixture с двумя build artifacts и **129 уникальными text entries**: после первых 128 cache entries новое чтение вытесняет самое старое; повтор первого entry вызывает новую policy scan. Это подтверждает реальную LRU границу, а не только наличие константы в коде.
- Отдельная cache accounting stress: для вытесненного entry тестовый callback возвращал sanitized ASCII text `20 MiB + 1`; два чтения вызвали callback дважды, oversized text не кешировался. Callback использован только для проверки accounting/eviction; он не является доказательством поведения настоящего security scanner. Скрипт: `/private/tmp/commons-g57-recheck-cache.py`, ограничение 45s.
- Полноценный реальный policy test через MCP подтверждает redaction секрета одновременно через chunk и line boundaries, отсутствие значения/credential key в возвращённых чанках, один scan при последующих chunks и финализацию только после покрытия sanitized text. Лимит «скрытый policy текст не проверен» остаётся явно указанным в summary и протоколе.
- Cache hit не обходит authority/integrity: каждый вызов повторно проверяет frozen artifact identity, CAS digest/size и entry digest. Regression с подменой CAS после cache fill получает IntegrityError. Character ranges по-прежнему не принимают gaps и manifest-only read.

Запуск tests: основной `.venv/bin/python` только как интерпретатор, frozen `src`/tests через PYTHONPATH, все `AGENT_COMMONS*` env удалены, bytecode/cacheprovider отключены, basetemp собственный `/private/tmp/commons-independent-g57-recheck-pytest`. Runner: `/private/tmp/commons-g57-recheck-run.py`.

## Ограничения

Full merged `make check`, asset/wheel packaging, реальный browser/HTTP контроль, installed-MCP fingerprint и exact provider/model canary находятся у root integrator. Другие policy expressions не получили универсальной гарантии линейной сложности: этот fix и timed regressions закрывают найденный bare-identifier случай; cache устраняет повторную обработку того же exact текста. 20 MiB accounting ограничивает encoded text/redaction accounting, а не обещает точный total RSS процесса Python. Model/provider работу и приёмку всего G5/G6/G7 объединения из этого scoped approval выводить нельзя.

## SHA256

| Файл | SHA256 |
|---|---|
| `G5_G7_FIX.patch` | `4c7b68a53825fc55e1f3bae8a30db52cacea283adf74b4ab8d0262313648754e` |
| `G5_G7_FIX_INTEGRATION.json` | `1d97de61873b702992faa6e9be46d63cfb0c0ae21c77e779369663e99b09b523` |
| `G5_G7_FIX_REPORT.md` | `803283d7743e21d3247dcef8c85ae0c3d1574919461eef79f5895219f66ad7da` |
| `src/agent_commons/security/policy.py` | `38d28367490fd162495fe177bf7f63b8e74ad64485d901a0a2ce90379f8a3f4c` |
| `src/agent_commons/mcp/server.py` | `7bcafabf229e30db9a594f6806b2cbce0d9ca5b010844336e4f3476cc4f8571b` |
| `src/agent_commons/mcp/design_output_tools.py` | `9ad2d9e94e7095c54ab07bbdc11bf1585bd647d46599bdb9034e6883c1d69623` |
| `tests/security/test_policy.py` | `51c47910261994c896d6d7523ee0620d32272cb1a27696031244530ed4d5610a` |
| `tests/mcp/test_design_output_tools.py` | `fd363a9453479aeaca69b93c6b0c04dc9450e8b61e649652001b5e44a256c566` |
| `docs/PROTOCOL.md` | `fce76b56fa2e92fd95a2430a02e171f0bea60d340646fdfd3660be8acd363ec2` |
