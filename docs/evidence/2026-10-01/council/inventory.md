# Исходный backlog и память проекта

Полный ограниченный [инвентарь](project-inventory.json): 304 задачи, 44 findings,
66 decisions. Он сохраняет идентификаторы, ревизии, состояния и зависимости;
не содержит actor credentials, транскриптов или operational logs. Это начальная
граница до задачи текущего консилиума, не автоматически обновляемая проекция.

## Ready и review на исходной границе

| ID | Состояние | Задача |
| --- | --- | --- |
| `task.07DZQY9AZYBK31F3T5ESSE6A9Z` | ready | Build workflow evals, CI gates, docs, and independent release evidence |
| `task.2R2FJEWZPD57MHV6GB2ENAEDRP` | ready | L1: Qualify skill-aware broker on authenticated Linux |
| `task.2SW6GF7XH16TK07HW5GA91KW3N` | ready | Independently review Context Pack and Gallery programme implementation |
| `task.3PA74TA6FF1WWPRYNRXHD8G68N` | ready | WP-20: Write-path speed — incremental ledger validation and single receipt read per write |
| `task.4A15VT58XX1W6XE7J6GG4DAG3N` | ready | R2: Assemble exact Wave 1 release evidence |
| `task.526331J4425W93ZEQFFV4NMJPQ` | review | Honor role:-prefixed recipients in handoff acknowledgement |
| `task.5NNT30SDSNRB43N3HAG1RGQ3BG` | ready | Audit test-suite breadth and CI cost |
| `task.5XWPCS0RKZTM7EQ6322E82MTZ9` | ready | Implement compact indexed orient, delta inbox, and maintenance UX |
| `task.68675SR8DNBJBBSWHM9PSBYAFY` | ready | WP-29: Stale handoffs without a live recipient leave the inbox |
| `task.7HY1A5FB3C1WR2R49V2XC3AG3K` | ready | Implement DAG, routing, council, governance, and builder attestation primitives |

## Как читать остальное

- Accepted фиксирует историческую приёмку точной ревизии; новые правки её не наследуют.
- Completed означает отчёт автора, поэтому 52 такие задачи не приняты массово.
- Cancelled сохраняется как история, а не новый backlog.
- Reported finding проверяется на текущем коде перед новым исправлением.
- Accepted decision сохраняет авторитет своего scope; рекомендация консилиума его не заменяет.

Группировка технического остатка и конкретные действия — в
[плане](../../../plans/2026-10-01-gpt6-and-product-completion.md).
