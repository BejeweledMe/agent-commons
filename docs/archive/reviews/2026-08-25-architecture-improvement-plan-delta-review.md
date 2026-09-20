# Независимый дельта-review: исправление P1 техплана

**Вердикт: APPROVED.**

## Граница и метод

- Проверен неизменяемый Git subject
  `33aa1cbac8cabab0d7cd76b3bf590c057f6fe1e9`, а не текущий рабочий каталог.
  Его diff от `33aa1cb^` меняет только техплан и сохраняет предыдущее
  changes-requested review как evidence для исправления.
- Основание проверки: две P1 из
  `docs/reviews/2026-08-25-architecture-improvement-plan-review.md`, accepted
  `decision.4TZDDRT5PF84KXV6RHQAPTG5BX`, decision register в самом плане и
  accepted решения по Context Pack / Gallery.
- Пользовательский
  `agent_commons_product_architecture_review.md` не открывался и не был
  источником вывода. Для ограничения источников использованы только названные
  в subject исправленные `codex_architecture_improvement_review.md` и
  `claude_architecture_improvement_review.md`.
- `make check` намеренно не запускался: это independent document delta review
  точного Git object, без изменения исполняемого кода.

## Проверенные исправления

1. **CLI freeze соблюдён.** В subject прямо запрещены новые методы в root
   `cli.py`/`cli/__init__.py`, а W0–W6 исключают новые CLI-команды,
   decomposition и behavioural repair (техплан, строки 87–101). A4 теперь
   определён как UI/MCP composition с явной пометкой `CLI excluded` (112,
   203–204); карта компонентов не содержит прежних `cli/work.py`, `work
   health` или `task next --dry-run`. Все оставшиеся упоминания CLI описывают
   freeze либо будущий owner-supersede/UI replacement, а не скрытый пакет
   разработки. Это соответствует accepted
   `decision.4TZDDRT5PF84KXV6RHQAPTG5BX`: security fixes only и отказ от
   decomposition frozen surface.
2. **Owner gates в Mermaid приведены к decision register.** Граф задаёт
   `D1 -> W3` и `D2 -> W3` (123–125), `D3 -> W4` (127–128), `D4 -> W5`
   (129–130) и `D9 -> Push` (131–132). Их подписи совпадают с D1–D4/D9 в
   register (360–368): review contract, reviewer routing/independence,
   finalisation trust, dependency unlock и VISION supersede/security gate.
3. **Границы фасадов и persisted semantics не ослаблены.** План сохраняет
   запрет роста `CommonsManager`, `build_server` и `UIContext` (87–100),
   разводит canonical/operational/derived/owner-authorised классы данных
   (74–83), а новые семантики требуют отдельного owner decision,
   event/schema/replay/migration/rollback project (370–373). W0/W1 остаются
   read-only/derived; W3 не начинается без D1/D2.
4. **`TaskReadiness` остаётся поздним advisory predicate, не DAG engine.**
   Mermaid — граф зависимостей пакетов работ и owner gates, а не модель
   задач (103–107). Сам `TaskReadiness` отложен до W5, является pure,
   deterministic, без take/start/delegation (151–155, 173–178), требует D4 и
   W1/W3/W4 evidence (312–334, 473). Review-pairing W3 от него не зависит.
5. **Approved Context Pack / Gallery scope сохранён.** План оставляет
   canonical revisioned Pack/Design Package отдельным semantic project после
   A8, React Flow Gallery и только current-revision public/internal PNG/JPEG
   preview (442–461). Он не добавляет visual editing, hotspots, arbitrary
   media, provider KV-cache promise или `runtime.yaml` как feature store.

## Итог

Обе P1 из предыдущего review устранены на exact subject. Этот verdict
проверяет лишь документальную дельту: он не принимает D1–D9, не authorises
semantic implementation и не заменяет будущий exact-revision review каждой
реализации.
