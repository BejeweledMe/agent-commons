# The frontend contract

The panel is one file — `src/agent_commons/ui/static/index.html` — served
with a strict CSP and no build step. Every rule below is enforced by a named
test, so this document is a map of the laws, not their only copy: break one
and the suite says so. Read this before editing the asset; otherwise the
tests will teach it to you one failure at a time.

## One file, one writer, no toolchain

- The whole panel is a single self-contained asset: one nonce'd `<style>`
  block, one `<script>` block, no external references, no npm, no bundler.
  This is a deliberate stance, not an omission — do not introduce a build
  step. (`test_the_spa_has_no_external_references`)
- One agent edits the file at a time. Take the workspace claim
  `path:src/agent_commons/ui/static/index.html` before touching it; the file
  is ~7000 lines and concurrent edits do not merge.

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

## Honesty rules

- The panel never advances state client-side: every repaint after a write
  comes back from the server, and only acceptance may render as a green tick.
  (`test_only_acceptance_may_render_as_a_green_tick`)
- A budget is the cap a run was permitted, never a spend — nothing measures
  consumption, so nothing may display one.
  (`test_a_run_card_says_when_it_ran_and_never_invents_a_spend`)
- Nothing here stores prompts or transcripts. The Runs surface may show the
  private attempt store's sanitized final 4 KiB stderr tail for an unsuccessful
  process and bounded sanitized terminal-tool rejection messages. It never
  receives stdout, successful-run stderr, or tool arguments; truncation and
  redaction must be visible beside the diagnostic.

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
