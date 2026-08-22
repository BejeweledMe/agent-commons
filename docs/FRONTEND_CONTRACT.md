# The frontend contract

The legacy panel is one file — `src/agent_commons/ui/static/index.html` —
served with a strict CSP and no build step. The first migrated screen, Design
Gallery, is a separately built React Flow application. Both surfaces are served
at once. Every rule below is enforced by a named test, so this document is a
map of the laws, not their only copy: break one and the suite says so. Read the
applicable section before editing a surface; otherwise the tests will teach it
to you one failure at a time.

## One file, one writer, no toolchain

- The whole panel is a single self-contained asset: one nonce'd `<style>`
  block, one `<script>` block, no external references, no npm, no bundler.
  This is a deliberate stance, not an omission — do not introduce a build
  step. (`test_the_spa_has_no_external_references`)
- One agent edits the file at a time. Take the workspace claim
  `path:src/agent_commons/ui/static/index.html` before touching it; the file
  is ~7000 lines and concurrent edits do not merge.

## Incremental React Flow migration

- Design Gallery lives at `/gallery`, alongside the legacy root, and is built
  from `frontend/gallery/` into the packaged
  `src/agent_commons/ui/static/gallery/` directory. The generated bundle is
  checked in and `ui/static/**/*` is wheel package data: cloning and launching
  the product never asks an operator to run npm. `npm ci && npm run build` in
  `frontend/gallery/` is the reproducible maintainer rebuild path.
- Do not modify the legacy asset while migrating Gallery. Its single-writer
  claim remains in force until the last legacy screen leaves it. A Gallery
  writer claims `path:frontend/gallery` and
  `path:src/agent_commons/ui/static/gallery` instead.
- The Gallery shell and its hashed same-origin assets contain no workspace data
  and may load without a bearer header; its API calls carry the bearer token
  from the URL fragment. Never put that token in a query string, an asset URL,
  source code or local storage. The Gallery document uses its own CSP limited
  to same-origin scripts/styles and has no inline script or style.
- `frontend/gallery/src/i18n.json` is the Gallery-owned paired locale source.
  A Gallery term may not be copied into the legacy string table; when a term is
  rendered by both stacks, it must first move to a shared source. The temporary
  disjoint keyspaces prevent translation drift during the incremental move.
- Every Gallery state is semantic and accessible: checking, missing bearer,
  typed backend refusal, and empty data. Before Design Package reads exist, the
  screen says so and renders no sample cards or demo screens.

## CSP-safe DOM only

- Never: `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`,
  `eval`. (`test_the_spa_never_uses_an_unsafe_dom_api`)
- Never: inline `style="…"` attributes or `setAttribute("style", …)` — the
  CSP carries no `style-src-attr`, so they are silently dropped. All styling
  goes through classes and the single nonce'd stylesheet.
  (`test_the_spa_carries_no_inline_style_the_csp_would_drop`)
- Build DOM with `createElement` / `textContent` / `setAttribute` / classes.
- SVG drawn inline takes its colours from the palette through classes, never
  through `fill="…"`/`stroke="…"`/`style` presentation attributes.
  (`test_the_guide_carries_one_picture_and_every_label_in_it_is_translated`)

## Two languages, one table

- Every user-facing string exists in BOTH `STRINGS.en` and `STRINGS.ru`; the
  parity test diffs the key sets.
  (`test_the_two_panel_languages_translate_exactly_the_same_keys`)
- An element is owned by its `data-i18n` marker OR by a JS write — never
  both. `applyI18n` runs on every stream snapshot, so a JS-written label on a
  marked element is overwritten two seconds later; for a dynamic label,
  switch the element's `dataset.i18n` key instead of its text.
- English fallback text in the markup must be a prefix of the table value for
  its key, or the source states two different sentences for one string.
  (`test_a_markup_fallback_never_contradicts_the_table_it_falls_back_to`)
- Surfaces the panel paints itself re-render through `LANGUAGE_SURFACES` on a
  language switch — and a repaint must never take an operator's half-typed
  input off screen.
  (`test_switching_language_repaints_every_surface_the_panel_paints_itself`,
  `test_a_language_switch_keeps_a_half_typed_acceptance`)

## Vocabulary is law

- One thing, one word, per language: role/роль, run/прогон, skill/навык,
  tool/инструмент, task/задача, board/доска. No transliterations (скилл,
  тулл, борд, агент, делегац…) anywhere in the Russian table.
  (`test_the_panel_uses_one_word_per_concept_in_each_language`)
- Canonical values — `deny`/`ask`/`auto`, `fresh`/`accumulated`, states like
  `succeeded`, entity kinds, profile ids — are NEVER translated. They keep
  the ledger's spelling and gain a human gloss beside them, dash-separated,
  never instead of them.
  (`test_the_russian_table_never_translates_a_canonical_value`,
  `test_a_canonical_value_shown_to_a_person_carries_its_gloss`)
- A term an operator cannot guess carries its "?" mark where the term
  appears, linking into the guide by stable heading id; the id set and the
  link set are diffed by test.
  (`test_every_guide_deep_link_lands_on_a_heading_that_actually_exists`)

## A registered route is not a usable route

A writing panel registers its whole non-`GET` surface unconditionally — the
union of `MUTATING_ROUTES`, `CATALOG_ROUTES`, `LAUNCH_ROUTES`, and
`SETUP_ROUTES` in `src/agent_commons/ui/server.py` — the instant it can hold a
session at all, before the workspace, the operator runtime config, or the
catalogue exist. A read-only panel registers none of it; that half of the
invariant is unconditional too. What changes over the panel's lifetime is
never the route table, only whether a given call succeeds: an unconfigured
environment answers a real POST with a typed 409 (`setup_uninitialized`,
`launch_not_configured`, a catalogue refusal), not a 404. Never treat a 404 as
"this panel can't do that yet" and never treat reaching a route without an
error as proof of capability — read the response body's `error.code`, the same
way `setup_state`/`launch_enabled`/`catalog_editing_enabled` on `GET
/api/setup` and `GET /api/catalog` do, and drive first-run and blocked-action
UI off those typed codes rather than off which requests happen to fail.

The current setup tuple is exactly `POST /api/setup/initialize`,
`POST /api/setup/runtime-config`, and
`POST /api/setup/add-discovered-providers`. The last route derives its only
input from trusted discovery and a byte-for-byte proof of the currently
generated config; it never receives a YAML fragment or a path from the browser.

## Honesty rules

- The panel never advances state client-side: every repaint after a write
  comes back from the server, and only acceptance may render as a green tick.
  (`test_only_acceptance_may_render_as_a_green_tick`)
- A budget is the cap a run was permitted, never a spend — nothing measures
  consumption, so nothing may display one.
  (`test_a_run_card_says_when_it_ran_and_never_invents_a_spend`)
- Nothing here stores prompts or transcripts. The Runs surface may show the
  private attempt store's sanitized final 4 KiB stderr tail for an unsuccessful
  process and bounded terminal-tool rejection reasons drawn from a fixed
  allowlist of the backend's own refusal strings (anything else arrives as a
  fixed withheld-details notice). It never receives stdout, successful-run
  stderr, or tool arguments; truncation and redaction must be visible beside
  the diagnostic.

## Testing the asset

- The suite reads the SPA as text (`read_spa()`) and pins behaviour by
  asserting on real functions, real string tables, and real markup — when a
  claim is behavioural, the actual function runs under node.
- Node harness scripts travel over stdin (Linux caps argv elements at
  128 KiB and the STRINGS table is past it), and user arguments start at
  `process.argv[2]` for a stdin program.
- The bulk of the enforcement lives in `tests/ui/test_stream_and_packaging.py`
  with the rest of `tests/ui/` beside it; grep for the test names quoted
  above to find the exact assertions.
