"""An unset-up project met by a panel that cannot set anything up.

`agent-commons ui --read-only` registers none of `SETUP_ROUTES`, and
`GET /api/setup` is the only route the first-run screen is guaranteed to reach
-- `/api/meta` reports nothing before a workspace exists.  So the screen drew
its buttons blind, and every one of them would have answered 404: a person
would read a broken product rather than a panel that was never given the power.

`operator_panel` closes that.  It is the same structural property the route
table itself is built from, present in every state, so the screen and the
router cannot disagree about what exists.  What the screen must do with it is
the subject here: keep describing the state, which is a GET and is the whole
use of this screen on a watching panel, and stop offering what it cannot do.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.setup import (
    CONFIG_REJECTED_BY_LOADER,
    SETUP_CONFIGURED,
    SETUP_UNCONFIGURED,
    SETUP_UNINITIALIZED,
)

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="no node to run the first-run screen in"
)


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
    assert header in body, header
    return header + body.split(header, 1)[1].split("\n}\n", 1)[0] + "\n}\n"


def _setup_module(body: str) -> str:
    head = "const SETUP_WITHOUT_A_BOARD = ["
    tail = "let setupOpenedByHand = false;"
    assert head in body and tail in body
    return head + body.split(head, 1)[1].split(tail, 1)[0] + tail


# The controls the screen offers, and which of them write.  Named here rather
# than derived, so a button added to this screen has to be classified by hand
# before this test will pass -- an unclassified write is exactly the 404.
SETUP_WRITE_CONTROLS = ("setup-init", "setup-write", "setup-demo")
SETUP_READ_CONTROLS = ("setup-rescan", "setup-preflight")


def _paint(lang: str, info: dict[str, object]) -> dict[str, object]:
    """Run the real `paintSetup` over the real tables, under node.

    Which buttons a screen offers is a claim about what a person can press, so
    it is run rather than grepped.  The document is stubbed just far enough to
    record what was painted into it.

    The program travels over stdin -- it embeds the whole STRINGS table, past
    Linux's 128 KiB per-argument ceiling -- so `process.argv[2]` is the first
    user argument.
    """

    body = read_spa()
    harness = "\n".join(
        [
            "let lang = process.argv[2];",
            "const STRINGS = {"
            + body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
            + "\n};",
            _function(body, "function t(key) {"),
            _function(body, "function said(key, values) {"),
            "function node(id) { return { id: id, hidden: false, disabled: false,"
            " textContent: '', title: '', dataset: {}, children: [], firstChild: null,"
            " appendChild(child) { this.children.push(child);"
            " this.firstChild = this.children[0]; },"
            " removeChild(child) {"
            " this.children = this.children.filter((each) => each !== child);"
            " this.firstChild = this.children[0] || null; } }; }",
            "const nodes = {};",
            "const document = { documentElement: {},"
            " getElementById(id) {"
            " if (!nodes[id]) { nodes[id] = node(id); } return nodes[id]; },"
            " createElement(tag) { return node(tag); },"
            " querySelectorAll() { return []; } };",
            _function(body, "function applyI18n() {"),
            _setup_module(body),
            _function(body, "function setupIsConfigured(state) {"),
            _function(body, "function setupNeedsAttention() {"),
            _function(body, "function setupHasNoBoard() {"),
            _function(body, "function setupLead(state) {"),
            _function(body, "function setupBlockingNote(info) {"),
            _function(body, "function paintSetupBinaries(info) {"),
            _function(body, "function paintSetup() {"),
            "setupInfo = JSON.parse(process.argv[3]);",
            "paintSetup();",
            "const at = (id) => document.getElementById(id);",
            "console.log(JSON.stringify({",
            "  screenHidden: at('setup').hidden,",
            "  intro: at('setup-intro').dataset.i18n,",
            "  lead: at('setup-state-note').textContent,",
            "  readonlyHidden: at('setup-readonly').hidden,",
            "  hidden: Object.fromEntries("
            "    Object.keys(nodes).map((id) => [id, Boolean(nodes[id].hidden)])),",
            "  rejectedWhy: at('setup-rejected-why').textContent,",
            "  rejectedPath: at('setup-rejected-path').textContent,",
            "}));",
        ]
    )
    done = subprocess.run(
        ["node", "-", lang, json.dumps(info)],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def _uninitialized(**extra: object) -> dict[str, object]:
    return {"state": SETUP_UNINITIALIZED, "operator_panel": True, **extra}


# --- a panel that can act, unchanged ----------------------------------------


@needs_node
def test_a_panel_that_can_act_still_offers_the_way_forward() -> None:
    """The control case, first: everything below is a difference, and a
    difference is worth nothing without the thing it differs from."""

    answer = _paint("en", _uninitialized())
    assert answer["screenHidden"] is False
    assert answer["readonlyHidden"] is True
    assert answer["intro"] == "setup_intro"
    assert answer["hidden"]["setup-init"] is False
    assert answer["lead"] == _value(_language_tables()[0], "setup_init_offer")


# --- and one that cannot -----------------------------------------------------


@needs_node
def test_a_watching_panel_describes_the_state_and_offers_nothing_it_cannot_do() -> None:
    """The angle the screen left open: on `--read-only` the setup routes are not
    registered at all, so every button here answers 404.  The screen keeps the
    half it can do -- naming the state, off a GET -- and drops the half it
    cannot, rather than being hidden entirely: a panel that says nothing about
    a directory that is not set up is the error this screen replaced."""

    answer = _paint("en", _uninitialized(operator_panel=False))
    # Still shown, and still saying which of the three things is missing.
    assert answer["screenHidden"] is False
    assert answer["lead"] == _value(_language_tables()[0], "setup_init_offer")
    # And saying, once, that it cannot act on it.
    assert answer["readonlyHidden"] is False
    # Every writing control is gone; every reading one stays.
    for control in SETUP_WRITE_CONTROLS:
        assert answer["hidden"][control] is True, control
    for control in SETUP_READ_CONTROLS:
        assert control in answer["hidden"], control
    assert answer["hidden"]["setup-rescan"] is False
    # The sentence that names the demo button goes with the button.
    assert answer["hidden"]["setup-demo-offer"] is True

    # The intro promises this screen writes the two missing things, which is
    # not true here -- so the KEY is switched, not the text, because an element
    # is owned by `data-i18n` or by a JS write and never by both.
    assert answer["intro"] == "setup_intro_readonly"
    english, russian = _language_tables()
    assert "watching only" in _value(english, "setup_intro_readonly")
    assert "только наблюдает" in _value(russian, "setup_intro_readonly")


@needs_node
def test_the_unconfigured_state_on_a_watching_panel_loses_all_three_buttons() -> None:
    """The state with the most to offer is the one with the most to withdraw:
    write, demo, and the demo sentence are all reachable here and none of their
    routes exists on a panel started to observe."""

    unconfigured = {
        "state": SETUP_UNCONFIGURED,
        "operator_panel": False,
        "providers": {},
        "providers_found": ["claude"],
        "providers_missing": ["codex"],
        "support_missing": [],
        "demo_available": True,
        "config_path": "/home/x/.config/agent-commons/runtime.yaml",
    }
    answer = _paint("en", unconfigured)
    for control in SETUP_WRITE_CONTROLS + ("setup-demo-offer",):
        assert answer["hidden"][control] is True, control
    assert answer["readonlyHidden"] is False
    # What was found is still listed: it is a fact about this machine and it is
    # exactly what the person who CAN act will need to be told.
    assert answer["hidden"]["setup-found"] is False


def test_the_note_says_where_the_power_is_instead_of_only_that_it_is_missing() -> None:
    """A refusal that names no way on is the dead end this screen may not be.
    The sentence says what to do -- open a panel without the switch -- rather
    than leaving the reader with a disabled screen and no next move."""

    english, russian = _language_tables()
    note_en = _value(english, "setup_readonly_note")
    note_ru = _value(russian, "setup_readonly_note")
    assert "cannot set anything up" in note_en
    assert "ничего настроить не может" in note_ru
    # It says WHY there is nothing to press -- the routes do not exist here --
    # so an absent button does not read as a bug.
    assert "do not exist on it" in note_en
    assert "нет вовсе" in note_ru
    # And it names the way on in both languages.
    assert "read-only" in note_en
    assert "только чтение" in note_ru


# --- one spelling of configured ---------------------------------------------


@needs_node
def test_configured_is_spelled_one_way_and_it_is_the_backend_s() -> None:
    """This was the only code in the frozen table with no test pinning it, which
    is how the backend and the contract came to disagree and why the panel had
    to accept both spellings blind.  The backend is pinned now, so the panel
    reads the canonical constant here -- imported, not retyped -- and carries
    exactly one spelling."""

    body = read_spa()
    assert 'const SETUP_CONFIGURED = "setup_configured";' in body
    assert SETUP_CONFIGURED == "setup_configured"
    assert '"configured", "setup_configured"' not in body
    assert "spelled two ways" not in body

    # And a configured project is left alone by every state the screen keeps.
    answer = _paint("en", {"state": SETUP_CONFIGURED, "operator_panel": True})
    assert answer["screenHidden"] is True


# --- the reason a rejected config was rejected ------------------------------


def _rejected(**extra: object) -> dict[str, object]:
    return {
        "state": CONFIG_REJECTED_BY_LOADER,
        "operator_panel": True,
        "providers": {},
        "providers_found": [],
        "providers_missing": [],
        "support_missing": [],
        "demo_available": True,
        **extra,
    }


@needs_node
def test_the_rejected_config_screen_carries_the_loader_s_own_words() -> None:
    """The reason arrives beside the state now.  It is canonical text out of the
    launch loader -- the same sentence `POST /api/setup/runtime-config` reports
    -- so it is printed exactly as it came, with only the label in front of it
    belonging to the panel.  The path is named where the file STANDS: this is
    the operator's own config, and the panel has not moved it."""

    reason = "commons.runtime.profile 'claude-builder': executable is not an allowed program"
    path = "/home/x/.config/agent-commons/runtime.yaml"
    answer = _paint("en", _rejected(config_path=path, rejected_reason=reason, rejected_path=path))
    assert answer["hidden"]["setup-rejected"] is False
    english, _ = _language_tables()
    assert answer["rejectedWhy"] == _value(english, "setup_rejected_why") + " " + reason
    assert answer["rejectedPath"] == _value(english, "setup_rejected_where") + " " + path
    # The reason survives whole: a screen that truncated or reworded it would
    # send the reader back to the terminal it exists to replace.
    assert reason in str(answer["rejectedWhy"])

    # The lead still says the file is untouched, which is the half of this code
    # that distinguishes it from a generated config the panel wrote and moved.
    assert "untouched" in _value(english, "setup_rejected_existing")

    # A state carrying no reason -- an older server -- says so rather than
    # showing an empty label with nothing after it.
    blind = _paint("en", _rejected(config_path=path))
    assert blind["rejectedWhy"] == _value(english, "setup_rejected_no_reason")
    assert blind["hidden"]["setup-rejected-path"] is True


@needs_node
def test_the_reason_reaches_a_watching_panel_too() -> None:
    """It arrives on a GET, so the panel that cannot repair the file can still
    say what is wrong with it -- which is the more useful half for whoever is
    going to be told about it."""

    answer = _paint(
        "ru",
        _rejected(
            operator_panel=False,
            config_path="/x/runtime.yaml",
            rejected_reason="loader said no",
            rejected_path="/x/runtime.yaml",
        ),
    )
    assert answer["hidden"]["setup-rejected"] is False
    assert "loader said no" in str(answer["rejectedWhy"])
    assert answer["readonlyHidden"] is False
    for control in SETUP_WRITE_CONTROLS:
        assert answer["hidden"][control] is True, control
