# PRD: Visual Multi-Agent Orchestrator

## 1. Концепция

Визуальная среда для создания, настройки, запуска и наблюдения за иерархическими multi-agent системами.

Пользователь собирает систему как организационную структуру на бесконечном canvas:

- создаёт агентов;
- объединяет их в команды/отделы;
- назначает роли и руководителей;
- выдаёт MCP, tools и permissions;
- определяет связи между агентами и командами;
- создаёт shared workspaces для коммуникации;
- запускает задачи;
- в реальном времени наблюдает, кто с кем взаимодействует и что происходит.

Главная UX-метафора:

> **Miro/Figma для проектирования AI-организации + runtime/debugger для наблюдения за её работой.**

---

## 2. Основные сущности

### Agent

Автономный исполнитель.

Основные настройки:

```text
Agent
├── Name
├── Role
├── System prompt / Instructions
├── Model
├── Reports to
├── Sub-agents
├── MCP servers
├── Tools
├── Permissions
├── Memory
├── Context policy
├── Budget / token limits
└── Runtime limits
```

Пример:

```text
Backend Engineer
────────────────────

Model:
GPT-5.x

Role:
Senior Backend Engineer

Reports to:
Tech Lead

MCP:
✓ GitHub
✓ Linear
✓ PostgreSQL
✗ Production DB

Filesystem:
read/write

Can communicate with:
✓ Tech Lead
✓ Reviewer

Can delegate:
✗
```

### Team

Логическая группа агентов — аналог отдела или команды.

```text
Engineering
├── Tech Lead
├── Backend Engineer
├── Frontend Engineer
└── Reviewer
```

Team имеет собственные:

- agents;
- supervisor;
- shared workspace;
- shared memory;
- MCP/tools;
- permissions;
- communication policy.

Например:

```text
Engineering Team

Supervisor:
Tech Lead

Shared MCP:
GitHub
Linear
CI

Shared memory:
architecture.md
decisions.md

Internal communication:
unrestricted

External communication:
through Tech Lead
```

Команды могут быть вложенными.

### Workspace

Persistent communication/memory layer между агентами или командами.

Это не просто edge графа, а отдельная сущность с историей.

Например:

```text
#product-engineering

Participants:
Product Lead
Tech Lead

Messages:
184

Artifacts:
PRD.md
architecture.md
api-contract.md

Context policy:
summary + last 30 messages

Permissions:
Product: read/write
Engineering: read/write
QA: read-only
```

Workspace используется одновременно как:

1. communication channel;
2. shared history;
3. context buffer;
4. место для shared artifacts.

### Relationship

Связь между сущностями на canvas.

Минимально необходимо разделять два вида.

#### Control relationship

```text
Tech Lead
    │
    │ delegates
    ▼
Backend Engineer
```

Определяет:

- delegation;
- supervision;
- reporting;
- возможность запускать sub-agent.

#### Communication relationship

```text
Product Team
      │
      │ #product-engineering
      ▼
Engineering Team
```

Определяет доступ к Workspace и обмен информацией.

---

## 3. Canvas

Основной экран приложения — infinite canvas.

Пример организации:

```text
┌──────────────── PRODUCT ─────────────────┐
│                                          │
│              Product Lead                │
│               /        \                 │
│             PM        Analyst            │
│                                          │
└──────────────────┬───────────────────────┘
                   │
          #product-engineering
                   │
                   ▼
┌────────────── ENGINEERING ───────────────┐
│                                          │
│                Tech Lead                 │
│          /         |          \          │
│     Backend    Frontend      ML Eng      │
│        │          │            │         │
│        └──────────┼────────────┘         │
│                   ▼                      │
│                Reviewer                  │
│                                          │
└──────────────────┬───────────────────────┘
                   │
               #eng-qa
                   │
                   ▼
┌────────────────── QA ────────────────────┐
│                                          │
│               QA Lead                    │
│              /       \                   │
│         QA Agent    Security             │
│                                          │
└──────────────────────────────────────────┘
```

Canvas является **source of truth**, а не просто визуализацией конфигурации.

Создание/удаление node или edge изменяет runtime topology.

---

## 4. Agent Inspector

Клик по агенту открывает боковую панель.

```text
┌ Backend Engineer ──────────────────┐

STATUS
● Working

Current task:
Implement authentication API

Reports to:
Tech Lead

MODEL
GPT-5.x

INSTRUCTIONS
[ Edit ]

TOOLS / MCP
✓ GitHub
✓ Linear
✓ Filesystem
✓ PostgreSQL

PERMISSIONS
GitHub: write
Filesystem: write
Production: none

CONTEXT
Private memory
Engineering workspace
Current task

RUNTIME
Tokens: 18,421
Cost: $0.34
Duration: 04:21

[ View trace ]
[ Stop agent ]

└────────────────────────────────────┘
```

---

## 5. Communication Inspector

Клик по communication edge открывает историю взаимодействия.

Например:

```text
Backend ═══════════════► Reviewer
```

Inspector:

```text
Backend ↔ Reviewer
──────────────────────────────

14:32:07  Backend

REQUEST

Review PR #812.
Focus on concurrency and database
transaction handling.


14:32:11  Reviewer

TOOL CALL

GitHub.get_pull_request(812)


14:32:19  Reviewer

RESPONSE

Potential race condition found in
UserRepository.create().


14:32:21  Reviewer → Backend

REQUEST

Please fix before merge.


14:38:04  Backend

RESPONSE

Fixed in commit 82ac31.
```

Filters:

```text
[ Messages ]
[ Tool Calls ]
[ Handoffs ]
[ Artifacts ]
[ Context ]
[ Tokens ]
[ Timing ]
```

---

## 6. Runtime visualization

После запуска canvas переходит в live mode.

Статусы:

```text
○ Idle
● Working
◐ Waiting
✓ Completed
✕ Failed
```

Активные связи подсвечиваются.

Например:

```text
Backend
● WORKING
    │
    │ PR #812
    ▼
GitHub MCP
● CALLING
```

После этого:

```text
Backend
◐ WAITING
    │
    │ review request
    ▼
Reviewer
● WORKING
```

Таким образом пользователь визуально видит execution flow без чтения общего лога.

---

## 7. Task execution

Пользователь может запустить задачу на любом уровне.

### Organization

```text
Build user authentication feature
```

### Team

```text
Engineering:
Implement OAuth support
```

### Agent

```text
Backend Engineer:
Investigate issue #481
```

Supervisor самостоятельно декомпозирует задачу и делегирует её доступным агентам.

---

## 8. Пример полного взаимодействия

Пользователь ставит Product Team задачу:

```text
Добавить возможность авторизации через Google.
```

### Step 1 — Product

```text
Product Lead
     │
     ▼
PM
```

PM формирует требования:

```text
Google OAuth

Requirements:
- Login
- Registration
- Account linking
- Existing email handling
```

Результат сохраняется:

```text
#product-engineering
```

### Step 2 — передача Engineering

```text
Product Lead
      │
      │ #product-engineering
      ▼
Tech Lead
```

Tech Lead читает workspace и декомпозирует работу:

```text
Backend:
OAuth API + account linking

Frontend:
Google login UI

Reviewer:
Review implementation
```

### Step 3 — параллельная работа

```text
                    Tech Lead
                  /           \
                 ▼             ▼

             Backend        Frontend
             ● WORKING      ● WORKING

                 │             │
                 ▼             ▼

              GitHub         Figma
              DB MCP         GitHub
```

Backend создаёт PR #812.

Frontend создаёт PR #813.

### Step 4 — review

```text
Backend ─────┐
             │
             ▼
          Reviewer
             ▲
             │
Frontend ────┘
```

Reviewer обнаруживает ошибку.

На canvas появляется активное взаимодействие:

```text
Reviewer ═══════════► Backend
         fix request
```

Backend исправляет код.

Reviewer подтверждает:

```text
✓ APPROVED
```

### Step 5 — завершение

Tech Lead получает результаты:

```text
Backend ✓
Frontend ✓
Review ✓
Tests ✓
```

И пишет в workspace:

```text
#product-engineering

Engineering completed.

Backend: PR #812
Frontend: PR #813
Tests: passed
Review: approved
```

Product Lead получает итоговый отчёт.

---

## 9. Context isolation

Каждый агент получает только необходимый ему контекст.

Например Backend Engineer видит:

```text
Private context
      +
Engineering workspace
      +
#product-engineering
      +
Current task
```

Но не получает автоматически:

```text
Product internal discussion
Frontend private context
QA internal discussion
```

Это необходимо для:

- снижения token usage;
- уменьшения context pollution;
- безопасности;
- предсказуемого поведения агентов.

---

## 10. Runtime model

UI не должен быть жёстко привязан к конкретному agent framework.

Внутренняя модель:

```text
Organization
├── Teams[]
├── Agents[]
├── Workspaces[]
├── Relationships[]
├── Resources[]
├── Policies[]
└── Runs[]
```

Runtime через adapters:

```text
                 Visual Canvas
                       │
                       ▼
                Organization DSL
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
          AutoGen   Agents SDK  LangGraph
             │         │         │
             └─────────┼─────────┘
                       ▼
                      MCP
                 /     |      \
             GitHub  Linear   Files
```

Canvas и DSL являются source of truth.

---

## 11. MVP

В первую версию должны войти только ключевые возможности.

### Authoring

- infinite canvas;
- Agent nodes;
- Team containers;
- создание control edges;
- создание communication edges;
- Agent Inspector;
- настройка model/instructions;
- MCP/tools configuration;
- permissions;
- сохранение organization graph.

### Runtime

- запуск задачи;
- supervisor → agent delegation;
- agent → agent messaging;
- persistent workspaces;
- MCP calls;
- shared/team context;
- agent statuses;
- live graph updates.

### Observability

- история сообщений;
- tool calls;
- handoffs;
- execution timeline;
- token usage;
- errors;
- просмотр истории непосредственно через nodes/edges.

---

## 12. Не входит в MVP

Не делать на первой итерации:

- marketplace агентов;
- сложный RBAC пользователей самого продукта;
- billing;
- autonomous agent creation;
- визуальный prompt builder;
- сложную аналитику;
- mobile UI;
- десятки agent frameworks;
- собственную LLM inference infrastructure.

Цель MVP — проверить главный UX:

> **Можно ли проектировать multi-agent систему как организацию и затем понимать её работу непосредственно через тот же визуальный граф.**

Ключевая продуктовая дифференциация:

> **Agents are nodes. Teams are containers. Communication is persistent edges/workspaces. The organization graph is both the configuration and the runtime debugger.**
