# Независимый review: Architecture Improvement Plan

**Вердикт: CHANGES_REQUESTED.**

## Граница и метод

- Проверен неизменяемый subject `918b61d4cbe16f89c3388e8f5221884439a0527d`
  через Git object content. Его diff от указанной планом кодовой границы
  `4844fdbc95adc12ed9b11937d1eb6415f3fb3ba6` содержит только сам план и три
  assessment-отчёта; исходный код не менялся.
- Сверены: план, три добавленных отчёта (`corrected-control-plane`,
  `corrected-product-evals`, `corrected-reviews-fact-check`), действующий audit
  target map и accepted owner decisions. Из пользовательских входов использованы
  только `codex_architecture_improvement_review.md` и
  `claude_architecture_improvement_review.md`.
- `agent_commons_product_architecture_review.md` не открывался и не был
  источником вывода. `make check` намеренно не запускался по области задачи.

## Findings

### P1 — планирует замороженную CLI-поверхность без supersede owner decision

План включает CLI в A4 и в новые product surfaces: `cli/work.py`, read-only
`work health`, затем `task next --dry-run` ([план, строки 159, 194–206,
473](../architecture-improvement-implementation-plan.md)). Условие «no
root-CLI growth» не меняет того, что это новые команды и новое поведение.

Это противоречит активному accepted
`decision.4TZDDRT5PF84KXV6RHQAPTG5BX` (`product/cli-surface-status`): CLI
заморожен для security fixes, без feature/behaviour changes; bootstrap остаётся
живым только пока panel не заменит его, а решение отдельно закрепляет отказ от
decomposition `cli.py`. Решение non-stale и не superseded на момент review.

**Требуемая правка:** исключить CLI из A4 и W1/W5 данного плана (оставить UI/MCP
или read-side service); не называть новый CLI group допустимым transport. Если
CLI всё же нужен позднее, поставить явный owner supersede как precondition, а не
подменять его формулировкой про тонкий adapter.

### P1 — Mermaid задаёт неверные owner gates для review routing и finalisation

В графе `D2` подписан как `finalisation trust envelope` и ведёт в W4, а `D3`
как `dependency unlock / authority / admission` ведёт в W5
([план, строки 117–128](../architecture-improvement-implementation-plan.md)).
Но decision register определяет `D2` как reviewer routing/independence, `D3`
как finalisation trust envelope и `D4` как dependency unlock
([план, строки 357–364](../architecture-improvement-implementation-plan.md)).
Из-за этого граф не делает D2 prerequisite W3, хотя prose требует D1+D2
(строка 268), и может быть прочитан как разрешающий W4 без D3 — security-owner
gate для parent finalisation.

**Требуемая правка:** связать `D1 + D2 -> W3`, `D3 -> W4` и `D4 -> W5` в
Mermaid, с теми же подписями, что в decision register. До этого диаграмма не
является безопасной картой очередности.

## Подтверждённое без finding

- План явно запрещает рост `CommonsManager`, root CLI, `build_server`,
  `UIContext` и legacy static UI ([строки 85–98](../architecture-improvement-implementation-plan.md)); это соответствует A3–A8 facade constraints при
  исправлении CLI-конфликта выше.
- Он отделяет derived reads от canonical truth и требует отдельного
  owner-authorised schema/event/replay/migration project для новой семантики
  ([строки 72–84, 352–372](../architecture-improvement-implementation-plan.md)).
  Не обнаружено скрытой persisted-semantics миграции в A3–A8.
- `TaskReadiness` верно остаётся поздним чистым advisory predicate, без DAG
  engine, automatic take/start/delegation или зависимости review-pairing repair
  ([строки 100–105, 170–175, 311–329](../architecture-improvement-implementation-plan.md)).
- Context Pack / Gallery не понижены: F1/F2 остаются на принятом пути, а F3/F4
  сохраняют отдельный после-A8 semantic/migration gate и approved preview scope
  ([строки 441–460](../architecture-improvement-implementation-plan.md)); это
  согласуется с accepted decisions `decision.2ASFCETB9SMAXTVQ5PXRFJYRXW`,
  `decision.0A252PQN9QH7HZCBF4ZDF8BR8X` и
  `decision.50RSN30Q2Q1QW7QYHXX4BZJDHQ`.

После двух P1 корректировок нужен новый exact-revision delta review.
