# Независимая проверка интегрированной UX-волны, 2026-10-09

Verdict: changes_requested.
Reviewer: session.456c6d4bf29e414ca341300fc73787f5 (distinct from requester/implementer session.50ac37a8c53042eba5db58b14e5b5da8).
Target: artifact.6ZQJYE3C9Y7EE5HJHBE1GYT2E2 @ evt.01M4GTBXK1HK8PGW2YZM2DGVTR.
Review: review.5G1WZ5XHCXKQ7XDKA6C7E4JPMT @ evt.01M4GTEFY7H7HFYDJDH8WPQB6M.

## Finding

[P2] Сохранить имя задачи в доступном имени кнопки на дальнем масштабе обеих SVG-карт.

`frontend/work/src/taskGraph.css:90` скрывает `.task-graph-node-title` через `display:none` в band `far`. Кнопки в `frontend/work/src/components/TaskGraph.tsx:119` и `frontend/work/src/components/TaskHierarchy.tsx:113` не имеют `aria-label`/`aria-labelledby`; после скрытия заголовка их accessible name строится только из оставшихся состояния (Dependencies) или типа и состояния (Structure). Непустое имя из содержимого имеет приоритет над `title`, поэтому однотипные задачи становятся неразличимы для экранного диктора. На первом Tab фокусе selected strip тоже не помогает: `onFocusTask` вызывается только в Arrow/Home/End обработчике, обычного onFocus у этих кнопок нет.

Сценарий: несколько ready-задач, fit либо zoom <30%, Tab на узел Dependencies/Structure. Вместо конкретной задачи слышен одинаковый статус. Это регрессия доступности от нового display:none, не изменение canonical state.

Исправление: добавить явное title-bearing доступное имя каждой task button в обоих компонентах (с fallback taskId и при желании human state), либо сохранить заголовок в accessibility tree через визуальное скрытие. Нужна регрессия, проверяющая имя с применённым far-band CSS, а не только наличие title в SSR. Дополнительно интегратор подтвердил браузером в синтетическом fixture на 390px: первые три SVG-кнопки имели aria-label=null, title с реальным именем задачи, `.task-graph-node-title` display=none, innerText только «Готово к запуску». Это сообщение интегратора, не моя независимая браузерная запись.

## Проверенные границы

172/172 файлов реального checkout совпали с `review-manifest-v1.json` до review. 172/172 файлов приватного снимка совпали с его MANIFEST.json. Checkout и canonical ledger интегратор держал без записи во время проверки; reviewer только зарегистрировал свою сессию и завершает existing review. Исходники изменённых компонентов, wave.diff относительно pre-wave baseline, связанные модели/потребители прочитаны непосредственно.

Самостоятельно выполнены 211 focused Node24 checks, 0 failures, 0 skipped: agent-settings (no-op, split drafts/acknowledgment, exact CAS/uncertain retry, method historical refs), board-state/task-picker (explicit confirmation, projection loading/error, pending launch restoration), map-zoom-bands (minimal pan, no-op visible focus, resize scale/center arithmetic), task-reading-surfaces (late ignored-abort library reads/files, safe prose, criteria), locale preference (ru/en allowlist + corrupt/blocked storage), task-graph, task-inspector, conversations, provider-availability, work-shell. Commands ran against the private snapshot, emitted temporary compilation outside checkout, and did not build assets.

No additional concrete P1/P2 regressions found in settings save scopes, library late completion, board launch selection ordering, locale/auth storage boundary, or unknown-recipient chat write gating. `prepareLaunch` plus queued `boardLaunchDraft` preserves the exact matching pending intent and does not change a different pending intent object; opening picker is read-only. Agent methods retain exact version options, multiline values, and RAM drafts. Locale code writes only the fixed locale key and whitelisted values; existing authentication still uses sessionStorage. Conversation changes are presentation-only and preserve canWrite/canSubmit logic.

Task brief native disclosure remains mounted for the same task through the retained description in TaskResults; short/long threshold changes can legitimately replace its DOM. Criteria are outside the brief disclosure. Task historical run messages are labelled as last-run context, while task next-action prose retains the task's current projection.

## Limits

This is source/code judgment and selected reproducible behavior verification, not user product acceptance. I did not reproduce all width/card-count/keyboard matrices in a live browser, inspect private user histories/logs, run backend full make check, or independently rebuild/package assets. Asset bytes were hash-verified; build correctness/full suite remain integration evidence. Geometry, CSS tooltip clipping, native browser focus return, cross-app reload, and 14/40/128 task or 7/20/50 agent usability require the separate synthetic browser acceptance evidence. The 211 existing tests passed but did not detect the far-band accessible-name defect; passing tests do not approve this revision.

Browser corroboration supplied by integrator after source freeze: `/private/tmp/oct09-ux-implementation/browser-evidence/far-accessibility-before.txt` contains DOM/accessibility evidence. Dependencies image children include nine repeated «Готово к запуску» role buttons and five repeated «В очереди» role buttons; title-bearing descendants are display:none and aria-label is null. Evidence was produced by the integrator and is attributed as such.

Canonical result: review.completed evt.01M4GTTDJAZMXJ74R2M0DDXV1Q, idempotency receipt key ux-implementation-v1-independent-review-20261009; existing review review.5G1WZ5XHCXKQ7XDKA6C7E4JPMT finalized as changes_requested against exact target evt.01M4GTBXK1HK8PGW2YZM2DGVTR.
