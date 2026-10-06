# Повторная квалификация после объединения продукта

Граница первых попыток: Python source `9b3368df09bceb1a5196663937886fbc0c7705ee3b814562f771463763868196`, постоянный официальный Codex CLI 0.159.3, `gpt-6-astra`. Исходный операторский YAML пока не изменён.

1. Builder preflight прошёл. Первый canary: provider exit0, canonical needs_operator, terminal2/1/1. Детальная причина не сохранена обычным canary; ошибочный reason_code скрыт штатным sanitizer. Это не квалификация.
2. Отдельная bounded диагностическая попытка с фильтром только terminal tool arguments/results и видимого final summary прошла1/1/0. Worker отметил, что обычный CLI недоступен, и полагался на scoped MCP. Это успех небольшого протокольного сценария, но недостаточное доказательство полного onboarding. Raw provider output и private reasoning не сохранены.
3. Координатор подтвердил, что существующий uv-installed `agent-commons` отсутствовал в наследуемом PATH. В существующий trusted bin добавлена ранее отсутствовавшая ссылка на тот же installed executable; файлы не перезаписывались. В следующем canary child PATH lookup стал успешен, однако CLI doctor/session/claim отказали: state принадлежал другому workspace. Worker сначала придумал недопустимый reason_code `workspace_state_mismatch`, затем корректно сообщил `integrity_error`:2/1/1, needs_operator. Эта попытка тоже сохранена.
4. Read-only проверка обнаружила в пользовательском `.zshrc` экспорт общего `AGENT_COMMONS_STATE_ROOT`. Native login shell может заменить переданное брокером значение. Доказательство broker child environment до shell было корректным; оно не доказывало финальный shell environment. Shell config не менялся. Следующее исправление передаёт implementation worker абсолютные CLI arguments с точными repo/state/child-session и `--read-only`; canonical writes остаются только в scoped MCP. Финальная квалификация этого исправления записывается отдельно.

Reviewer не использует native shell и отдельно прошёл canary1/1/0, затем настоящие image/build reviews1/1/0. Нельзя трактовать последовательность builder failures как сравнительный benchmark качества Astra/Sol. Первоначальная причина неуспеха Sol ретроспективно не установлена.

Отдельная рекомендация: `commons_delegation_needs_operator.reason_code` пока имеет MCP type string, хотя домен проверяет закрытый enum. Типизированная schema поможет модели выбирать допустимый код; это не причина подмены workspace и не повод ослаблять доменную проверку.

Финальные source-bound Astra canaries и постоянный pin подтверждены отдельно: [итог](final-qualification-summary.json), [pin](permanent-profile-pin.json). Описанные выше отказы остаются историческими попытками.
