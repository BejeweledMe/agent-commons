"""The panel stops advising three switches the product no longer has.

`agent-commons ui` had `--enable-writes`, `--enable-catalog-editing` and
`--enable-launch`; the wave removed all three, and the asset went on telling
operators to restart with them -- in both languages, and in a whole reference
paragraph of the guide.  An unrunnable instruction inside the product is worse
than stale documentation: documentation is read once and doubted, a panel is
believed.

What replaced each of them is the same shape: name the state, and give the way
out INSIDE the interface.
"""

from __future__ import annotations

import re

from agent_commons.cli import cli
from agent_commons.ui import read_spa


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


def _ui_switches() -> set[str]:
    """Every switch `agent-commons ui` actually accepts, from the command."""

    command = cli.commands["ui"]
    options: set[str] = set()
    for parameter in command.params:
        options.update(parameter.opts)
        options.update(parameter.secondary_opts)
    return {option for option in options if option.startswith("--")}


def test_no_capability_switch_is_named_anywhere_in_the_asset() -> None:
    """All three shared one prefix, so one assertion covers them and anything
    shaped like them that a later edit might invent.  Comments are included on
    purpose: a comment naming a dead switch is how the next person reintroduces
    it into a string."""

    body = read_spa()
    assert "--enable-" not in body

    # And the check is against the product, not against a memory of it: the
    # command has no capability switch to advise in the first place.
    switches = _ui_switches()
    assert not [switch for switch in switches if switch.startswith("--enable-")], switches
    # The two that remain are overrides of where a file is read from, plus the
    # view that records nothing -- none of them turns a capability on.
    assert switches == {
        "--port",
        "--no-browser",
        "--read-only",
        "--role-catalog",
        "--profile-config",
    }


def test_the_asset_names_no_agent_commons_switch_at_all() -> None:
    """The stronger rule the replacement texts were written to: this panel does
    not tell a person to restart it.  The only long options left in the string
    tables belong to other programs -- the provider CLIs -- and the sentences
    around them describe what those programs are run WITH, never what the
    operator should type."""

    foreign = {"--help", "--strict-mcp-config"}
    for block in _language_tables():
        for switch in set(re.findall(r"--[a-z][a-z-]+", block)):
            assert switch in foreign, switch
        # And each of the two is named as somebody else's, in a sentence about
        # what the panel already does rather than about what to do next.
        for key in ("setup_preflight_credential_free", "mcp_builtin_body"):
            assert _value(block, key)


def test_writes_being_off_is_described_as_a_state_in_both_languages() -> None:
    """The four "start the UI with --enable-writes" notes.  Writes are off for
    one reason now -- this panel holds no operator session -- and that has two
    causes the panel genuinely cannot tell apart before a write is attempted:
    it was opened as a view, or another panel owns the project.  The answer
    sentence says both rather than picking one and being wrong half the time."""

    english, russian = _language_tables()
    for key in ("chat_empty_readonly", "readonly_answer", "readonly_confirm", "readonly_reply"):
        assert "operator session" in _value(english, key), key
        assert "операторской сессии" in _value(russian, key), key
    # The one that can name both causes does.
    assert "another panel" in _value(english, "readonly_answer")
    assert "другая панель" in _value(russian, "readonly_answer")


def test_the_catalogue_note_names_the_two_conditions_and_the_screen_that_meets_them() -> None:
    """`--role-catalog` plus `--enable-catalog-editing` became "a catalogue file
    of its own AND a session to record under", and the first-run screen writes
    the catalogue beside the runtime config -- so the way out is a screen in
    this panel rather than a restart."""

    english, russian = _language_tables()
    note = _value(english, "catalog_readonly")
    assert "catalogue file" in note and "operator session" in note
    assert "first-run screen" in note
    russian_note = _value(russian, "catalog_readonly")
    assert "файл каталога" in russian_note and "операторская сессия" in russian_note


def test_the_reference_page_describes_what_is_configured_not_which_switch_to_add() -> None:
    """`guide_lm_gates_p` described three gates in full.  The heading kept its
    place -- loopback, one printed token and "no transcripts" are still the
    panel's whole security posture, and it is the only place that states them --
    and its id moved with its subject, which the deep-link set allows because it
    compares links against headings rather than against a fixed list."""

    body = read_spa()
    assert "guide_lm_gates" not in body
    assert 'id="g-lm-access"' in body
    for block in _language_tables():
        paragraph = _value(block, "guide_lm_access_p")
        assert "loopback" in paragraph
        assert "--" not in paragraph
    english, russian = _language_tables()
    assert "no capability switches" in _value(english, "guide_lm_access_p")
    assert "Переключателей возможностей нет" in _value(russian, "guide_lm_access_p")


def test_an_unconfigured_runtime_shows_the_run_tab_and_says_why_it_cannot_run() -> None:
    """The launch switch's replacement.  Hiding the tab while nothing was
    configured rendered a missing capability as an absence: no tab, no refusal,
    no next step.  It shows for every staffable role a writing panel has, the
    note switches to the reason, the button is dead, and a second button leads
    to the screen that removes the reason."""

    body = read_spa()
    assert 'runTab.hidden = !(isRole && writesEnabled && node.state === "active" &&' in body
    launch = body.split("async function loadLaunch() {", 1)[1].split("\n}\n", 1)[0]
    # The label switches its KEY, not its text: `applyI18n` runs on every stream
    # frame and would put the other sentence back two seconds later.
    assert 'launchEnabled ? "run_note" : "launch_not_configured_note";' in launch
    assert 'document.getElementById("run-go").disabled = !launchEnabled;' in launch
    assert 'document.getElementById("run-setup").hidden = launchEnabled;' in launch
    assert (
        'document.getElementById("run-setup").addEventListener("click", () => openSetup());' in body
    )

    english, russian = _language_tables()
    assert "first-run screen" in _value(english, "launch_not_configured_note")
    assert "экрана настройки" in _value(russian, "launch_not_configured_note")
    for block in (english, russian):
        assert "--" not in _value(block, "launch_not_configured_note")


def test_the_states_that_refuse_a_write_are_matched_on_their_frozen_code() -> None:
    """`launch_not_configured`, `setup_uninitialized` and `panel_already_open`
    are states of this panel rather than complaints about a request, and every
    form in it can meet all three.  They are matched on the code the server
    sends -- frozen by the wave contract -- and not on the English of the
    message, which is canonical text and may be reworded.  The canonical
    sentence still follows in brackets, and for `panel_already_open` that is the
    half carrying the first panel's address."""

    body = read_spa()
    codes = body.split("const REFUSAL_CODES = {", 1)[1].split("};", 1)[0]
    for code in ("setup_uninitialized", "launch_not_configured", "panel_already_open"):
        assert f"{code}:" in codes, code
    humanize = body.split("function humanizeError(error, fieldLabels) {", 1)[1].split("\n}\n", 1)[0]
    assert 'if (coded) { return t(coded) + " (" + message + ")"; }' in humanize
    # The code has to survive `post()` for any of this to fire; it used to be
    # thrown away with the response.
    post = body.split("async function post(path, body) {", 1)[1].split("\n}\n", 1)[0]
    assert 'failure.code = (payload.error && payload.error.code) || "";' in post

    english, russian = _language_tables()
    assert "Another panel" in _value(english, "panel_already_open")
    assert "другая панель" in _value(russian, "panel_already_open")
