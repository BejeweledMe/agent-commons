"""What a second panel on the same project is told, and how.

`GET /api/meta` carries `session_refusal`: always present, null in the ordinary
case, and a coded refusal the panel SURVIVED when it is not.  The state it was
added for is `panel_already_open` -- a second window on a project the first one
owns.  That window boots, reads, and refuses every write, and until now nothing
on it said why: the operator met the refusal one form at a time.

The address is the part worth pinning hardest.  It is host and port, and it is
deliberately NOT a link: each panel's bearer token lives only in its own tab's
URL fragment, so a link assembled here would open a page with no way to
authorize itself, and a panel that is merely busy would read as broken.  The
server can also report no address at all -- the lock file holding the first
panel's port may be unreadable -- and a blank chip is not an answer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.context import PANEL_ALREADY_OPEN, PANEL_ALREADY_OPEN_ACTIONS


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


def _function(body: str, header: str) -> str:
    return header + body.split(header, 1)[1].split("\n}\n", 1)[0] + "\n}"


def _banner_markup() -> str:
    return read_spa().split('<div id="panel-refusal"', 1)[1].split("</div>", 1)[0]


def test_the_address_is_an_address_and_the_asset_carries_no_way_to_make_it_one() -> None:
    """The decision, pinned as a decision.  A future reader looking at a host
    and a port beside a "use that window" sentence will want to make it
    clickable; this is the test that stops them, and the note beside it is the
    reason -- the token is in the other tab's fragment and cannot travel."""

    body = read_spa()
    banner = _banner_markup()
    # An id chip, like every other canonical string the operator has to copy.
    assert '<code class="idchip" id="panel-refusal-address">' in banner
    # And no anchor anywhere in the banner, nor any href assembled for it.
    assert "<a " not in banner
    paint = _function(body, "function paintPanelRefusal() {")
    for forbidden in ("href", 'createElement("a")', "window.open", "location."):
        assert forbidden not in paint, forbidden
    # The address is set as text, never as a destination.
    assert "chip.textContent = address;" in paint

    # And the reason travels with it, in both languages, where the operator
    # meets the address rather than in a comment only a maintainer reads.
    english, russian = _language_tables()
    assert "does not make it a link" in _value(english, "panel_refusal_not_a_link")
    assert "не делает его ссылкой" in _value(russian, "panel_refusal_not_a_link")


def test_a_refusal_with_no_address_says_so_instead_of_showing_an_empty_chip() -> None:
    """`address` is null when the lock file that records the first panel's port
    cannot be read.  The server refuses to guess, so the panel must not print
    the guess either -- and an empty chip beside "the panel that owns this
    project:" would read as the panel having lost it."""

    body = read_spa()
    paint = _function(body, "function paintPanelRefusal() {")
    assert 'document.getElementById("panel-refusal-where").hidden = !address;' in paint
    assert 'document.getElementById("panel-refusal-nowhere").hidden = Boolean(address);' in paint

    english, russian = _language_tables()
    # It says the window still exists: the missing thing is the address, not
    # the panel, and an operator told otherwise would go looking for a bug.
    assert "still there" in _value(english, "panel_refusal_nowhere")
    assert "всё равно существует" in _value(russian, "panel_refusal_nowhere")


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run paintPanelRefusal() in")
def test_the_banner_is_absent_in_the_ordinary_case_and_drawn_only_by_a_refusal() -> None:
    """`session_refusal` is null on every healthy panel, so an absent banner is
    the ordinary case.  Run rather than read, over the real string table and
    the real function, in all three shapes `/api/meta` can send.

    The script travels over stdin -- it embeds the whole STRINGS table, past
    Linux's 128 KiB per-argument ceiling -- so user arguments start at
    `process.argv[2]`.
    """

    body = read_spa()
    harness = "\n".join(
        [
            "let lang = process.argv[2];",
            "const STRINGS = {"
            + body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
            + "\n};",
            _function(body, "function t(key) {"),
            "const REFUSAL_CODES = {"
            + body.split("const REFUSAL_CODES = {", 1)[1].split("};", 1)[0]
            + "};",
            "function node(id) { return { id: id, hidden: false, textContent: '', title: '',"
            " children: [], firstChild: null,"
            " appendChild(child) { this.children.push(child);"
            " this.firstChild = this.children[0]; },"
            " removeChild(child) {"
            " this.children = this.children.filter((each) => each !== child);"
            " this.firstChild = this.children[0] || null; } }; }",
            "const nodes = {};",
            "const document = { getElementById(id) {"
            " if (!nodes[id]) { nodes[id] = node(id); } return nodes[id]; },"
            " createElement(tag) { return node(tag); } };",
            "let metaInfo = JSON.parse(process.argv[3]);",
            _function(body, "function paintPanelRefusal() {"),
            "paintPanelRefusal();",
            "const at = (id) => document.getElementById(id);",
            "console.log(JSON.stringify({",
            "  hidden: at('panel-refusal').hidden,",
            "  note: at('panel-refusal-note').textContent,",
            "  whereHidden: at('panel-refusal-where').hidden,",
            "  address: at('panel-refusal-address').textContent,",
            "  nowhereHidden: at('panel-refusal-nowhere').hidden,",
            "  actions: at('panel-refusal-actions').children.map((each) => each.textContent),",
            "}));",
        ]
    )

    def paint(lang: str, meta: dict[str, object]) -> dict[str, object]:
        done = subprocess.run(
            ["node", "-", lang, json.dumps(meta)],
            input=harness,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(done.stdout)

    # The ordinary panel: the field is present and null, and nothing is drawn.
    assert paint("en", {"session_refusal": None})["hidden"] is True

    # The state it exists for, with the address the operator cannot derive.
    busy = paint(
        "en",
        {
            "session_refusal": {
                "code": PANEL_ALREADY_OPEN,
                "message": "another panel already serves this project",
                "address": "127.0.0.1:4321",
                "safe_next_actions": list(PANEL_ALREADY_OPEN_ACTIONS),
            }
        },
    )
    assert busy["hidden"] is False
    assert busy["address"] == "127.0.0.1:4321"
    assert busy["whereHidden"] is False and busy["nowhereHidden"] is True
    # This panel's sentence, with the server's canonical one still in brackets.
    assert str(busy["note"]).startswith("Another panel already owns this project")
    assert "(another panel already serves this project)" in str(busy["note"])
    # The server's safe next actions, verbatim: the panel has nothing truer.
    assert busy["actions"] == list(PANEL_ALREADY_OPEN_ACTIONS)

    # The same state with the port unreadable.
    blind = paint(
        "ru",
        {
            "session_refusal": {
                "code": PANEL_ALREADY_OPEN,
                "message": "another panel already serves this project",
                "address": None,
                "safe_next_actions": [],
            }
        },
    )
    assert blind["hidden"] is False
    assert blind["whereHidden"] is True and blind["nowhereHidden"] is False
    assert blind["address"] == ""
    assert blind["actions"] == []
    # Russian, because a panel that falls back to English exactly when it has
    # bad news is a panel that abandons the reader at the worst moment.
    assert str(blind["note"]).startswith("Этим проектом уже владеет другая панель")

    # A code this build has never seen is still shown -- verbatim, with no
    # invented sentence in front of it. Silence would be the worse failure.
    unknown = paint(
        "en",
        {"session_refusal": {"code": "some_code_from_a_newer_server", "message": "raw text"}},
    )
    assert unknown["hidden"] is False
    assert unknown["note"] == "raw text"


def test_the_banner_is_painted_at_boot_and_again_when_the_session_is_replaced() -> None:
    """Both places `metaInfo` is assigned.  A banner painted only at boot would
    survive its own cause: the panel that lost the singleness race can win it
    back when the first window closes, and the tab re-reads `/api/meta` on the
    recovery frame precisely so its cached identity stops being stale."""

    body = read_spa()
    boot = _function(body, "async function boot() {")
    assert "paintPanelRefusal();" in boot
    recovery = _function(body, "async function applySessionRecovery(payload) {")
    assert "paintPanelRefusal();" in recovery
    assert 'metaInfo = await api("/api/meta");' in recovery
    # Three calls and no more: the two assignments above and the language
    # surface. A fourth would be a second place deciding when this is true.
    assert body.count("paintPanelRefusal()") == 4  # 3 calls + the definition
