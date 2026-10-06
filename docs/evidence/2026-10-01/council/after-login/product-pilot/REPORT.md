# Реальный Astra frontend product pilot

**Продукт создан реальным брокерным worker и независимо проверен.** Это отдельный временный проект, не изменение основного Commons и не приёмка всей программы. Продукт: Engineering Desk — адаптивный HTML/CSS/JS overview с3 агентами,6 задачами,4 результатами, комбинированными фильтрами и просмотром результата. Все данные явно обозначены как demo.

## Запуск и две честно разделённые попытки

Точный модельный профиль `gpt-6-astra`, официальный временный `codex-cli 0.159.3`, Python/MCP source SHA `fab6f03a9ca61162e4495cc8758c662cf4720a7588d7cbee169b8e2c4dcc274e`. Profile YAML явно фиксирует модель; CLI создания роли не имеет отдельного model argument, поэтому роль наследует этот pin. Это не независимая аттестация внутренней provider-модели и не сравнительный eval.

Preflight прошёл; qualification canary в собственном state-root прошёл47.208s,1/1/0 terminal, без mismatch. Первый настоящий product attempt закончился через73.343s с `needs_operator`: обычный CLI отсутствовал в PATH и дефолтный Python не импортировал Commons. Worker корректно не выдумал реализацию. Terminal audit2 calls/1 completion/1 rejection, provider exit0 при canonical needs_operator — сохранённый неуспех. Подробности и минимальное provisioning-исправление: `provisioning-diagnosis.md`; исходный результат сохранён в `attempt-1/`.

После отдельного разрешения создана новая delegation против новой точной task revision. В brief добавлен существующий абсолютный same-source CLI только для read-only onboarding. Ранее созданная child session и scoped MCP для canonical writes сохранены; backend, sandbox, credentials и постоянные профили не изменялись.

Успешная попытка: `delegation.6F04S76ZY67CWR8PWT03AJZD1P`, result revision `evt.01M3VM26TZ63VRMT14E7MRGMKH`; duration623.080s, exit0, canonical `succeeded`, mismatch=false, terminal **1 call / 1 completion / 0 rejection**. Лимиты:1200s, provider_units1, attempts1, concurrency1, depth0. Exact target `task.37536WFDJPKD7YA5X3433WEXTP` @ `evt.01M3VKFBHMQ3HFECZ1JV3F0PW6`. Terminal result_refs содержит этот task, как предписывает generated worker contract. Изображения — отдельные реальные canonical artifacts, не подставные terminal events.

## Реальные файлы и независимые проверки

`repo/index.html`, `styles.css`, `app.js`, `build.py`, `check.py`, `scripts/`, `README.md`; воспроизводимый build в `repo/site/`. `product-files-manifest.json` фиксирует SHA/size19 файлов. Координатор прочитал build/check и повторно выполнил `check.py`: deterministic build, local assets, JS syntax и14 data/filter assertions прошли. Новых зависимостей/npm пакетов нет; проверке нужен существующий Node, сборке — стандартный Python.

Worker не смог запустить браузер: loopback bind отказан sandbox, отдельный Chrome(file URL) завершился134. Он честно создал AppKit design previews с видимой маркировкой, не объявляя их screenshots. Координатор отдельно поднял только loopback static server и через **CUA Chrome** проверил настоящий HTML:

- Реальные viewport1440/390/320 CSS px, horizontal overflow отсутствует.
- Mira:2 задачи/1 результат; комбинированный пустой фильтр:0/0; Reset:6/4.
- Dialog показывает producer/task/type; Enter открывает, Escape возвращает фокус на opener; Tab из поиска переходит к status select.
- Captured browser warnings/errors:0. Это ограниченная функциональная проверка, не полный accessibility audit.

Настоящие CUA screenshots: `independent-overview.jpg`, `independent-mobile.jpg`, `independent-narrow.jpg`; подробности `independent-browser-checks.json`. Они **отдельны** от worker AppKit PNG.

## Worker publication и реальная галерея Commons

| Worker artifact | Файл | SHA-256 |
|---|---|---|
| artifact.371604EAKB48D88Q437GSVH1R5 @ evt.01M3VM0MA2HGGCJCGS0DEA4SR3 | outputs/engineering-desk-desktop-v2.png;1440×1620;224810 bytes | 3deaed840d19d68f19326ba5aee9806b1853abf3ab56628cf0183e7080503e55 |
| artifact.31GVB7CHNC5YF2W1746SFHPEPC @ evt.01M3VM0MF76D3HE2M3MN6VGR17 | outputs/engineering-desk-mobile-v2.png;390×2450;169728 bytes | 586f60f0c19f47b123489d2ec3e04199941062ec9c978a20d42be4a1c68d98c3 |

Оба опубликованы worker через `commons_publish_design_image`; manifest SHA/size и независимый `ArtifactPreviewReader.read` прошли. Task и agent outputs projections возвращают оба как `ready`, с точными task/agent/delegation/session bindings, review_state=null. Versioned bytes не переписаны; v1 — неопубликованные отброшенные render attempts, не история подтверждённых версий.

Дополнительно открыт **поставляемый Commons `/work`**, с `ui --single-project --read-only`, только synthetic repo/state. Через CUA клик по имени/аватару Astra Frontend Pilot открыл настоящую непустую галерею. Оба image elements complete=true, natural dimensions соответствуют PNG; видны task title, agent name и честные AppKit labels. Скриншоты `commons-agent-gallery.jpg` и `commons-agent-gallery-provenance.jpg`; `commons-gallery-checks.json`. Это UI с реальными provider outputs, не mocked fixture. UI canonical writes отсутствовали; глобальный registry не использовался.

## Границы

Публикация metadata-only: PNG bytes остаются в temp repo. Это не новая durable build-storage система, не live-preview hosting и не exact-output independent approval (G5/G7 не закрыты целиком). Frontend demo не подключён к production data. Успешная ограниченная попытка после provisioning не доказывает стабильность профиля или превосходство Astra. Постоянная конфигурация модели не переключалась.

Child sessions закрыты. Claims и собственные parent sessions освобождены/закрыты; итог — `server-cleanup.json` и `retry-parent-session-ended.json`. Временные браузерные вкладки закрыты и viewport override сброшен; оба verification servers остановлены. Synthetic task остаётся не принятым; lifecycle outcome worker не заменяет независимую приёмку.

Для переноса использовать только `safe-artifact-list.json`/`promotable/`. Не переносить state/, private session/claim files, UI one-time access log, browser/Swift caches или полные provider outputs.
