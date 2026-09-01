"""The screen an unset-up project is met with, instead of an error.

`agent-commons ui` now opens on whatever directory it was started in, including
one git never touched, and the wiring answers every one of those states by name
(`tests/ui/test_first_run_wiring.py`).  This is the other half: what the tab
actually shows for each of those names, and that none of them is a dead end.

The decisions are read by running the panel's own functions under node over the
panel's own string tables, because "the screen says which provider is missing"
is a claim about a sentence a person reads, and a grep for a key cannot make it.
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
    SETUP_NO_PROVIDER_FOUND,
    SETUP_NOT_A_REPOSITORY,
    SETUP_UNCONFIGURED,
    SETUP_UNINITIALIZED,
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
    """The screen's constants and its state, verbatim from the asset."""

    head = "const SETUP_WITHOUT_A_BOARD = ["
    tail = "let setupOpenedByHand = false;"
    assert head in body and tail in body
    return head + body.split(head, 1)[1].split(tail, 1)[0] + tail


def _decide(lang: str, call: str, argument: object) -> object:
    """Run one of the screen's own decisions, over the real tables, under node.

    The program travels over stdin: it embeds the whole STRINGS table, which is
    past Linux's 128 KiB per-argument ceiling, and a stdin program finds its
    first user argument at `process.argv[2]`.
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
            _setup_module(body),
            _function(body, "function setupIsConfigured(state) {"),
            _function(body, "function setupNeedsAttention() {"),
            _function(body, "function setupHasNoBoard() {"),
            _function(body, "function setupLead(state) {"),
            _function(body, "function setupBlockingNote(info) {"),
            _function(body, "function setupRefusalText(error) {"),
            "const argument = JSON.parse(process.argv[3]);",
            f"console.log(JSON.stringify({call}));",
        ]
    )
    done = subprocess.run(
        ["node", "-", lang, json.dumps(argument)],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="no node to run the first-run decisions in"
)


# -- the screen exists at all -------------------------------------------------


def test_the_panel_asks_the_one_route_that_answers_before_the_one_that_raises() -> None:
    """The bug this screen was built out of: `boot()` led with `/api/meta`,
    which reads a workspace and raises without one, so a person who opened the
    panel one directory too early was told "no access" about a directory they
    owned.  `GET /api/setup` answers in every state, so it is asked first and it
    decides whether there is a panel to boot at all."""

    body = read_spa()
    boot = body.split("async function boot() {", 1)[1].split("\n}\n", 1)[0]
    assert boot.index("await loadSetup();") < boot.index('await api("/api/meta")')
    # And with no workspace behind it, boot stops there rather than walking into
    # five more routes that read state which does not exist yet.
    assert "if (setupHasNoBoard()) {" in boot
    assert boot.index("if (setupHasNoBoard()) {") < boot.index('await api("/api/meta")')
    # `connect()` is a reconnect loop that never returns, so the second boot
    # after first run must not leave two of them running.
    assert "if (!streamStarted) { streamStarted = true; connect(); }" in body


def test_the_screen_is_not_a_dialog_and_owns_no_focus_trap() -> None:
    """It is what the tab shows INSTEAD of the shell, not a layer over it: on an
    uninitialized directory there is nothing behind it to trap focus against,
    and Esc may not dismiss the only thing on screen."""

    body = read_spa()
    markup = body.split('<div id="setup" hidden>', 1)[1].split("\n</div>", 1)[0]
    assert 'role="dialog"' not in markup
    assert 'class="scrim"' not in body.split('<div id="setup"', 1)[1].split(">", 1)[0]
    assert "#setup{" in body
    assert "position:fixed" in body.split("#setup{", 1)[1].split("}", 1)[0]


# -- one state at a time ------------------------------------------------------


@needs_node
def test_a_directory_git_never_touched_is_told_so_and_given_the_one_command_left() -> None:
    """Every other way out of this screen is a button.  This one cannot be: a
    page served out of a directory cannot make that directory a repository."""

    for lang, table in (("en", 0), ("ru", 1)):
        lead = _decide(lang, "setupLead(argument)", SETUP_NOT_A_REPOSITORY)
        assert lead == ["setup_repo_missing"]
        sentence = _value(_language_tables()[table], "setup_repo_missing")
        assert "git" in sentence

    body = read_spa()
    # The command is a command, so it is spelled once, in the markup's own
    # element, and never translated.
    assert 'command.textContent = "git init";' in body
    for block in _language_tables():
        values = re.findall(r'^\s*[a-z0-9_]+: "(.*)",$', block, re.MULTILINE)
        assert [value for value in values if "git init" in value] == []


@needs_node
def test_a_repository_with_no_workspace_is_offered_the_workspace_not_a_command() -> None:
    lead = _decide("en", "setupLead(argument)", SETUP_UNINITIALIZED)
    assert lead == ["setup_init_offer"]
    body = read_spa()
    assert 'id="setup-init"' in body
    assert 'setupWrite("/api/setup/initialize", "setup_initialized");' in body
    english, russian = _language_tables()
    for block in (english, russian):
        # It says what creating a workspace WRITES, because that is the consent
        # being asked for.
        assert ".agent-commons/" in _value(block, "setup_init_offer")


@needs_node
def test_no_provider_at_all_is_an_honest_refusal_with_the_guide_and_rescan() -> None:
    """The product does not invent a runnable path when neither CLI exists."""

    absent = {
        "state": SETUP_UNCONFIGURED,
        "blocking_refusal": SETUP_NO_PROVIDER_FOUND,
        "providers_found": [],
        "providers_missing": ["claude", "codex", "grok"],
        "support_missing": [],
        "operator_panel": True,
    }
    for lang, table in (("en", 0), ("ru", 1)):
        note = _decide(lang, "setupBlockingNote(argument)", absent)
        assert note == _value(_language_tables()[table], "setup_found_none")
        assert "claude" in note and "codex" in note and "grok" in note

    body = read_spa()
    assert "demo_available" not in body
    assert "setup-demo" not in body
    assert 'id="setup-guide"' in body
    assert "info.blocking_refusal !== SETUP_NO_PROVIDER" in body
    assert "const noProvider = info.blocking_refusal === SETUP_NO_PROVIDER;" in body
    assert "write.hidden = observing || !configurable || noProvider;" in body
    for block in _language_tables():
        sentence = _value(block, "setup_found_none")
        assert "claude" in sentence and "codex" in sentence and "grok" in sentence
        assert _value(block, "setup_no_provider_guide")


@needs_node
def test_one_provider_of_two_names_the_missing_one_and_what_its_absence_costs() -> None:
    partial = {
        "state": SETUP_UNCONFIGURED,
        "blocking_refusal": None,
        "providers_found": ["claude"],
        "providers_missing": ["codex", "grok"],
        "support_missing": [],
    }
    for lang in ("en", "ru"):
        note = _decide(lang, "setupBlockingNote(argument)", partial)
        assert "claude" in note and "codex" in note and "grok" in note
        # Both names reach the sentence through one substitution each, so a
        # provider name never lands mid-clause in a language whose word order
        # differs from English.
        assert "{found}" not in note and "{missing}" not in note
    # And nothing is blocked by it: a half-found machine still writes a config.
    body = read_spa()
    assert "write.hidden = observing || !configurable || noProvider;" in body
    assert "write.disabled = blocked && !noProvider;" in body


@needs_node
def test_a_provider_without_its_support_binaries_names_the_program_before_the_click() -> None:
    """The code this wave added: a provider resolves, and the executables every
    generated profile names beside it do not.  Written anyway, the profile would
    fail at the operator's first run instead of failing here."""

    unresolved = {
        "state": SETUP_UNCONFIGURED,
        "blocking_refusal": "setup_support_binary_unresolved",
        "providers_found": ["claude", "codex", "grok"],
        "providers_missing": [],
        "support_missing": ["agent-commons-mcp"],
    }
    for lang in ("en", "ru"):
        note = _decide(lang, "setupBlockingNote(argument)", unresolved)
        assert "agent-commons-mcp" in note
    body = read_spa()
    # Refused before the click, not after it: the button carries the same
    # sentence as its reason for being dead.
    assert 'write.title = blocked ? setupBlockingNote(info) : "";' in body


@needs_node
def test_a_configured_project_is_left_alone() -> None:
    body = read_spa()
    assert _decide("en", "setupLead(argument)", SETUP_CONFIGURED) == ["setup_all_set"]
    # `GET /api/setup` stops naming paths the moment the state is configured, and
    # the screen stops asking for them at the same moment.
    assert "if (configurable) {" in body
    path = body.split("if (configurable) {", 1)[1].split("\n  }", 1)[0]
    assert "info.config_path" in path
    assert _decide("en", "setupNeedsAttention()", None) is False, (
        "no answer read yet is not a reason to demand attention"
    )


# -- one frozen code, two operationally different things ----------------------


@needs_node
def test_the_two_rejections_wearing_one_code_never_say_the_same_thing() -> None:
    """`setup_config_rejected_by_loader` covers both "the config this panel
    generated was refused" and "the config you already had is refused".  The
    difference is what happened to the file: the generated one was moved aside
    to `runtime.yaml.rejected`; the operator's own is untouched, because the
    panel did not write it and may not rename it.  A screen that said one
    sentence for both would be telling half its readers their file had been
    moved when it had not."""

    lead = _decide("en", "setupLead(argument)", CONFIG_REJECTED_BY_LOADER)
    assert lead == ["setup_rejected_existing", "setup_manual_yaml"]

    english, russian = _language_tables()
    for block in (english, russian):
        existing = _value(block, "setup_rejected_existing")
        generated = _value(block, "setup_rejected")
        assert existing != generated
        # The operator's own file: named as untouched, and never as renamed.
        assert ".rejected" not in existing
        # The generated one: named as moved aside, under the name it now has.
        assert "runtime.yaml.rejected" in generated

    # The generated case is the only one of the two that arrives with a reason
    # attached, and the reason is the whole value of the message, so it is kept
    # verbatim after the panel's own sentence.
    reason = "generated operator runtime config was refused by the launch loader: bad profile"
    said = _decide(
        "en",
        "setupRefusalText(argument)",
        {"code": CONFIG_REJECTED_BY_LOADER, "message": reason},
    )
    assert said.startswith(_value(english, "setup_rejected"))
    assert said.endswith(reason)


def test_the_screen_names_what_the_loader_objected_to() -> None:
    """The gap this test used to state as an honest floor is closed.

    `setup_state` swallowed the loader's `CommonsError` and handed the screen a
    bare code, so the panel could say the file was refused and not one word
    about why -- and the only way to learn more was the terminal, which is the
    regress the whole wave exists to remove.  `rejected_reason` and
    `rejected_path` now travel beside the code, and the reason is the loader's
    own text, the same one `POST /api/setup/runtime-config` puts in its error
    message, so one failure reads one way whichever half of the screen met it.

    What the panel must NOT do is paraphrase it: the reason is canonical text
    out of the launch loader and is printed exactly as it arrived.
    """

    body = read_spa()
    markup = body.split('<div id="setup" hidden>', 1)[1].split("\n</div>", 1)[0]
    assert '<div id="setup-rejected" hidden>' in markup
    assert '<p class="note" id="setup-rejected-why">' in markup
    assert '<p class="note mono" id="setup-rejected-path">' in markup

    paint = _function(body, "function paintSetup() {")
    assert "const rejected = state === SETUP_REJECTED;" in paint
    assert 'document.getElementById("setup-rejected").hidden = !rejected;' in paint
    # Label plus the reason verbatim -- concatenated, never rewritten, and never
    # passed through `t()`, which would be the panel translating the loader.
    assert 't("setup_rejected_why") + " " + info.rejected_reason' in paint
    assert 't("setup_rejected_where") + " " + info.rejected_path' in paint

    english, russian = _language_tables()
    for block in (english, russian):
        for key in ("setup_rejected_why", "setup_rejected_where", "setup_rejected_no_reason"):
            assert _value(block, key), key
    # And the paragraph that used to end "it cannot say which line is wrong"
    # now points at the reason instead, in both languages: a sentence that
    # apologises for an absence outlives the absence and becomes a lie.
    assert "below, in its own words" in _value(english, "setup_manual_yaml")
    assert "ниже его же словами" in _value(russian, "setup_manual_yaml")
    for block in (english, russian):
        assert "carries the verdict and not the reason" not in _value(block, "setup_manual_yaml")

    # A rejected state that arrives with no reason -- an older server -- is
    # still said out loud rather than shown as an empty label.
    assert 't("setup_rejected_no_reason")' in paint
    assert "was not told" in _value(english, "setup_rejected_no_reason")


# -- the paths, and the consent asked for them --------------------------------


def test_the_screen_says_the_working_tree_is_writable_before_it_offers_the_button() -> None:
    """The one thing this screen must never soften: pressing the button lets a
    provider process write into this working tree without asking per action."""

    body = read_spa()
    markup = body.split('<div id="setup" hidden>', 1)[1].split("\n</div>", 1)[0]
    assert 'data-i18n="setup_trust_warning"' in markup
    # It stands above the button it is about, not below it.
    assert markup.index("setup_trust_warning") < markup.index('id="setup-write"')

    english, russian = _language_tables()
    warning = _value(english, "setup_trust_warning")
    for phrase in ("delete files", "without asking", "read-only"):
        assert phrase in warning, phrase
    russian_warning = _value(russian, "setup_trust_warning")
    for phrase in ("удалять файлы", "не спрашивая", "только на чтение"):
        assert phrase in russian_warning, phrase


def test_every_string_the_screen_needs_is_in_both_tables() -> None:
    frozen = (
        "setup_title",
        "setup_intro",
        "setup_repo_missing",
        "setup_init_offer",
        "setup_init_button",
        "setup_found_title",
        "setup_found_none",
        "setup_found_partial",
        "setup_path_label",
        "setup_trust_warning",
        "setup_write_button",
        "setup_written",
        "setup_rejected",
        "setup_rescan_button",
        "setup_manual_yaml",
        "setup_no_provider_guide",
        "setup_preflight_button",
        "setup_add_discovered_providers_button",
        "setup_providers_added",
        "setup_no_new_providers",
        "setup_preflight_credential_free",
    )
    for block in _language_tables():
        for key in frozen:
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


def test_the_way_back_into_the_screen_survives_being_set_aside() -> None:
    """The screen can be set aside over a board that works, and everything it
    configures stays off until it is not -- so it has to be reachable again from
    the surface it was set aside on.  Once nothing is missing the same control
    becomes the credential-free check, which is the screen's only remaining use
    and would otherwise have no door at all."""

    body = read_spa()
    assert 'id="board-setup"' in body
    assert 'document.getElementById("board-setup").addEventListener("click", openSetup);' in body
    paint = body.split("function paintSetup() {", 1)[1].split("\n}\n", 1)[0]
    assert (
        'reopen.dataset.i18n = setupNeedsAttention() ? "setup_reopen" : "setup_preflight_button";'
        in paint
    )
    # The label switches its KEY, so `applyI18n` -- which runs on every stream
    # frame -- repaints it instead of overwriting it two seconds later.
    assert "applyI18n();" in paint
    # And the Run tab's own way in, for the state that sends a person looking
    # for Run in the first place.
    assert 'id="run-setup"' in body
