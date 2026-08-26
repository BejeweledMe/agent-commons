# C0: инвентаризация CLI и границы миграции

**Статус:** C0 inventory baseline для принятой политики. Полный C0 exit
заблокирован до characterization fixtures и recovery drills из раздела 4.
[`decision.65J1MEQNYC1GNHJYK9KDBDK49S`](architecture-improvement-implementation-plan.md#23-поэтапный-уход-от-cli-без-потери-операционной-способности).
Это не план удаления команд и не разрешение менять `cli/__init__.py`.

**Проверенная ревизия:** `de4e4096afeb7a597b65a78485c0be0311ec26d6`.

## 1. Что именно поддерживается

Поддерживаемый C0-контур ограничен тремя источниками:

1. зарегистрированным Click command tree в
   `src/agent_commons/cli/__init__.py` и `cli/workspace.py`;
2. вызовами и parsing ожиданиями в репозиторных docs, tutorials, skills,
   tests и `agent_commons.evals`;
3. явно названным владельцем external consumer.

Неизвестный script вне этого контура не объявляется отсутствующим. Он остаётся
documented compatibility risk: C3 не вправе заявить, что таких потребителей
нет, но и C0 не должен бесконечно ждать недоступный инвентарь чужих машин.

Публичный console entry point — `agent-commons = agent_commons.cli:cli` в
[`pyproject.toml`](../pyproject.toml). Полный список ниже основан на реальной
регистрации, а core command contract дополнительно пиннит
[`tests/cli/test_cli.py`](../tests/cli/test_cli.py).

### 1.1. Общий compatibility contract

- Global options: `--repo`, `--session-id`, `--json`, `--state-root`,
  `--state-base`, `--read-only`.
- Success без `--json` является YAML-представлением; с `--json` — compact,
  sorted JSON.
- Domain refusal в JSON имеет форму
  `{ok: false, error: {type, message, safe_next_actions, code?, details?}}`
  и exit code `1`.
- Health/preflight/canary могут завершаться exit code `2` при честном unhealthy
  result. Это отдельный контракт, а не generic failure.

Доказательства: [`cli/__init__.py`](../src/agent_commons/cli/__init__.py),
[`cli/_shared.py`](../src/agent_commons/cli/_shared.py) и
[`cli/workspace.py`](../src/agent_commons/cli/workspace.py).

Репозиторные shell consumers парсят значения, а не только читают текст:
[`FIRST_DELEGATION.md`](tutorials/FIRST_DELEGATION.md) использует
`entity_ref.id`, `revision`, `claim_id` и `nonce`. До конца compatibility
window замена такого command family обязана сохранять эти field-level
expectations либо иметь owner-approved migration guide и новый contract.

## 2. Command-family inventory

`UI` означает UI-first successor только для названного normal-human flow.
`MCP` означает purpose-scoped agent successor, не generic scripting API.
`None` — нет доказанного successor; CLI остаётся legacy/bootstrap/emergency
adapter. “Partial” никогда не является основанием удалить family.

| CLI family | Доказанный репозиторный consumer / назначение | Evidence / bounded-consumer status | Уровень successor | Диспозиция и риск |
| --- | --- | --- | --- | --- |
| `init` | Quickstart и manual setup. | [`README`](../README.md); registration [`L119`](../src/agent_commons/cli/__init__.py#L119). | UI partial: first-run вызывает тот же initializer. | Headless bootstrap остаётся; не удалять. |
| `ui` | README/tutorial human entry point. | [`README`](../README.md); registration [`L161`](../src/agent_commons/cli/__init__.py#L161). | Already UI, но command — loopback launcher. | Bootstrap prerequisite; не UI replacement самого launcher. |
| `chat` | Human chat и coordination flow. | [`USER_WORKFLOWS`](USER_WORKFLOWS.md); registration [`L356`](../src/agent_commons/cli/__init__.py#L356). | UI partial; MCP — только addressed worker thread. | Не заменяет arbitrary agent/operator coordination. |
| `search` | Onboarding, operator investigation, eval harness. | Registration [`L417`](../src/agent_commons/cli/__init__.py#L417); repo eval caller. | UI partial reads; MCP bounded repo search. | Нужны parity и read-only tests. |
| `support` | Troubleshooting/support guidance. | [`TROUBLESHOOTING`](TROUBLESHOOTING.md); registration [`L418`](../src/agent_commons/cli/__init__.py#L418). | None. | Recovery/support contract сохраняется. |
| `session` | Quickstart, First Delegation, agent skills. | [`FIRST_DELEGATION`](tutorials/FIRST_DELEGATION.md); registration [`L421`](../src/agent_commons/cli/__init__.py#L421). | UI browser session only. | Нет headless/agent successor; высокий риск. |
| `orient` | Human/agent orientation. | [onboarding](../.agent-commons/ONBOARDING.md); registration [`L526`](../src/agent_commons/cli/__init__.py#L526). | UI overview + MCP worker orientation partial. | Characterize `--fresh`/`--verbose` and JSON. |
| `inbox` | Human/agent coordination. | [onboarding](../.agent-commons/ONBOARDING.md); registration [`L539`](../src/agent_commons/cli/__init__.py#L539). | UI partial + MCP worker inbox partial. | Нет universal automation successor. |
| `objective` | Manual strategic work lifecycle. | Registered-only; no repo caller found; registration [`L552`](../src/agent_commons/cli/__init__.py#L552). | None. | Не включать в C1 до отдельной semantic/UI scope. |
| `task` | Tutorials и ежедневный coordination flow. | [`FIRST_DELEGATION`](tutorials/FIRST_DELEGATION.md); registration [`L640`](../src/agent_commons/cli/__init__.py#L640). | UI partial: create/revise/request-review/accept/reopen. | Take/start/block/unblock/complete/submit/cancel и CAS lifecycle остаются CLI-only. |
| `delegation` | First Delegation, runtime lifecycle. | [`FIRST_DELEGATION`](tutorials/FIRST_DELEGATION.md); registration [`L958`](../src/agent_commons/cli/__init__.py#L958). | UI launch partial; root MCP request/cancel/recover partial. | Full lifecycle, recovery и terminal ambiguity — высокий риск. |
| `agent` | Role governance. | Registered-only; no repo CLI caller found; registration [`L1054`](../src/agent_commons/cli/__init__.py#L1054). | UI partial: create/reconfigure/retire/link. | Проверить authority, grants и non-UI lifecycle before migration. |
| `broker` | Provider preflight, canary, attempts, reconcile and ops. | [`USER_WORKFLOWS`](USER_WORKFLOWS.md); registration [`L1733`](../src/agent_commons/cli/__init__.py#L1733). | UI setup/launch narrow; root MCP bounded runtime controls. | CLI-only operator/recovery transport; very high risk. |
| `thread` | Handoff/review/coordination. | [`USER_WORKFLOWS`](USER_WORKFLOWS.md); registration [`L1933`](../src/agent_commons/cli/__init__.py#L1933). | UI chat partial; MCP only addressed thread. | Full thread governance не мигрирована. |
| `artifact` | Provenance, manifest and preview. | Registered core surface/test; registration [`L2050`](../src/agent_commons/cli/__init__.py#L2050). | UI authenticated PNG/JPEG preview is read-only partial; MCP bounded reads. | Register/revise and arbitrary artifact lifecycle remain CLI-only. |
| `review` | Independent review lifecycle. | [`USER_WORKFLOWS`](USER_WORKFLOWS.md); registration [`L2134`](../src/agent_commons/cli/__init__.py#L2134). | UI request/accept partial; reviewer MCP completes own review. | Exact revision, independence and admin paths must remain explicit. |
| `verification` | Evidence record workflow. | Registered core surface/test; registration [`L2214`](../src/agent_commons/cli/__init__.py#L2214). | MCP verification worker can record; UI none. | High governance/authority risk. |
| `finding` | Defect/risk truth workflow. | Registered-only; no repo caller found; registration [`L2261`](../src/agent_commons/cli/__init__.py#L2261). | None. | Preserve canonical governance contract. |
| `decision` | Owner decision truth workflow. | Registered-only; no repo caller found; registration [`L2378`](../src/agent_commons/cli/__init__.py#L2378). | None. | Preserve canonical governance contract. |
| `handoff` | Durable transfer/acknowledgement. | Registered-only; no repo caller found; registration [`L2547`](../src/agent_commons/cli/__init__.py#L2547). | UI/MCP partial read/interaction. | Typed recipients and acknowledgement remain unproven. |
| `claim` | Shared-work path/resource leases. | [`FIRST_DELEGATION`](tutorials/FIRST_DELEGATION.md); registration [`L2626`](../src/agent_commons/cli/__init__.py#L2626). | None. | Critical coordination/recovery; no browser substitute. |
| `event` | Correction/invalidation/revocation. | Registered-only; no repo caller found; registration [`L2699`](../src/agent_commons/cli/__init__.py#L2699). | None. | Emergency/forensics only; no removal candidate. |
| `receipt` | Idempotency recovery. | [`TROUBLESHOOTING`](TROUBLESHOOTING.md); registration [`L2785`](../src/agent_commons/cli/__init__.py#L2785). | None. | Emergency recovery; contract includes status/reconcile/abandon. |
| `views` | Generated operational views. | Registered-only; no repo caller found; registration [`L2834`](../src/agent_commons/cli/__init__.py#L2834). | UI partial reads. | Read parity must be proven per view, not inferred. |
| `index` | Index maintenance/query support. | Registered-only; no repo caller found; registration [`L2847`](../src/agent_commons/cli/__init__.py#L2847). | UI search partial. | Operational rebuild/support remains CLI-only. |
| `doctor` | Integrity/health diagnosis. | [`TROUBLESHOOTING`](TROUBLESHOOTING.md); registration [`L2860`](../src/agent_commons/cli/__init__.py#L2860). | None. | Exit-2 health semantics and recovery guidance are compatibility anchors. |

## 3. What UI and MCP can honestly replace today

### Normal human work: candidate C1 flows

The panel can credibly cover first-run generated configuration, role creation
and reconfiguration, task create/revise, launching a run, review request and
acceptance, chat and selected reads. It does **not** cover the complete task,
objective, governance, session, claim, receipt or broker lifecycle.

The current React Flow app is Gallery-only and deliberately returns
`gallery_data_unavailable` until Design Package reads exist. It is not a
general product shell. The existing panel asset is a single-writer legacy file.
Therefore C1 needs a separate owner-approved UI-surface contract before any
writer: named route and target subtree, build/package delivery, browser session
boundary, paired locales/shared vocabulary source, CSP/storage model, typed
DTO/refusal contract, rollout/rollback and exclusive path claims. It may not
silently use Gallery or `ui/static/index.html`.

### Agent work: MCP remains purpose-scoped

Worker MCP offers bounded work/result/blocker/input/progress, review and
repository-read operations only for the live child session and its matching
delegation. Root MCP offers bounded delegation/runtime operations. It has no
generic command executor and intentionally lacks full claims, receipts,
forensics, administration and scripting authority. MCP is thus the right
successor for a named agent-in-run flow, not a successor for CLI automation.

Evidence: [`mcp/server.py`](../src/agent_commons/mcp/server.py),
[`tests/mcp/test_worker_scope.py`](../tests/mcp/test_worker_scope.py) and
[`tests/mcp/test_server.py`](../tests/mcp/test_server.py).

## 4. Required characterization and parity gates

### C0 compatibility characterization

1. Golden `--help` tree, command names, required arguments and defaults.
2. For each family, success and typed-error fixtures: exit code, stdout,
   stderr and JSON shape.
3. Field-level fixtures for tutorial parsers (`entity_ref.id`, `revision`,
   `claim_id`, `nonce`).
4. Recovery drills for state-root mismatch, receipt reconciliation and broker
   post-crash reconciliation.

### C1 human-flow parity

A named flow can leave the normal-human CLI class only after all of these pass:

1. Fresh workspace browser E2E: initialise, generate trusted config, hire,
   create task and launch; assert fragment exchange, opaque API base/cookie
   path and typed 409 state rather than route existence.
2. Daily-loop E2E: revise task, observe run, answer input/blocker, request
   independent review, then accept/refuse/stale-review behaviour. Process
   success must never render as task acceptance.
3. Negative matrix: read-only panel, missing/invalid session, unavailable
   catalogue/runtime and no body parsing before typed refusal.
4. Cross-surface assertion: a MCP result becomes a UI outcome, never an
   acceptance; current independent-review requirements still govern acceptance.

### C2 automation/recovery boundary

C2 starts only after C0 exit, A8 thematic collaborators and an owner-selected
automation/recovery family. Its ADR and fixtures must specify a versioned typed
service/Python request/result boundary with authority, expected revision,
idempotency and typed refusal. It must include hermetic recovery and
compatibility cases. UI, MCP and retained CLI commands then adapt the same
service; no second business-logic path is added to CLI. This is design-only:
actual adapter behaviour or emergency migration still waits for C4 owner
decision, compatibility evidence and recovery drills.

## 5. C0 exit and next owner gates

This document completes the **inventory baseline**: every registered family has
a disposition, consumer evidence or explicit registered-only classification, and
every “no successor” state is visible. The full C0 **phase exit** remains
blocked until the golden help/output/error fixtures and recovery drills in
section 4 are implemented and independently evidenced. Neither baseline nor
phase exit creates deprecation warnings or changes command behaviour.

- **C3:** owner decision, migration guide, compatibility window and evidence
  that no bounded-support consumer remains CLI-only for that normal human flow.
  Unknown external use stays a disclosed risk.
- **C4:** owner decision after C2 contract and recovery drills for the selected
  bootstrap/automation/recovery family.

Neither C3 nor C4 authorises broad CLI decomposition. A narrowly
contract-preserving split needs its own measured owner exception if actual
contention or safety evidence justifies it.
