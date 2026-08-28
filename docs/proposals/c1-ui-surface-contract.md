# C1: контракт UI-поверхности — proposal для owner decision DC1

**Статус:** proposal-only. Это **не принятое решение** — документ готовит
owner decision **DC1 «Owner: UI surface contract»** из графа зависимостей
плана ([architecture-improvement-implementation-plan.md:171](../architecture-improvement-implementation-plan.md),
gate `DC1{Owner: UI surface contract} --> C1`). Выбор варианта принадлежит
владельцу; ни один вариант ниже не является выбранным по умолчанию.

**Проверенная ревизия:** `216cbec5f76898ddc077f4e73cc55330427a6d16`.
Каждое фактическое утверждение ниже проверено на этой ревизии и несёт
citation `file:line`. Не проверенные утверждения помечены **UNVERIFIED**.

Этот документ **не меняет** product behaviour, schemas/events, UI source,
static assets и сам architecture-improvement plan. Он не создаёт route,
subtree, claim или тест — только формулирует альтернативы и вопросы для
владельца, как того требует план: «до первого writer нужен отдельный
owner-approved UI-surface contract: route, target directory, build/package
delivery, session/auth boundary, localisation and `FRONTEND_CONTRACT`
compatibility, plus exclusive path-claim»
([plan, Phase C1/C2, строки 319–326](../architecture-improvement-implementation-plan.md)).

## 1. Проверенные текущие границы UI

**Две HTML-поверхности, и только две.**

- Legacy root panel на `/`: per-response nonce (`secrets.token_urlsafe(16)`),
  подстановка `__CSP_NONCE__` в единственный файл через `read_spa()`,
  `content_security_policy(nonce)` — [`src/agent_commons/ui/server.py:474-480`](../../src/agent_commons/ui/server.py).
  Сам asset — [`src/agent_commons/ui/static/index.html`](../../src/agent_commons/ui/static/index.html),
  **8 694 строки / 552 705 байт**, single-writer закон
  ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md), «One file, one writer, no toolchain»).
- Gallery на `/gallery`: `read_gallery_shell()` +
  `gallery_content_security_policy()` (CSP `'self'`, без nonce) —
  [`server.py:482-487`](../../src/agent_commons/ui/server.py),
  [`src/agent_commons/ui/security.py:180-194`](../../src/agent_commons/ui/security.py).
  Gallery — **не generic product shell**: до появления Design Package reads
  route `/api/gallery` намеренно возвращает typed 409
  `gallery_data_unavailable` ([`server.py:497-507`](../../src/agent_commons/ui/server.py)),
  а inventory прямо говорит: «current React Flow app is Gallery-only… It is
  not a general product shell» ([cli-migration-inventory.md:96-103](../cli-migration-inventory.md)).

**Route tables как test-pinned declarations.** `MUTATING_ROUTES` (16 POST-пар,
[`server.py:99-116`](../../src/agent_commons/ui/server.py)), `CATALOG_ROUTES`
(`:124-127`), `LAUNCH_ROUTES` (`:138`), `SETUP_ROUTES` (`:153-157`) и
отдельный `AUTH_ROUTES` ([`security.py:39`](../../src/agent_commons/ui/security.py)) —
это декларации для invariant-тестов, а не драйверы регистрации: тесты читают
`app.routes` напрямую и сравнивают с объединением таблиц
([`tests/ui/test_readonly_invariant.py:195-206`](../../tests/ui/test_readonly_invariant.py),
[`tests/ui/conftest.py:150-152`](../../tests/ui/conftest.py)). Отсюда
**flat-routes constraint**: никакого `APIRouter` — вложенный router прячет
поверхность от `mutating_surface()`; docstring `_RouteGroup` фиксирует это
как намеренное решение ([`server.py:234-241`](../../src/agent_commons/ui/server.py)).
Точный счётчик `assert len(MUTATING_ROUTES) == 16` существует в
[`tests/ui/test_profile_choice_and_link_offer.py:422`](../../tests/ui/test_profile_choice_and_link_offer.py).

**Middleware guard order** ([`server.py:358-388`](../../src/agent_commons/ui/server.py)):
(1) loopback `Host` allowlist → 403 `forbidden_host`; (2) `Origin` allowlist →
403 `forbidden_origin`; (3) любой `/api/` путь вне `AUTH_EXCHANGE_PATH` и вне
opaque bound base → 404 `not_found`; (4) непубличный путь без валидной session
cookie → 401 `unauthorized`. `SECURITY_HEADERS` ставятся на каждый ответ,
включая ошибки (`:386-388`).

**PUBLIC_PATHS** — данные в
[`security.py:117`](../../src/agent_commons/ui/security.py):
`{"/", "/favicon.ico", "/gallery", "/gallery/", AUTH_EXCHANGE_PATH}`, плюс
жёстко зашитый префикс `path.startswith("/gallery/assets/")` в
`is_public_path` (`:120-129`).

**Session/auth.** Printed URL несёт exchange code только во фрагменте:
`http://127.0.0.1:{port}/#c={code}` — и оба места жёстко указывают на `/`
([`server.py:1128`](../../src/agent_commons/ui/server.py),
[`src/agent_commons/cli/__init__.py:300`](../../src/agent_commons/cli/__init__.py)).
`POST /api/auth/exchange` ([`server.py:403-435`](../../src/agent_commons/ui/server.py)) —
единственный неаутентифицированный не-GET route: exact same-origin check,
одноразовое потребление кода, ответ **только** `{"api_base": ...}`, cookie
`HttpOnly`, `SameSite=Strict`, `path=browser_session.api_base`
(opaque per-process base из `new_api_base()`,
[`security.py:55-64`](../../src/agent_commons/ui/security.py)). Клиент хранит
base только в `sessionStorage`
([`frontend/gallery/src/main.tsx:20-60`](../../frontend/gallery/src/main.tsx));
`localStorage` и `Authorization` запрещены и запинены source-text-тестом
([`tests/ui/test_react_gallery.py:104-124`](../../tests/ui/test_react_gallery.py)).

**Packaging.** Bundle Gallery **checked in** с хэшированными именами
(`static/gallery/assets/gallery-DGB1oohC.js`, `...-DqkBrq2K.css`); glob
`"ui/static/**/*"` в [`pyproject.toml:88-94`](../../pyproject.toml)
рекурсивен — новый static-subtree пакуется в wheel **без правок pyproject**.
`make check` = `lint format-check test` ([`Makefile:13`](../../Makefile)) —
**без npm-шага**; CI ставит node только для behaviour-harness legacy панели и
**нигде не запускает `npm ci && npm run build`**
([`.github/workflows/ci.yml:34-42`](../../.github/workflows/ci.yml)).

**UIContext — compatibility facade, который не должен расти.**
`class UIContext(UIReads, UIActions)`
([`src/agent_commons/ui/context.py:120`](../../src/agent_commons/ui/context.py),
403 строки после A4-сплита) владеет только shared state; план запрещает новые
control-plane методы ([plan:269](../architecture-improvement-implementation-plan.md),
[plan §2.2, строки 90-98](../architecture-improvement-implementation-plan.md)).
In-code прецеденты обхода: route `artifact_preview` строит collaborator
`ArtifactPreviewReader(context.manager())` прямо в handler'е, без метода в
`UIContext` и без метода в `CommonsManager`
([`server.py:509-522`](../../src/agent_commons/ui/server.py)); route
`/api/gallery` возвращает typed 409 вместо роста фасада, с явным комментарием
«without… growing UIContext with a temporary workflow»
([`server.py:499-502`](../../src/agent_commons/ui/server.py)). Композиция
через `UILaunchCoordinator` (не наследование) запинена
[`tests/ui/test_context_seams.py:44-75`](../../tests/ui/test_context_seams.py).

**Typed DTO / refusal contract.**
[`src/agent_commons/ui/read_dtos.py`](../../src/agent_commons/ui/read_dtos.py) —
образец A7-паттерна: `TypedDict` wire payload с `Literal`-дискриминантом +
`@dataclass(frozen=True, slots=True)` runtime record + единственный
`to_wire()` (`read_dtos.py:47-106, 115-277`). Refusal wire shape строится в
одном месте — `_error()`
([`server.py:289-296`](../../src/agent_commons/ui/server.py)):
`{"error": {"code", "message", "safe_next_actions"}}`; клиенты обязаны вести
UI по `error.code`, никогда по 404
([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md), «A registered route is not a
usable route»).

## 2. Ограничения, которые связывают любой вариант

1. **Нельзя** расширять legacy `index.html` и Gallery subtree: «Нельзя неявно
   писать в Gallery React subtree или legacy `index.html`»
   ([plan:322-323](../architecture-improvement-implementation-plan.md));
   claim paths уже назначены
   ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md): legacy —
   `path:src/agent_commons/ui/static/index.html`; Gallery —
   `path:frontend/gallery` + `path:src/agent_commons/ui/static/gallery`).
2. **Flat routes**: не-GET routes нового surface регистрируются плоско на
   `app` и декларируются в таблицах + `tests/ui/conftest.py:150-152`
   (§1 выше).
3. **Starlette `check_dir=True`**: `StaticFiles` бросает `RuntimeError`, если
   directory не существует на момент `create_app` — asset-каталог нового
   surface обязан быть committed (или mount охраняется проверкой
   существования) (проверено в `.venv` Starlette `staticfiles.py`, `__init__`).
4. **`create_app` — живой hotspot**: 332 строки (`server.py:312-643`), почти
   вдвое больше, чем на момент аудита
   ([structure-report.md:98](../audits/2026-08-18-code-quality/structure-report.md)).
   Регистрация нового surface должна быть отдельным модулем (по образцу
   `_register_writes`/`_register_launch`, `server.py:627-632`), а не inline
   ростом.
5. **Registered ≠ usable**: канонический route table не меняется за время
   жизни панели; неготовое окружение отвечает typed 409, не 404
   ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md)).
6. **Никаких schema/event изменений**: C1 — transport re-homing; adapter не
   вычисляет authority, readiness, acceptance или lifecycle
   ([plan §2.2](../architecture-improvement-implementation-plan.md)).
7. **CLI fallback живёт весь compatibility period**
   ([plan:325-326](../architecture-improvement-implementation-plan.md)).
8. **Новая поверхность требует собственного approved plan gate** до старта
   writer'а: «Start Context Pack injection, Gallery evolution or A8
   collaborator migration only after their own approved plan gates»
   ([r-status-reconciliation.md:135-137](../audits/2026-08-18-code-quality/r-status-reconciliation.md)).
   Настоящий документ и есть подготовка этого gate.

## 3. Варианты размещения

### 3.1. Вариант A — отдельное Vite/React-приложение `frontend/work/`

Параллель Gallery-паттерну: source в `frontend/work/`, committed bundle в
`src/agent_commons/ui/static/work/`, отдача на новом route (предлагается
`/work`; имя — решение владельца, см. §5).

- **Route / PUBLIC_PATHS / mount.** Новый `@app.get("/work")` (+ `/work/`)
  рядом с двумя существующими handlers (`server.py:474-487`); новый
  `app.mount("/work/assets", StaticFiles(...))` рядом с Gallery mount
  (`server.py:467-472`); `PUBLIC_PATHS` + prefix-правило в `is_public_path`
  (`security.py:117-129`); новые readers `read_work_shell()` /
  `work_static_directory()` в
  [`src/agent_commons/ui/__init__.py`](../../src/agent_commons/ui/__init__.py)
  (по образцу `:24-53`). Committed `assets/` удовлетворяет `check_dir=True`
  (§2 п.3). Регистрация — отдельный модуль `ui/work_routes.py` (или
  аналогичное имя), вызываемый из `create_app` ~2 строками (§2 п.4).
- **Session/auth.** Полное переиспользование существующего протокола:
  fragment `#c` → `POST /api/auth/exchange` → opaque `api_base` + HttpOnly
  `SameSite=Strict` cookie, path-scoped к base; base только в
  `sessionStorage`, очистка на failed restoration (§1; контракт —
  [FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md), «Incremental React Flow
  migration»). Серверная сторона surface-agnostic — правок не требует
  (`security.py:33`, `server.py:403-435`). **Открытый пункт для DC1, не
  изменение этого proposal:** printed URL жёстко указывает на `/`
  (`server.py:1128`, `cli/__init__.py:300`); достижение не-root surface со
  свежим `#c` требует либо operator-typed пути, либо позднейшего изменения
  ровно этих двух строк.
- **Build / package ownership.** Свой `frontend/work/`
  (`package.json`/`vite.config.ts` с собственным `base: "/work/"` и
  `outDir: "../../src/agent_commons/ui/static/work"`); bundle checked in;
  pyproject-глоб уже покрывает subtree; `make check` не меняется.
  **Честно названный риск:** CI не пересобирает и не diff'ит bundles —
  checked-in bundle может дрейфовать от source без падения какого-либо
  теста (проверено grep'ом по `.github/workflows/` и `Makefile`; аналогичный
  gap уже существует у Gallery). Митигейшн в составе варианта (предложение,
  не мандат): bundle-freshness check как designated test нового surface —
  войдёт ли он в контракт, решает владелец (§5c). Важно:
  `emptyOutDir: true` у Gallery wipe'ает только
  `ui/static/gallery/` ([`frontend/gallery/vite.config.ts:4-7`](../../frontend/gallery/vite.config.ts)) —
  sibling-каталог `work/` безопасен.
- **CSP / локализация.** Своя функция `work_content_security_policy()` по
  образцу `gallery_content_security_policy()` (`security.py:180-194`):
  `'self'`, без inline script/style. i18n — собственный paired `i18n.json`
  (en/ru с идентичными key sets, по образцу
  [`frontend/gallery/src/i18n.json`](../../frontend/gallery/src/i18n.json) и
  типизации `main.tsx:9-10`). По
  [FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md) копировать термины между
  keyspaces нельзя; термин, отображаемый двумя стеками, обязан сначала
  переехать в shared source — **которого сегодня в репозитории нет
  (UNVERIFIED-gap: grep не нашёл третьего locale-файла)**. Словарные законы
  (одно слово на концепт; canonical values не переводятся) распространяются
  на новый surface.
- **Typed UI / refusal contracts.** Новые DTO следуют
  `read_dtos.py`-паттерну (TypedDict + frozen dataclass, `Literal`
  discriminants, `to_wire()`); отказы — через `_error()`-shape с frozen
  codes; UI ведётся по `error.code`, никогда по 404; никаких
  `dict[str, Any]` boundaries (норма также зафиксирована в
  [context-pack-gallery-implementation-plan.md:94-95](../context-pack-gallery-implementation-plan.md)).
  Новые reads — collaborators от `context.manager()` в route (прецедент
  `artifact_preview`), не методы `UIContext`/`CommonsManager`.
- **Designated paths / claims / tests.** Exclusive claims по конвенции
  FRONTEND_CONTRACT: `path:frontend/work` +
  `path:src/agent_commons/ui/static/work`. Тесты — новый файл по шаблону
  [`tests/ui/test_react_gallery.py`](../../tests/ui/test_react_gallery.py)
  (packaging, publicness shell/assets при приватных данных, shared refusal
  codes, `is_public_path`, CSP, paired locale, source-text auth-assertions);
  плюс parity-гейты inventory §4.1
  ([cli-migration-inventory.md:130-143](../cli-migration-inventory.md)).
- **Rollout / rollback.** Registered-not-usable принцип; выключение surface =
  снять route/mount, ledger не тронут; никаких schema/event изменений;
  kill-switch семантика per plan §7 («hide surface/revert adapter»,
  [plan:606](../architecture-improvement-implementation-plan.md)); CLI
  fallback живёт весь compatibility period.

### 3.2. Вариант B — новый no-build single-file asset

Второй nonce-CSP HTML — например `src/agent_commons/ui/static/work.html`,
отдаваемый на `/work` по механике legacy root panel, но **новым файлом**, не
расширением `index.html`.

- **Route / PUBLIC_PATHS / mount.** Новый `@app.get("/work")` с per-response
  nonce и подстановкой `__CSP_NONCE__` (образец `server.py:474-480`); новый
  reader `read_work_spa()` в `ui/__init__.py`. **Mount не нужен** — asset
  один файл, нет `assets/`-каталога, нет Starlette `check_dir`-вопроса и нет
  prefix-правила; в `PUBLIC_PATHS` добавляется только `/work`
  (`security.py:117`).
- **Session/auth.** Тот же протокол, что в A, но клиентская реализация —
  in-file, как у legacy панели (fragment read
  `static/index.html:3384`, exchange POST `:3424`, `replaceState`
  `:3455, :3466`); те же запреты `localStorage`/`Authorization`. Тот же
  открытый пункт про printed URL (`server.py:1128`,
  `cli/__init__.py:300`) — для DC1.
- **Build / package ownership.** Никакого toolchain: нет npm, нет bundler,
  нет drift-риска — файл и есть артефакт. pyproject-глоб покрывает; `make
  check` не меняется. Наследуется stance legacy asset'а («no npm, no
  bundler» — [FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md)), но
  **применённый к новому файлу**, что contract сегодня формулирует только для
  `index.html`.
- **CSP / локализация.** Переиспользуется `content_security_policy(nonce)`
  (`security.py:165-177`) — включая `require-trusted-types-for 'script'` и
  запреты FRONTEND_CONTRACT на unsafe DOM API и inline styles. i18n —
  собственная in-file paired таблица `STRINGS.en`/`STRINGS.ru` (образец
  `static/index.html:1950/:2669`); тот же запрет на копирование терминов и
  тот же shared-source gap (UNVERIFIED), что в A.
- **Typed UI / refusal contracts.** Идентично A: серверные DTO по
  `read_dtos.py`-паттерну, `_error()`-shape, `error.code`-driven client,
  collaborators-from-manager. Клиентский код — vanilla
  `createElement`/`textContent` без построения по innerHTML.
- **Designated paths / claims / tests.** Single-writer claim на новый файл:
  `path:src/agent_commons/ui/static/work.html`. Тесты — по стилю
  `read_spa()`-text-assertions
  ([`tests/ui/test_stream_and_packaging.py:161-203`](../../tests/ui/test_stream_and_packaging.py):
  package resource, no external references, no unsafe DOM API, no inline
  style), с собственным reader'ом; parity-гейты inventory §4.1 те же.
- **Rollout / rollback.** Идентично A: снять route — surface скрыт, ledger
  не тронут; kill-switch per plan §7; CLI fallback живёт весь период.

### 3.3. Non-options — отклонённые ограничениями варианты

- **Вариант C — расширение legacy панели `index.html`** (использование
  существующей поверхности как временного хоста C1-флоу). **Отклонён самими
  ограничениями**: single-writer закон
  ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md), claim
  `path:src/agent_commons/ui/static/index.html`), масштаб файла (8 694
  строки — сам по себе предостерегающий прецедент) и прямой запрет плана и
  inventory: «Нельзя неявно писать в… legacy `index.html`»
  ([plan:322-323](../architecture-improvement-implementation-plan.md)),
  «It may not silently use Gallery or `ui/static/index.html`»
  ([cli-migration-inventory.md:99-103](../cli-migration-inventory.md)).
- **Вариант C′ — расширение Gallery build** (второй entry в
  `frontend/gallery/`). Также non-option: `emptyOutDir: true` + единый
  `outDir` + единый `base: "/gallery/"`
  ([`frontend/gallery/vite.config.ts:4-7`](../../frontend/gallery/vite.config.ts))
  положили бы второй app в `ui/static/gallery/` под `/gallery/`, что
  сталкивается с Gallery path-claim
  ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md)) и с тем же запретом
  плана; Gallery — отдельная product surface, а не shell (§1).

Третий «живой» вариант (multi-entry Vite workspace и т.п.) в репозитории не
имеет прецедента (`frontend/` содержит ровно один entry — `frontend/gallery`),
и честно обосновать его на текущих фактах нельзя; поэтому proposal выносит на
DC1 **два живых варианта** (A, B) и два документированных non-options.

## 4. Trade-offs и рекомендация

| Критерий | A: отдельное Vite/React app | B: no-build single-file |
| --- | --- | --- |
| Масштабируемость под multi-flow программу C1 («по одному полному flow за раз», plan:324-325) | Компонентная структура, typed states, рост числом модулей | Рост одним файлом; предостерегающий прецедент — legacy asset в 8 694 строки |
| Прецедент в репозитории | Полный: build/packaging/claims/tests Gallery (§1, §3.1) | Полный: legacy panel + `read_spa()`-тестовый стиль |
| Toolchain surface | Второй npm-workspace (node уже в CI для harness'ов) | Нулевой — нет npm/bundler |
| Bundle-drift риск | Есть (CI не пересобирает bundles — проверенный gap); нужен freshness-check по решению владельца | Нет — файл и есть артефакт |
| CSP | `'self'`, строго no-inline, по образцу Gallery | nonce + Trusted Types; дисциплина inline-кода на авторах |
| TypeScript / типизация клиента | `strict: true` по образцу gallery `tsconfig` | Нет типизации клиентского кода |
| Правки серверной стороны | route + mount + PUBLIC_PATHS + prefix + CSP-функция + readers | route + PUBLIC_PATHS + reader (без mount/prefix) |
| Claims | Два path-claim (source + bundle) | Один path-claim |

**Рекомендация proposal (не решение):** **вариант A**. Основания из
доказанного: программа C1 — это последовательность полных workflows, а не
один экран (plan:324-325), и компонентное приложение с typed states
масштабируется на неё лучше, чем единый файл — траектория которого уже
продемонстрирована legacy asset'ом на 8 694 строки; паттерн
build/packaging/claim/test для A полностью precedented Gallery (обе стороны —
и `frontend/gallery/`, и `tests/ui/test_react_gallery.py` — служат шаблонами);
строгий no-inline `'self'` CSP уже есть в `security.py:180-194`. При этом
честно в пользу B: меньшая toolchain-поверхность и отсутствие drift-риска —
если владелец оценивает объём C1-флоу как малый, B дешевле в сопровождении.
Выбор принадлежит владельцу (DC1).

## 5. Необходимое решение владельца (DC1)

По стилю decision register плана
([plan §5, «Decision register — no silent defaults»](../architecture-improvement-implementation-plan.md)):
technical writer готовит альтернативы, выбирает владелец. До старта любого
C1 writer'а владелец должен решить:

- **(a) Вариант размещения:** A (отдельное built app) или B (no-build
  single-file). Non-options C/C′ зафиксированы в §3.3.
- **(b) Имя route** (proposal предлагает `/work`; вместе с ним — имена
  static subtree/файла и claim paths, производные от route).
- **(c) Build/package ownership:** для A — становится ли bundle-freshness
  check частью designated tests нового surface (закрывает проверенный
  CI-gap); для B — вопрос отпадает.
- **(d) i18n shared source:** где живёт общий словарный источник, когда
  термин рендерится двумя стеками — FRONTEND_CONTRACT требует его, но
  локация не названа и файла сегодня нет (UNVERIFIED-gap, §3.1).
- **(e) Printed URL / entry point:** остаётся ли `#c`-handoff на `/`
  (operator сам набирает путь нового surface), или две строки
  `server.py:1128` / `cli/__init__.py:300` меняются позднее отдельным
  решением. Этот proposal их не меняет.
- **(f) Первый мигрируемый workflow** — см. §6.

## 6. Первый workflow и гейты

Первый мигрируемый workflow стартует **только после** C0 characterization
evidence — golden help/output/error fixtures и recovery drills, которых
сегодня нет (проверено: `find tests -iname "*golden*" -o -iname
"*characteriz*"` пуст; [cli-migration-inventory.md:160-163](../cli-migration-inventory.md)
блокирует полный C0 exit) — и релевантных semantic gates
([plan §9.1:642](../architecture-improvement-implementation-plan.md):
«C0 row + owner-approved UI-surface contract + relevant W/P semantic gate»).

**Evidence-backed рекомендация UX-анализа (не решение):**

1. **Первым — «cold start»**: init → runtime config → hire role → create
   task → launch одного run. Это gate 1 из inventory §4 («initialise,
   generate trusted config, hire, create task and launch» — здесь в
   пересказе;
   [cli-migration-inventory.md:134-136](../cli-migration-inventory.md)); без
   W-gate зависимостей (flow не читает `RunView`/`AcceptanceView`/Attention);
   честный framing — **re-homing существующих panel-возможностей плюс
   закрытие evidence-гэпов**, а не новая capability: все шаги уже имеют
   endpoints (`POST /api/setup/*` `server.py:939-947`, `POST /api/agents`
   `:710`, `POST /api/tasks` `:766`, `POST /api/delegations` `:967`).
2. **Вторым — «daily loop»** (attention → answer → review → accept/reopen),
   **жёстко gated на W1 exit criteria**: attention adapter
   (`ui/attention_queue.py` + `ui/read_dtos.py`, plan §9.1:648),
   детерминизм «identical snapshot + policy + `now` ⇒ identical results»,
   полные card-поля и ≥20 blinded cards калибровки
   ([plan:397-401](../architecture-improvement-implementation-plan.md)).

**Два расхождения — pre-writer attention items для владельца:**

1. **Inventory L69 vs shipped panel.** Inventory говорит:
   «Take/start/block/unblock/complete/submit/cancel… остаются CLI-only»
   ([cli-migration-inventory.md:69](../cli-migration-inventory.md)), но
   shipped `request_task_review` внутренне эмитит переходы
   start→complete→submit через `_REVIEW_WALK`
   ([`src/agent_commons/ui/actions.py:417-423`](../../src/agent_commons/ui/actions.py))
   с фиксированным operator-authored summary (`:424-428`) — при этом не
   выставляя их как operator-selectable команды, что, по-видимому, и
   описывает disposition-колонка inventory. Напряжение между строкой и
   shipped-поведением реально и требует owner-реконсиляции до того, как
   flow используется как parity baseline; утверждение, что одна из двух
   формулировок неверна, этим не установлено.
2. **Claim-less writable launch.** `ui/launch.py:107-140` не берёт claim
   перед передачей writable checkout провайдеру, тогда как
   [FIRST_DELEGATION.md](../tutorials/FIRST_DELEGATION.md) (step 5) и
   [USER_WORKFLOWS.md](../USER_WORKFLOWS.md) §2 делают claim обязательным, а
   у family `claim` successor = `None`
   ([cli-migration-inventory.md:80](../cli-migration-inventory.md)). Нужно
   либо явное narrowing decision (записанное, refuse-able), либо claim-шаг —
   **никогда молчаливое допущение**.

**Анти-рекомендации (не могут быть первым flow)** — компактно, с inventory
rows: `claim` (L80: successor None, «no browser substitute»), `receipt` (L82:
None, emergency recovery), `broker` ops attempts/reconcile/canary (L72:
«CLI-only operator/recovery transport; very high risk» — A1 вправе
претендовать только на launch одного leaf run), governance-семейство
`decision`/`finding`/`verification`/`event` (L78/L77/L76/L81: canonical
governance truth; hard-zero инварианты plan §6.2), `session` (L65: нет
headless successor), `objective` (L68: «Не включать в C1 до отдельной
semantic/UI scope»), `doctor`/`support` (L85/L64: сами являются measuring
stick), `thread` (L73: «Full thread governance не мигрирована») и `handoff`
(L79: «Typed recipients and acknowledgement remain unproven»; на стороне
плана handoff-семантика дополнительно покрыта открытым D6
«handoff/escalation semantics»,
[plan:533](../architecture-improvement-implementation-plan.md) — вывод
плана, а не текст inventory-строк),
`artifact`/`views`/`index` (L74/L83/L84: read parity доказывается per view,
не выводится), и всё из Gallery/Context-Pack программы F1–F4 (параллельная
программа со своим path claim, plan §8).

## 7. Конфликты с аудитом A3–A8

- **A4 UI** — accepted (commit `a455200a…`,
  [r-status-reconciliation.md:38](../audits/2026-08-18-code-quality/r-status-reconciliation.md));
  его следствие обязательно для C1: «subsequent panel workflows belong in
  dedicated modules, not new `UIContext` methods».
- **A7 UI DTO** — записи **completed, не accepted**: «Reconcile each intended
  A7 task at its current subject revision before using it as a prerequisite»
  ([r-status:47, :127-129](../audits/2026-08-18-code-quality/r-status-reconciliation.md)).
  C1 может следовать паттерну `read_dtos.py`, но не вправе ссылаться на A7
  как на принятый prerequisite без реконсиляции на exact revision.
- **A8** — **not started** ([r-status:48](../audits/2026-08-18-code-quality/r-status-reconciliation.md)):
  никакого роста `CommonsManager`; новые reads — collaborators от
  `context.manager()` в route (паттерн `artifact_preview`,
  `server.py:509-522`), с готовностью к будущей A8-миграции binding'ов.
- **Gate новой поверхности** — r-status:135-137: новая surface-работа
  стартует только после собственного approved plan gate; этот документ
  готовит его.
- **Attention/blocked_on_human consolidation — выполнена на этой ревизии.**
  Правка, предписанная в
  [structure-report.md:441-444](../audits/2026-08-18-code-quality/structure-report.md),
  уже реализована: единый предикат `awaits_human(snapshot) -> AttentionSet`
  живёт в
  [`src/agent_commons/domain/attention.py:60`](../../src/agent_commons/domain/attention.py),
  а `blocked_on_human` в
  [`src/agent_commons/ui/graph.py:217-220`](../../src/agent_commons/ui/graph.py)
  стал compatibility view `set(awaits_human(snapshot).node_ids)`; тот же
  источник питает `/api/attention` (`ui/reads.py:19,248`;
  `server.py:575-577` — «One canonical queue: the same source as the amber
  ring and the footer count»). Surface, потребляющий attention data,
  опирается на уже консолидированный канонический источник.
- **`create_app` hotspot** — 332 строки (§2 п.4): регистрация нового
  surface — модулем, не inline.

## 8. Rollout/rollback и совместимость

- **Registered-not-usable**: канонический route table нового surface
  регистрируется целиком с момента, когда панель способна держать session;
  неготовность отвечает typed 409 по frozen code
  ([FRONTEND_CONTRACT.md](../FRONTEND_CONTRACT.md)).
- **Rollback = скрыть surface**: снять route/mount (для B — route),
  ledger не тронут; никаких data migrations, schema или event изменений —
  C1-флоу не добавляет canonical semantics. Семантика kill-switch — plan §7
  («hide surface/revert adapter», [plan:606](../architecture-improvement-implementation-plan.md)).
- **CLI fallback живёт весь compatibility period**
  ([plan:325-326](../architecture-improvement-implementation-plan.md));
  каждое family сохраняет exact registered contract (command tree, `--help`,
  YAML/JSON shape, `{ok:false, error:{...}}` при exit 1, exit-2 health
  семантика — [cli-migration-inventory.md:31-39](../cli-migration-inventory.md)),
  включая field-level parsing ожидания tutorials (`entity_ref.id`,
  `revision`, `claim_id`, `nonce`, inventory:45-49).
- **Триггеры отката, предлагаемые в контракт:** E2E, assert'ящий route
  existence вместо typed code; client-side state advance; run, отрисованный
  как acceptance; refusal после body parsing; claim-less writable launch без
  записанного narrowing decision (§6).
- Достигнутая C1-parity **не** авторизует C3-deprecation: C3 — отдельный
  owner gate с migration guide, compatibility window и evidence отсутствия
  CLI-only consumers ([cli-migration-inventory.md:165-167](../cli-migration-inventory.md)).

## Приложение: явно не проверенное (UNVERIFIED)

1. Общего (cross-surface) locale-источника в репозитории нет; contract
   требует его до рендеринга термина двумя стеками, но локацию не называет.
2. Никакой CI job не собирает и не freshness-проверяет frontend bundles;
   ничто не верифицирует соответствие `ui/static/gallery/` исходникам
   `frontend/gallery/src/`.
3. Должен ли новый surface переиспользовать существующий opaque API base /
   `_BoundApiRoutes` или декларировать собственные read routes — нигде не
   зафиксировано (proposal исходит из переиспользования как из
   surface-agnostic механики, но правило должно быть записано в DC1).
4. Намерена ли printed-URL точка входа (`server.py:1128`,
   `cli/__init__.py:300`) стать surface-selectable — нигде не заявлено
   (вопрос §5e).
5. `frontend/gallery/tsconfig.tsbuildinfo` отслеживался в git без
   документированного намерения — это было обнаружено как generated cache.
   На cleanup-срезе 2026-08-28 файл удалён из рабочей копии этим diff, а
   `*.tsbuildinfo` добавлен в `.gitignore`; после принятия diff этот пункт
   больше не будет открытым C1-вопросом.
