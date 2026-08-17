"""Round 4, findings 4 and 5: who is going to run this, and who does it answer to.

Both are answered by saying what is true rather than by adding a control.  The
profile picker reports the provider and the model the operator's own config
names -- and says the model is fixed in the profile when the server did not name
one, because a model invented here would be a model that is not the one that
runs.  There is no model control and no reasoning control: the first is not the
panel's to set, and the second has no field to set anywhere in a runner profile.

The hierarchy the operator looked for in the hire form is not offered either: a
direct hire carries no `created_by_agent_id`, and the rank a card sits in is
computed from exactly that field, so an arrow drawn here would stand for nothing
in the ledger.  What can be recorded -- a link -- is offered after the hire as a
second, deliberate press, and never as a second request chained to the first.

Read out of the single frontend asset, the way the rest of the panel's
invariants are, except the option text itself: that is a claim about behaviour,
so the real `profileOptions` is run under node against the real string tables.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.server import MUTATING_ROUTES

PROFILE_IDS = (
    "claude-builder",
    "codex-builder",
    "claude-independent-reviewer",
    "codex-independent-reviewer",
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


def _in_both_tables(*keys: str) -> None:
    for block in _language_tables():
        for key in keys:
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


def _function(body: str, header: str) -> str:
    """One top-level function, header included, up to its closing brace."""

    return header + body.split(header, 1)[1].split("\n}\n", 1)[0] + "\n}"


def _hire_modal(body: str) -> str:
    return body.split('id="hire-modal"', 1)[1].split('id="task-modal"', 1)[0]


# --- item 12, the option itself ---------------------------------------------


def _options(lang: str, profiles: list[str], info: dict[str, object]) -> list[dict[str, str]]:
    """The real `profileOptions`, over the real tables, under node."""

    body = read_spa()
    # The script travels over stdin, not as an argv element: it embeds the
    # whole STRINGS table, which has outgrown Linux's 128 KiB per-argument
    # ceiling (E2BIG on the CI runners).  With a stdin program node spells
    # argv as [node, "-", ...args], so the user arguments start at index 2.
    harness = "\n".join(
        [
            "let lang = process.argv[2];",
            # The tables themselves, so the sentence under test is the sentence
            # an operator reads and not a fixture that agrees with it.
            "const STRINGS = {"
            + body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
            + "\n};",
            _function(body, "function t(key) {"),
            _function(body, "function said(key, values) {"),
            "const PROFILE_PROVIDER = {"
            + body.split("const PROFILE_PROVIDER = {", 1)[1].split("};", 1)[0]
            + "};",
            _function(body, "function profileOptions(profiles, info) {"),
            "const argv = JSON.parse(process.argv[3]);",
            "console.log(JSON.stringify(profileOptions(argv.profiles, argv.info)));",
        ]
    )
    done = subprocess.run(
        ["node", "-", lang, json.dumps({"profiles": profiles, "info": info})],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run profileOptions() in")
def test_an_option_never_names_a_model_the_server_did_not() -> None:
    """`profile_info` is empty when the operator config cannot be read, and its
    `model` is null when the panel was started without `--profile-config`.  Both
    are the same answer: say where the model is fixed, name nothing."""

    unreadable: dict[str, object] = {}
    unconfigured = {
        name: {"provider": name.split("-", 1)[0], "model": None} for name in PROFILE_IDS
    }

    for info in (unreadable, unconfigured):
        english = _options("en", list(PROFILE_IDS), info)
        russian = _options("ru", list(PROFILE_IDS), info)
        assert [option["id"] for option in english] == list(PROFILE_IDS)
        for option, name in zip(english, PROFILE_IDS, strict=True):
            provider = name.split("-", 1)[0]
            assert option["title"] == f"{name} — {provider} · model: fixed in the profile"
        for option, name in zip(russian, PROFILE_IDS, strict=True):
            provider = name.split("-", 1)[0]
            assert option["title"] == f"{name} — {provider} · модель: закреплена в профиле"

    # And the asset carries no model name of its own to fall back on: the four
    # ids and the two providers are the only proper names in it.
    body = read_spa()
    for invented in ("opus", "sonnet", "haiku", "gpt-", "o3", "fable"):
        assert invented not in body.lower(), invented


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run profileOptions() in")
def test_a_named_model_reaches_the_option_and_the_value_stays_the_bare_id() -> None:
    """With the config readable the option answers "who will run this" in full --
    and the option's VALUE is still the bare `profile_id`, because that id is
    what `POST /api/agents` carries (item 20's compromise, unchanged)."""

    info = {
        "claude-builder": {"provider": "claude", "model": "claude-opus-4-6"},
        "codex-builder": {"provider": "codex", "model": "gpt-5.2-codex"},
    }
    options = _options("en", ["claude-builder", "codex-builder"], info)
    assert [option["id"] for option in options] == ["claude-builder", "codex-builder"]
    assert options[0]["title"] == "claude-builder — claude · model: claude-opus-4-6"
    assert options[1]["title"] == "codex-builder — codex · model: gpt-5.2-codex"

    russian = _options("ru", ["claude-builder"], info)
    assert russian[0]["title"] == "claude-builder — claude · модель: claude-opus-4-6"
    # The canonical half is spelled the ledger's way in both languages; only the
    # words beside it move.
    assert russian[0]["title"].startswith("claude-builder — claude · ")

    # A provider the server reports is what is shown, not what the id spells:
    # the fallback table is a fallback and nothing more.
    reported = {"claude-builder": {"provider": "codex", "model": "x"}}
    surprising = _options("en", ["claude-builder"], reported)
    assert surprising[0]["title"] == "claude-builder — codex · model: x"

    # An id outside the fixed enum claims no provider rather than reading one
    # out of its own spelling.
    unknown = _options("en", ["mystery-profile"], {})
    assert unknown[0]["title"] == "mystery-profile — model: fixed in the profile"


@pytest.mark.skipif(shutil.which("node") is None, reason="no node to run profileOptions() in")
def test_a_profile_the_operator_config_does_not_carry_says_so_in_the_option() -> None:
    """The live run's dead end: the picker offered all four enum ids, the
    operator's runtime.yaml configured fewer, and the tester hired a reviewer
    no launch could ever start.  With the config readable, an id it does not
    carry is marked in its own option — the absence takes the model slot — and
    a warning under the picker says what to do.  With the config unreadable
    the panel knows nothing and marks nothing (the case pinned above)."""

    info = {"claude-builder": {"provider": "claude", "model": "m"}}
    options = _options("en", ["claude-builder", "claude-independent-reviewer"], info)
    assert options[0]["title"] == "claude-builder — claude · model: m"
    assert options[1]["title"] == (
        "claude-independent-reviewer — claude · not in the operator's config (runtime.yaml)"
    )
    russian = _options("ru", ["claude-independent-reviewer"], info)
    assert russian[0]["title"] == (
        "claude-independent-reviewer — claude · нет в конфиге оператора (runtime.yaml)"
    )

    body = read_spa()
    modal = _hire_modal(body)
    assert 'id="hire-profile-missing"' in modal
    assert 'data-i18n="hire_profile_missing"' in modal
    availability = _function(body, "function paintProfileAvailability() {")
    assert "catalog.profile_info" in availability
    assert "Object.keys(info).length > 0" in availability
    # The warning follows the picker off screen on the template path, where the
    # profile is already decided.
    mode = _function(body, "function applyHireMode() {")
    assert 'document.getElementById("hire-profile-missing").hidden = true;' in mode
    _in_both_tables("hire_profile_unconfigured", "hire_profile_missing")
    english, russian_table = _language_tables()
    for block in (english, russian_table):
        assert "runtime.yaml" in _value(block, "hire_profile_missing")


def test_the_option_carries_its_gloss_and_the_long_one_keeps_its_surface() -> None:
    """The composed line is the picker's gloss, and it obeys the same rule the
    table-driven ones do: canonical token first, human words after the dash,
    never instead of it.  `gl_builder` / `gl_reviewer` deliberately stay off the
    option -- a closed select is one line wide (round 4, finding 2) -- and keep
    the drawer summary, which is the surface with room for them."""

    body = read_spa()
    options = _function(body, "function profileOptions(profiles, info) {")
    assert 'return { id: id, title: id + " — " + parts.join(" · ") };' in options
    # The model half is a translated fallback, never a literal standing in for a
    # name the server owes us.
    assert 'const model = known.model || t("hire_profile_model_fixed");' in options

    # The role-shape gloss is still keyed by all four ids, and still reaches the
    # summary rows through `valueWithGloss`.
    gloss = body.split("const VALUE_GLOSS = {", 1)[1].split("};", 1)[0]
    for name in PROFILE_IDS:
        assert f'"{name}"' in gloss, name
    assert 'summaryRow(host, "hire_profile_label", valueWithGloss(record.profile_id));' in body

    # The picker is fed by this and by nothing else, and a repaint still cannot
    # move the operator's choice: `held` re-selects by the bare id, which is the
    # same token in either language.
    paint = _function(body, "function paintHire() {")
    assert "profileOptions(catalog.profiles || [], catalog.profile_info || {})" in paint
    assert 'held("hire-profile", null)' in paint
    assert body.count("profileOptions(") == 2

    _in_both_tables("hire_profile_model", "hire_profile_model_fixed", "hire_profile_help")
    english, russian = _language_tables()
    assert "{model}" in _value(english, "hire_profile_model")
    assert "{model}" in _value(russian, "hire_profile_model")


def test_the_panel_offers_no_model_control_and_no_reasoning_control() -> None:
    """The refusal, pinned.  A reasoning level is not a field of either runner
    profile, and the model belongs to a config the panel may not write -- so
    neither gets a control, in the hire form or anywhere else."""

    body = read_spa()
    for element in re.findall(r'<(?:select|input)[^>]*id="([a-z0-9_-]+)"', body):
        # `task-reopen-reason` is a person's sentence about a decision, not a
        # setting on a process, so the word itself is not what is forbidden --
        # a control that claims to set how hard a role thinks is.
        for forbidden in ("model", "reasoning", "effort", "thinking"):
            assert forbidden not in element, element
    assert "ризонинг" not in _hire_modal(body).lower()

    # Nothing about a model goes onto the wire either: the hire's payload names
    # a profile and the profile decides the rest.
    hire = _function(body, "async function hireRole() {")
    assert "model" not in hire
    assert 'body.profile_id = document.getElementById("hire-profile").value;' in hire

    # The one line under the picker says where the answer is edited instead, and
    # the mark beside it opens the paragraph that says why there are four.
    modal = _hire_modal(body)
    assert 'data-i18n="hire_profile_help"' in modal
    assert 'data-guide-anchor="g-ag-profile"' in modal
    # And it travels with the picker it explains: on the template path the
    # profile is already decided, so a line about choosing one would stand over
    # a control that is not on screen.
    mode = _function(body, "function applyHireMode() {")
    assert 'document.getElementById("hire-profile-help").hidden = fromPreset;' in mode
    english, russian = _language_tables()
    for block in (english, russian):
        assert "runtime.yaml" in _value(block, "hire_profile_help")
        assert "workspace" in _value(block, "hire_profile_help")
    # The reasoning refusal is explained where the reader can reach it, in both
    # languages, rather than left as an absence.
    assert "reasoning level is not offered" in _value(english, "guide_ag_profile_p")
    assert "ризонинга не предлагается" in _value(russian, "guide_ag_profile_p")


def test_the_guide_says_a_variant_is_a_profile_and_the_set_is_fixed_at_four() -> None:
    """Item 12 (5).  The set is closed because the narrowing rule that keeps a
    role from creating a wider role is keyed by exactly these ids; the page says
    so, and says where new variants would be described instead."""

    body = read_spa()
    page = body.split('<div id="gpage-agents"', 1)[1].split("\n        </div>", 1)[0]
    assert '<h3 id="g-ag-profile"' in page
    for marker in ("guide_ag_profile_h", "guide_ag_profile_p", "guide_ag_profile_ex"):
        assert f'data-i18n="{marker}"' in page, marker
    # Owned by `data-i18n` and not also written into the markup, like every
    # other paragraph on this page.
    written = re.findall(r'data-i18n="guide_ag_profile_(?:\w+)"[^>]*>([^<]*)<', page)
    assert written and all(shown.strip() == "" for shown in written), written

    english, russian = _language_tables()
    for block in (english, russian):
        paragraph = _value(block, "guide_ag_profile_p")
        assert "runtime.yaml" in paragraph
        # The example names a real id rather than a shape, so the reader can
        # match what the page says to what the picker offered.
        assert "claude-builder" in _value(block, "guide_ag_profile_ex")
    assert "четырьмя" in _value(russian, "guide_ag_profile_p")
    assert "four" in _value(english, "guide_ag_profile_p")
    # The Russian page keeps the panel's one word per concept.
    for suffix in ("h", "p", "ex"):
        line = _value(russian, f"guide_ag_profile_{suffix}").lower()
        for forbidden in ("агент", "делегац", "скилл", "тулл", "борд"):
            assert forbidden not in line, (suffix, forbidden)


# --- item 11, the link as a second press -------------------------------------


def test_the_hire_form_states_who_the_new_role_answers_to() -> None:
    """Item 11 (1) and (4).  The form says what is true about rank instead of
    offering a control for it, and nothing anywhere draws a subordination the
    ledger cannot back."""

    body = read_spa()
    assert 'data-i18n="hire_chain_note"' in _hire_modal(body)
    _in_both_tables("hire_chain_note")
    english, russian = _language_tables()
    assert "answers to you" in _value(english, "hire_chain_note")
    assert "approves" in _value(english, "hire_chain_note")
    assert "подчиняется оператору" in _value(russian, "hire_chain_note")
    assert "утверждённого человеком" in _value(russian, "hire_chain_note")
    # Saving a template hires nobody, so on that path the sentence has nothing
    # to be about and does not appear.
    mode = _function(body, "function applyHireMode() {")
    assert 'document.getElementById("hire-chain-note").hidden = hireTemplate;' in mode

    # Every place the panel says one thing answers to another, and nothing else.
    # `band_operator` and `session_you_tip` caption the ranks the SERVER computed
    # out of `created_by_agent_id`; `chat_empty_writable` is about the operator's
    # own chat reaching every role; `hire_chain_note` is the sentence above.  A
    # fifth claim of rank has to justify itself here before it reaches a screen.
    claims = {
        line.strip().split(":", 1)[0] for line in russian.split("\n") if "подчин" in line.lower()
    }
    assert claims == {
        "band_operator",
        "session_you_tip",
        "chat_empty_writable",
        "hire_chain_note",
    }, claims
    # The one rank flag the panel draws is read off the node the server sent and
    # is never assembled here: `reports_to_operator` is computed in ui/graph.py
    # out of the lineage field, which is what makes it something to draw.
    assert set(re.findall(r"(\w*\.)?reports_to_operator", body)) == {"node."}
    # The lineage field is READ in one place -- the drawer, off the record the
    # server sent -- and written nowhere: no hire payload carries it.
    assert "body.created_by_agent_id" not in body
    hire = _function(body, "async function hireRole() {")
    assert "created_by" not in hire


def test_the_link_after_a_hire_is_a_button_and_not_a_second_request() -> None:
    """Item 11 (2) and (3).  A chain of two writes has a half-state -- role
    hired, link refused -- and the button removes that state rather than
    reporting it.  The dialog opens with `from` filled and `to` blank, because
    who the new role may reach is the question."""

    body = read_spa()
    hire = _function(body, "async function hireRole() {")
    # Exactly one write, and it is the hire.
    assert hire.count("post(") == 1
    assert 'await post("/api/agents", body)' in hire
    assert "openLinkModal" not in hire, "the hire must not open the link itself"
    assert "if (!hireTemplate) { offerLinkFrom(newId, hiredName || newId); }" in hire

    offer = _function(body, "function offerLinkFrom(agentId, name) {")
    assert 'openLinkModal(agentId, "");' in offer
    assert "post(" not in offer
    assert 'said("open_link_from", { name: name })' in offer
    # Silent when the board does not carry the role: the same rule
    # `openNodeById` keeps, read off the same candidate list the dialog uses.
    assert "linkCandidates(null).some((role) => role.id === agentId)" in offer
    _in_both_tables("open_link_from")
    english, russian = _language_tables()
    assert "{name}" in _value(english, "open_link_from")
    assert "{name}" in _value(russian, "open_link_from")

    # And no route was added for any of it: both writes this feature touches
    # already existed, and the second one is the dialog's, reached by a press.
    assert ("POST", "/api/agents") in set(MUTATING_ROUTES)
    assert ("POST", "/api/agent-links") in set(MUTATING_ROUTES)
    assert len(MUTATING_ROUTES) == 14, len(MUTATING_ROUTES)


def test_the_offer_cannot_outlive_the_role_or_the_moment_it_names() -> None:
    """A button standing on the board pointing at a role that has gone, or at a
    decision the operator walked away from, would be worse than no button.  Four
    ends to its life, and all four are here."""

    body = read_spa()
    offer = _function(body, "function offerLinkFrom(agentId, name) {")
    # (1) spent on the first press, before the dialog it opens.
    assert offer.index("clearLinkOffer();") < offer.index("openLinkModal(agentId")

    # (2) the board's line replaces it whole, so the flag goes with the node.
    report = _function(body, "function reportOnBoard(sentence, canonicalId) {")
    assert report.index("boardOfferedRole = null;") < report.index("reportResult(")

    # (3) a repaint that no longer lists the role takes it away, and it happens
    # where every snapshot passes: beside the assignment of `lastGraph` itself.
    render = _function(body, "function render(graph) {")
    assert render.index("pruneLinkOffer();") < render.index("onboarding")
    assert "lastGraph = graph;" in render
    prune = _function(body, "function pruneLinkOffer() {")
    assert "linkCandidates(null).some((role) => role.id === boardOfferedRole)" in prune
    assert "clearLinkOffer();" in prune

    # (4) opening any dialog is the next action, and the offer belonged to the
    # one before it.
    modal = _function(body, "function openModal(scrimId, focusId) {")
    assert "clearLinkOffer();" in modal

    # It is a real button on the board's line, removed through the DOM rather
    # than through a markup write the CSP would drop.
    clear = _function(body, "function clearLinkOffer() {")
    assert 'document.querySelector("#board-result .followup")' in clear
    assert "parentElement.removeChild(button)" in clear
    assert "#board-result .followup{" in body
    assert 'button.className = "followup";' in offer


def test_the_flat_team_and_the_handoff_right_explain_themselves() -> None:
    """Round 5, two multi-role testers independently: both went looking for a
    team-lead control and both read handoff_work as an automatic transfer.
    The hire form now says the flat start is deliberate and carries a mark
    into the lineage paragraph; the link dialog and the reference both say a
    handoff_work link is a right, not a process — nothing moves until a run
    is actually launched."""

    body = read_spa()
    english, russian = _language_tables()

    note = _hire_modal(body).split('id="hire-chain-note"', 1)[1].split("</p>", 1)[0]
    assert 'data-guide-anchor="g-ag-lineage"' in note
    assert 'data-i18n-title="gref_chain"' in note
    assert "on purpose" in _value(english, "hire_chain_note")
    assert "намеренно" in _value(russian, "hire_chain_note")
    _in_both_tables("gref_chain")
    # The popover parks under the note, so the note is its positioned ancestor.
    assert "#hire-chain-note{position:relative}" in body

    # The right-not-a-process sentence sits where the choice is made AND in the
    # reference, in both languages.
    assert "a right, not a process" in _value(english, "link_permission_note")
    assert "право, а не процесс" in _value(russian, "link_permission_note")
    assert "a right, not a process" in _value(english, "guide_ln_handoff_p")
    assert "право, а не процесс" in _value(russian, "guide_ln_handoff_p")
    assert "hands nothing over" in _value(english, "guide_ln_handoff_p")
