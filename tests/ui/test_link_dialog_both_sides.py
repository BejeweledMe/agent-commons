"""Round 4, finding 6: a link could only be drawn to a neighbour.

The operator dragged a port, missed, and nothing happened; the dialog that a
successful drag opened printed the pair as a sentence, so the far side could
not be corrected without dragging again — and a role the layout had put on the
other side of the board could not be reached at all.

Wave 3 item 10 makes both sides fields, marks the card a release would land on
while the release is still coming, and turns a miss into the same dialog with
the far side left blank.  It is read out of the single frontend asset, the way
the rest of the panel's invariants are, plus one end-to-end open that proves
the pair the two selects produce still fits the route that already existed.
"""

from __future__ import annotations

import re
from typing import Any

from agent_commons.ui import read_spa
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import MUTATING_ROUTES
from tests.ui.conftest import authorized, expected_surface, mutating_surface


def _language_tables() -> tuple[str, str]:
    table = read_spa().split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    return (
        table.split("en: {", 1)[1].split("\n  },", 1)[0],
        table.split("ru: {", 1)[1].split("\n  },", 1)[0],
    )


def _function(body: str, signature: str) -> str:
    return body.split(signature, 1)[1].split("\n}\n", 1)[0]


NEW_KEYS = (
    "link_from_label",
    "link_to_label",
    "link_pick_placeholder",
    "link_swap",
    "link_needs_role",
    "link_same_role",
    "link_already_open",
)


# --- (1) and (2): the pair is a pair of fields, and nothing else holds it -----


def test_both_sides_of_the_link_are_fields_fed_by_one_candidate_list() -> None:
    """The static `#link-pair` sentence is gone, and with it the module global
    that shadowed it.  The two selects are the state now, which is the point:
    the dismissal guard walks the dialog's controls, so a pair kept in a
    variable was invisible to it and a pair kept in selects is not."""

    body = read_spa()
    dialog = body.split('id="link-modal"', 1)[1].split('id="close-link-modal"', 1)[0]

    # The sentence and the global are both gone.  `close-link-pair` is a
    # different dialog and keeps its own line.
    assert 'id="link-pair"' not in body
    assert re.search(r"\blinkPair\b", body) is None, "the pair still lives outside the form"

    for control in ('id="link-from"', 'id="link-to"', 'id="link-swap"'):
        assert dialog.count(control) == 1, control
    assert dialog.count('data-i18n="link_pick_placeholder"') == 2, "one side cannot be emptied"

    # One collector, and the same rule the board draws by: an active role that
    # is not shelf stock.  Two readings would let the dialog offer a role the
    # drop refuses, or hide one the drop accepts.
    collector = _function(body, "function linkCandidates(excludeId) {")
    assert 'node.kind === "agent" && node.state === "active" && node.id !== excludeId' in collector
    assert "!(node.attrs && node.attrs.template)" in collector
    assert body.count("function linkCandidates(") == 1
    # The drawer's picker was the second copy of that filter; it is a caller now.
    links = _function(body, "function paintLinks(agentId) {")
    assert "const others = linkCandidates(agentId);" in links
    assert 'node.kind === "agent" && node.state === "active"' not in links

    # The submit reads the controls, not a remembered pair.
    submit = body.split('document.getElementById("link-open-go").addEventListener', 1)[1].split(
        "\n});", 1
    )[0]
    assert 'const fromId = document.getElementById("link-from").value;' in submit
    assert 'const toId = document.getElementById("link-to").value;' in submit
    assert "from_agent_id: fromId," in submit
    assert "to_agent_id: toId," in submit

    # And the guard can see them, because it walks every control in the scrim.
    state = _function(body, "function modalFieldState(scrim) {")
    assert 'scrim.querySelectorAll("input, select, textarea")' in state


# --- the risk the plan named: filled before the guard takes its sample -------


def test_the_dialog_fills_the_pair_before_the_guard_samples_the_form() -> None:
    """Named as a risk in the plan and worth a test of its own: `openModal`
    samples the dialog's values LAST, as its opening state.  Fill the selects
    after that call and the sample is taken over two blank controls, so the
    pair the drag just put there reads as an edit the operator never made — and
    the first Esc asks whether to discard something nobody typed."""

    body = read_spa()
    opener = _function(body, "function openLinkModal(fromId, toId) {")

    fill_from = opener.index('fillRoleSelect(document.getElementById("link-from")')
    fill_to = opener.index('fillRoleSelect(document.getElementById("link-to")')
    opened = opener.index('openModal("link-modal"')
    assert fill_from < opened, "the from side is filled after the guard's sample"
    assert fill_to < opened, "the to side is filled after the guard's sample"

    # The other half of the same fact: the sample really is taken last, so the
    # ordering above is what decides it.
    modal = _function(body, "function openModal(scrimId, focusId) {")
    assert modal.rstrip().endswith("modalOpenedAs = modalFieldState(scrim);")

    # Stale refusals from the previous opening do not survive into this one.
    assert "clearFieldErrors(modal);" in opener


# --- (3): the direction is correctable without a second drag ------------------


def test_one_button_reverses_a_link_that_was_drawn_the_wrong_way() -> None:
    """Direction is the whole meaning of the record, and a drag fixes it before
    the operator has read a word of the dialog."""

    body = read_spa()
    swap = body.split('document.getElementById("link-swap").addEventListener', 1)[1].split(
        "\n});", 1
    )[0]
    assert "const held = from.value;" in swap
    assert "from.value = to.value;" in swap
    assert "to.value = held;" in swap
    # A swap is an edit like any other: it does NOT re-sample the guard, so
    # pressing it and then Esc still asks before dropping the form.
    assert "modalOpenedAs" not in swap


# --- (4) and (5): a miss costs a click, not the drag -------------------------


def test_a_release_into_empty_space_opens_the_dialog_instead_of_cancelling() -> None:
    """The release used to be judged and then, on a miss, discarded in silence:
    the operator aimed, missed, and the panel behaved exactly as if they had
    never dragged.  The same dialog opens either way now — with the far side
    blank, and the caret in it."""

    body = read_spa()
    finish = body.split("const finish = (up) => {", 1)[1].split("\n  };", 1)[0]
    assert "const best = nearestDropTarget(up.clientX, up.clientY, from.id);" in finish
    assert 'openLinkModal(from.id, best ? best.id : "");' in finish
    assert "if (best) {" not in finish, "a miss is still being thrown away"

    # An empty far side puts the caret on the question the operator has to
    # answer; a drag that landed leaves it where it was, on the reason.
    opener = _function(body, "function openLinkModal(fromId, toId) {")
    assert 'openModal("link-modal", toId ? "link-reason" : "link-to");' in opener
    # A blank id selects the placeholder rather than standing selected and
    # unofferable, which is what makes "empty" a real state of the control.
    fill = _function(body, "function fillRoleSelect(element, roles, selected) {")
    assert 'element.value = roles.some((role) => role.id === selected) ? selected : "";' in fill

    # The reach is NOT widened to pay for the miss.  Every pixel of it is
    # another chance to land on the card beside the one intended, and the miss
    # no longer costs anything (plan, wave 3 item 10, point 5).
    assert "const DROP_REACH = 64;" in body
    assert "distance <= DROP_REACH" in body
    assert body.count("DROP_REACH = ") == 1


# --- (6): the card that lights up is the card the drop lands on --------------


def test_the_highlight_during_the_drag_is_the_same_search_as_the_drop() -> None:
    """A highlight computed separately from the drop is a promise the release
    does not keep: the two would disagree the moment either changed.  There is
    one search, called from the move and from the release."""

    body = read_spa()
    assert body.count("function nearestDropTarget(") == 1
    search = _function(body, "function nearestDropTarget(clientX, clientY, fromId) {")
    assert 'group.getAttribute("data-node-state") !== "active"' in search
    assert "toId === fromId" in search
    assert "distance <= DROP_REACH" in search

    trace = body.split("const trace = (move) => {", 1)[1].split("\n  };", 1)[0]
    assert "markDropTarget(nearestDropTarget(move.clientX, move.clientY, node.id));" in trace
    finish = body.split("const finish = (up) => {", 1)[1].split("\n  };", 1)[0]
    assert "markDropTarget(null);" in finish, "the mark outlives the drag"
    # Cleared before the early return, or a drag that ended with no origin would
    # leave a card marked for good.
    assert finish.index("markDropTarget(null);") < finish.index("if (!from) { return; }")

    # The mark is a class and the look of it is CSS.  `style-src 'nonce-…'`
    # carries no `style-src-attr`: a stroke written from JS would be dropped.
    mark = _function(body, "function markDropTarget(best) {")
    assert 'best.group.classList.add("droptarget");' in mark
    assert 'group.classList.remove("droptarget");' in mark
    assert ".node.droptarget rect{" in body
    assert ".style." not in body
    assert 'setAttribute("style"' not in body


# --- (7): what the form can answer itself, it answers before it writes -------


def test_the_form_answers_a_blank_side_and_a_self_link_before_it_writes() -> None:
    body = read_spa()
    submit = body.split('document.getElementById("link-open-go").addEventListener', 1)[1].split(
        "\n});", 1
    )[0]

    assert 'if (!fromId) { showFieldNote("link-from", t("link_needs_role")); return; }' in submit
    assert 'if (!toId) { showFieldNote("link-to", t("link_needs_role")); return; }' in submit
    assert (
        'if (fromId === toId) { showFieldNote("link-to", t("link_same_role")); return; }'
    ) in submit
    # Before the write, all three of them.
    for guard in ("link_needs_role", "link_same_role", "link_already_open"):
        assert submit.index(guard) < submit.index('post("/api/agent-links"'), guard

    # The line is the same line the server's refusals get, from the same placer.
    assert "function showFieldNote(inputId, text) {" in body
    assert "showFieldNote(entry[1], text);" in _function(body, "function showFormError(")
    # ...and the server's refusal about a side now has a side to land on.
    assert 'from_agent_id: ["link_from_label", "link-from"],' in body
    assert 'to_agent_id: ["link_to_label", "link-to"],' in body
    assert "showFormError(error, LINK_FIELDS, result);" in submit


def test_an_already_open_permission_is_a_warning_and_never_a_veto() -> None:
    """The plan is explicit that this one may only advise.  The domain records a
    second permission for the same pair without complaint, so a hard stop would
    be the panel enforcing a rule the ledger does not have -- and `lastGraph` is
    a snapshot that can be behind, so the stop would sometimes be about a link
    that has already been closed.  One press says so; the same press again goes
    through, and the journal answers."""

    body = read_spa()
    submit = body.split('document.getElementById("link-open-go").addEventListener', 1)[1].split(
        "\n});", 1
    )[0]

    # Remembered, and compared against, so the second press is not stopped again.
    assert 'const pair = fromId + "|" + toId + "|" + action;' in submit
    assert (
        "if (pair !== linkWarnedPair && permissionAlreadyOpen(fromId, toId, action)) {"
    ) in submit
    assert "linkWarnedPair = pair;" in submit
    # A fresh opening starts over: a warning is about a pair, not about a session.
    assert "linkWarnedPair = null;" in _function(body, "function openLinkModal(fromId, toId) {")

    # It reads the board's own snapshot, through the same edge reading the Links
    # tab uses -- two readings would disagree about which links exist.
    check = _function(body, "function permissionAlreadyOpen(fromId, toId, action) {")
    assert "for (const [linkId, entry] of linksFromEdges(graph)) {" in check
    assert 'if (!node || node.state !== "open") { continue; }' in check
    # Keyed on the action too: `ask` and `handoff_work` between the same roles
    # are two permissions, not one recorded twice.
    assert "entry.allowed_action === action" in check
    assert "const linksById = linksFromEdges(graph);" in _function(
        body, "function paintLinks(agentId) {"
    )


# --- the words, in both languages --------------------------------------------


def test_the_link_dialog_speaks_both_panel_languages() -> None:
    body = read_spa()
    english, russian = _language_tables()
    for key in NEW_KEYS:
        for block, name in ((english, "en"), (russian, "ru")):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), (key, name)
        # Every new key is actually rendered -- three from the markup, three
        # from the guards above.  A key nothing reaches proves nothing.
        assert f'data-i18n="{key}"' in body or f't("{key}")' in body, key

    # The placeholder is owned by `data-i18n`, not written from JS: the dialog
    # has no repaint of its own, and the roles beneath it are proper names that
    # nobody translates.  `fillRoleSelect` therefore leaves it standing.
    fill = _function(body, "function fillRoleSelect(element, roles, selected) {")
    assert "if (option.value) { element.removeChild(option); }" in fill


def test_the_asset_still_carries_no_literal_nul_byte() -> None:
    """Wave 1 item 6 replaced the one literal NUL in this file with an escape:
    a grep-family tool treats an asset containing one as binary and skips it in
    silence, and a tool that strips the byte instead would collapse the
    separator it spells to "" and glue adjacent fields together.  Writing this
    dialog put one straight back, through a key built the same way -- so the
    byte gets a guard rather than a note in a commit message."""

    assert "\x00" not in read_spa()
    assert '.join("\\u0000")' in read_spa(), "the guard's separator lost its escape"


# --- (8): the same one write path, and no new route --------------------------


def test_the_pair_the_two_selects_produce_still_fits_the_route_that_existed(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """Both sides being editable changes what the operator can say, not what the
    panel may call: the body is the one `POST /api/agent-links` already took,
    and the mutating surface is untouched."""

    body = read_spa()
    assert body.count('post("/api/agent-links"') == 1
    assert ("POST", "/api/agent-links") in set(MUTATING_ROUTES)

    def hire(name: str) -> str:
        response = writable_client.post(
            "/api/agents",
            json={
                "name": name,
                "profile_id": "claude-builder",
                "rationale": "a standing owner for " + name,
            },
            headers=authorized(),
        )
        assert response.status_code == 200, response.text
        return str(response.json()["entity_ref"]["id"])

    a_id, b_id = hire("A"), hire("B")

    def open_link(from_id: str, to_id: str) -> Any:
        # Exactly the four keys the submit sends: no deadline, no link id.
        return writable_client.post(
            "/api/agent-links",
            json={
                "from_agent_id": from_id,
                "to_agent_id": to_id,
                "allowed_action": "ask",
                "reason": "B answers questions about the schema",
            },
            headers=authorized(),
        )

    assert open_link(a_id, b_id).status_code == 200

    # The swapped pair is a different permission and is recorded as one, which
    # is why the button is worth having and why the direction is not cosmetic.
    assert open_link(b_id, a_id).status_code == 200

    # And the panel's own pre-send check is advice, not law: the domain accepts
    # the same permission twice.  A UI that refused would be inventing a rule.
    assert open_link(a_id, b_id).status_code == 200

    assert mutating_surface(writable_client.app) == expected_surface(writable)
