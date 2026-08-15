"""Round 4, finding 7 and finding 10: an order on the board a person can name,
and a page that says what the system's units actually are.

Everything except the determinism check is read out of the single frontend
asset, the way the rest of the panel's invariants are.  The determinism check
runs the real `layout()` under node, because "the same snapshot lays out the
same way" is a claim about behaviour and a grep cannot make it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.graph import _TERMINAL_STATES
from agent_commons.ui.server import _ENTITY_KINDS


def _language_tables() -> tuple[str, str]:
    table = read_spa().split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    return (
        table.split("en: {", 1)[1].split("\n  },", 1)[0],
        table.split("ru: {", 1)[1].split("\n  },", 1)[0],
    )


def _value(block: str, key: str) -> str:
    match = re.search(rf'^\s*{key}: "(.*)",$', block, re.MULTILINE)
    assert match, key
    return match.group(1)


def _in_both_tables(*keys: str) -> None:
    for block in _language_tables():
        for key in keys:
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


def _function(body: str, header: str) -> str:
    """One top-level function, header included, up to its closing brace."""

    assert header in body, header
    return header + body.split(header, 1)[1].split("\n}\n", 1)[0] + "\n}\n"


# --- finding 7: the order, and the words for it -----------------------------


def test_a_rank_lays_out_in_one_named_order_that_no_language_can_move() -> None:
    """The operator's complaint was "непонятная сортировка": a rank kept
    whatever order the projection handed back.  The order is now stated in one
    comparator -- kind, then whether it is settled, then when it was recorded,
    then the id -- and none of those four keys is a name, so the language
    toggle cannot restack the board."""

    body = read_spa()

    placement = _function(body, "function layout(nodes) {")
    assert "const members = bands.get(band).slice().sort(bandCompare);" in placement

    compare = _function(body, "function bandCompare(left, right) {")
    # Roles first, then work, then everything else -- and the three buckets are
    # named where a reader will look for them rather than inlined as magic.
    assert 'const BAND_KIND_ORDER = { agent: 0, task: 1 };' in body
    assert "bandKindRank(left) - bandKindRank(right)" in compare
    assert "BAND_SETTLED.has(left.state)" in compare
    assert "left.recorded_at" in compare and "right.recorded_at" in compare
    assert "String(left.id) < String(right.id)" in compare

    # The whole point of the rewrite: no collation anywhere near the board.  A
    # `localeCompare(lang)` would move cards when the language moved, which is
    # the reported complaint in a harder-to-explain form.
    assert ".localeCompare(" not in body

    # One notion of "finished" across the seam: the panel's set is the server's.
    settled = body.split("const BAND_SETTLED = new Set([", 1)[1].split("]);", 1)[0]
    assert set(re.findall(r'"(\w+)"', settled)) == set(_TERMINAL_STATES)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run layout() in")
def test_the_same_snapshot_lays_out_the_same_way_however_the_nodes_arrive() -> None:
    """The acceptance a grep cannot give: shuffle the input and the placement
    has to come back identical, because the comparator's last key is the id and
    ids are unique, so the order it defines is total."""

    body = read_spa()
    harness = "\n".join(
        [
            "const COLUMN = 210, ROW = 118;",
            # The one thing layout() reaches out of itself for.
            "function visibleBoardRect() { return { width: 1280, height: 800 }; }",
            # The ordering slab: the two tables, the rank helper and the
            # comparator all sit between the constant and layout() itself.
            "const BAND_KIND_ORDER"
            + body.split("const BAND_KIND_ORDER", 1)[1].split(
                "function layout(nodes) {", 1
            )[0],
            _function(body, "function bandColumns(count) {"),
            _function(body, "function layout(nodes) {"),
            "const placed = layout(JSON.parse(process.argv[1])).placed;",
            # A Map keeps insertion order, and insertion order IS the order the
            # comparator produced -- so this carries both the coordinates and
            # the sequence, and a shuffle has to reproduce both.
            "console.log(JSON.stringify([...placed]));",
        ]
    )

    def place(nodes: list[dict[str, object]]) -> object:
        done = subprocess.run(
            ["node", "-e", harness, "--", json.dumps(nodes)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(done.stdout)

    nodes = [
        {"id": "01T", "kind": "task", "state": "ready", "recorded_at": "2026-08-01", "band": 1},
        {"id": "01A", "kind": "agent", "state": "active", "recorded_at": "2026-08-03", "band": 1},
        {"id": "01D", "kind": "delegation", "state": "succeeded",
         "recorded_at": "2026-08-02", "band": 1},
        {"id": "01B", "kind": "agent", "state": "retired", "recorded_at": "2026-08-01", "band": 0},
        {"id": "01C", "kind": "task", "state": "accepted", "recorded_at": "2026-07-30", "band": 1},
        {"id": "01E", "kind": "session", "state": "", "recorded_at": "2026-08-04", "band": 0},
    ]
    expected = place(nodes)
    for rotation in range(1, len(nodes)):
        assert place(nodes[rotation:] + nodes[:rotation]) == expected, rotation
    assert place(list(reversed(nodes))) == expected

    # And the order is the one the legend promises: inside rank 1 the role comes
    # before the live task, the live task before the accepted one, and the run
    # -- neither role nor task -- last.
    inside = [entry[0] for entry in expected if entry[1]["band"] == 1]
    assert inside == ["01A", "01T", "01C", "01D"], inside


def test_a_rank_caption_says_what_the_row_is_without_naming_a_kind() -> None:
    """`_reporting_ranks` (ui/graph.py) files work into the same band as roles,
    by `owner_session_id`.  So a caption that said "hired by the rank above"
    would be a lie about half the cards -- and, until now, there was no caption
    at all unless the runtime nodes were switched on."""

    body = read_spa()
    captions = body.split("// Rank captions,", 1)[1].split("for (const edge of graph.edges)", 1)[0]

    # Drawn for every rank, runtime or no runtime: the old early-`continue` that
    # skipped every band but zero is gone.
    assert "!showRuntime) { continue; }" not in captions
    # Computed from the rank, never from the node that happened to come first.
    assert 't("band_rank").replace("{n}", position.band)' in captions
    assert "node.kind" not in captions

    _in_both_tables("band_rank", "band_operator", "legend_bands")
    english, russian = _language_tables()

    # The guard the plan asks for: a rank holding a task and a role at once must
    # not be described as a hire.  The caption cannot say it in any language.
    for word in ("нанят", "нанял", "hired", "hires"):
        assert word not in _value(russian, "band_rank").lower(), word
        assert word not in _value(english, "band_rank").lower(), word
    # It says what IS true of a role, a task and a run alike.
    assert "заведено или запущено" in _value(russian, "band_rank")
    assert "set up or started" in _value(english, "band_rank")

    # The legend gained the row rule, and the board's hint one sentence about
    # it: one line in each place, beside the mark key it already carried.
    panel = body.split('<div id="legend-panel"', 1)[1].split("\n      </div>", 1)[0]
    assert panel.count('data-i18n="legend_bands"') == 1
    assert panel.count('data-i18n="legend_note"') == 1
    for block in (english, russian):
        assert "localeCompare" not in _value(block, "legend_bands")
    assert _value(russian, "board_hint").count(".") == 3
    assert _value(english, "board_hint").count(".") == 3


def test_the_board_carries_its_own_mark_into_the_guide() -> None:
    """The rows are the one thing on the board with a meaning and no words, so
    the caption gets the same "?" every unguessable term in the chrome carries.
    A caption is SVG, so the mark is drawn rather than styled -- but the two
    data attributes are identical, which is all the delegated listener reads."""

    body = read_spa()
    mark = _function(body, "function bandHelpMark(y) {")
    assert 'mark.setAttribute("data-guide-page", "agents");' in mark
    assert 'mark.setAttribute("data-guide-anchor", "g-ag-lineage");' in mark
    assert 't("gref_bands")' in mark
    # The anchor is a heading that exists, on the page named beside it.
    agents = body.split('<div id="gpage-agents"', 1)[1].split("\n        </div>", 1)[0]
    assert 'id="g-ag-lineage"' in agents

    # `closest` walks up from the circle or the glyph to the group that carries
    # the attributes, so the ONE listener catches it unchanged.
    assert 'const link = event.target.closest("[data-guide-anchor]");' in body
    # ... and a pan that ends over the mark is a pan, not a click on it.
    assert "if (suppressClick && canvas.contains(link)) { return; }" in body

    # Drawn with the palette through classes: a presentation attribute or a
    # `style` would put it out of the stylesheet's reach, and the CSP would drop
    # the second one on the floor.
    for forbidden in ('fill="', 'stroke="', "style="):
        assert forbidden not in mark, forbidden
    for rule in (".bandhelp{cursor:pointer}", ".bandhelp-disc{", ".bandhelp-q{"):
        assert rule in body, rule

    _in_both_tables("gref_bands")


# --- finding 10: the units, named -------------------------------------------


def test_the_units_page_names_every_kind_the_panel_can_open() -> None:
    """The operator asked what the units of the system are.  The authoritative
    list is `_ENTITY_KINDS`, and the page is checked against it rather than
    against a copy of it, so a kind added to the server cannot go missing from
    its own glossary."""

    body = read_spa()
    english, russian = _language_tables()

    for block in (english, russian):
        listed = set(re.split(r"\s*·\s*", _value(block, "guide_ov_kinds_ex")))
        assert listed == set(_ENTITY_KINDS), listed ^ set(_ENTITY_KINDS)
        # Twelve projections and one operational record: the page says which is
        # which, because `session` is the one that expires on its own.
        assert "session" in _value(block, "guide_ov_kinds_p")

    assert 'data-i18n="guide_ov_kinds_ex"' in body


def test_the_units_page_separates_the_four_pairs_an_operator_confuses() -> None:
    """Finding 10 names them, and finding 9 is what happens without them: the
    composer's two fields read as one question asked twice because nothing said
    that a conversation and a piece of work are different records."""

    body = read_spa()
    page = body.split('<div id="gpage-units"', 1)[1].split("\n        </div>", 1)[0]

    # A reference page like its neighbours: it says so at its own top, because a
    # reader arriving by deep link never passes the strip.
    assert 'data-i18n="guide_ref_lead"' in page
    for anchor in ("g-ov-kinds", "g-ov-task", "g-ov-agent", "g-ov-delegation", "g-ov-review"):
        assert f'<h3 id="{anchor}"' in page, anchor
    # Every string on it is a marker, and none is also written into the markup:
    # an element is owned by `data-i18n` or by a JS write, never both, and a
    # hardcoded English default would show through under a Russian panel.
    elements = re.findall(r"<(p|h3)\b([^>]*)>([^<]*)</\1>", page)
    # One reference lead, then five headings each with its paragraph and its
    # example, in the shape the four pages beside it already use.
    assert len(elements) == 16, elements
    for _, attributes, shown in elements:
        assert "data-i18n=" in attributes, attributes
        assert shown.strip() == "", shown

    english, russian = _language_tables()
    for stem in ("kinds", "task", "agent", "delegation", "review"):
        _in_both_tables(f"guide_ov_{stem}_h", f"guide_ov_{stem}_p", f"guide_ov_{stem}_ex")

    # The canonical names are the ledger's own spelling in BOTH languages: this
    # page's whole job is teaching that vocabulary, which is the opposite of
    # translating it away (item 20).
    for pair in (("task", "thread"), ("agent", "session"),
                 ("delegation", "task"), ("review", "verification")):
        stem = pair[0]
        for block in (english, russian):
            heading = _value(block, f"guide_ov_{stem}_h")
            assert heading.startswith(pair[0] + " "), heading
            assert pair[1] in heading, heading

    # And the Russian page keeps the panel's one-word-per-concept discipline:
    # the canonical names are Latin, the human words beside them are the
    # glossary's own.
    for stem in ("kinds", "task", "agent", "delegation", "review"):
        for suffix in ("h", "p", "ex"):
            line = _value(russian, f"guide_ov_{stem}_{suffix}").lower()
            for forbidden in ("агент", "делегац", "скилл", "тулл", "борд"):
                assert forbidden not in line, (stem, suffix, forbidden)

    # The pair that fixes finding 9 is spelled out, not implied.
    assert "ни одно сообщение ничего не завершает" in _value(russian, "guide_ov_task_p")
    assert "no message finishes anything" in _value(english, "guide_ov_task_p")


def test_the_units_tab_opens_the_reference_side_of_the_strip() -> None:
    """It defines vocabulary rather than teaching a route, so it belongs after
    the "Reference" divider -- and it goes FIRST there, because the four pages
    behind it spend the words it settles."""

    body = read_spa()
    strip = body.split('id="guide-tabs"', 1)[1].split("</div>", 1)[0]
    assert strip.index("tabsplit") < strip.index('data-gpage="units"')
    for later in ("agents", "tasks", "links", "limits", "catalog"):
        assert strip.index('data-gpage="units"') < strip.index(f'data-gpage="{later}"'), later
    _in_both_tables("guide_tab_units")

    # No handler of its own: the one tab listener and `guideShowPage` cover it
    # because the page id is derived from the tab's own dataset.
    assert 'document.getElementById("gpage-" + tab.dataset.gpage).hidden = !active;' in body


def test_a_mark_no_longer_promises_a_page_it_may_not_open() -> None:
    """Round 4, finding 1 moved the answer into the dialog: inside a form the
    mark now opens a popover in place, and the Overview is behind a second
    button.  Eight strings still said the mark opened the Overview, which had
    become untrue in exactly the case the popover was built for."""

    english, russian = _language_tables()
    marks = [
        "gref_grants", "gref_context", "gref_turnover", "gref_accept",
        "gref_link_type", "gref_skills", "gref_tools", "gref_run_states",
    ]
    for key in marks:
        assert _value(english, key).endswith("The full account is in the Overview.")
        assert _value(russian, key).endswith("Полный разбор — в «Обзоре».")
    # The claim that became false is gone from both tables entirely.
    assert "Opens the Overview." not in "\n".join(_language_tables())
    assert "Откроет «Обзор»." not in "\n".join(_language_tables())

    # The popover shows exactly these strings and adds only its two words, so a
    # tail that reads correctly here reads correctly in both places.
    body = read_spa()
    assert 'document.getElementById("gpop-text").textContent = t(gpopKey);' in body
    _in_both_tables("gpop_more", "gpop_close")
