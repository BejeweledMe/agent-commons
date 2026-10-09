# Agent Commons: реальная работа владельца Blizhe и UX-фидбэк

Дата: 8 октября 2026. Срез обновления: 20:15 UTC; эксперимент продолжается. Автор — оператор от лица владельца Blizhe, не разработчик Agent Commons. Код Blizhe меняли нанятые агенты; оператор ставил задачи, проверял доказательства, выполнял необходимые fallback и принимал работу. Один технически опытный оператор — не исследование репрезентативной выборки новичков. PRODUCT.md:30–34 называет основной аудиторией технически подготовленного разработчика/владельца; здесь проверяется первый опыт такого владельца, а не обещание no-code для любого человека.

## 1. Краткий вывод

**BASE, TOOL, PHOTO, REASONS, SEARCH-SAFE, SEARCH-INDEX и SEARCH-RANK приняты после независимой проверки.** Реальный цикл вернул TOOL с замечанием к очистке тестовых файлов; агент исправил его, сохранил исторические доказательства и прошёл повторный QA. Это сильная сторона системы: проверка дала содержательное замечание, а завершение прогона не стало автоматической приёмкой.

**Полностью пройти путь владельца через UI пока не получилось.** Регистрация текстовых результатов, передача точной ревизии и принятие одобренного результата потребовали CLI и чтения исходников Commons. UI несколько раз предлагал «Запросить проверку» после уже записанного approved. Для новичка это существеннее косметики: непонятно, что готово и какое действие завершает работу.

По новому поручению владельца в UI подготовлены PHOTO (OpenAI Decisions) и SEARCH (два независимых аудита). Консилиум поиска завершил два первых ответа и две критики, сохранён консолидированный TODO-граф. PHOTO после возврата на исправление очереди прошёл fresh QA и принят:219tests/lint/typecheck, отдельный реальный one-call API smoke через неизменённый адаптер. REASONS и SEARCH-SAFE прошли независимый QA и приняты. SEARCH-INDEX принят; SEARCH-RANK принят, SEARCH-BOUNDS запущен. Production не включали.

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
| Время и расход | новое разрешённое окно 16:19:31–22:20:01 UTC; на срезе 24 проектных прогона, включая ошибки, SEARCH-A/C и PHOTO; одна попытка Claude canary, остановленная до работы модели; два браузерных ответа Claude (первый и критика, не два независимых мнения) |

Позднейшие разрешения владельца расширили первоначальное число прогонов и scope. Старые ошибки и расходы не скрыты. Одновременные пишущие воркеры не запускались. Коммитов, push, PR, Linear-изменений, SQL-политик и deploy не выполняли. CHATRLS/PUBLIC/GRANTS не трогали.

**Операция:** сверка owner snapshot в 18:41:03 UTC дала совпадение branch/HEAD/origin-main/status/exclude и всех 4815 Git-visible файлов в 44 dirty entries, включая 7 вложенных репозиториев, с baseline. Игнорируемые секреты, caches и Git internals не входят в покрытие. Последний read-only doctor: ok=true,207events,57manifests,issues=[]; предупреждения о трёх намеренно устаревших review после reopen и о пропуске writable SQLite/receipt recovery. Это не полный writable doctor.

**Новая граница сохранности 19:00:54UTC:**44Git entries и 825 оставшихся файлов совпали, но 6 старых вложенных `.claude/worktrees/agent-*` исчезли (3990files), их Git регистрации также отсутствуют. В 18:41 все 4815/7 ещё совпадали. Причина/автор удаления не установлены; оператор удаления не выполнял, у владельца запрошена информация об отдельной уборке. Ничего не восстановлено/не удалено нами; полное прежнее совпадение после 19:00 не заявляется. Последующая read-only проверка установила: все 6worktrees былиclean в 18:41, все 6commit objects остались вGit; уцелевшие 825file records совпадают. Автор уборки неизвестен, ignored файлы не покрываются. Признаков потери non-ignored незакоммиченной работы в этом наборе не обнаружено.

Метки: **UI** — наблюдалось в браузере; **Операция** — CLI/broker/фактический прогон; **Код** — чтение реализации, не UI-проверка; **Гипотеза** — интерпретация/предложение, которое ещё надо проверить.

## 3. Карта пути: ожидал → увидел → время

| Шаг | Ожидал → увидел | Время и доказательство | UX-ID |
| --- | --- | --- | --- |
| 1. Вход/проект | Открыть работающую панель → connection refused; пришлось штатно перезапустить. Исходный и изолированный Blizhe доступны раздельно. Смысл архивирования без подсказок заново не проверен | UI/Операция; точная длительность восстановления не замерена; свежий capability URL открыт один раз | UX-01, CLI fallback |
| 2. Команда | Выбрать QA и начать проверку → built-in QA несовместим с reviewer, создана custom специализация через UI. Для PHOTO и SEARCH ручной найм понятен после изучения терминов; создание не запускает модель | UI/Код; custom save ≤6.384s, QA hire ≤22.418s — верхние границы между наблюдениями, не точная latency. PHOTO показывал Still saving; секунды не замерены | UX-37, UX-47, UX-50 |
| 3. Задачи/граф | BASE блокирует TOOL/CONFIG/REASONS → зависимость соблюдена; после приёмки BASE задачи разблокировались. PHOTO зависит от TOOL. SEARCH — компонент с восемью дочерними узлами: два аудита, критика и пять реализаций | UI; сохранение уточнения TOOL ≤14.493s. Длинное описание обрезано с отсылкой к diagnostics | UX-44, UX-46 |
| 4. Прогон | Подготовить→роль→лимит→запустить → UI честно отделяет запуск, но промежуточно показывает ложный auth recovery. TOOL израсходовал первый прогон на отсутствие dependencies | UI/Операция; BASE QA~83s, первый TOOL~240s. Запуск TOOL подтверждён ≤19.673s; это не измерение чистого сохранения | UX-41, UX-42, UX-45 |
| 5. Разговор/файл | Уточнить работающему агенту → TOOL подтвердил чтение и ответил, все 5 стадий достигнуты. SEARCH-A также подтвердил все 5 стадий, ответ спустя~530s по UI timestamps. PHOTO подтвердил чтение и план. Файл не приложен из-за разрешения browser extension | UI; TOOL reply~22s по message timestamps, отдельно от latency записи. Повтор upload вернул точный permission отказ | UX-15; ограничение инструмента |
| 6. Проверка/приёмка | Открыть результат→QA→принять → текстовые artifacts и handoff через CLI. QA нашёл cleanup bug, последовал fix, fresh QA approved. UI после approved снова Request review; BASE и TOOL приняты CLI | UI/Операция/Код; исходный TOOL QA~118s; cleanup fix~2min; final QA~102s. Результаты проверок ниже | UX-38, UX-39, UX-40, UX-48 |
| 7. Переключение/draft/save | Черновик должен быть предсказуем → при закрытии разговора и реальной смене проекта сохраняется; reload очищает, как прямо написано. Дополнительная проба 19:48–19:49 UTC подтвердила agent-commons project.11c… с 65/310 tasks, затем возврат в isolated Blizhe project.a372… и сохранённый SEARCH-INDEX draft. Во время сохранения hire и сообщения форма disabled | UI; latency не замерена. Повторный click при Saving наEN/RU отклонён disabled-кнопкой; запись одна, BOUNDS UI ack1.9s. Не заявляем server idempotency | UX-15, UX-50 |

Историческое сравнение из [прохода 21 сентября](../../../handoffs/2026-09-21-blizhe-dogfooding-feedback.md), source5153a56 (позже только документационные изменения доbbc19e8): improve-service создал 4 роли/5 задач и явно сообщил «Ни один прогон не запущен». Подтверждение замечено≤17.429s, это верхняя граница. Шаблон оказался понятнее для старта; ручной найм точнее задавал frontend/backend ответственность, но требовал понимания специализаций/профилей и восстановления readiness. Шаблон включалlead/SRE/QA/security, поэтому frontend/backend добавлялись вручную, а лишняя общая задача была отменена с пояснением. Это оценка одного исторического прохода; 8 октября шаблон заново не применяли.

В том же историческом UI-проходе подтверждение архива ясно говорило о скрытии проекта, сохранении файлов и возможности восстановления, но не объясняло продолжение активного run. Тестовый проект без прогонов исчез из основного списка за 0.362s и обнаружен в архиве; Blizhe не архивировали, restore не тестировали. Подключение уже открытой папки повторно дало общую ошибку: смысл подключения не был полностью понятен без чтения.

Вложение было успешно проверено 21 сентября: несекретный BASE-owner-constraints.txt отправлен 19:13:54UTC и полное чтение подтверждено агентом 19:20:19UTC,385s. Это доставка+чтение, не серверная запись. 8 октября повторный upload заблокирован настройкой browser extension; прежний успех не доказывает доступность текущего инструмента.

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
| UX-56 | P3, глоссарий | PRODUCT.md задаёт Conversation / «Разговор», RU интерфейс и руководство используют «Обсуждение» → выбрать единый термин; Agent/role — уже документированный переход, не новая регрессия |
| UX-53 | P3, гипотеза | Reply recorded при отсутствии explicit reading ack → пояснить отличие ответа и отдельного подтверждения чтения |
| UX-55 | P2, UI/DOM | В RU после отправки история перекрыла textbox; центр hit-test попадает вP → проверить scroll/layout composer. ВEN после reopen overlap не повторился |
| UX-54 | P2, UI | Fit width даёт 25% и нечитаемые подписи; закрытие инспектора сбрасывает branch scope → читаемый outline/раскрытие веток |
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

**UX-55 (UI/DOM).** На viewport1042×467 после отправки RU сообщения textarea визуально перекрыта историей: bounding rect220–309px поY, center hit-test возвращаетP сообщения, opacity1. После закрытия/открытия наEN центр ужеTEXTAREA, нижний край 486px за viewport467. Языковая причина не доказана; нужна проверка scroll/layout. Семантический fill обходил необходимость обычного клика, поэтому прежний успешный send не доказывает доступность поля мышью. [DOM-снимок](composer-overlap-dom.json), V32.

**UX-56 (UI/Код документации).** PRODUCT.md:126 задаёт Conversation / «Разговор». RU кнопка и заголовок у SEARCH-SAFE говорят «Обсуждение» (V33), README.md:270 и service-library.md:79 поддерживают именно «Обсуждение». Это расхождение единого глоссария с UI и руководством, а не доказательство непонимания пользователя. PRODUCT.md:116 и README.md:39 прямо признают временное Agent/role; его не выдаём за новый скрытый дефект.

### Новая работа PHOTO и консилиум

PHOTO delegation `delegation.27MCQAMC7P8T9F4YW93V65A9PM` завершён needs_operator18:27:22:86 новых mocked tests,210 всего/14files, lint/typecheck pass; sandbox build EPERM. Оператор стандартный build с явно dummy Decisions config выполнил exit0/8.28s, все 21 записи source/evidence SHAmanifest сохранились. Новый review `review.5WS0BEZYNCYNKB9GRAWT6YMHGY` относится к 12 точным artifacts. QA вернул кандидата: 20 старых refusal/uncertain pending повторно вызывают API и блокируют следующие фото. Доработка hold завершена агентом: явный NULL/not-in фильтр до limit,9 новых регрессий,219 тестов, lint/typecheck pass; worker-only fix не требует нового build по контракту. Старые 12artifacts совпали по hash. Новый review `review.74GG40PDJHCZR35KP347YEX5PY` с 16 артефактами approved/independent/current. PHOTO принят `evt.01M4EE5CM30ZSJRYM34VEFDJER`. Отдельный operator smoke:1real request,synthetic64x64PNG,3.6s,completed/approved и четыре нулевые вероятности. Проверены API access/typed wire/parser, не качество фильтра. Временные/невалидные/неуверенные ответы pending; threshold0.95 некалиброван. Возможные повторные расходы на transient/persistence-failure pending, multi-write lifecycle и пределы blob download перед byte check явно вынесены в QA/runbook, не скрыты.

Полные ответы/критики и консолидированный TODO-граф (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/search/decision.md`) доступны. Current joint text+photo сохраняется исходной реализацией; photo-off/named vectors не выбраны без сравнения. Кодовая критика подтвердила private reasons, blocks fail-open, catalog mismatch и опасный purge; уточнила, что desired hash перезаписывается уже enqueue, поэтому простой перенос skip не чинит идемпотентность. Противоречия source authority/private purpose/TMA квот и durable deletion переданы отдельным решениям, SQL не меняли.

UI созданы SEARCH-SAFE(TOOL), SEARCH-INDEX(TOOL), SEARCH-RANK(REASONS+TOOL), SEARCH-BOUNDS(SAFE), SEARCH-EVAL(RANK+BOUNDS). REASONS описание обновлено по расширению владельца; CONFIG остаётся подготовленным. Создание задач не запускало их.

REASONS:13artifacts, review `review.2X2M4XHTXCFZQNM18BG2A2GPNJ` approved/current/independent; принято `evt.01M4EEY55RP18Y4S9F60AM1G4K`. Изменён compatibility.ts и две regression suites; exact-public visibility до чтения ответов, public-only порядок/strength/text, весь список/top3 инвариантен при private perturbation. Numerical scorer сохранён.233tests/lint/typecheck; build не нужен по условию routes/config. Сервисные mocks используют реальные feed/search serializers; live DB/browser и широкая private-purpose policy не проверены/не одобрены.

SEARCH-SAFE:329 авторских tests/lint/typecheck,96 новых кейсов. Независимый QA `review.2WCDWNPT6MX4B8Z8H38H8AQNMS` вернулP2: viewer-targets null-data безerror ошибочно даёт 403 вместо 503; раскрытия карточек в этой ветке нет. Узкое исправление завершено: 100 focused / 333 total tests, lint/typecheck; 20 точных artifacts. Fresh независимый QA `review.3D1RVM7WEFWMK7HNX4VAAA90CF` approved в 19:42:14 UTC, текущая ревизия принята `evt.01M4EGNWZSF1K7HMPWAH0M0B3G`. Проверка QA документальная, не собственный запуск команд.

SEARCH-INDEX candidate: 65 новых mocked route cases,398 total tests/lint/typecheck. У агента сборка завершилась EPERM при bind port; отдельная обычная операторская сборка с dummy env прошла за 8.395s,404 source/evidence hashes сохранены.22 exact artifacts прочитаны independent review `review.2G7M5KM3TEHAMCMSN5F7VSVPRR`; approved/current в 19:59:50, принят `evt.01M4EHQ9TDQ97WHKNM5EY4MK77`. QA статический, авторские/операторские команды себе не приписывал.

SEARCH-RANK candidate: 48 новых/446 общих тестов,170 focused, lint/typecheck. Восемь случаев сравнения со снятым preimage воспроизводят ложный cap0.3 и исправление. Явные aliases, unknown/already_have исключены из ordinal coverage; единая формула с прежними весами 0.6/0.4 и min-max; stable ID ties, raw response score сохранён.8 финальных снимков совпадают с source,26 artifacts прочитаны QA;873 других файлов неизменны по авторскому inventory. Independent review `review.2B8E7VJKNQNVPRHB0XDY8B0CWY` approved в 20:13:40; принят `evt.01M4EJFR34XCSEPE3G363F5JHY`. Качества рекомендаций по этому не заявляем.

Доказательство одного реального Decisions API smoke (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/photo-live-smoke/verification.md`).

Первичный read-only presence check: OpenAI отсутствовал, Google/Qdrant entries непустые. Позже пользователь добавил OpenAI key. Только этот ключ прочитан в память для 1authorized synthetic Decisions request, не скопирован в .env isolated checkout и не попал в evidence. Исходный env hash до/после smoke совпал; Google/Qdrant значения не использовались.

## 7. Доказательства

Статический JSON-граф задач и ролей (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/search/program-graph.json`):14 узлов,13 зависимостей, проверенDAG; без живых статусов.

[Manifest, подписи и SHA256](manifest.md), [журнал по ходу](journal.md), [начальный срез](initial-feedback.md).

V01–V18 — вход, галерея, найм QA, BASE→TOOL, разговор/draft/reload, нужное действие оператора. V19 — retry form; V20 — SEARCH structure; V21 — QA retry; V22 — hire уже после долгого сохранения; V23 — неправильный CTA после approved TOOL; V24 — SEARCH running и stale evidence; V25 — PHOTO launch; V26 — подтверждённая доставка PHOTO; V27 — содержательное замечание независимого QA и возврат PHOTO; V28 — читаемость SEARCH graph с/без инспектора; V29 — PHOTO принят; V30 — REASONS принят; V31 — повторное send/disabled Saving; V32 — состояние перекрытого composer и сравнение после reopen; V33 — SEARCH-SAFE принят после повторной проверки; V34 — другой проект, сохранившийся после возврата draft и его очистка после reload, EN/RU; V35 — принятая SEARCH-INDEX; V36 — видимая карточка приёмки SEARCH-RANK. Основные пары EN/RU сохранены, кадры показывают видимую часть экрана. В ранних `accepted` кадрах карточка решения могла оставаться ниже края viewport; для PHOTO и INDEX дополнительно сохранены `decision-EN/RU` после прокрутки, где заголовок задачи и зелёное решение видны вместе. Прежние кадры не подменены. V14 и V22 имена содержат wait/long-save, но сами кадры сняты после ожидания. Auth flash наблюдался в DOM, отдельного синхронного кадра нет.

TOOL worker report (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-retry/TOOL.md`), operator build/watch (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-operator/verification.md`), cleanup fix и ограничения (внешний файл, путь от этого отчёта: `../../../../../../blizhe-workspaces/dogfooding-20261007/docs/dogfooding/2026-10-08/tool-review-fix/README.md`). Старый needs_operator report сохранён отдельно в `results/TOOL.*`.

## 8. Что НЕ проверено / не завершено

PHOTO принят в описанной границе. SEARCH имеет завершённый консилиум и подготовленные bounded задачи; REASONS и SEARCH-SAFE приняты, SEARCH-INDEX принят; SEARCH-RANK принят; SEARCH-BOUNDS исполняется, SEARCH-EVAL ещё не принят. CONFIG подготовлен. Пользователь добавил OpenAI key в исходный .env.local; наличие проверено без вывода значения. В isolated env ключи не копировались. Один операторский smoke через неизменённый adapter на синтетическом geometric PNG выполнен успешно,3.6s. Запросы к production БД и реальным фотографиям, а также live calibration не выполнялись. Не заявляем качество модерации либо ранжирования по mocked tests/совету моделей.

Не проверены SQL/CHATRLS/PUBLIC/GRANTS, deploy, реальная платежная среда и server idempotency двойного действия (повтор был остановлен UI). На текущей версии заново не проверялись архивирование/восстановление, onboarding с пустой папкой, improve-service, непустая галерея и успешное вложение. Очистка несохранённого draft при reload подтверждена; сохранение через reload интерфейс не обещает. Полный writable doctor и свежесть удалённого main не проверены. Приёмка TOOL не означает принятие всей волны улучшений.
