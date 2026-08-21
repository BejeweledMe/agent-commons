"""The panel's half of "which model does this role run on".

The backend's half is pinned in `test_model_choice_at_hire.py`: the choice is
recorded under `payload.extensions`, an unsafe name is refused at the request
that carries it, and `reconfigure` will not move a hired role to another model.
This file pins what the operator actually meets:

- a free-text field with the server's own names offered beside it, never a
  closed select -- `model_options` is a list of what this machine and this
  project have already named, which is honest but not exhaustive;
- an empty offer list rendered as an empty offer list, because a provider
  ships models nobody here has named yet and padding the list would be the
  panel inventing one;
- the refusal landing under the field it is about, matched on the sentence the
  backend actually sends rather than on one this file made up;
- the gear panel reporting the model and offering no way to change it.

The asset-wide ban on model names lives next door in
`test_profile_choice_and_link_offer.py`; what is added here is the positive
control for it, because a "no matches" answer is worth nothing until the same
reading has found something it was supposed to find.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.context import MODEL_NAME_REFUSED


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


def _hire_modal(body: str) -> str:
    return body.split('id="hire-modal"', 1)[1].split('id="task-modal"', 1)[0]


# --- the field itself --------------------------------------------------------


def test_the_model_is_typed_and_the_offers_sit_beside_it_rather_than_replacing_it() -> None:
    """`model_options` is not a closed list -- the backend says so in as many
    words -- so the control that reads it must not be a closed one either.  A
    `<select>` here would refuse every model a provider ships that this machine
    has not already named, which is most of them."""

    modal = _hire_modal(read_spa())
    field = modal.split('id="hire-model-field"', 1)[1].split("</label>", 1)[0]
    assert 'type="text"' in field, "the model must be typed, not chosen from a closed set"
    assert 'list="hire-model-offers"' in field
    assert '<datalist id="hire-model-offers">' in modal
    # And the offers are also SHOWN, not only completed from: a datalist a
    # person never opens is a list nobody saw.
    assert '<p class="note" id="hire-model-hint">' in modal

    # The empty answer is a legitimate answer and the field says so where the
    # cursor is, which is the placeholder.
    assert 'data-i18n-placeholder="hire_model_placeholder"' in field
    english, russian = _language_tables()
    assert _value(english, "hire_model_placeholder") == "empty — the profile's model"
    assert _value(russian, "hire_model_placeholder") == "пусто — модель профиля"


def test_the_form_says_the_choice_is_final_where_the_choice_is_made() -> None:
    """The decision is not a temporary gap in the settings panel, so the hire
    form says the model is fixed from here on -- at the moment it can still be
    changed, which is the only moment saying so is any use."""

    english, russian = _language_tables()
    for block in (english, russian):
        for key in ("hire_model_label", "hire_model_hint", "hire_model_offered", "hire_model_none"):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key
    # Both languages state the reason rather than only the rule: a restriction
    # with no reason reads as an unfinished feature.
    assert "cannot be changed after the hire" in _value(english, "hire_model_hint")
    assert "не поменять" in _value(russian, "hire_model_hint")


# --- what an empty catalogue looks like, run rather than read ----------------


def _offers(lang: str, catalog: dict[str, object], profile: str) -> dict[str, object]:
    """The real `paintModelOffers`, over the real tables, under node.

    The function paints DOM, so the harness carries the smallest document that
    can record what it did -- the point is which names reach the list and which
    sentence lands beside them, and both are readable off that.

    The script travels over stdin: it embeds the whole STRINGS table, which is
    past Linux's 128 KiB per-argument ceiling.  With a stdin program node spells
    argv as [node, "-", ...args], so user arguments start at index 2.
    """

    body = read_spa()
    harness = "\n".join(
        [
            "let lang = process.argv[2];",
            "const STRINGS = {"
            + body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
            + "\n};",
            _function(body, "function t(key) {"),
            "const PROFILE_PROVIDER = {"
            + body.split("const PROFILE_PROVIDER = {", 1)[1].split("};", 1)[0]
            + "};",
            # A document just real enough to be painted into.
            "function node(id) { return { id: id, value: '', textContent: '',"
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
            "const argv = JSON.parse(process.argv[3]);",
            "const catalog = argv.catalog;",
            "document.getElementById('hire-profile').value = argv.profile;",
            # A stale list is the case worth painting into: an offer for the
            # previously chosen provider must not survive the repaint.
            "document.getElementById('hire-model-offers').appendChild(node('stale'));",
            _function(body, "function paintModelOffers() {"),
            "paintModelOffers();",
            "console.log(JSON.stringify({",
            "  offers: document.getElementById('hire-model-offers')"
            ".children.map((each) => each.value),",
            "  hint: document.getElementById('hire-model-hint').textContent,",
            "}));",
        ]
    )
    done = subprocess.run(
        ["node", "-", lang, json.dumps({"catalog": catalog, "profile": profile})],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run paintModelOffers() in")
def test_an_empty_offer_list_is_shown_as_an_empty_offer_list() -> None:
    """`model_options` carries a key per provider, always present and possibly
    empty -- a fresh project with an operator config that names no models has
    nothing honest to offer.  The panel says exactly that and points at the two
    ways on, instead of padding the list with a name it invented."""

    catalog = {
        "profile_info": {"claude-builder": {"provider": "claude", "model": None}},
        "model_options": {"claude": [], "codex": []},
    }
    english = _offers("en", catalog, "claude-builder")
    assert english["offers"] == []
    assert "Nothing has been named here yet" in str(english["hint"])
    russian = _offers("ru", catalog, "claude-builder")
    assert russian["offers"] == []
    assert "пока ничего не названо" in str(russian["hint"])


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run paintModelOffers() in")
def test_the_offers_are_the_selected_profile_s_provider_s_and_nobody_else_s() -> None:
    """One key per provider, looked up by the provider of the profile the
    operator picked.  Offering the other provider's models would be offering
    names that could never start this role."""

    catalog = {
        "profile_info": {
            "claude-builder": {"provider": "claude", "model": "claude-model-a"},
            "codex-builder": {"provider": "codex", "model": "codex-model-a"},
        },
        "model_options": {
            "claude": ["claude-model-a", "claude-model-b"],
            "codex": ["codex-model-a"],
        },
    }
    claude = _offers("en", catalog, "claude-builder")
    assert claude["offers"] == ["claude-model-a", "claude-model-b"]
    # Shown outright, in the order the server sorted them, and spelled the way
    # the server spelled them -- these are canonical names, not prose.
    assert str(claude["hint"]).endswith("claude-model-a, claude-model-b")

    codex = _offers("en", catalog, "codex-builder")
    assert codex["offers"] == ["codex-model-a"], "a stale provider's offers survived the repaint"

    # A profile the config does not describe still gets its provider from the
    # closed enum, exactly like the picker's own gloss does.
    fallback = _offers(
        "en",
        {"profile_info": {}, "model_options": catalog["model_options"]},
        "codex-independent-reviewer",
    )
    assert fallback["offers"] == ["codex-model-a"]

    # And a payload with no `model_options` at all -- an older server, or a
    # provider key this build has never heard of -- is an absence, not a crash.
    assert _offers("en", {"profile_info": {}}, "claude-builder")["offers"] == []
    assert _offers("en", catalog, "mystery-profile")["offers"] == []


# --- the wire, and the refusal that comes back from it -----------------------


def test_an_untouched_field_sends_no_model_at_all() -> None:
    """Blank means "you choose", and the honest spelling of that on the wire is
    an absent key rather than an empty string -- the same discipline the skills
    picker beside it already keeps.  On the template path the field is not on
    screen at all, because the template carries the model it was saved with and
    a field there would override it silently."""

    body = read_spa()
    hire = _function(body, "async function hireRole() {")
    assert 'const model = document.getElementById("hire-model").value.trim();' in hire
    assert "if (model) { body.model = model; }" in hire
    # Inside the else branch -- the from-scratch path -- and never beside
    # `from_preset_id`, which is the whole of the preset payload.
    preset_half = hire.split("if (fromPreset) {", 1)[1].split("} else {", 1)[0]
    assert "model" not in preset_half, preset_half

    mode = _function(body, "function applyHireMode() {")
    assert 'document.getElementById("hire-model-field").hidden = fromPreset;' in mode
    assert 'document.getElementById("hire-model-hint").hidden = fromPreset;' in mode


def test_the_refused_name_lands_under_the_field_and_matches_the_sentence_sent() -> None:
    """The seam, checked from both sides.  The panel places this refusal by
    matching the backend's own sentence, so the two are compared here rather
    than trusted to stay in step -- a reworded refusal would otherwise go on
    rendering, silently, under the buttons at the bottom of a scrolling form."""

    body = read_spa()
    controls = body.split("const REFUSED_CONTROLS = [", 1)[1].split("];", 1)[0]
    match = re.search(r'\[/(\^[^/]+)/, "model"\]', controls)
    assert match, controls
    assert re.match(match.group(1).replace("\\", ""), MODEL_NAME_REFUSED), MODEL_NAME_REFUSED

    # And the table that says which control that is.
    hire = _function(body, "async function hireRole() {")
    assert 'model: ["hire_model_label", "hire-model"],' in hire

    # The backend's sentence is kept verbatim: it already names what a model
    # name may contain AND says that empty is a legitimate answer, so there is
    # nothing for the panel to rewrite -- only somewhere better to put it.
    assert "Leave the field empty" in MODEL_NAME_REFUSED
    assert "hire_model_refused" not in body, "the panel started rewriting a refusal it should keep"


# --- the gear panel: reported, never edited ---------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run roleModel() in")
def test_a_replayed_role_record_is_never_trusted_to_name_a_model_here_either() -> None:
    """The panel's `roleModel` reads the same field the backend's `role_model`
    does, out of a record replayed from a file this tab did not write, so it
    keeps the same distrust: anything that is not a plain non-empty string is
    an absence.  A record that named `["x"]` and got `x` printed would be the
    panel showing a model no launch would use."""

    body = read_spa()
    harness = "\n".join(
        [
            _function(body, "function roleModel(record) {"),
            "for (const record of JSON.parse(process.argv[2])) {",
            "  console.log(JSON.stringify(roleModel(record)));",
            "}",
        ]
    )
    hostile = [
        None,
        {},
        {"extensions": None},
        {"extensions": "a-model"},
        {"extensions": {"model": None}},
        {"extensions": {"model": ""}},
        {"extensions": {"model": "   "}},
        {"extensions": {"model": ["a-model"]}},
        {"extensions": {"models": "a-model"}},
    ]
    done = subprocess.run(
        ["node", "-", json.dumps(hostile + [{"extensions": {"model": "a-model"}}])],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.split() == ['""'] * len(hostile) + ['"a-model"']


def test_the_gear_panel_reports_the_model_and_says_why_it_cannot_change_it() -> None:
    """The decision the owner froze, said on the surface an operator would go
    looking for it on.  An absence with no sentence beside it reads as a
    feature nobody got round to; the sentence is what makes it a decision."""

    body = read_spa()
    labels = _function(body, "function paintSettingsLabels(record) {")
    assert "roleModel(record)" in labels
    assert 'said("settings_model_chosen", { model: roleModel(record) })' in labels
    assert 't("settings_model_fixed")' in labels
    assert 't("settings_model_profile")' in labels

    english, russian = _language_tables()
    for block in (english, russian):
        assert "{model}" in _value(block, "settings_model_chosen")
    # The reason, in both languages: the role's memory was accumulated by the
    # model that accumulated it.
    assert "rebuilding that memory" in _value(english, "settings_model_fixed")
    assert "пересборку этой памяти" in _value(russian, "settings_model_fixed")
    # And the way out is named, so the sentence is not a dead end.
    assert "Hire another role" in _value(english, "settings_model_fixed")
    assert "наймите другую роль" in _value(russian, "settings_model_fixed")


def test_the_board_drawer_names_the_model_without_inventing_one_for_a_role_that_has_none() -> None:
    """Settings is a writable panel's surface; a read-only tab has the drawer
    and nothing else.  The row rides beside the profile because it qualifies the
    profile -- and it is absent, not "default", when the role named no model."""

    body = read_spa()
    summary = _function(body, "function summarizeAgent(host, node, record) {")
    assert 'summaryRow(host, "hire_profile_label", valueWithGloss(record.profile_id));' in summary
    assert 'summaryRow(host, "sum_model", roleModel(record));' in summary
    # `summaryRow` drops an empty value, which is what makes the absence honest.
    row = _function(body, "function summaryRow(host, labelKey, value) {")
    assert "if (!text.trim()) { return; }" in row


# --- the ban, with the control that makes a "no matches" answer mean anything -


def test_the_asset_names_no_model_and_the_search_that_says_so_can_find_things() -> None:
    """`test_an_option_never_names_a_model_the_server_did_not` next door asserts
    the absence.  An absence is only worth as much as the search that reported
    it, and this repository has been burned by a grep that quietly skipped this
    very file -- so the same reading is made to find something first."""

    body = read_spa().lower()

    # The positive control: strings that are certainly in the asset, found by
    # the same substring reading the ban below uses.  If these ever stop being
    # found, the ban is passing vacuously and this says so instead.
    for present in ("hire-model", "model_options", "profile_id", "runtime.yaml"):
        assert present in body, ("the reading itself is broken", present)

    # The ban.  Every model name the panel can show arrives from
    # `GET /api/catalog`; none is written here, in either table or in any
    # comment, so the panel cannot offer a model this machine cannot run.
    for invented in ("opus", "sonnet", "haiku", "gpt-", "o3", "fable", "gemini", "llama"):
        assert invented not in body, invented
