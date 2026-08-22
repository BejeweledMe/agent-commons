"""Round 4, findings 1 and 3: reading a definition without losing the form, and
choosing skills while hiring rather than after.

Both are read out of the single frontend asset, the way the rest of the panel's
invariants are, plus one end-to-end hire that proves the extra field is carried
by the route that already existed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_commons.catalog import write_role_catalog
from agent_commons.services import CommonsManager
from agent_commons.ui import read_spa
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from tests.ui.conftest import (
    OPERATOR_SURFACE,
    PORT,
    authorized,
    expected_surface,
    mutating_surface,
)


def _language_tables() -> tuple[str, str]:
    table = read_spa().split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    return (
        table.split("en: {", 1)[1].split("\n  },", 1)[0],
        table.split("ru: {", 1)[1].split("\n  },", 1)[0],
    )


def _has_key(block: str, key: str) -> bool:
    return re.search(rf'^\s*{key}: "', block, re.MULTILINE) is not None


# --- finding 1: the answer comes to the field --------------------------------


def test_a_mark_inside_a_dialog_answers_in_place_instead_of_taking_the_form_away() -> None:
    """The "?" beside "Memory between runs" left the hire dialog for the guide,
    so the guard then asked the operator to choose between one definition and
    everything they had typed.  Inside an open dialog the sentence the mark
    already carries as its tooltip is brought to the field instead; outside one
    nothing changes, and the whole guide stays one button away through the same
    departure guard."""

    body = read_spa()

    # One popover for the whole panel, parked outside every dialog: a node per
    # mark would need a language repaint and a closer each.
    assert body.count('id="gpop"') == 1
    assert body.count('id="gpop-text"') == 1
    tail = body.split('id="close-link-modal"', 1)[1]
    assert tail.index('id="gpop"') < tail.index("<script nonce"), "the popover is inside a dialog"

    # The delegate splits on where the mark is, and only there.
    listener = body.split('const link = event.target.closest("[data-guide-anchor]");', 1)[1].split(
        "\n});", 1
    )[0]
    assert "document.getElementById(openModalId).contains(link)" in listener
    assert "if (inDialog) { openGuidePopover(link); return; }" in listener
    assert "openGuide(link.dataset.guidePage, link.dataset.guideAnchor);" in listener

    # Opening it moves ONE node into the field the mark belongs to, and touches
    # neither the open dialog nor the values the guard compares against.
    opener = body.split("function openGuidePopover(link) {", 1)[1].split("\n}\n", 1)[0]
    assert '(link.closest(".field") || link.parentElement).appendChild(pop);' in opener
    assert "pop.hidden = false;" in opener
    for forbidden in ("openModalId =", "modalOpenedAs", "closeModal(", "dismissOpenModal"):
        assert forbidden not in opener, forbidden
    # Focus stays where the operator left it: they were typing, and taking the
    # caret out of the field to read a sentence is the complaint, not the fix.
    assert ".focus(" not in opener
    assert 'aria-live="polite"' in body

    # Placed by a class and by nothing else.  `style-src 'nonce-…'` carries no
    # `style-src-attr`, so a computed position written from JS would be dropped
    # and the popover would render in a corner.
    assert ".gpop{" in body
    assert "position:absolute; top:100%; left:0; right:0;" in body
    assert ".style." not in body
    assert 'setAttribute("style"' not in body

    # Esc has a layer now: the innermost thing closes first, and the second
    # press is the ordinary one, guard and all.
    escape = body.split('if (event.key === "Escape") {', 1)[1].split("\n  }", 1)[0]
    assert 'if (!document.getElementById("gpop").hidden) { closeGuidePopover(); }' in escape
    assert "else { dismissOpenModal(); }" in escape

    # A press elsewhere closes the popover and nothing else.  It cannot dismiss
    # the dialog under it: a backdrop dismissal still requires the press to land
    # on the scrim itself, and the popover is a descendant of the `.modal`.
    press = body.split('document.addEventListener("pointerdown", (event) => {', 1)[1].split(
        "\n});", 1
    )[0]
    assert "if (pop.hidden || pop.contains(event.target)) { return; }" in press
    assert "closeGuidePopover();" in press
    assert "pressedBackdrop = event.target === scrim;" in body

    # A dialog closing hands the popover back, or it would travel inside the
    # dialog's markup and surface in the next one that opens.
    closing = body.split("function closeModal(scrimId) {", 1)[1].split("\n}\n", 1)[0]
    assert "closeGuidePopover();" in closing
    back = body.split("function closeGuidePopover() {", 1)[1].split("\n}\n", 1)[0]
    assert "document.body.appendChild(pop);" in back
    assert "pop.hidden = true;" in back

    # "Read the whole paragraph" is the same departure as before, taken on
    # purpose: it goes through `openGuide`, which goes through the guard.
    more = body.split('document.getElementById("gpop-more").addEventListener', 1)[1].split(
        "\n});", 1
    )[0]
    assert "openGuide(gpopTarget.page, gpopTarget.anchor);" in more
    assert "closeGuidePopover()" not in more, "a refused departure must leave both as they were"

    # Its button is reachable by Tab through the existing trap, because it is
    # inside the scrim and rendered -- the trap itself is not touched.
    trap = body.split("function focusablesIn(scrim) {", 1)[1].split("\n}\n", 1)[0]
    assert "element.offsetParent !== null" in trap


def test_the_popover_repaints_in_place_when_the_language_changes() -> None:
    """A language switch that closed it would be the same interruption in a
    smaller form, so the popover remembers the KEY it was opened with and the
    sentence is rewritten where it stands."""

    body = read_spa()
    assert "gpopKey = link.dataset.i18nTitle;" in body
    paint = body.split("function paintGuidePopover() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("gpop-text").textContent = t(gpopKey);' in paint
    assert "hidden" not in paint, "a repaint must not close the popover"
    table = body.split("const LANGUAGE_SURFACES = [", 1)[1].split("\n];", 1)[0]
    assert '["help_popover", () => paintGuidePopover()]' in table


def test_every_mark_carries_the_sentence_the_popover_shows() -> None:
    """The popover says what the mark's tooltip says, so a mark without a
    `data-i18n-title` -- or with one missing from a table -- would open a
    silently empty box.  Both languages are checked, because the English
    fallback would hide a missing Russian line."""

    body = read_spa()
    english, russian = _language_tables()

    marks = [chunk.split(">", 1)[0] for chunk in body.split('class="gref"')[1:]]
    assert len(marks) >= 17, len(marks)
    for mark in marks:
        found = re.search(r'data-i18n-title="([a-z0-9_]+)"', mark)
        assert found, mark
        for block in (english, russian):
            assert _has_key(block, found.group(1)), (found.group(1), mark)

    # And the popover's own chrome is exactly two new strings: everything else
    # it shows already existed.
    for key in ("gpop_more", "gpop_close"):
        for block in (english, russian):
            assert _has_key(block, key), key
    assert 'data-i18n="gpop_more"' in body
    assert 'data-i18n="gpop_close"' in body


# --- finding 3: skills are chosen while hiring -------------------------------


def test_the_hire_form_chooses_skills_in_the_settings_panel_s_own_words() -> None:
    """The operator had installed skills in the catalogue and no way to require
    them at the moment a role is created.  The picker is the settings panel's,
    verbatim -- same label, same mark, same contract line, same warning past
    three -- because it is the same decision made at two moments."""

    body = read_spa()

    assert '<select id="hire-skills" multiple size="4"></select>' in body
    assert '<select id="setting-skills" multiple size="4"></select>' in body
    block = body.split('id="hire-skills-block"', 1)[1].split('id="hire-context-field"', 1)[0]
    for marker in (
        'data-i18n="setting_skills_label"',
        'data-guide-anchor="g-ct-skill"',
        'data-i18n-title="gref_skills"',
        'data-i18n="skill_contract_line"',
        'data-i18n="skills_too_many"',
    ):
        assert marker in block, marker

    # The threshold is one function on both pickers rather than two copies that
    # can drift apart.
    count = body.split("function paintSkillCount(pickerId, noteId) {", 1)[1].split("\n}\n", 1)[0]
    assert "selectedValues(document.getElementById(pickerId)).length <= 3;" in count
    assert '["setting-skills", "skills-count-note"],' in body
    assert '["hire-skills", "hire-skills-count"],' in body
    assert 'paintSkillCount("hire-skills", "hire-skills-count");' in body

    # The modal is reused, so a fresh opening starts with nothing chosen: the
    # skills of the last role hired must not be the opening state of the next
    # form (round 2's leak, in the one control with no single value to blank).
    opening = body.split("function showHire(open, options) {", 1)[1].split("\n}\n", 1)[0]
    assert 'for (const option of document.getElementById("hire-skills").options) {' in opening
    assert "option.selected = false;" in opening
    assert opening.index("option.selected = false;") < opening.index('openModal("hire-modal"')

    hire = body.split("async function hireRole() {", 1)[1].split("\n}\n", 1)[0]
    # Nothing chosen is not a choice: the key stays off the wire entirely, so a
    # hire that says nothing about skills cannot narrow anything to none.
    assert 'const skills = selectedValues(document.getElementById("hire-skills"));' in hire
    assert "if (skills.length) { body.skills = skills; }" in hire
    # A refusal about the selection lands under the picker, by the label the
    # operator is reading, like the other three fields.
    assert 'skills: ["setting_skills_label", "hire-skills"],' in hire
    assert "showFormError(error, fields, result);" in hire
    # The catalogue's own refusal names no wire field and is not about a blank
    # field, so it is matched for PLACEMENT only: the sentence stays verbatim
    # and lands under the picker instead of under the buttons.
    assert '[/^these skills are not in the operator catalogue: /, "skills"],' in body
    place = body.split("function showFormError(error, fields, result) {", 1)[1].split("\n}\n", 1)[0]
    assert "fields[refusedField(message)] || fields[refusedControl(message)]" in place
    humanize = body.split("function humanizeError(error, fieldLabels) {", 1)[1].split("\n}\n", 1)[0]
    assert "refusedControl" not in humanize, "a precise refusal must not be rewritten as 'blank'"
    # Tools stay out of this form: narrowing needs the profile's tool reference,
    # which is loaded per role, and a hire has no role yet.  (The code, not the
    # comment beside it that says so.)
    assert "tool_allowlist" not in re.sub(r"//.*", "", hire)
    assert 'id="hire-tools' not in body

    # A template carries its own skills, and the backend fills only what the
    # payload leaves unset -- so the block leaves with the rest of what the
    # template decides rather than silently replacing them.
    mode = body.split("function applyHireMode() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("hire-skills-block").hidden = fromPreset;' in mode


def test_a_hire_carrying_a_skill_records_it_through_the_route_that_existed(
    workspace: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The panel gained a field, not a route: `POST /api/agents` already carried
    `skills`, and the domain already refuses one the catalogue does not define.
    This drives the hire the panel now makes and reads the skill back off the
    record, with the mutating surface asserted unchanged beside it."""

    from fastapi.testclient import TestClient

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="hire-with-skills-window",
        principal="local-operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    catalog_file = tmp_path / "catalog.yaml"
    write_role_catalog(
        catalog_file,
        {
            "skills": [{"id": "pytest-runner", "title": "Pytest", "instruction": "run the tests"}],
            "tools": [],
        },
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        catalog_path=catalog_file,
    )
    app = create_app(context, token="test-token", port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        # No route was added for the field.
        assert mutating_surface(app) == expected_surface(context) == OPERATOR_SURFACE

        # The picker offers what the catalogue holds, which is what the panel
        # fills `#hire-skills` from.
        catalog = client.get("/api/catalog", headers=authorized()).json()
        assert [entry["id"] for entry in catalog["skills"]] == ["pytest-runner"]

        created = client.post(
            "/api/agents",
            json={
                "name": "Backend owner",
                "profile_id": "claude-builder",
                "rationale": "owns the backend surface",
                "context_mode": "fresh",
                "grants": {"create_roles": "deny", "retire_roles": "deny", "open_links": "deny"},
                "turnover_budget": None,
                "skills": ["pytest-runner"],
            },
            headers=authorized(),
        )
        assert created.status_code == 200, created.text
        agent_id = created.json()["entity_ref"]["id"]

        record = client.get(f"/api/entities/agent/{agent_id}", headers=authorized()).json()
        assert list(record["record"]["skills"]) == ["pytest-runner"]

        # And a skill the catalogue does not define is still refused at click
        # time, which is why the refusal needed a field to land on.
        ghost = client.post(
            "/api/agents",
            json={
                "name": "Ghost owner",
                "profile_id": "claude-builder",
                "rationale": "asks for a skill nobody installed",
                "skills": ["ghost-skill"],
            },
            headers=authorized(),
        )
        assert ghost.status_code == 409, ghost.text
        assert "ghost-skill" in ghost.json()["error"]["message"]
