# ADR 0022: Retire the legacy single-file panel

Date: 2026-09-18. Status: accepted by the owner (decision scope
`repo/legacy-panel-retirement`, `decision.719S9ZV8HZ9G9YNR4WV02KGAQK`);
implementation is work package C6 of the
[consolidation plan](../plans/2026-09-18-consolidation-and-wave-4-plan.md) and
has not landed yet.

## Context

`src/agent_commons/ui/static/index.html` is the original hand-authored panel:
8 696 lines, one nonce'd stylesheet and one script, its own bilingual string
table and 16 Python test modules that read it as text through `read_spa()`. It
is served only at `/` of a non-project host (`hosted=False`); every project host
is created with `hosted=True` and never serves it. The CLI opens `/work`, the
compiled Work application, which since ADR 0014, 0019 and 0020 holds every
product surface: board, tasks, agents, library, settings, results and
conversations.

The panel still shaped the engineering rules: `AGENTS.md` and the frontend
contract described it as "the UI asset" with a single-writer claim and a
no-bundler stance, which contradicted the Vite-built Work and Gallery
applications and sent new agents to edit a file the product does not show.

## Decision

1. The legacy panel leaves the product. `GET /` answers with a redirect to
   `/work` (preserving the `#c=` exchange fragment) on a non-project host and
   keeps its current behaviour on project hosts, which never served it.
2. `src/agent_commons/ui/static/index.html`, `read_spa()` and the tests that
   exist only to pin the legacy asset are removed. Rules that were true of the
   legacy file alone (one file, no toolchain, `STRINGS` table, `data-i18n`
   markers) leave `docs/FRONTEND_CONTRACT.md`; rules that protect the product
   (CSP-safe DOM, two languages, canonical values untranslated, green only for
   acceptance, honest run diagnostics) stay and are enforced by the Work
   harnesses.
3. The single-writer rule survives only in its useful form: one asset
   integrator per wave holds `path:src/agent_commons/ui/static/work` while the
   Work bundle is rebuilt.
4. Any capability the panel offered that Work does not yet offer is listed in
   the C6 package before removal and either lands in Work, is served by the
   CLI, or is explicitly dropped with a changelog entry. The current list from
   the user guides: answering `input_needed` from the browser (already
   impossible in the headless runtime; runs become `needs_operator`), the
   "Runs", "Attention", "Main chat" and "Role catalogue" views (covered by the
   task inspector, the Tasks tab, project conversations and the Library), and
   "Record → Edit task" (covered by the task editor).

## Consequences

- The wheel shrinks by the asset and its CSP nonce path is used only by Work.
- `tests/ui/test_board_order_and_units_guide.py`, `test_removed_switches.py`
  and the other `read_spa` modules are deleted or rewritten against Work.
- `docs/THREAT_MODEL.md` drops the legacy shell from its served surfaces.
- Rollback is a revert of the C6 commits; the asset remains in Git history and
  in the tag `archive/2026-09-pre-consolidation`.

## Alternatives

- Keep the panel at `/` as an explicitly labelled fallback: rejected by the
  owner on 2026-09-18 because it is unreachable from a project, costs a parallel
  test suite and keeps a contradictory contract alive.
- Migrate the remaining panel screens one by one before removal: unnecessary,
  since the product surfaces already exist in Work; gaps are handled by rule 4.
