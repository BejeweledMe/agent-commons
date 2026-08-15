"""Round 4, findings 9, 8 and 2: the words the panel uses for the chat and the
task, the sentence under the hire rationale, and the shape of the hire form.

All three are read out of the single frontend asset, the way the rest of the
panel's invariants are, so a later edit to the markup fails a test rather than
quietly undoing a finding.
"""

from __future__ import annotations

import re

from agent_commons.ui import read_spa


def _language_tables() -> tuple[str, str]:
    """The two STRINGS blocks, parsed the way the symmetry test parses them."""

    table = read_spa().split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    return (
        table.split("en: {", 1)[1].split("\n  },", 1)[0],
        table.split("ru: {", 1)[1].split("\n  },", 1)[0],
    )


def _value(block: str, key: str) -> str:
    match = re.search(rf'^\s*{key}: "(.*)",$', block, re.MULTILINE)
    assert match, key
    return match.group(1)


def _hire_markup(body: str) -> str:
    return body.split('id="hire-modal"', 1)[1].split('id="task-modal"', 1)[0]


def _grant_grids(body: str) -> list[str]:
    """Every `.grants` block in the markup, cut at its own closing tag.

    The blocks hold labels and nothing nested, so the first `</div>` after the
    opening tag is the block's own.
    """

    return [
        chunk.split("</div>", 1)[0]
        for chunk in body.split('<div class="grants"')[1:]
    ]


# --- finding 9: the chat and the task are two surfaces, with two names -------


CHAT_SURFACE_KEYS = (
    "tab_chat",
    "chat_subject_label",
    "chat_message_label",
    "send",
    "start_work",
    "chat_empty_writable",
    "chat_empty_readonly",
    "addressed_roles",
    "unaddressed_prefix",
    "unaddressed_suffix",
)


def test_the_composer_stops_calling_its_own_field_a_task() -> None:
    """The operator met two boxes -- "В чём задача?" and "Сообщение" -- and could
    not tell what the second was for or why there were two.  The chat's own
    field is a SUBJECT and its button OPENS A CHAT; the word "задача" is left to
    the surface that actually records one."""

    english, russian = _language_tables()

    assert _value(english, "chat_subject_label") == "Subject"
    assert _value(russian, "chat_subject_label") == "Тема"
    assert _value(english, "start_work") == "Open the chat"
    assert _value(russian, "start_work") == "Открыть чат"

    # Across the whole chat surface, the Russian word for the other surface's
    # unit appears in exactly one string, and there only as the NAME OF THE
    # BUTTON that leads to it.  Take the button's name out and the word is gone:
    # nothing on this surface calls a field, a box or a message a task.
    button = _value(russian, "task_fab")
    carriers = [key for key in CHAT_SURFACE_KEYS if "задач" in _value(russian, key).lower()]
    assert carriers == ["chat_empty_writable"], carriers
    without_reference = _value(russian, "chat_empty_writable").replace(button, "")
    assert "задач" not in without_reference.lower(), without_reference

    # And the reference points the other way too, from the task form back to the
    # chat -- so whichever door the operator opened first, the form they are
    # looking at says what the other one is for.
    for block, other in ((english, "chat"), (russian, "чат")):
        assert other in _value(block, "task_modal_note").lower(), other


def test_the_empty_chat_offers_the_door_to_the_task_form() -> None:
    """The two surfaces were confused for each other in the empty state, where
    the chat asks to be started.  That is where the other door goes -- opening
    the same modal the board's own button opens, never a second creation path --
    and it is gated on writes exactly as `task-fab` is, because a button whose
    only possible outcome is a refusal is worse than no button."""

    body = read_spa()

    assert 'id="chat-new-task"' in body
    button = re.search(r'<button[^>]*id="chat-new-task"[^>]*>', body, re.S)
    assert button is None or 'data-i18n="task_fab"' in button.group(0)
    # The label and its tooltip are the board button's own, so the two doors
    # cannot come to be called different things.
    empty = body.split('id="chat-empty-actions"', 1)[1].split("</div>", 1)[0]
    assert 'data-i18n="task_fab"' in empty
    assert 'data-i18n-title="tip_task_fab"' in empty

    # One modal, not a second creation path.
    assert (
        'document.getElementById("chat-new-task").addEventListener("click",'
        " () => showTaskModal(true));"
    ) in body

    # Shown only in the empty state, and only where writes are on -- the same
    # condition the subject field is under, and the same gate `task-fab` uses.
    chat = body.split("async function loadChat() {", 1)[1].split("\n}\n", 1)[0]
    assert (
        'document.getElementById("chat-empty-actions").hidden =\n'
        "    Boolean(engagement) || !writesEnabled;"
    ) in chat
    assert 'document.getElementById("task-fab").hidden = !writesEnabled;' in body


# --- finding 8: what the rationale is, and what it is not --------------------


def test_the_rationale_says_it_is_a_record_and_not_an_instruction() -> None:
    """The operator could not tell whether "Why this role exists" was
    documentation or a system prompt.  It is documentation: nothing in it
    reaches the runner.  The form says so under the field, and names the two
    things that DO decide behaviour, so the answer arrives where the question
    is asked rather than in a guide page."""

    body = read_spa()
    english, russian = _language_tables()

    for block in (english, russian):
        assert _value(block, "hire_rationale_help")
    # The label itself is untouched: "kept forever" is a true and useful signal,
    # and renaming the field was not what the finding asked for.
    assert _value(english, "hire_rationale_label") == "Why this role exists (kept forever)"
    assert _value(russian, "hire_rationale_label") == "Зачем нужна эта роль (хранится навсегда)"

    # The Russian line uses the panel's own word for the thing being hired --
    # "агент" is banned by the vocabulary guard, and the entity is a роль here.
    help_ru = _value(russian, "hire_rationale_help")
    assert "агент" not in help_ru.lower()
    assert "роль" in help_ru.lower()
    # It says what it is (a record), what it is not (an instruction), and what
    # decides behaviour instead.
    assert "инструкц" in help_ru.lower()
    for word in ("навык", "задач"):
        assert word in help_ru.lower(), word

    hire = _hire_markup(body)
    stripped = re.sub(r"<!--.*?-->", "", hire, flags=re.S)

    # Directly under the field it explains: nothing at all between the input and
    # the note but the tags that close the one and open the other.
    between = stripped.split('id="hire-rationale"', 1)[1].split(
        'data-i18n="hire_rationale_help"', 1
    )[0]
    assert between.count("<") == 2, between

    # ...and directly above the skills picker it names, which wave 2 landed in
    # the slot this test used to hold open with a marker comment.  The sentence
    # points at the skills and at the task form; the skills are now the very
    # next thing on screen, with no element in between.
    assert (
        hire.index('data-i18n="hire_rationale_help"')
        < hire.index('id="hire-skills-block"')
        < hire.index('id="hire-context-field"')
    )
    after = stripped.split('data-i18n="hire_rationale_help"', 1)[1].split(
        'id="hire-skills-block"', 1
    )[0]
    assert after.count("<") == 2, after


# --- finding 2: the grid, the order, and the budget --------------------------


def test_the_grant_grid_holds_the_three_grants_and_nothing_else() -> None:
    """The two-column grid held four fields of two different shapes, so the
    fourth sat alone in a half-row, the columns ended at different heights, and
    a number read as a fourth permission.  Both forms now put one kind of
    control in the grid, and the odd ones out have rows of their own."""

    body = read_spa()
    grids = _grant_grids(body)
    assert len(grids) == 2, len(grids)
    for grid in grids:
        assert grid.count("<select") == 3, grid
        assert "<input" not in grid, grid
        for name in ("create_roles", "retire_roles", "open_links"):
            assert name in grid, name

    # The three fields that left the grid are in the markup, outside every grid.
    for control in ("hire-turnover_budget", "hire-context_mode", "setting-context_mode"):
        assert f'id="{control}"' in body, control
        for grid in grids:
            assert control not in grid, (control, grid)


def test_the_grant_grid_is_sized_by_what_fits_not_by_a_column_count() -> None:
    """The rules that make the row independent of how long a label runs, and the
    dialog wide enough for two cells of the size the grid asks for."""

    body = read_spa()

    assert "grid-template-columns:repeat(auto-fit,minmax(220px,1fr))" in body
    assert ".grants > *{min-width:0}" in body
    assert "align-items:end" in body.split(".grants{", 1)[1].split("}", 1)[0]
    assert "#hire-modal .modal{width:min(560px,92vw)}" in body
    # The existing cap stays in force under the new width rather than being
    # replaced by it.
    assert ".modal{max-width:92vw}" in body


def test_the_turnover_budget_is_marked_when_it_becomes_required() -> None:
    """The budget is refused-without only above `deny`, and nothing said so.
    The mark and the line arrive with the requirement and leave with it -- but
    the FIELD never does: a control appearing and disappearing under the
    operator's hands is the class of bug round 3 found three times in this
    modal, and hiding the field would hide the one thing the refusal is about."""

    body = read_spa()

    paint = body.split("function paintTurnoverRequirement() {", 1)[1].split("\n}\n", 1)[0]
    assert '["hire-create_roles", "hire-retire_roles"]' in paint
    assert '.some((id) => document.getElementById(id).value !== "deny")' in paint
    assert (
        'document.getElementById("hire-budget-caption").classList.toggle("req", required);'
        in paint
    )
    assert 'document.getElementById("hire-budget-required").hidden = !required;' in paint

    # Nothing in the file may hide the budget field or its input.  The
    # permissions group as a whole still travels with the template path, which
    # is a different mechanism and is not this one.
    for forbidden in (
        'document.getElementById("hire-budget-field").hidden',
        'document.getElementById("hire-turnover_budget").hidden',
    ):
        assert forbidden not in body, forbidden

    # Recomputed everywhere the two grants can move: on the operator's own
    # change, on a fresh opening, and on a repaint that may have reset a value.
    assert (
        'for (const id of ["hire-create_roles", "hire-retire_roles"]) {\n'
        '  document.getElementById(id).addEventListener("change", paintTurnoverRequirement);\n'
        "}"
    ) in body
    for owner in ("function showHire(open, options) {", "function paintHire() {"):
        block = body.split(owner, 1)[1].split("\n}\n", 1)[0]
        assert "paintTurnoverRequirement();" in block, owner

    # The mark is a class, never an inline style, and the static markup still
    # carries exactly the three marks that are unconditionally required.
    assert _hire_markup(body).count('class="req"') == 3
    english, russian = _language_tables()
    for block in (english, russian):
        assert _value(block, "hire_budget_required")
    # The canonical token is printed, not translated, in the sentence about it.
    assert "deny" in _value(russian, "hire_budget_required")


def test_the_hire_form_asks_its_questions_in_the_order_the_plan_settled() -> None:
    """How -> Template -> Name* -> Profile* -> Why* -> Skills -> Context ->
    Grants -> Budget.  Identity before behaviour before permissions,
    with the two visual groups carried by `.subhead` -- collapsing blocks was
    refused, because a collapsed block holding a field the server can refuse
    needs a rule that forces it open and new aria machinery inside the focus
    trap."""

    body = read_spa()
    hire = _hire_markup(body)

    order = [
        'id="hire-mode"',
        'id="hire-preset-field"',
        'data-i18n="hire_group_who"',
        'id="hire-name"',
        'id="hire-profile-field"',
        'id="hire-rationale"',
        'data-i18n="hire_rationale_help"',
        'id="hire-skills-field"',
        'id="hire-context-field"',
        'data-i18n="hire_group_rights"',
        'id="hire-grants"',
        'id="hire-budget-field"',
        'id="hire-budget-required"',
    ]
    seen = [hire.index(anchor) for anchor in order]
    assert seen == sorted(seen), [
        anchor for anchor, _ in sorted(zip(order, seen, strict=True), key=lambda pair: pair[1])
    ]

    # Two groups, both translated, and no collapsing machinery behind them.
    assert hire.count('class="subhead"') == 2
    english, russian = _language_tables()
    for key in ("hire_group_who", "hire_group_rights"):
        for block in (english, russian):
            assert _value(block, key), key
    for forbidden in ("aria-expanded", "<details", "<summary"):
        assert forbidden not in hire, forbidden

    # A hire from a template takes all four permission values from the template,
    # so the heading, the grid, the budget and its line leave as one block --
    # never a heading standing over nothing.
    mode = body.split("function applyHireMode() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("hire-rights").hidden = fromPreset;' in mode
