# Причина отказов кандидатных GPT-6 профилей

1 октября 2026. Исходники и настройки не изменялись. Проверены кандидатные профили с Codex CLI 0.145.0; источник Agent Commons `fab6f03a9ca61162e4495cc8758c662cf4720a7588d7cbee169b8e2c4dcc274e`.

Sol, один диагностический model attempt: synthetic preflight/MCP handshake зелёные; provider exit 1, canonical failed, terminal tools 0, дочерняя сессия закрыта. Точная безопасная ошибка HTTP 400: `The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.` Это подтверждённый отказ выбранной модели при данном способе входа. Он не доказывает недоступность модели через все клиенты или API. Дополнительный stderr: models cache не читается из-за отсутствующего `base_instructions`; причинность этого сообщения для HTTP 400 не доказана.

Astra, один model attempt с внутренним synthetic preflight: preflight зелёный; точная ошибка HTTP 400: `The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again.` Следовательно, старый CLI — подтверждённый блокер именно Astra. Модель не выполнила работу и не вызвала terminal tools.

Первый запуск диагностики внутри tool sandbox остановился до model work на host_sandbox_refused. Отдельный preflight против main/default state тоже не запустил модель. Этот отрицательный MCP результат не относится к корректно созданной synthetic fixture: после очищения унаследованных AGENT_COMMONS_STATE_ROOT/STATE_BASE/SESSION_ID внутренний preflight Astra прошёл. Координатор отдельно подтвердил зелёный project preflight с правильным operational state root.

Сохранены только bounded sanitized stderr и известные error/turn.failed события; prompts, tool payloads и обычный stdout не сохранялись. Копии модели, auth, billing и глобальные настройки не менялись. `canary-sol-diagnostic.json` и `canary-astra-diagnostic.json` содержат результаты; `preflight-astra-diagnostic.json` — предыдущий результат с неверной границей state, не текущая оценка исправного профиля.

Следующий разрешённый эксперимент: изолированный Codex CLI 0.159.3 и неизменный ChatGPT account login, новая static preflight, затем один Sol attempt только при зелёной проверке. Это изменение проверяемого условия, а не повтор прежнего запроса.

## Проверка изменённого условия: изолированный CLI 0.159.3

Временная установка `@openai/codex@0.159.3` создана npm под `codex-cli-candidate` внутри каталога этого отчёта. Новый `profiles-gpt6-cli01593.yaml` копирует исходный кандидатный конфиг; изменены только поля executable обоих Codex-профилей. Модели, аккаунт ChatGPT, MCP-пути, лимиты и политика сохранены. Глобальная установка CLI и операторский конфиг не менялись.

| Модель / CLI | canary ok | canonical state | duration s | terminal calls/completions/rejections |
|---|---|---|---:|---|
| Sol 0.145.0 | False | failed | 5.316873583011329 | 0/0/0 |
| Astra 0.145.0 | False | failed | 3.6372389160096645 | 0/0/0 |
| Sol 0.159.3 | False | needs_operator | 69.66664491605479 | 2/1/1 |
| Astra 0.159.3 | True | succeeded | 41.69461087498348 | 1/1/0 |

На CLI 0.159.3 Sol прошёл initialization и обе static/synthetic preflight, после чего модель реально вызвала terminal MCP. Исходный отказ HTTP 400 больше не наблюдался. Однако process exit 0 не был успешным каноническим завершением: `needs_operator`, `process_canonical_mismatch:true`, две terminal calls, одно completion и один rejected `commons_delegation_needs_operator` с ValidationError; причины аргументов намеренно withheld сервисом. Дочерняя сессия закрыта. Утверждать точную внутреннюю причину needs_operator по этому отчёту нельзя: обычный stdout и tool arguments не сохранялись. Этот builder пока не прошёл compatibility canary.

Astra на CLI 0.159.3 прошёл внутреннюю preflight и фиксированный независимый review flow: `ok:true`, canonical `succeeded`, одна terminal call/completion, ноль rejections и mismatch, дочерняя сессия закрыта. Точная модель `gpt-6-astra`; Agent Commons source SHA во всех четырёх результатах `fab6f03a9ca61162e4495cc8758c662cf4720a7588d7cbee169b8e2c4dcc274e`. Это положительное доказательство синтетического terminal-review поведения на конкретной комбинации, не замена project review или полного macOS/Linux release gate.

Рекомендация: кандидат 0.159.3 подходит для дальнейшей exact-model проверки Astra и может быть закреплён оператором с собственным fingerprint/qualification receipt. Для Sol оставить статус не квалифицирован и сделать bounded разбор needs_operator без автоматических повторов или подмены модели. Не менять billing/auth на API для обхода отказа: такой эксперимент не проводился и не требуется для уже прошедшего Astra. Ни один постоянный receipt не записывался моей диагностикой: qualification_state_root не передавался.

Потрачены ровно четыре model attempts: Sol и Astra на 0.145.0, Sol и Astra на 0.159.3; предварительная sandbox-остановка и отдельные preflight не стартовали model work. Суммы стоимости не измерялись. Никаких prompts/transcripts/tool payloads в сохранённых результатах нет.

## Единственный дополнительный terminal diagnostic Sol

Позднее разрешён ровно один пятый model attempt с извлечением только безопасных terminal payloads и финального user summary. Sol 0.159.3 завершился succeeded за 54.73 с, 1/1/0 terminal, mismatch false. Все семь проверок child environment зелёные. Это не воспроизвело прежний needs_operator и не позволяет восстановить утраченные аргументы ValidationError. Уточнённый разбор и рекомендация: sol-terminal-diagnosis.md; bounded sanitized report: canary-sol-cli01593-terminal-diagnostic.json. Таким образом, Astra имеет один зелёный canary нового CLI, Sol — один красный и один зелёный, полная release qualification не заявляется. В первых четырёх попытках tool payloads не сохранялись; пятая сохраняет лишь явно разрешённые sanitized terminal bodies, без prompts/reasoning/transcript.
