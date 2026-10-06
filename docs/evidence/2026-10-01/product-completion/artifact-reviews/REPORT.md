# Настоящие Astra reviews сохранённых результатов

Оба review выполнены через Commons broker и worker-scoped MCP на source
`9b3368df09bceb1a5196663937886fbc0c7705ee3b814562f771463763868196`.
Выбран `gpt-6-astra`, официальный Codex CLI 0.159.3 из постоянного versioned
каталога. Это подтверждённый profile pin, а не независимая аттестация внутренней
модели провайдера. Изолированный workspace имеет собственный успешный
[canary reviewer](reviewer-canary.json), 1/1/0 terminal audit.

PNG и build — явно маркированная synthetic фикстура. Provider действительно
проверял их содержимое; их создание моделью не заявляется. Оригиналы были
удалены до проверки. Координатор отдельно подтвердил отсутствие исходников и
hashes сохранённых bytes; reviewer честно не приписал эту проверку себе.

| Объект | Канонический результат | Проверенное содержимое |
| --- | --- | --- |
| Image v2 `artifact.0KN7CWA64CB305GW4RV08J9ZRP` | `review.1QYYHES3F1GAB21TYAC0F4S5V2`, approved; delegation `delegation.2AJE23Q2K33QBMFY48E46ZNA1B` succeeded | Реальные PNG pixels через `commons_read_output_image`: VERSION 2, тёмная sidebar, три карточки, синий квадрат в нижней карточке |
| Static build `artifact.6DZNY3RA52CSWSWDGJVV4A2B4X` | Exact artifact review approved; delegation `delegation.7Y72Q9YHMGKXKP3P37J7CFVMHJ` succeeded | Manifest и полностью прочитанные index.html, app.js, style.css через `commons_read_build_file`; reader подтвердил complete content |

Каждый запуск имел отдельную попытку, provider_units=1, 600 секунд, depth=0;
оба завершились exit 0, одним успешным `commons_finalize_review`, без
отклонённых terminal-вызовов и process/canonical mismatch. Canonical actor —
отдельный child session, target revision — точная revision артефакта.

Image v1 не получила чужое approval. Проверка производящей design task
осталась requested. Build approval ограничен статическим чтением: визуальный
вид бинарного PNG и выполнение JavaScript в браузере reviewer не проверял.
Одобрение этих артефактов не является приёмкой задач или продукта.

Полные безопасные receipts и summaries: [image](real-review-image2-verified.json),
[build](real-review-build-verified.json). Результаты broker:
[image run](real-review-image2-run.json), [build run](real-review-build-run.json).
[Проверка состояния после обоих review](verification.json) подтверждает
сохранность bytes, раздельные containment/dependency связи и отсутствие
автоматического одобрения task/v1. Полный provider stdout и private reasoning
в evidence не сохранены.

После review Chrome повторно открыт через новую ссылку без очистки storage.
В карточках v2 показано «Exact result: check approved», v1 — «no recorded check»,
у обеих «Current task: awaiting check». [Снимок статусов](../browser/exact-review-statuses-en.png).
