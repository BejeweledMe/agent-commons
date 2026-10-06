I now have all the information needed. Here is the memorandum.

---

# Меморандум Совету — перспектива UX, дизайн-галерея, граф задач

**Дата:** 2026-10-01  
**Аудитор:** Claude (Opus), независимая перспектива  
**Снимок:** HEAD `aa9d373` (immutable)  
**Не проверено:** backend-сервисы `design_gallery.py`, `generated_outputs.py`, `outputs.py`; содержимое `ARCHITECTURE.md`, `PROTOCOL.md`; полный `decisions.json` (обрезан на ~67K из 118K).

---

## 1. Фактическая архитектура и готовность

Два фронтенд-приложения: **Work** (`frontend/work/`, React + Vite + TypeScript, маршрут `/work`) и **Gallery** (`frontend/gallery/`, маршрут `/gallery`). Оба собираются в хешированные бандлы, вкладываются в Python wheel. Legacy-панель (`index.html` на `/`) — решение D-12 о выводе принято, ADR 0022 написан, реализация (C6) ещё не выполнена.

**Провайдерная модель** — исключительно CLI: `CodexRunnerProfile`, `ClaudeRunnerProfile`, `GrokRunnerProfile` (`src/agent_commons/runtime/model.py:742–1021`). Поле `model: str | None = None` есть у всех трёх. Провайдер запускается через `executable` (по умолчанию `codex`, `claude`, `grok`). Прямого API-вызова нет — проект не использует ни Chat Completions, ни Responses API напрямую.

**CSS-система:** 47 токенов в `:root`, ratchet lint запрещает raw hex вне `:root`, WCAG AA тесты на контраст. CSP запрещает inline-стили и `innerHTML`. Визуальный фундамент (Inter + Lucide) запланирован в WP-42, но **не реализован**.

**Строгие парсеры:** `exactFields` / `keys()` на всех DTO с обеих сторон (`frontend/work/src/outputsTypes.ts:67–71`, `frontend/gallery/src/contracts.ts`). Любое аддитивное поле с сервера ломает клиент, пока парсер не обновлён. Это системный интеграционный риск, проявившийся в волне 3.

---

## 2. Что сделано, что осталось

### Завершено и принято
- **Волны 1–3** (17+ пакетов): полный цикл Board → Tasks → Agents → Outputs → Review/Acceptance в Work-приложении. Двуязычность (EN/RU). React Flow доска с сохранением layout, CAS-ревизией, department frames. Три вида задач (Now/Map/All). OutputsPanel с blob-превью, review state, live preview. DecisionCard с stop reason (UX-10), findings, blocking dependencies.
- **WP-40** (принят): run доходит до агента — исправлены P0 UX-19 (nested Git worktrees) и P0 UX-21 (верификация доступа до траты).
- **WP-41** (принят): stop reason видим в новом UI.
- **WP-20** (частично): инкрементальная валидация, warm `task_create` снижен с 33 с до 2.26 с. Цель — ≤1 с на 3206 событиях (решение D-16). Не достигнута.
- **Gallery** как отдельное приложение: сессионный обмен, выбор проекта, пакеты/экраны, инспектор с превью, AuthoringPanel с кандидатами, idempotency key, feedback.

### Не сделано
- **WP-42** (визуальный фундамент: Inter, Lucide, компонентный слой) — не начат.
- **WP-18** (первый запуск), **WP-19** (board UX), **WP-22** (attention badges), **WP-23** (refusal texts), **WP-24** (screen pack), **WP-25** (a11y gate), **WP-27–30**.
- **C3, C5, C6** (post-merge консолидация).
- **Связка Gallery ↔ Agent card** — отсутствует полностью (подробнее в §4).
- **Унификация TaskGraph на React Flow** — рекомендована советом, не начата.
- **Legacy-панель** — жива, redirect не реализован.

---

## 3. Опыт и уроки: что не повторять

**3.1. Строгие парсеры без версионированной схемы.** `exactFields` защищает от drift, но делает каждое серверное поле breaking change. При волне 3 клиент падал на новом поле. Решение: либо перейти на `allowAdditionalFields` с whitelist известных, либо ввести `schema_version` в ответе и парсить по версии.

**3.2. 17 пакетов, все принятые — ни один реальный run не привёл к результату.** Dogfooding на Blizhe (6 прогонов, 11 процессов, 0 принятых результатов — `2026-09-21-blizhe-dogfooding-feedback.md`) показал, что plan acceptance ≠ production readiness. Волны 1–3 строили UI для lifecycle, который ни разу не отработал до конца. Это было исправлено WP-40/41, но урок: **приёмочный критерий пакета должен включать реальный прогон**, а не только код.

**3.3. Два графических движка для одного продукта.** Board — React Flow, TaskGraph — custom SVG с Kahn's algorithm (`taskGraph.ts:1–113`). Два layout engine, два набора interaction patterns, два пути accessibility. Рекомендация совета (D-20+) — унифицировать на React Flow. Это стоило бы сделать до появления визуального фундамента.

**3.4. Gallery как изолированный остров.** Gallery не знает об `agent_id` / `roleName` (проверено: `grep agent_id|roleName` в `frontend/gallery/src` — 0 совпадений). Work-приложение показывает `OutputsButton` с `scope: { kind: "agent", id }` на доске (`ProjectBoard.tsx:43`), но это открывает OutputsPanel, а не Gallery. Путь от агента к его дизайн-пакетам требует двух отдельных приложений и ручной навигации.

---

## 4. P0/P1 блокеры

| Приоритет | ID | Статус | Суть |
|---|---|---|---|
| **P0** | UX-19 | ✅ Исправлен (WP-40) | Nested Git worktrees ломали MCP snapshot |
| **P0** | UX-21 | ✅ Исправлен (WP-40) | Executor access verification до траты |
| **P1** | — | ⛔ Блокер UX | Gallery не доступна с agent card — нет маршрута «агент → его дизайн-пакеты» |
| **P1** | UX-20 | Частично | Inconsistent run activity / task state / conversation status |
| **P1** | UX-27 | ⛔ Открыт | Review verdict скрыт, нет reopen action в новом UI |
| **P1** | UX-29 | ⛔ Открыт | Stale qualification vs failed canary confusion |
| **P1** | WP-20 | Частично | Write latency 2.26 с, цель ≤1 с |
| **P1** | WP-42 | Не начат | Визуальный фундамент — без него UI выглядит «самодельно» (диагноз совета: 5 причин) |

---

## 5. UX для Gallery + Task Graph: точные решения

### 5.1. Agent card → Design Gallery

**Текущее состояние:** `ProjectBoard.tsx:43` — кнопка Outputs на agent card вызывает `OutputsButton` с `scope: { kind: "agent", id }`, который открывает `OutputsPanel` (модальный `<dialog>`). Gallery (`/gallery`) — отдельное SPA с собственной аутентификацией (session exchange через fragment). Между ними нет маршрута.

**Целевой UX для технического разработчика:**
1. На agent card (RoleNode в `ProjectBoard.tsx:22–42`) добавить действие «Gallery» рядом с существующим Outputs.
2. По клику — deeplink `/gallery?project={projectId}&agent={agentId}` с session token в fragment (существующий механизм exchange code).
3. В Gallery — фильтр пакетов по `producer_agent_id` (поле уже есть в output items, `outputsTypes.ts:67`). Показывать только пакеты, где хотя бы один screen произведён этим агентом.
4. Provenance: в карточке screen уже есть `producer_agent_id`, `producer_session_id`, `producer_delegation_id`. Показать цепочку: агент → сессия → делегация → revision.
5. Review state: `review_state` уже парсится (`outputsTypes.ts:67`, optional field). В Gallery его нужно отобразить как badge на screen card (approved/returned/awaiting).

**Минимальная реализация:** фильтр `?agent=` в Gallery main.tsx + deeplink из ProjectBoard. Без этого нужна редизайн Gallery, но достаточно query-параметра и `filter()` по `producer_agent_id`.

### 5.2. Task Graph как читаемый flowchart

**Текущее состояние:** `TaskGraph.tsx` (56 строк) + `taskGraph.ts` (113 строк). Custom SVG, Kahn's algorithm, rank-based layout, max 4 колонки, cap 128 задач. 9 состояний (`graphTaskState`). Focus mode через BFS (`connectedTaskIds`). Keyboard navigation (arrows, Home, End). Fallback на list при `tooLarge` или `invalid`.

**Что работает хорошо:** компактность, focus mode, cycle detection (`wouldCreateTaskCycle`), capacity decision до mount (без double render, `TaskViews.tsx`).

**Что нужно для технического пользователя:**
1. **Унификация на React Flow.** Board уже использует React Flow. Два движка — лишняя когнитивная нагрузка и двойная работа по accessibility/theming. React Flow даёт zoom, pan, minimap, edge labels, custom nodes из коробки.
2. **State-driven styling.** 9 состояний в `graphTaskState` сейчас — цветные rect в SVG. С компонентным слоем (WP-42) каждый node станет мини-DecisionCard: состояние + stop reason + кто ответственный.
3. **Dependency editing.** `wouldCreateTaskCycle()` уже готов. Добавить drag-to-connect (как agent_links в Board) для создания зависимостей прямо на графе.
4. **Масштаб > 128.** Для больших проектов — hierarchical grouping по objective/application. Но cap 128 адекватен для текущей аудитории.

---

## 6. GPT-6: план миграции

Проект использует **CLI-провайдеров** — Codex CLI, Claude Code CLI, Grok CLI. Модель задаётся в `RunnerProfile.model` (`model.py:747, 861, 1021`). Нет прямого вызова API.

**Для GPT-6 (Astra/Sol/Luna) миграция:**
1. **Pin модели в профиле Codex:** `model: "gpt-6-astra"` / `"gpt-6.1-sol"` / `"gpt-6-luna"` в `CodexRunnerProfile`. Это конфигурационное изменение, не код.
2. **Reasoning levels:** GPT-6 поддерживает low/medium/high/xhigh/max, но **не none/minimal**. Проверить `provider_qualification.py` — если canary отправляет запрос без reasoning level, он может быть отклонён. Нужен тест.
3. **Tool calling:** GPT-6 использует Responses API, но Codex CLI абстрагирует это. Если CLI обновлён для GPT-6 — миграция прозрачна. Если нет — ждать обновления CLI.
4. **Рекомендация по ролям:** Astra для reviewer (высший интеллект, independent review), Sol для executor (сбалансированное кодирование), Luna для canary и массовых операций (high-volume). Это profile-level config: разные `profile_id` для разных specialization.

**Нет необходимости в API migration.** Это pin модели в профиле + проверка canary.

---

## 7. Рабочие пакеты: порядок, зависимости, приёмка

| # | Пакет | Зависит от | Модель | Приёмочный критерий |
|---|---|---|---|---|
| 1 | **WP-20 финал** (write ≤1 с) | — | Opus (Terra) | `task_create` ≤1 с на 3206 событиях, бенчмарк до/после, golden replay pass |
| 2 | **C6** (ADR 0022 в коде: redirect `/` → `/work`, удаление legacy) | Merge волны 3 + PR#14 | Opus | `/` отдаёт 302 → `/work`, smoke wheel в CI, 0 обращений к `index.html` |
| 3 | **WP-42** (Inter + Lucide + component layer) | C6 | Opus | `make check` green, 0 raw hex вне `:root`, Inter/Lucide в бандле, визуальный diff кадров V01–V14 |
| 4 | **Gallery ↔ Agent deeplink** | WP-42 | Sonnet/Sol | Deeplink `/gallery?agent={id}` фильтрует пакеты; agent card имеет кнопку Gallery; e2e тест |
| 5 | **TaskGraph → React Flow** | WP-42 | Opus/Astra | Board и Map используют один движок; zoom/pan/minimap; focus mode сохранён; ≤128 cap; keyboard nav |
| 6 | **WP-22** (attention badges) | WP-42 | Sonnet/Sol | Badge на agent card при awaitsHuman/blocked/UX-27; count; доступность |
| 7 | **UX-27 fix** (review verdict + reopen) | WP-22 | Sonnet/Sol | Review state видим в Task views; reopen action; e2e на review→reopen→re-run |
| 8 | **WP-18** (first launch) | C6, WP-42 | Opus/Astra | Новый пользователь: от `/work` до первого результата ≤5 минут на demo проекте |
| 9 | **WP-25** (a11y gate) | WP-42 | Sonnet | axe-core 0 violations на key screens; WCAG AA; keyboard-only прохождение |

**Порядок:** WP-20 и C6 параллельно → WP-42 → пакеты 4–7 параллельно (разные компоненты) → WP-18 → WP-25.

---

## 8. Минимальный первый завершённый инкремент

**MVP:** C6 + WP-42 + Gallery deeplink (пакеты 2, 3, 4).

После этого: пользователь открывает `/work`, видит Board с Inter/Lucide, кликает агента, видит его outputs **и** может перейти в Gallery с фильтром по этому агенту. Legacy-панель убрана.

**Критерий done:** реальный прогон на тестовом проекте, где агент создаёт дизайн-пакет, и пользователь видит его через deeplink с Board. Не plan acceptance — end-to-end run.

---

## 9. Риски и несогласия

| Риск | Вероятность | Последствие | Митигация |
|---|---|---|---|
| `exactFields` ломает интеграцию при каждом серверном обновлении | Высокая | Белый экран | Версионированная схема или `allowAdditional` с whitelist |
| WP-42 затягивается (Inter + component layer = большой scope) | Средняя | Блокирует всё ниже | Разбить: Inter/Lucide — один коммит, component layer — отдельный пакет |
| TaskGraph → React Flow: потеря keyboard nav и focus mode | Средняя | Регрессия a11y | Приёмочный критерий: keyboard nav + focus mode как explicit checklist |
| GPT-6 CLI ещё не поддерживает reasoning levels | Неизвестно | Canary fails, run не запускается | Проверить до pin: `codex --model gpt-6-astra --reasoning high` на canary |
| Gallery deeplink: session exchange между двумя SPA | Низкая | Двойная аутентификация | Использовать существующий fragment exchange; расшарить sessionStorage origin |
| Write latency (WP-20) не достигает 1 с | Средняя | UX friction на больших проектах | D-16 явно указывает: «продуктовый SLO не фиксируется» — это инженерная цель, не блокер |

**Несогласие с текущим планом:** план (`consolidation-and-wave-4-plan.md:240`) ставит WP-30 (agent→role rename) **перед** WP-18/19. Считаю это неверным — переименование терминологии без визуального фундамента (WP-42) приведёт к двойной правке. WP-30 должен идти **после** WP-42.

**Не проверено (explicit):** backend-реализация design_gallery.py (есть ли фильтрация по agent_id на сервере), полный список decisions (JSON обрезан), runtime поведение canary с GPT-6 моделями, реальная производительность write path после частичного WP-20.

---

*Этот меморандум — первая стадия аудита. Критика будет отдельной.*