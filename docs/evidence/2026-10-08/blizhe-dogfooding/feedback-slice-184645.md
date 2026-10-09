# Agent Commons: реальная работа владельца Blizhe и UX-фидбэк

Дата: 8 октября 2026. Срез обновления: 18:33 UTC; эксперимент продолжается. Автор — оператор от лица владельца Blizhe, не разработчик Agent Commons. Код Blizhe меняли нанятые агенты; оператор ставил задачи, проверял доказательства, выполнял необходимые fallback и принимал работу. Один технически опытный оператор — не исследование репрезентативной выборки новичков.

## 1. Краткий вывод

**BASE и TOOL приняты после независимой проверки.** Реальный цикл вернул TOOL с замечанием к очистке тестовых файлов; агент исправил его, сохранил исторические доказательства и прошёл повторный QA. Это сильная сторона системы: проверка дала содержательное замечание, а завершение прогона не стало автоматической приёмкой.

**Полностью пройти путь владельца через UI пока не получилось.** Регистрация текстовых результатов, передача точной ревизии и принятие одобренного результата потребовали CLI и чтения исходников Commons. UI несколько раз предлагал «Запросить проверку» после уже записанного approved. Для новичка это существеннее косметики: непонятно, что готово и какое действие завершает работу.

По новому поручению владельца в UI подготовлены PHOTO (OpenAI Decisions) и SEARCH (два независимых аудита). Консилиум поиска завершил два первых ответа и две критики, сохранён консолидированный TODO-граф. PHOTO реализован с 86 новыми mocked tests (всего 210), lint/typecheck и операторским build exit0; передан на независимую проверку. Ни PHOTO, ни SEARCH на этом срезе не приняты и не объявлены работающими в production.

## 2. Граница опыта и версии

| Объект | Проверенная граница |
| --- | --- |
| Agent Commons | local main `9ba5cbd5962b74a80423138174de153418fc683c`; удалённый main в этом проходе не fetch/pull |
| Инструмент и UI | `.venv/bin/agent-commons`, версия `0.1.0`; asset `work-C0nYR3qF.js`; отдельная панель `127.0.0.1:8794/work` |
| Браузер | Chrome; EN/RU; тёмная тема; зафиксированный viewport 1042×467 |
| Исходный Blizhe | main `cbdcad62052808343feca6a0a84a8b9b9e54976a`; 44 dirty entries |
| Рабочая копия агентов | `/Users/dmitrijersov/Desktop/blizhe-workspaces/dogfooding-20261007`, ветка `codex/blizhe-improvement-20261007`, HEAD `ae2de22f81767c63c3b2806343f2ca0be196aa2a` |
| Среда TOOL | временный Node22.23.3, `npm ci` 844 packages; lock SHA256 `e033e5945acf3a0374ac581482f66b6fb89c14a85143cacb4c671291097e9c02` |
| Исполнители | Codex CLI0.159.3, requested builder/reviewer profile `gpt-6-astra`, actual model у SEARCH runtime unknown; Claude Code2.1.257 не прошёл no-model initialization timeout; браузерный Claude Opus5.5 medium |
| Время и расход | новое разрешённое окно 16:19:31–22:19:31 UTC; на срезе десять проектных прогонов, включая ошибки, SEARCH-A/C и PHOTO; одна попытка Claude canary, остановленная до работы модели; два браузерных ответа Claude (первый и критика, не два независимых мнения) |

Позднейшие разрешения владельца расширили первоначальное число прогонов и scope. Старые ошибки и расходы не скрыты. Одновременные пишущие воркеры не запускались. Коммитов, push, PR, Linear-изменений, SQL-политик и deploy не выполняли. CHATRLS/PUBLIC/GRANTS не трогали.

**Операция:** сверка owner snapshot в 17:42:54 UTC дала совпадение branch/HEAD/origin-main/status/exclude и всех 4815 Git-visible файлов в 44 dirty entries, включая 7 вложенных репозиториев, с baseline. Игнорируемые секреты, caches и Git internals не входят в покрытие. Последний read-only doctor: ok=true,122events,29manifests,issues=[]; предупреждения о двух намеренно устаревших review после reopen и о пропуске writable SQLite/receipt recovery. Это не полный writable doctor.

Метки: **UI** — наблюдалось в браузере; **Операция** — CLI/broker/фактический прогон; **Код** — чтение реализации, не UI-проверка; **Гипотеза** — интерпретация/предложение, которое ещё надо проверить.

## 3. Карта пути: ожидал → увидел → время

| Шаг | Ожидал → увидел | Время и доказательство | UX-ID |
| --- | --- | --- | --- |
| 1. Вход/проект | Открыть работающую панель → connection refused; пришлось штатно перезапустить. Исходный и изолированный Blizhe доступны раздельно. Смысл архивирования без подсказок заново не проверен | UI/Операция; точная длительность восстановления не замерена; свежий capability URL открыт один раз | UX-01, CLI fallback |
| 2. Команда | Выбрать QA и начать проверку → built-in QA несовместим с reviewer, создана custom специализация через UI. Для PHOTO и SEARCH ручной найм понятен после изучения терминов; создание не запускает модель | UI/Код; custom save ≤6.384s, QA hire ≤22.418s — верхние границы между наблюдениями, не точная latency. PHOTO показывал Still saving; секунды не замерены | UX-37, UX-47, UX-50 |
| 3. Задачи/граф | BASE блокирует TOOL/CONFIG/REASONS → зависимость соблюдена; после приёмки BASE задачи разблокировались. PHOTO зависит от TOOL. SEARCH — компонент с двумя дочерними аудитами | UI; сохранение уточнения TOOL ≤14.493s. Длинное описание обрезано с отсылкой к diagnostics | UX-44, UX-46 |
| 4. Прогон | Подготовить→роль→лимит→запустить → UI честно отделяет запуск, но промежуточно показывает ложный auth recovery. TOOL израсходовал первый прогон на отсутствие dependencies | UI/Операция; BASE QA~83s, первый TOOL~240s. Запуск TOOL подтверждён ≤19.673s; это не измерение чистого сохранения | UX-41, UX-42, UX-45 |
| 5. Разговор/файл | Уточнить работающему агенту → TOOL подтвердил чтение и ответил, все 5 стадий достигнуты. SEARCH-A также подтвердил все 5 стадий, ответ спустя~530s по UI timestamps. PHOTO подтвердил чтение и план. Файл не приложен из-за разрешения browser extension | UI; TOOL reply~22s по message timestamps, отдельно от latency записи. Повтор upload вернул точный permission отказ | UX-15; ограничение инструмента |
| 6. Проверка/приёмка | Открыть результат→QA→принять → текстовые artifacts и handoff через CLI. QA нашёл cleanup bug, последовал fix, fresh QA approved. UI после approved снова Request review; BASE и TOOL приняты CLI | UI/Операция/Код; исходный TOOL QA~118s; cleanup fix~2min; final QA~102s. Результаты проверок ниже | UX-38, UX-39, UX-40, UX-48 |
| 7. Переключение/draft/save | Черновик должен быть предсказуем → внутри приложения сохраняется; reload очищает, как прямо написано. Полная загрузка другого проекта в пробе не подтверждена. Во время долгого hire форма disabled | UI; latency не замерена. Двойной server-write8 октября не отправлялся; не заявляем проверку идемпотентности | UX-15, UX-50 |

Сравнение improve-service и ручного найма не повторяли 8 октября; исторический проход 7 октября отделён от текущего. Без нового воспроизведения не объявляем шаблон понятнее. На текущем ручном пути нужные специализации найдены, но подтверждение исполнителей требует ожидания и знания профилей.

### Результат TOOL и границы проверки

Агент изменил `eslint.config.mjs`, `tsconfig.json`, добавил `docs/engineering/tooling-scope.md`. Worker evidence: lint/typecheck с ошибочными fixture в 9 исключённых каталогах — exit0;234baseline TS inputs сохранены; ошибки в `src` обе проверки обнаружили;11 test files/124 tests passed, lint10warnings. Первый build был заблокирован port binding, watch пересёкся со сборкой и был недостоверен.

Оператор затем выполнил стандартный Turbopack build с теми же fixture и dummy CI env: exit0/7.807s. Последовательный TypeScript watch: exit0/8.506s, initial1→excluded1→src2, TS2322. Это не проверка всех Tailwind/bundler watchers, UI или production.

Независимый QA нашёл опасный cleanup в evidence script. Fix сохранил preimage, учитывает только созданные файлы/каталоги, пять filesystem regression tests покрывают 10collision paths, partial mkdir/write, command exception и сохранность sentinel. Первые две ошибки тестовой инъекции /var↔/private/var сохранены, причина исправлена; это не замалчивание неудачных тестов.

Final review `review.2AZWQGMP9W2GV4WK6M9E61DMR7` — approved, independent=true, stale=false,23 артефакта прочитаны. QA документальный: reviewer сам команды не запускал и байты owner checkout не сравнивал. Принятие TOOL `evt.01M4E9TANPR1VG8RNNK0NY882R`; BASE `evt.01M4E60FZ88RJ3NHR9N338ZHBQ`.

### CLI fallback и документация

Каждый уход из UI отмечен в [журнале](journal.md). Группы: запуск панели; Git/owner snapshots/doctor; операторская сессия; artifact registration/handoff; task lifecycle/review/accept; index rebuild без устранения UX-дефекта; подготовка Node/dependencies; операторский build/watch; Claude preflight/canary/no-model диагностика; регистрация новых ревизий после замечаний QA; сохранение source packet.

Чтобы продолжить, понадобились PRODUCT, RU README/service-library, руководство CLI и исходники eligibility/execution_plan/provider_canary. Исходники Commons не меняли. Ошибочные вызовы оператора `task show`, `doctor --read-only` и submit без repeated refs признаны ошибками оператора; их не выдаём за UI-ошибки.

## 4. Приоритеты

| ID | Приоритет | Наблюдение → предложение |
| --- | --- | --- |
| UX-38 | P1 | Worker не может зарегистрировать текстовый результат/submit → поддержанный сквозной handoff в UI и scoped tools |
| UX-40 | P1 | Approved и changes_requested не приводят к правильному действию → карточка точного verdict, Accept либо Fix, без повторного review |
| UX-39 | P1 | Общий needs_operator/inspect evidence → конкретная причина, сохранённый результат и следующий ответственный |
| UX-37 | P1 | Built-in QA не нанимается на reviewer → готовая независимая QA-специализация и видимая совместимость |
| UX-42 | P1, операционный пробел | Model run потрачен на отсутствие dependencies → readiness среды проекта до запуска отдельно от provider qualification |
| UX-47 | P1 для Claude | Initialization timeout5s меньше наблюдённых 6.34s → измеряемая диагностика и путь исправления; не ослаблять isolation |
| UX-48 | P1, CLI | submit по умолчанию стирает artifact refs → сохранять refs либо явно предупреждать, проверять evidence до платного QA |
| UX-41 | P2 | Нормальная запись кратко выглядит как auth failure → обычное saving состояние |
| UX-51 | P2, глоссарий | Add subtask показывает кнопку «Создать зависимую задачу», хотя parent/dependencies записывает правильно → согласовать термин |
| UX-52 | P1, handoff | Новая SEARCH-C не получила адресованного обсуждения и не смогла передать готовый отчёт → readiness канала до запуска |
| UX-43 | P2 | В Settings требуется check run, но нет действия → понятный canary CTA с расходом и ограничением |
| UX-44 | P2 | Описание обрезается и отправляет в diagnostics → раскрытие полного текста рядом с задачей |
| UX-45 | P2 | Текущий run идёт, task Ready/Now In progress0 → согласованная карточка, не теряя различия run/task |
| UX-46/50 | P2 | Карта ниже первого экрана, много терминов → меньше служебного текста и ясный первый маршрут |
| UX-15 | P2 | Reload уничтожает draft, хотя честно предупреждает → устойчивый draft как улучшение, не скрытая ошибка текущего контракта |
| UX-32 | Не воспроизводится | Галерея показывает честное пустое состояние EN/RU; непустая не проверялась |
| Глоссарий | P2 | Подсказка ссылается на отсутствующий раздел «Роли»; смешиваются роль/агент/воркер/профиль → единый словарь с progressive disclosure |

## 5. Что сохранить при упрощении

Сохранить независимость reviewer, привязку verdict к точной ревизии, отдельное принятие владельцем, ограничения бюджета и одного writer, immutable версии специализаций, блокировку зависимостей. Не прятать ошибки под красивым success.

Сохранить разделение структуры компонентов и зависимостей, возможность выбрать Fresh, явное «сообщение не запускает прогон», пять стадий доставки и честное пустое состояние галереи. Конфликт ревизии при редактировании сохранил draft и объяснил Reload task: это полезная защита от перезаписи. Автоматический merge не проверен — оператор копировал и восстанавливал текст сам.

**UI:** тёмная тема спокойная, основные кнопки различимы, RU/EN переключение понятно. **Гипотеза:** onboarding должен вести через папку, команду, задачу, readiness среды и первый результат. Сейчас знания приходится собирать по разделам. В окне 1042×467 шапка/status/help/buttons занимают большую часть первого экрана, карта уходит вниз; это ограниченный desktop-срез, не мобильный аудит.

## 6. Дефекты с воспроизведением

**UX-40 (UI/Операция/Код).** Зарегистрировать результаты→submit точной ревизии→independent review→QA approved. Открыть task: «Запросите независимую проверку этой редакции» и Request review, без Accept. Refresh/reload на BASE не помогли; на TOOL повторилось. Canonical review approved/current; CLI accept успешен. `_task_readiness` в execution_plan заменяет acceptance.next_action на run.next_action при наличии run; это соответствует наблюдению, исправление не делалось.

**UX-38/39 (UI/Операция).** Builder завершает файлы и проверки, но scoped MCP не предоставляет регистрацию text artifacts/task submit. Он корректно пишет needs_operator и пути/хеши в обсуждение. Инспектор даёт общую причину, UI-передачи нет; оператор вынужден регистрировать через CLI. Содержательный thread спасает работу, но не завершает UX-путь.

**UX-37 (UI/Код).** Нанять встроенного QA-engineer на codex-independent-reviewer: «Исполнителя только для ревью нельзя нанять на специализацию, которая не является ролью ревью». Copy специализации с ID `blizhe-qa-reviewer`, независимыми инструкциями и qa-testing через UI позволяет hire. Код распознаёт review-role по суффиксу ID; permissions не расширялись.

**UX-41 (UI).** Start run готового Codex: кратко Critical attention/Provider authentication/Authentication is ready и disabled Continue рядом с saving; затем самостоятельно Run recorded. Это ложная промежуточная тревога, не реальный OAuth запрос.

**UX-42 (Операция).** Fresh checkout без node_modules: offline npm ci ENOTCACHED vite, registry ENOTFOUND getaddrinfo registry.npmjs.org; lint/typecheck/test/build exit127. Node23 при CI22. Подготовка временного Node22 и npm ci оператором исправила конкретную среду без lockfile изменений. Сетевая причина внутри продукта не доказана.

**UX-47 (Операция/Код).** Claude preflight прошёл, canary остановился до модельной работы. Exact-profile no-model initialization: timeout5s→timed_out5.56s; диагностические 15s→ready6.34s. Runtime и qualification не меняли, повторный canary не запускали. Прямой browser fallback использовал Opus5.5; Fable5.1 требовал отдельные credits и не использован.

**UX-48 (Операция/Код).** complete с 13refs→submit без флагов→artifact_refs=[]→QA input_needed. Ошибка передачи оператора плюс неожиданный CLI default. Reopen→complete/submit с повторёнными 13refs дал содержательный QA. История не переписана, ненужный расход учтён.

**Загрузка файла (ограничение инструмента).** setFiles вернул: “To enable file upload, go to chrome://extensions in Google Chrome, click Details under the ChatGPT extension, and enable ‘Allow access to file URLs.’” Разрешение не менялось; успешный upload/чтение вложения агентом не заявляются. Текстовые уточнения доставлялись отдельно. Это не установленный дефект Commons.

**UX-51/52 (UI/Операция).** В редакторе «Добавить подзадачу» submit подписан «Создать зависимую задачу»; canonical parent/dependencies новых SEARCH задач проверены и корректны. SEARCH-C закончил critique, но commons_list_my_threads вернул[] наstartup/checkpoint/finalization, поэтому обязательный handoff недоступен. Он корректно не написал неадресованному thread и завершился needs_operator. Discussion открыли после прогона; lazy-creation причина пока гипотеза. Повтор модели ради того же отчёта не делали.

### Новая работа PHOTO и консилиум

PHOTO delegation `delegation.27MCQAMC7P8T9F4YW93V65A9PM` завершён needs_operator18:27:22:86 новых mocked tests,210 всего/14files, lint/typecheck pass; sandbox build EPERM. Оператор стандартный build с явно dummy Decisions config выполнил exit0/8.28s, все 21 записи source/evidence SHAmanifest сохранились. Новый review `review.5WS0BEZYNCYNKB9GRAWT6YMHGY` относится к 12 точным artifacts. Кандидат ещё не принят. Временные/невалидные/неуверенные ответы pending; threshold0.95 некалиброван. Возможные повторные расходы на recurring pending, multi-write lifecycle и пределы blob download перед byte check явно вынесены в QA/runbook, не скрыты.

Полные ответы/критики и консолидированный TODO-граф (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/search/decision.md`) доступны. Current joint text+photo сохраняется исходной реализацией; photo-off/named vectors не выбраны без сравнения. Кодовая критика подтвердила private reasons, blocks fail-open, catalog mismatch и опасный purge; уточнила, что desired hash перезаписывается уже enqueue, поэтому простой перенос skip не чинит идемпотентность. Противоречия source authority/private purpose/TMA квот и durable deletion переданы отдельным решениям, SQL не меняли.

UI созданы SEARCH-SAFE(TOOL), SEARCH-INDEX(TOOL), SEARCH-RANK(REASONS+TOOL), SEARCH-BOUNDS(SAFE), SEARCH-EVAL(RANK+BOUNDS). REASONS описание обновлено по расширению владельца; CONFIG остаётся подготовленным. Создание задач не запускало их.

Read-only presence check исходного .env.local показал: OpenAI entry отсутствует, Google/Qdrant entries непустые. Значения не выводились, не переносились и не использовались; файл не менялся. Это не проверка production endpoints.

## 7. Доказательства

[Manifest, подписи и SHA256](manifest.md), [журнал по ходу](journal.md), [начальный срез](initial-feedback.md).

V01–V18 — вход, галерея, найм QA, BASE→TOOL, разговор/draft/reload, нужное действие оператора. V19 — retry form; V20 — SEARCH structure; V21 — QA retry; V22 — hire уже после долгого сохранения; V23 — неправильный CTA после approved TOOL; V24 — SEARCH running и stale evidence; V25 — PHOTO launch; V26 — подтверждённая доставка PHOTO. Основные пары EN/RU сохранены, кадры показывают видимую часть экрана. V14 и V22 имена содержат wait/long-save, но сами кадры сняты после ожидания. Auth flash наблюдался в DOM, отдельного синхронного кадра нет.

TOOL worker report (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-retry/TOOL.md`), operator build/watch (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-operator/verification.md`), cleanup fix и ограничения (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-review-fix/README.md`). Старый needs_operator report сохранён отдельно в `results/TOOL.*`.

## 8. Что НЕ проверено / не завершено

PHOTO реализован, но ещё не принят; SEARCH имеет завершённый консилиум и подготовленные bounded задачи, но не реализованные исправления. CONFIG/REASONS подготовлены. OpenAI/Google/Qdrant credentials в isolated env отсутствуют, production calls и live calibration не выполнялись. Не заявляем качество модерации либо ранжирования по mocked tests/совету моделей.

Не проверены SQL/CHATRLS/PUBLIC/GRANTS, deploy, реальная платежная среда, архивирование/восстановление, повтор onboarding с пустой папкой, новый improve-service проход, непустая галерея, успешное вложение, устойчивость draft после reload и server idempotency двойного действия 8 октября. Полный writable doctor и свежесть удалённого main не проверены. Приёмка TOOL не означает принятие всей волны улучшений.
