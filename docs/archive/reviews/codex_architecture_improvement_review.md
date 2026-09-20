# Agent Commons: независимое консилиумное ревью архитектурных улучшений

**Дата:** 25 августа 2026  
**Revision boundary:** текущий checkout на момент синтеза —
4844fdbc95adc12ed9b11937d1eb6415f3fb3ba6.

**Статус:** архитектурное предложение. Публикация не заменяет operator-authorized
decision, evidence и exact-revision binding.

## Provenance и метод

Этот документ переписан с нуля после обнаруженной коллизии авторства. Предыдущая
версия сохранена в codex_architecture_improvement_review.provenance-copy.md, а
claude_architecture_improvement_review.md сохранён отдельно как provenance
evidence. Ни один из этих файлов не использовался как источник текста.

Источники синтеза:

- Pascal — ML/LLM architecture, agents, context and evals;
- Euler — product/UX, onboarding and founder attention;
- Dalton — CTO/system/software architecture, migration and reliability;
- текущий код, схемы, тесты, docs/ARCHITECTURE.md, docs/PROTOCOL.md и
  agent_commons_product_architecture_review.md.

Метки:

- [FACT] — проверенный факт текущего среза;
- [ASSUMPTION] — явно обозначенная гипотеза;
- [RECOMMENDATION] — предложение;
- [DEFERRED] — отложенная возможность;
- [RISK] — риск;
- [DECISION NEEDED] — вопрос для owner.

## 1. Executive verdict

Agent Commons уже имеет сильный governance-core, но ещё не имеет доказанного
автономного work loop. Ближайшая цель:

~~~text
Objective
  -> admitted Task DAG
  -> deterministic runnable projection
  -> dispatch_once
  -> bounded existing delegation/broker
  -> artifact and revision-bound evidence
  -> independent review
  -> acceptance
  -> dependent task becomes runnable
~~~

Консилиум сходится в следующем:

1. [FACT] Immutable event ledger, manifests, exact-revision evidence,
   stale cascade, independent review, completed != accepted, SQLite projections
   и fail-closed runtime — наиболее зрелая часть продукта.
2. [FACT] Scheduler/work-admission слоя нет как связного end-to-end контура.
   Dependencies существуют, но безопасный DAG, readiness и dispatch не
   образуют рабочий цикл.
3. [FACT] Persistent Agent/Role, Delegation, Session и Attempt частично
   различены, но first-class ExecutionRun отсутствует.
4. [RECOMMENDATION] Сначала сделать read-model и вертикальный slice,
   совместимый с существующей Delegation. Не делать механическое переименование
   Delegation в Run и не вводить dual-write.
5. [RECOMMENDATION] LLM может предлагать decomposition, route candidates,
   explanations и tie-break между уже допустимыми вариантами. LLM не является
   authority для eligibility, acceptance, budget escape или truth promotion.

Продуктовая формула:

> Agent Commons помогает локальной команде довести ограниченную работу от
> objective до принятого revision-bound результата и подключает человека только
> там, где требуется решение, разрешение или recovery.

До доказательства этого обещания нельзя честно продавать продукт как
autonomous company OS.

## 2. Текущая архитектура: verified baseline

### 2.1. Canonical truth

[FACT] Проект строится вокруг immutable event ledger и manifests. SQLite/WAL,
Markdown views, attention, runtime journals и telemetry являются projection или
operational state и должны быть rebuildable.

[FACT] Канонические сущности включают Objective, Task, Agent, Delegation,
Review, Verification, Finding, Decision, Artifact, Handoff, Thread и
AgentLink.

[FACT] Exact-revision binding связывает evidence с тем состоянием субъекта,
которое проверил reviewer. Изменение основания делает evidence stale через
cascade, а stale evidence исключается из effective truth.

[FACT] completed не равен accepted. Provider success, artifact creation,
review approval и governance acceptance — разные trust levels.

[FACT] Independent review ограничивает self-approval через principals,
sessions и work-author lineage.

### 2.2. Application and runtime

[FACT] CLI, MCP и UI используют общий CommonsManager/application boundary.
Canonical mutation не должна обходить эту boundary.

[FACT] Runtime является локальным allowlisted broker для Codex/Claude.
Профили purpose-specific; role может сузить tool scope, но не расширить
operator-allowlisted profile.

[FACT] Runtime ограничен depth, fanout, attempts, wall time и concurrency.
Leaf-only delegation и один writable worker на checkout остаются текущими
safety boundaries.

[FACT] Broker не создаёт и не merge-ит worktrees. Claims не заменяют Git
ownership и locks.

[FACT] input_needed не означает гарантированную resumability. Provider
reattach не является доступной общей capability; ambiguous post-start state
должен идти в needs_operator.

[FACT] Prompts, transcripts, reasoning, raw tool arguments и raw provider
output не являются canonical telemetry. Точная token/cost truth для текущего
Codex runtime не гарантирована.

### 2.3. Product surfaces

[FACT] Attention projection уже собирает blocked runs, returned work,
human-facing threads и configuration problems, но не является typed Decision
Inbox.

[FACT] Canvas/Gallery — projection/editor surface, не source of truth.
Context Pack и Context Compiler в текущем срезе остаются планом либо неполной
реализацией.

[FACT] UI уже умеет честно различать succeeded, approved, accepted и stale,
а также показывать empty/error/refusal states.

### 2.4. Revision and evidence limitations

- [FACT] Текущий HEAD указан в заголовке этого документа.
- [FACT] Документы и HEAD могут иметь разные revision boundaries.
- [ASSUMPTION] Early adopter — один оператор или небольшая локальная команда,
  а не multi-tenant cloud.
- [FACT] Этот review не является security authorization, production readiness
  assessment или обещанием provider cost accuracy.

## 3. Findings

| ID | Severity | Finding | Effect |
|---|---:|---|---|
| F1 | P0 | Нет Objective -> runnable work -> accepted result -> unlock loop | Founder остаётся ручным coordinator |
| F2 | P0 | Dependencies не образуют безопасный DAG/readiness contract | Cycle, ложная готовность, deadlock и premature launch |
| F3 | P0 | Нет единой семантики Task/Run/Delegation/Attempt/Session | Retry, ownership, context и metrics плохо связываются |
| F4 | P1 | Capability, permission и authority не разделены полностью | Технически возможное действие может быть организационно запрещено |
| F5 | P1 | Writable parallelism не готова | Shared checkout создаёт гонки и stale evidence |
| F6 | P1 | Attention не сжата до typed decision/blocker surface | Человек сам расшифровывает, что требует действия |
| F7 | P1 | Context/Gallery опережают доказанный core loop | Structural work может расти быстрее outcome value |
| F8 | P1 | Evals больше покрывают safety/storage, чем accepted-work outcome | Нет evidence снижения coordination load |
| F9 | P2 | Observability не даёт точной cost/quality truth | Cost dashboard и ranking будут выглядеть точнее фактов |
| F10 | P2 | Structural и documentation drift | Новые contexts могут усилить coupling и stale decisions |

### F1. Нет work loop

[FACT] Система умеет сохранять Task, вручную запускать Delegation, хранить
Artifact и проводить Review, но эти элементы не образуют scheduler-driven
цепочку, которая после acceptance открывает следующую работу.

[IMPACT] Продукт пока остаётся сильным coordination/governance workspace, но
не доказывает control-plane promise.

[RECOMMENDATION] Сначала построить один bounded loop A -> B с одним builder,
одним independent reviewer и human gate только если policy требует.

### F2. Dependencies не являются DAG

[FACT] Проверка существования dependency не равна acyclicity. Readiness нельзя
выводить только из Task state ready.

[RECOMMENDATION] Ввести чистую функцию:

~~~text
readiness(task, effective_snapshot) ->
  runnable | blocked_by_dependency | blocked_by_policy |
  blocked_by_claim | blocked_by_budget | stale | invalid
~~~

Cycle detection должен быть частью command validation. Derived blocked_reason
не становится второй truth-сущностью.

### F3. Run semantics

[FACT] Delegation уже хранит intent, target, target revision, limits, child
session, lineage и terminal/recovery information. AttemptStore описывает
provider/process attempt.

[DISAGREEMENT] Pascal предлагает additive canonical ExecutionRun для будущих
retry/worktree/context semantics. Euler и Dalton предупреждают о migration и
dual-write risk.

[RECOMMENDATION] Сначала TaskRun/RunView как derived projection над Task +
Delegation + Attempt. Канонический Run принимать только после evidence из
реальных retry/rework/worktree сценариев.

### F4. Authority

[FACT] Role grants, capabilities, lineage и narrow links не являются полной
моделью organizational authority.

[RECOMMENDATION] Разделять:

~~~text
capability = что runtime технически умеет;
permission = какие операции/tool/filesystem разрешены;
authority = какие business/governance решения можно принять.
~~~

Model label, session identity, title и self-declared capability не являются
authentication или authority proof.

### F5. Writable concurrency

[FACT] Shared mutable paths не готовы к нескольким writable workers.

[RECOMMENDATION] Read-only fan-out можно рассмотреть раньше. Writable fan-out
возможен только как:

~~~text
Run -> dedicated worktree -> path attestation -> review -> explicit merge
~~~

Автоматический merge и shared checkout mutation запрещены.

### F6. Attention

[FACT] Existing Attention полезна, но смешивает blocked run, returned work,
thread и configuration issue.

[RECOMMENDATION] Сначала сделать Operator Attention Queue с типами:

- decision required;
- stale evidence;
- review missing;
- provider/runtime recovery;
- blocked dependency;
- returned work;
- configuration refusal.

Decision Inbox становится главным экраном только когда decision volume и
precision измерены. Каждый item имеет owner, why_now, evidence, options и
next_safe_action.

## 4. Решение по предыдущему review

| Предложение | Решение | Обоснование |
|---|---|---|
| Ledger, manifests, revisions, stale cascade | Принять | Trust moat и safety invariant |
| completed != accepted, independent review | Принять | Provider success не governance truth |
| SQLite projection, не Postgres сейчас | Принять | Нет multi-host evidence |
| Task сделать executable | Принять через vertical slice | Ценность только в связном loop |
| Немедленно заменить Delegation на Run | Изменить | Сначала RunView, затем evidence-based canonicalization |
| Разделить execution и acceptance | Принять как projection | UX benefit без breaking migration |
| Scheduler в P0 | Уточнить | Сначала plan и pull dispatch_once, затем push gate |
| Agent-created tasks | Принять с BACKLOG/admission | Создание не означает authority или launch |
| Organization/authority | Сузить | Minimal policy, без hierarchy theatre |
| Decision Inbox | Принять после loop | Сначала Attention Queue |
| Context Compiler | Принять концептуально, отложить runtime | Нужен frozen binding и leakage eval |
| Canvas как source of truth | Отвергнуть | Ledger/DSL revision остаётся truth |
| Full transcripts/tool arguments | Отвергнуть | Privacy boundary |
| MCP/A2A как internal core | Отвергнуть | Оставить adapters, lifecycle через domain commands |
| Kafka/Temporal/Postgres/microservices | Отложить | Нужны multi-host/capacity evidence |
| Vector DB | Отложить | Сначала structured retrieval/FTS и recall eval |
| Marketplace, self-hiring, ranking | Отложить | Не нужны для первой outcome-гипотезы |
| Gallery как главный приоритет | Понизить | Сначала доказать work/governance loop |

Главный trade-off: первый slice менее эффектен визуально, зато проверяет
снижение ручной маршрутизации без новой distributed infrastructure.

## 5. Target architecture

### 5.1. Logical architecture

~~~mermaid
flowchart TB
    H[Human / CLI / UI / MCP] --> APP[CommonsManager application boundary]
    APP --> OBJ[Objective + Task commands]
    APP --> GOV[Review / Verification / Acceptance]
    APP --> ATT[Attention projections]
    APP --> RUN[RunView / Execution service]
    OBJ --> DAG[Dependency and readiness projection]
    DAG --> ADM[Deterministic admission]
    ADM --> SCH[Pull scheduler / dispatch_once]
    SCH --> RUN
    RUN --> DEL[Canonical Delegation]
    DEL --> BROKER[Allowlisted Codex / Claude broker]
    BROKER --> ATTEMPT[(Private Attempt state)]
    ATTEMPT --> ART[Artifact / evidence refs]
    ART --> GOV
    GOV --> DAG
    APP --> LEDGER[(Immutable event + manifest ledger)]
    OBJ --> LEDGER
    DEL --> LEDGER
    ART --> LEDGER
    GOV --> LEDGER
    LEDGER --> REPLAY[Deterministic replay]
    REPLAY --> SQLITE[(SQLite/WAL rebuildable projection)]
    SQLITE --> H
    CTX[Future Context Pack / Compiler] -.-> RUN
    ORG[Future Authority / Escalation policy] -.-> ADM
    GAL[Future Gallery vertical] -.-> ART
~~~

Границы:

1. Canonical mutations проходят через application boundary.
2. Ledger и manifests — authoritative durable truth.
3. SQLite, DAG readiness, Attention, SchedulerTrace, Pulse и Context manifests
   — rebuildable projections.
4. Attempt/process state — operational/private state.
5. Scheduler выбирает допустимую работу, но не пишет обходной truth.
6. Gallery объясняет и редактирует projection, но не становится ledger.

### 5.2. End-to-end sequence

~~~mermaid
sequenceDiagram
    participant U as Operator
    participant W as Work projection
    participant A as Admission
    participant S as Scheduler
    participant M as CommonsManager
    participant B as Existing broker
    participant G as Governance
    U->>M: objective + task A + task B depends on A
    M->>W: replay canonical snapshot
    W-->>A: A runnable, B blocked
    U->>S: plan or dispatch_once
    S->>A: validate policy, deps, claim, revision
    A-->>S: admissible A + exact task revision
    S->>M: create bounded delegation once
    M->>B: launch allowlisted provider
    B-->>M: typed terminal outcome
    M->>G: artifact/evidence bound to exact revision
    G-->>M: independent review
    G-->>W: accepted or changes_requested
    W-->>S: recompute B readiness
    S-->>U: explainable next safe action
~~~

### 5.3. Execution and acceptance states

Execution и acceptance отображаются раздельно, даже если текущая canonical
lifecycle ещё линейна.

~~~mermaid
stateDiagram-v2
    [*] --> BACKLOG
    BACKLOG --> READY: admitted and deps satisfied
    READY --> ASSIGNED: bounded dispatch intent
    ASSIGNED --> ACTIVE: reservation/start proven
    ACTIVE --> DONE: bounded execution result
    ACTIVE --> FAILED: typed failure
    ACTIVE --> NEEDS_OPERATOR: ambiguous process state
    FAILED --> READY: new explicit run
    NEEDS_OPERATOR --> READY: reconcile, then new run
    DONE --> REVIEW_PENDING: evidence submitted
    REVIEW_PENDING --> ACCEPTED: current independent approval
    REVIEW_PENDING --> CHANGES_REQUESTED: reviewer asks rework
    CHANGES_REQUESTED --> READY: new task revision
    ACCEPTED --> [*]
~~~

Acceptance projection:

~~~text
NOT_REQUIRED | PENDING | APPROVED | CHANGES_REQUESTED | STALE
~~~

task.completed, delegation.succeeded, review.approved и task.accepted нельзя
смешивать. Policy light означает явно defined NOT_REQUIRED либо другой
policy outcome, но не поддельное independent approval.

## 6. Domain and data contracts

### 6.1. Objective

[RECOMMENDATION] Objective имеет явную связь с Task. Нельзя выводить её из
названия, canvas-положения или proximity в graph.

Минимум:

~~~text
objective_ref
objective_revision
desired_outcome
constraints
success_signal
owner
~~~

Objective — durable intent, но не разрешение на запуск всех возможных
подзадач. Agent-created work начинается в BACKLOG.

### 6.2. Task

Task — business work unit с outcome, acceptance criteria, dependencies,
priority, objective reference, owner/admission status, exact revision,
artifact/evidence refs, policy preset, bounded budget и timeout.

Invariant: revision, на которую запущен Run, immutable для этого Run.

### 6.3. RunView, ExecutionRun and Delegation

Migration path:

1. Сейчас: TaskRun/RunView выводится из Task + Delegation + Attempt.
2. Первый slice: launch получает task ref, exact task revision и stable
   dispatch idempotency key.
3. После retry/worktree evidence рассмотреть additive canonical ExecutionRun.
4. До отдельного решения не делать dual-write Run и Delegation.

Если canonical Run будет принят, он binding-ит task_ref + task_revision,
agent/role_ref, purpose, acceptance_policy, selected profile, limits, budget,
optional context binding, compiler fingerprint и delegation refs.

Delegation остаётся provider-launch detail с bounded lineage. Attempt остаётся
операционной попыткой, а не acceptance object.

### 6.4. Agent, Role and Session

- Agent/Role — persistent identity and intended responsibility.
- Session — bounded runtime/client identity.
- Delegation — bounded execution intent.
- Attempt — one provider/process attempt.

Session, model, client и role title не являются authentication.

### 6.5. Artifact, Review, Verification and Acceptance

Каждая связь с mutable subject имеет exact reference:

~~~text
{kind, id, exact_revision}
~~~

Review — judgment. Verification — reproducible fact. Acceptance — governance
transition. Artifact — durable output/reference.

### 6.6. Attention

AttentionItem — derived projection:

~~~text
type
subject_ref + subject_revision
owner
why_now
severity
evidence_refs
unknowns
next_safe_action
dedup_key
~~~

AttentionItem не становится автоматически Decision или Acceptance. UI-action
вызывает manager mutation с expected_revision и idempotency key.

## 7. Invariants and runtime safety

### 7.1. Single write path

- [FACT] Canonical changes use CommonsManager/application boundary.
- [RECOMMENDATION] Scheduler, UI, MCP и context tools не append events
  напрямую.
- [GUARDRAIL] Integration test должен ломаться при обходе record_event.

### 7.2. CAS and idempotency

- Mutable mutation включает expected revision.
- Duplicate dispatch с одним operation key даёт один стабильный result/receipt.
- Один idempotency key повторяется только для идентичной операции.
- После terminal ambiguity новый Run получает новый intent и scope.

### 7.3. Staleness

- Run не запускается на stale Task revision.
- Evidence старого artifact/task revision не промотирует acceptance.
- Stale review остаётся историческим свидетельством, но исключается из
  effective truth.
- Reopen или changes создают новый revision-bound evidence path.

### 7.4. Review independence

- Work-author principal не становится independent reviewer там, где policy
  это запрещает.
- Router и scheduler используют тот же independence predicate, что ручной flow.
- Fan-out и majority vote не становятся authority.

### 7.5. Runtime

Typed outcomes:

~~~text
queued | started | succeeded | failed | timed_out |
needs_operator | refused
~~~

Rules:

- provider exit не равен acceptance;
- blind retry после ambiguous crash запрещён;
- active cancellation не записывается без termination proof;
- input_needed не считается resumable;
- profile, executable, path, depth, fanout, attempts и wall-time проверяются
  до launch;
- raw prompts, transcripts, reasoning, secrets и tool payloads не входят в
  canonical history.

### 7.6. Writable work

Writable run требует:

~~~text
isolated worktree
path attestation
claim scope
bounded runtime
artifact diff
explicit merge/review ownership
~~~

Shared checkout не допускает automatic writable parallelism.

## 8. Deterministic DAG, admission and scheduler

### 8.1. Eligibility

Scheduler eligibility — pure function over effective snapshot:

~~~text
eligible(task, snapshot) =
    task.state in {BACKLOG, READY}
    and objective_binding_is_valid
    and dependency_graph_is_acyclic
    and all_required_dependencies_are_effectively_accepted
    and admission_policy_allows(task)
    and role_capabilities_are_operator_verified
    and claim_is_available
    and budgets_are_available
    and task_revision_is_current
~~~

Результат объяснимый, не только boolean:

~~~json
{
  eligible: false,
  reason: blocked_by_dependency,
  blocking_refs: [task:A],
  snapshot_revision: revision
}
~~~

Readiness derived; mutable READY flag не становится отдельной truth.

### 8.2. Cycle detection

Create/update dependency command rejects graph cycle. Tests покрывают self-cycle,
two-node cycle, longer cycle, repeated dependency, missing task, stale CAS и
concurrent update.

### 8.3. Admission

Agent-created и LLM-proposed tasks идут:

~~~text
candidate -> BACKLOG -> duplicate/objective/budget/authority checks
         -> READY only after admission
         -> dispatch only after deterministic eligibility
~~~

Проверяются objective relevance, duplicate signal, priority, dependency,
authority, subtree depth, task count, budget и follow-up loop.

LLM может объяснить или предложить candidate, но не может пропустить BACKLOG.

### 8.4. Pull before push

Первый интерфейс:

~~~text
Scheduler.plan(scope) -> deterministic plan + reasons
Scheduler.dispatch_once(plan_id, expected_revision) -> one bounded launch
~~~

Background daemon откладывается. Operator видит plan и может сделать one-shot
dispatch.

### 8.5. Determinism

Одинаковые snapshot, policy version и input scope дают одинаковый ordering:

~~~text
critical path -> dependency depth -> priority
              -> creation timestamp -> stable task id
~~~

LLM tie-breaker допускается лишь среди already eligible candidates и выдаёт
advisory trace, а не canonical authority.

## 9. ML/LLM-specific architecture

### 9.1. Allowed uses

LLM может:

- предлагать decomposition и acceptance criteria;
- предлагать candidate role/profile;
- суммировать bounded canonical facts;
- объяснять blocked reason;
- ранжировать already eligible candidates как advisory;
- предлагать review questions и risk findings.

LLM не может:

- промотировать acceptance;
- bypass admission;
- выдавать authority или permission;
- выбрать ineligible profile;
- расширить tools/filesystem;
- изменить budget/depth caps;
- объявить процесс terminated;
- сделать stale evidence current;
- писать canonical events в обход manager policy.

### 9.2. Context Pack and Compiler

[DEFERRED] Реализовать после первого work loop. Desired contract:

~~~text
canonical facts + decisions + constraints + selected evidence
  -> bounded Context Pack revision
  -> frozen run binding
  -> deterministic compiler
  -> source refs + fingerprint + ephemeral provider input
~~~

Компилятор фиксирует source refs, compiler version, section selection и
fingerprint, но не хранит prompt body или reasoning. Два child runs с одной Pack
revision разделяют baseline fingerprint, но имеют разные task/role wrappers.

Security requirements:

- source classification;
- secret scanning;
- task boundary and cross-task leakage tests;
- bounded context size;
- deterministic truncation;
- untrusted artifact instructions не могут override system/policy boundary.

### 9.3. ModelPolicy

ModelPolicy отделена от Role:

~~~text
required capabilities
allowed operator profiles
quality tier
timeout/latency
provider-unit or monetary cap when enforceable
fallback rules
reviewer-independence constraints
~~~

Пока нет точной provider cost truth, UI показывает authorized cap,
provider-reported usage, observed attempt units и unknown раздельно.

### 9.4. Eval strategy

Evals layered and provider-free:

1. Domain/property: cycles, readiness, CAS, stale bindings, independent
   reviewer, idempotent dispatch.
2. Runtime safety: fake provider, crash before/after start, timeout, missing
   terminal proof, ambiguity, profile refusal, path escape.
3. Context: source recall, fingerprint equality, budget truncation and
   cross-task leakage.
4. Outcome: accepted result, rework, founder intervention and dependency
   unlock versus manual baseline.

Planned или unsupported capability не является passing eval. Real provider
canaries — opt-in и не заменяют hermetic presubmit.

## 10. Product and UX model

### 10.1. First-time journey

~~~text
Create objective
  -> choose safe role template
  -> create one task
  -> inspect readiness
  -> dispatch one bounded run
  -> review artifact
  -> accept or request changes
~~~

Advanced grants, skills, tool narrowing, model policy и reporting relations
доступны через progressive disclosure.

### 10.2. Surfaces

| Surface | Вопрос пользователя | Source |
|---|---|---|
| Company Pulse | Что изменилось? | Projection |
| Work Board | Что runnable, blocked, stale или accepted? | DAG projection |
| Operator Attention Queue | Что требует безопасного действия? | Projection |
| Decision Inbox | Какое решение нужно и почему сейчас? | Typed projection |
| Agent Inbox | Какие assignment/review идут ролям? | Coordination projection |
| Truth Lineage | На какой revision основан вывод? | Canonical refs |
| Org View | Какие relations объявлены? | Narrow org projection |
| Canvas/Gallery | Как объяснить или редактировать workflow? | Projection/editor |

Canvas не source of truth и не главный control surface.

### 10.3. Trust display

Каждый статус показывает:

~~~text
canonical fact
operational signal
unknown / next safe action
~~~

Exit 0 означает «provider завершился с exit 0», а не «artifact принят».

### 10.4. State matrix

Core loop покрывает no objective, no role template, backlog candidate, blocked
dependency, queued, active, artifact submitted, review pending,
changes requested, stale, accepted, provider unavailable, ambiguous runtime,
configuration refusal, expired session и partial recovery.

У каждого состояния есть safe action и explicit operator fallback.

### 10.5. Attention card

Карточка отвечает:

1. что произошло;
2. почему сейчас;
3. какой subject и exact revision;
4. что известно;
5. что неизвестно;
6. какие варианты;
7. кто owner;
8. какое действие безопасно повторить.

### 10.6. Founder value

North Star не число agents, runs, messages или canvas nodes. Ближайшая
проверяемая ценность — снижение ручных routing interventions при сохранении
accepted-result quality.

## 11. Vertical slice: Objective -> A -> B

### 11.1. Scenario

1. Operator создаёт Objective O, Task A и Task B, где B depends on A.
2. Command rejects cycles and derives A runnable, B blocked.
3. Admission переводит A из BACKLOG в READY.
4. Scheduler.plan возвращает deterministic plan and reasons.
5. dispatch_once проверяет exact A revision и создаёт одну bounded Delegation.
6. Existing broker запускает fixed allowlisted builder profile.
7. Provider returns bounded result; process outcome не acceptance.
8. Artifact/evidence binding использует exact revision.
9. Independent reviewer проверяет current artifact.
10. A accepted только через existing governance contract.
11. Projection recomputes B as runnable.
12. Operator видит why B unlocked и может сделать следующий dispatch.

### 11.2. Exit criteria

- cycle creation rejected;
- B не запускается до effective acceptance A;
- stale A revision не запускается;
- duplicate dispatch даёт один stable result;
- replay даёт тот же runnable set;
- reviewer independence сохранена;
- provider success без valid evidence не принимает A;
- crash before/after start и ambiguous exit дают safe states;
- prompt/transcript/raw tool payload не входят в canonical metadata;
- fake-provider scenario работает в provider-free CI;
- один checkout и один writable worker остаются поддержанными;
- founder routing interventions сравниваются с manual baseline.

### 11.3. Failure sequence

~~~mermaid
sequenceDiagram
    participant S as Scheduler
    participant C as CAS and Manager
    participant B as Broker
    participant R as Reconciler
    participant H as Human attention
    S->>C: dispatch_once(task_id, task_revision, idem_key)
    C-->>S: refuse if stale, blocked or duplicate
    C->>B: bounded start
    alt terminal proof exists
        B-->>C: typed terminal outcome
        C-->>S: result; governance remains pending
    else process status ambiguous
        B-->>R: bounded diagnostics
        R->>H: needs_operator with unknowns
        H->>C: explicit reconcile or new run
    end
~~~

## 12. Roadmap

### Phase 0 — Baseline and truth alignment

**Owner:** CTO + product owner.  
**Dependencies:** none.

- freeze exact revision and dirty-tree boundary;
- define manual single-agent baseline;
- add read-only scheduler plan;
- define execution/acceptance state vocabulary;
- add DAG cycle/readiness tests;
- define metrics numerator/denominator;
- update architecture snapshot revision.

**Exit:** plan performs no write; cycles and stale targets are deterministic;
replay is unchanged.

### Phase 1 — Bounded pull execution

**Owner:** runtime architect + application owner.  
**Dependencies:** Phase 0.

- dispatch_once through existing manager/delegation path;
- task-revision binding;
- idempotent launch;
- fake provider;
- failure/recovery projection;
- TaskRun/RunView;
- no background daemon;
- no writable fan-out.

**Exit:** A -> review -> accepted -> B runnable passes hermetic scenario.

### Phase 2 — Admission and follow-up safety

**Owner:** product + governance owner.  
**Dependencies:** Phase 1.

- BACKLOG admission;
- objective relevance and duplicate detection;
- budget/depth/fanout caps;
- follow-up and ping-pong loop guards;
- acceptance presets as additive policy/read model;
- agent-created work never skips admission.

### Phase 3 — Human attention and minimal authority

**Owner:** product/UX + governance owner.  
**Dependencies:** measured Phase 1 loop.

- Operator Attention Queue;
- typed Decision Inbox;
- owner/why-now/options/evidence/next action;
- minimal can_assign, can_review, can_decide, can_escalate policy;
- separate Agent Inbox and Truth Lineage;
- honest timeline without raw traces.

### Phase 4 — Context and safe concurrency

**Owner:** ML/LLM architect + runtime owner.  
**Dependencies:** stable loop and leakage evals.

- Context Pack v0;
- compiler fingerprint and source manifest;
- deterministic budgeted selection;
- isolated worktree per writable run;
- path attestation and explicit merge;
- limited read-only council/fan-out only after evidence.

### Phase 5 — Gallery and specialist verticals

Gallery, Design Package, release/research workflows and richer organization
views become projections over proven work/artifact/governance semantics.

### Push-scheduler gate

Background autonomous dispatch is allowed only after:

- zero stale-target launches in canary;
- zero false acceptance;
- deterministic replay;
- bounded runtime reconciliation;
- acceptable needs_operator trend;
- no unauthorized writes;
- operator kill switch;
- explicit decision superseding anti-goals that prohibit auto-dispatch.

## 13. Metrics and guardrails

| Metric | Numerator / denominator | Initial rule | Action |
|---|---|---|---|
| Dispatch validity | valid launches / launch requests | 100% valid | block path on regression |
| Cycle rejection | rejected seeded cycles / seeded cycles | 100% | remain dry-run |
| Stale-launch refusal | stale launches / stale attempts | 100% refusal | kill switch |
| False acceptance | provider-only accepts / all accepts | 0 | invalidate path |
| Review coverage | current independent reviews / tasks entering review | 100% | stop new intake |
| Rework rate | changes-requested / reviewed tasks | establish baseline | fix routing/context |
| Blocked-time ratio | blocked time / task lifetime | establish baseline | prioritize bottleneck |
| Founder intervention | unscheduled routing actions / accepted tasks | lower than manual | revert if worse |
| Needs operator | operator-required / terminal runs | classify first | fix reason class |
| Context leakage | leakage cases / leakage tests | 0 | disable compiler |
| Fingerprint determinism | equal baseline fingerprints / repeats | 100% | reject compiler change |
| Duplicate dispatch | duplicate launches / duplicate requests | 0 | fix idempotency |
| Attention precision | actionable cards / sampled cards | baseline first | tune projection |

Cost is split into authorized cap, provider-reported usage, observed attempt
units and unknown. Exact currency is not shown in unknown.

Hard guardrails:

- no acceptance from process exit;
- no stale evidence promotion;
- no LLM-only scheduler/admission;
- no recursive task explosion;
- no writable parallelism without worktree proof;
- no blind retry after ambiguity;
- no raw prompt/transcript/reasoning storage;
- no remote deployment/auth claim from local metadata;
- no hidden action behind Pulse or Canvas.

### Rollback and kill switch

Every automation has dry-run, one-dispatch mode, feature flag, operator disable,
replayable inputs, manual Delegation fallback and no destructive canonical
cleanup.

Disabling scheduler leaves Task, evidence and review history intact. Compiler
failure falls back to bounded explicit selection. Runtime ambiguity remains
needs_operator rather than invented success.

## 14. Trade-offs and rejected alternatives

### Deterministic scheduler vs LLM scheduler

Deterministic eligibility gives replay, explainability, safety and stable evals,
at the price of less flexible decomposition. Decision: deterministic first;
LLM advisory only.

### RunView vs canonical Run

RunView avoids migration and dual-write, at the price of limited durable retry
semantics. Decision: RunView first; canonical Run after demonstrated pressure.

### One writable worker vs fan-out

One worker has lower throughput but safe current checkout and simple rollback.
Fan-out requires worktree, attestation, merge ownership and stronger recovery.

### SQLite/file ledger vs Postgres

Current stack is local-first and inspectable. Postgres adds network trust,
authentication, migrations and availability failure modes. Revisit only with
multi-host requirement or measured SQLite bottleneck.

### In-process/pull vs Kafka/Temporal

Pull has minimal ownership and recovery surface. Kafka/Temporal may solve
durable distributed execution but add operations before the loop is proven.
Revisit only after capacity and multi-host evidence.

### Structured retrieval vs vector DB

Structured/FTS preserves provenance and deterministic selectors. Vector index
is derived, tunable and harder to explain. Add only after context-recall
evidence demonstrates structured retrieval failure; it never becomes truth.

### MCP/A2A vs domain commands

MCP/A2A can remain adapter edges. They must not own lifecycle, idempotency, CAS,
authority or acceptance inside one local control plane.

### Full autonomy vs governed autonomy

Full autonomy improves demo effect but multiplies task explosion, unauthorized
writes, stale evidence and recovery ambiguity. Governed autonomy grows only
where existing invariants prove the outcome.

## 15. Risk register

| Risk | Likelihood | Impact | Mitigation / owner |
|---|---:|---:|---|
| Scheduler launches stale work | medium | critical | revision CAS, kill switch, property tests / runtime |
| LLM creates recursive task explosion | medium | high | BACKLOG, caps, dedup, loop guard / product |
| Authority inferred from title | medium | high | explicit policy, no metadata auth / governance |
| Needs operator becomes sink | medium | high | reason taxonomy and reconcile path / runtime |
| Worktree collision | high after fan-out | critical | isolated worktree and attestation / CTO |
| Context leaks sensitive facts | medium | critical | classification, budget, leakage eval / ML |
| Cost dashboard fabricates precision | high | medium | cap/usage/unknown fields / product |
| Structural refactor increases coupling | medium | high | stop-list, narrow ports, replay / CTO |
| Attention queue becomes noisy | medium | medium | dedup, owner, why-now, sampling / UX |
| Gallery becomes roadmap escape | medium | medium | vertical gate after core loop / product |
| Documentation drifts from HEAD | high | medium | exact revision snapshots / maintainer |
| Provenance collision repeats | observed | high | hashes, source inputs, session evidence and independent review |

The last risk is a process invariant: file name and completion summary do not
prove authorship. Provenance requires independent inputs, write events, exact
hashes and a clear source boundary.

## 16. Open decisions and defaults

1. **Dependency unlock:** [DECISION NEEDED] completed or accepted?  
   **Default:** accepted for governance-first work; explicit policy override for
   work whose acceptance is not required.

2. **Canonical Run:** [DECISION NEEDED] now or after RunView?  
   **Default:** RunView first; canonical Run only after retry/worktree evidence.

3. **Scheduler mode:** [DECISION NEEDED] pull or background push?  
   **Default:** operator-visible plan and dispatch_once.

4. **Agent-created tasks:** [DECISION NEEDED] auto-ready or backlog?  
   **Default:** BACKLOG with admission and loop guards.

5. **Acceptance presets:** [DECISION NEEDED] new entity or current modes?  
   **Default:** additive policy/read model over current modes.

6. **Authority owner:** [DECISION NEEDED] who can admit, assign, review, decide
   and escalate?  
   **Default:** explicit local operator/policy; role labels are not credentials.

7. **Context storage:** [DECISION NEEDED] store compiled prompt?  
   **Default:** source refs, fingerprint and manifest only; body ephemeral.

8. **Cost semantics:** [DECISION NEEDED] how to show cost before usage truth?  
   **Default:** cap, reported usage, attempt units and unknown separately.

9. **Writable concurrency:** [DECISION NEEDED] when allow fan-out?  
   **Default:** after worktree, attestation, merge ownership and rollback.

10. **Provenance governance:** [DECISION NEEDED] how accept generated reviews?  
    **Default:** authoring session, source inputs, exact hash and independent
    review are required; copying is not synthesis.

## 17. Council dissent and synthesis

### Run canonicalization

Pascal sees future retry, worktree and context semantics as a reason to add
ExecutionRun. Euler and Dalton see current Delegation as already carrying much
of the run contract and warn against premature schema migration.

**Synthesis:** implement RunView and task-revision binding now. Keep canonical
Run as a decision gate, not a naming refactor.

### Attention priority

Euler emphasizes founder attention compression. Dalton places typed inbox after
the first loop. Pascal requires runtime and eval evidence before fan-out.

**Synthesis:** build minimal Attention Queue alongside the vertical slice, but
make Decision Inbox a full surface only after volume and precision are measured.

### Gallery priority

Gallery can carry future Context Pack semantics, but cannot prove that the
general organization loop works.

**Synthesis:** maintain a narrow parallel Context/Gallery track only when it
reuses existing manager and artifact contracts; do not expand it ahead of Phase
0/1 evidence.

## 18. Final recommendation

Agent Commons should evolve as a **ledger-first governed execution control
plane**, not as a new distributed platform.

Implementation order:

~~~text
1. freeze facts and revision boundary;
2. validate DAG and derive runnable/blocked reasons;
3. implement deterministic plan and dispatch_once;
4. reuse existing bounded broker;
5. bind artifacts and reviews to exact revisions;
6. prove independent acceptance;
7. unlock dependent work;
8. measure interventions, rework, blocked time and recovery;
9. only then expand authority, context, attention and safe concurrency.
~~~

The strongest differentiator is not number of roles or canvas nodes. It is the
ability to say what is true, why it is true, which revision supports it, who
independently checked it and what remains unknown.

Immediate acceptance test:

~~~text
Task A accepted exactly once;
Task B blocked before that fact;
Task B runnable after replay;
no stale or ambiguous launch silently accepted;
every transition explainable to the operator.
~~~

Remote infrastructure, marketplace, self-hiring, vector memory, A2A core,
exact financial routing and a 30-agent promise remain later hypotheses, not
current product facts.
