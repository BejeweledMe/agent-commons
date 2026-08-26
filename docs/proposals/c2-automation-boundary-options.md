# C2.0: первая граница автоматизации и восстановления — варианты для решения владельца

**Статус:** proposal only. Документ выбирает порядок исследования, а не
создаёт новый transport, API, capability, event или схему. Единственный
предлагаемый первый срез — read-only диагностика профиля runtime; его нельзя
реализовывать, пока не выполнены нижеуказанные предпосылки и владелец не
примет решение.

**Проверено по рабочему дереву:** 2026-08-26. Внешние скрипты вне C0 support
envelope не обследовались и здесь не считаются отсутствующими.

## 1. Рамка C2 и доказанные ограничения

Принятая CLI-политика оставляет bootstrap, unattended automation, diagnosis,
recovery и broker-операции legacy-адаптерами, пока C2 не выберет и не
протестирует один service/Python successor. C2 начинается только после полного
C0 exit, A8 и выбора одной семьи владельцем; UI, MCP и CLI затем должны быть
тонкими адаптерами одного сервиса, а не повторно реализовывать бизнес-логику.
См. [architecture implementation plan](../architecture-improvement-implementation-plan.md#phase-c1c2--replace-dependencies-through-services-not-another-monolith)
и [C0 inventory](../cli-migration-inventory.md#4-required-characterization-and-parity-gates).

Это согласуется с целевой картой аудита:

- `CommonsManager` должен стать малым ядром, а тематические команды жить в
  `services/<theme>.py`; новая C2-логика не добавляется в
  `services/manager.py` ([structure report](../audits/2026-08-18-code-quality/structure-report.md#2-предлагаемая-карта-модулей)).
- A7 вводит только in-memory typed DTO поверх прежнего wire shape; A8 лишь
  после этого даёт потребителям узкие Protocol через тематические collaborators
  ([audit plan](../audits/2026-08-18-code-quality/audit-plan.md#a7-ввести-узкие-публичные-dto),
  [A8](../audits/2026-08-18-code-quality/audit-plan.md#a8-мигрировать-от-фасада-к-сотрудникам)).
- Persisted ledger/events не меняются. Если будущий срез потребует новой
  семантики события или lifecycle, это отдельный поток В, не C2 и не A3–A8
  ([audit plan](../audits/2026-08-18-code-quality/audit-plan.md#поток-в--решения-человека-и-изменения-поведения)).

Ниже «service/Python boundary» означает versioned in-process Python contract.
Он не означает HTTP API, generic command runner, новые MCP tools или доступ
внешнего процесса к файловой системе.

## 2. Кандидаты

Шкала в таблице: **низкий** означает меньшую цену первого безопасного среза,
а не меньшую важность. Все утверждения привязаны к текущему коду и должны быть
переоценены перед фактической реализацией.

| Семья | Подтверждённая текущая поверхность | Authority и compatibility | Safety / testability | Ценность и зависимости | Вывод |
| --- | --- | --- | --- | --- | --- |
| **A. Broker preflight + bounded diagnostics** | CLI `broker preflight` вызывает `runtime.preflight.preflight_profile`; создаёт read-only manager, проверяет fixed provider flags, MCP contract и stdio handshake. Он не allocates delegation attempt. `broker attempts --diagnostic` — отдельный существующий diagnostic read. [CLI](../../src/agent_commons/cli/__init__.py#L1748), [preflight](../../src/agent_commons/runtime/preflight.py#L230). | Preflight сегодня не требует canonical session и не пишет ledger. Он использует только operator-owned profile registry, resolved workspace/state root и allowlisted executable; будущий адаптер обязан сохранить отсутствие обязательной session, пока владелец явно не выберет новый access rule. Current CLI JSON/exit-2 остаются compatibility contract. | Preflight запускает credential-free `--help` и MCP probes, поэтому он не является pure calculation; однако код явно возвращает `consumed_delegation_attempt: false` и `provider_work_process_started: false`. Hermetic `ProbeRunner` tests уже существуют; C0-test доказывает refusal, exit 2 и отсутствие runtime attempt. [preflight result](../../src/agent_commons/runtime/preflight.py#L230), [runtime tests](../../tests/runtime/test_preflight.py), [C0 drill](../../tests/cli/test_cli_c0_recovery_drills.py). | Высокая: это ранняя диагностика конфигурации до оплачиваемой/изменяющей ledger работы и обязательный предшественник безопасного runtime flow. Нужны C0 exit и A8, но не нужна новая event semantics. | **Рекомендованный первый C2 family.** Ограничить только preflight; не включать launch, canary, stop или reconcile. |
| **B. Receipt / idempotency recovery** | `receipt status`, `receipt reconcile`, `receipt abandon` вызывают `ReceiptCommands`: reconcile восстанавливает/сверяет receipt state; abandon требует активную сессию с `receipt:abandon` capability и создаёт audit tombstone. [CLI](../../src/agent_commons/cli/__init__.py#L2785), [service](../../src/agent_commons/services/receipts.py). | Это compatibility anchor для checkout-scoped recovery. В нём уже есть capability, actor, canonical lock, reconciliation и rollback/legacy modes. Нельзя превратить его в convenience API без явной модели operator authority и миграции help/JSON/exit contract. | Более высокий риск: ошибка может оставить workspace unhealthy или необратимо tombstone-ить key identity. Имеются хорошие recovery fixtures (missing receipt, clone/worktree, conflict, post-abandon arrival), но это не означает готовность для нового transport. [C0 drill](../../tests/cli/test_cli_c0_recovery_drills.py), [checkout recovery](../../tests/contract/test_h2_checkout_recovery_contract.py). | Высокая operational value, но C0 прямо называет receipts emergency recovery без successor и high risk. Нужны security/rollback decision, full recovery-drill evidence и отдельный owner acceptance. | **Отложить после A.** Не смешивать с диагностикой только потому, что обе читают/лечат state. |
| **C. Broker post-crash reconciliation** | `broker reconcile` вызывает `DelegationRuntimeService.reconcile`; не слепо relaunch-ит: live process только сообщает оператору, а ambiguous non-terminal attempt может перейти в `needs_operator` и затем canonical finalisation. [CLI](../../src/agent_commons/cli/__init__.py#L1914), [service](../../src/agent_commons/services/delegation_runtime.py), [runtime reconcile](../../src/agent_commons/runtime/broker.py#L144). | Это уже canonical/runtime coordination, привязанная к active requester session и exact delegation lifecycle. Сохранение неявной authority или неудачный retry могут ложно заявить, что provider stopped/finished. | Safety-critical: код намеренно не записывает outcome, пока процесс live; `run` связывает expected requested revision и launch idempotency key. Нужны fake-provider crash/race tests, а не только pure DTO tests. [runtime service](../../src/agent_commons/services/delegation_runtime.py#L1040). | Value высокий после реального crash, но для первой границы он тащит delegation authority, operational state и finalisation policy. Зависит не только от C0/A8, но и от W4/D3 security decision. | **Не первый C2.** Планировать как отдельный recovery proposal после preflight и W4 decision. |

## 3. Рекомендация: C2.1 `RuntimePreflightService` (design first)

Владельцу предлагается выбрать **A: broker preflight как отдельную
диагностическую Python-границу**. Это не «перенос broker в API»: новый contract
охватывает только проверку profile/MCP compatibility до delegation attempt.

Почему именно он:

1. Он имеет самый маленький authority envelope: нет canonical write, session
   lifecycle, delegation transition, billing attempt или prompt body.
2. Он уже fail-closed для невалидного executable/profile/MCP contract и имеет
   наблюдаемые признаки отсутствия provider-work/attempt.
3. Он даёт пользователю/автоматизации практическую ценность сейчас: до запуска
   отличает configuration failure от runtime launch/recovery failure.
4. Его граница естественно оставляет сложные B/C отдельно, вместо того чтобы
   преждевременно слить diagnostics с recovery mutations.

Это рекомендация порядка, не решение о доступе к профилям, сроке сохранения
CLI или новой capability.

### 3.1. Предварительный typed contract (не реализовывать до gate)

Возможный после-A8 service lives alongside runtime service ownership, не в
`CommonsManager`, CLI или `mcp/server.py`:

```python
@dataclass(frozen=True, slots=True)
class RuntimePreflightRequest:
    schema_version: Literal["agent_commons.runtime_preflight.v1"]
    profile_id: BuiltinProfileId
    purpose: Literal["implementation", "independent_review", "verification"]


@dataclass(frozen=True, slots=True)
class RuntimePreflightCheck:
    name: str
    ok: bool
    diagnostic_code: str | None
    safe_next_actions: tuple[str, ...]
    bounded_details: Mapping[str, str | int | bool]


@dataclass(frozen=True, slots=True)
class RuntimePreflightResult:
    schema_version: Literal["agent_commons.runtime_preflight.v1"]
    profile_id: BuiltinProfileId
    provider: Provider
    ok: bool
    checks: tuple[RuntimePreflightCheck, ...]
    consumed_delegation_attempt: Literal[False]
    provider_work_process_started: Literal[False]
```

`RuntimePreflightService.preflight(request, *, context)` should receive a
composition-root-only context containing an already-loaded `ProfileRegistry`,
resolved workspace root and exact state root. The public request must **not**
accept arbitrary executable argv, prompt/instruction, MCP config text,
environment overrides, raw output or a mutable filesystem path. The service
delegates existing `preflight_profile`, and a narrow adapter normalizes its
current `dict[str, Any]` result into frozen DTOs; it does not duplicate the
probes.

**Authority:** preserve today’s read-only/no-session semantics for v1, with
the profile registry supplied by the operator-controlled composition root. That
is deliberately weaker than a new canonical capability and therefore does not
invent one. The owner must explicitly choose whether a later remote/service
caller needs an operator permission; such a change is out of this v1 parity
proposal.

**Expected revision and idempotency:** both are explicitly inapplicable:
`RuntimePreflightRequest` carries neither `delegation_id` nor
`expected_revision`, and it rejects an idempotency key. A repeated preflight is
a fresh observation of local executables/configuration, not a retry of a
canonical write. Result constants must remain `False` for attempt consumption
and provider work. If a later operation binds to a delegation or persists a
report, that is a different contract requiring exact revision, stable
idempotency and a semantics/migration proposal.

**Typed refusal:** invalid request shape/unsupported purpose and locally unsafe
configuration return a typed refusal with stable diagnostic code and safe next
actions; configuration detail and provider raw output remain excluded. A valid
preflight with failed checks is a `RuntimePreflightResult(ok=False)`, matching
current diagnostic intent. CLI retains its mapping to compact JSON and exit 2;
MCP/UI adapters are future work, not implied by this document.

## 4. Required gates, evidence and rollback

### Preconditions before C2.1 implementation

1. **C0 full exit:** golden command/help/output/error fixtures and recovery
   drills must be independently evidenced. The current inventory is expressly
   only a baseline, not the exit ([C0 status](../cli-migration-inventory.md#5-c0-exit-and-next-owner-gates)).
2. **A8:** thematic collaborators and narrow consumer Protocols must exist;
   C2 must not add a new method to the retiring manager facade.
3. **Owner decision below:** select family A and its authority/parity envelope.
4. **Quiescent exact-revision review:** implementation, DTO adapter and tests
   receive independent security/compatibility review before a legacy adapter
   changes behaviour.

### Hermetic evidence plan

- Golden v1 cases for every built-in profile/purpose: request validation,
  missing provider/MCP/Git executable, missing provider flag, wrong MCP schema,
  handshake error, and success.
- Assert that each case has no canonical event, no runtime attempt file, no
  delegation mutation, no provider-work process and no secret/raw-argv echo.
- Contract adapter parity: old CLI JSON fields/typed codes and exit-2 mapping
  match the v1 DTO outcome. Existing `ProbeRunner` remains the primary
  hermetic double; real provider canaries remain opt-in and outside presubmit.
- Regression cases prove repeated observations are allowed but cannot reuse a
  stale positive response to authorize `broker run`.

### Rollback and compatibility

The first release is additive: existing `broker preflight` continues to call
the same logical service and preserves names, input flags, compact JSON and
exit semantics. Rollback switches the adapter back to the current
`preflight_profile` implementation; it does not migrate/delete events or
receipts because C2.1 owns none. Do not deprecate any CLI command, expose a
network endpoint, or advertise MCP parity in this release. A mismatch in
preflight result, a provider-work start, an attempt allocation, or secret
exposure is a fail-closed release blocker.

## 5. Explicit non-goals

- No generic Python `run(command, env, prompt)` or generic MCP command tool.
- No `broker run`, `canary`, `stop`, `reconcile`, retry, provider process
  control, child session creation, canonical finalisation or automatic launch.
- No receipt reconcile/abandon, ledger/event/schema changes, authority grants
  or remote HTTP API.
- No decomposition of `cli/__init__.py`, new CLI command, UI product surface
  or changes to `mcp/server.py::build_server`.
- No claim that unknown external scripts have migrated, and no C3/C4
  deprecation or removal decision.

## 6. Exact owner decision requested

**D-C2.1 — choose the first automation/recovery family and its authority
envelope.**

Approve or reject the following bounded option:

> After C0 full exit and A8, implement only `RuntimePreflightService v1` for
> local, operator-configured profile/MCP compatibility diagnostics. Preserve
> today’s no-session read-only semantics; expose no arbitrary command/prompt,
> no remote API and no canonical write. Keep broker recovery and receipt
> recovery legacy until separate proposals, with `broker reconcile` gated by
> W4/D3 and receipt mutation gated by its capability/rollback review.

If rejected, the owner must choose one of B or C and explicitly supply its
authority, expected-revision/idempotency, persistence and rollback policy;
those facts cannot be inferred from the current CLI implementation.
