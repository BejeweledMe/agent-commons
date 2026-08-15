"""Stream framing, resume honesty, and the self-contained frontend asset."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from agent_commons.ui import read_spa
from agent_commons.ui.context import UIContext, ledger_fingerprint
from agent_commons.ui.server import _events, _parse_last_event_id, create_app
from tests.ui.conftest import PORT


def drive(context: UIContext, last_event_id: str | None, count: int) -> list[bytes]:
    async def run() -> list[bytes]:
        frames: list[bytes] = []
        generator = _events(context, last_event_id)
        try:
            for _ in range(count):
                frames.append(await anext(generator))
        finally:
            await generator.aclose()
        return frames

    return asyncio.run(run())


def test_stream_opens_with_hello_then_a_self_contained_snapshot(context: UIContext) -> None:
    frames = drive(context, None, 2)
    assert b"event: hello" in frames[0]
    assert b"event: snapshot" in frames[1]
    assert b'"graph"' in frames[1]


def test_a_last_event_id_from_another_instance_reports_a_restart(context: UIContext) -> None:
    frames = drive(context, "some-other-instance:4", 3)
    assert b"event: resume_gap" in frames[2]
    assert b'"server_restarted"' in frames[2]


def test_a_behind_client_is_told_about_the_gap_explicitly(context: UIContext) -> None:
    context.rebuild_graph()
    context.rebuild_graph()
    frames = drive(context, f"{context.server_instance_id}:1", 3)
    assert b"event: resume_gap" in frames[2]
    assert b'"no_event_history"' in frames[2]


def test_a_caught_up_client_receives_no_gap(context: UIContext) -> None:
    context.rebuild_graph()
    frames = drive(context, f"{context.server_instance_id}:{context.seq}", 2)
    assert not any(b"resume_gap" in frame for frame in frames)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("7", None),
        ("instance:notanumber", None),
        ("instance:7", ("instance", 7)),
    ],
)
def test_last_event_id_parsing(value: str | None, expected: tuple[str, int] | None) -> None:
    assert _parse_last_event_id(value) == expected


def test_the_stream_route_requires_a_token(client) -> None:  # type: ignore[no-untyped-def]
    with client.stream("GET", "/api/stream") as response:
        assert response.status_code == 401


def test_fingerprint_is_stable_until_the_ledger_changes(
    context: UIContext, workspace: dict[str, Any]
) -> None:
    paths = context.paths()
    first = ledger_fingerprint(paths)
    assert first == ledger_fingerprint(paths)
    (paths.events / "2026" / "01" / "01").mkdir(parents=True, exist_ok=True)
    (paths.events / "2026" / "01" / "01" / "evt.probe.json").write_text("{}", encoding="utf-8")
    assert ledger_fingerprint(paths) != first


def test_the_app_registers_only_read_routes(context: UIContext) -> None:
    app = create_app(context, token="t", port=PORT)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/graph" in paths
    for route in app.routes:
        assert (getattr(route, "methods", set()) or set()) <= {"GET", "HEAD"}


def test_the_spa_is_readable_as_a_package_resource() -> None:
    body = read_spa()
    assert "__CSP_NONCE__" in body
    assert len(body) > 1000


def test_the_spa_has_no_external_references() -> None:
    body = read_spa()
    for forbidden in ("http://", "https://", "//cdn", "<script src", '<link rel="stylesheet"'):
        if forbidden in ("http://", "https://"):
            # The XML namespace literal is a URI, not a fetched resource.
            occurrences = [
                match
                for match in re.findall(r"https?://[^\"'\s]+", body)
                if match != "http://www.w3.org/2000/svg"
            ]
            assert occurrences == [], occurrences
            continue
        assert forbidden not in body, forbidden


def test_the_spa_never_uses_an_unsafe_dom_api() -> None:
    body = read_spa()
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert forbidden not in body, forbidden


def test_the_spa_carries_no_inline_style_the_csp_would_drop() -> None:
    """The CSP is `style-src 'nonce-…'` with no `style-src-attr`, so any inline
    style attribute or CSSOM style write is silently dropped -- which is how the
    search box lost its styling and the catalogue headings theirs (L6). All
    styling must live in the nonce'd <style> block."""

    body = read_spa()
    assert "style=" not in body, "a markup style attribute would be blocked by the CSP"
    assert ".style.cssText" not in body, "a CSSOM cssText write would be blocked by the CSP"


def test_stream_ids_round_trip_through_the_parser(context: UIContext) -> None:
    """Regression: the server emitted a bare counter while the parser required
    instance:seq, so the resume_gap branch was unreachable for any client that
    did not reassemble the id itself."""

    frames = drive(context, None, 2)
    first_line = frames[0].split(b"\n")[0].decode()
    assert first_line.startswith("id: ")
    parsed = _parse_last_event_id(first_line.removeprefix("id: "))
    assert parsed is not None
    assert parsed[0] == context.server_instance_id


def test_a_replayed_stream_id_produces_a_resume_gap(context: UIContext) -> None:
    context.rebuild_graph()
    context.rebuild_graph()
    frames = drive(context, None, 1)
    stale = frames[0].split(b"\n")[0].decode().removeprefix("id: ")
    instance, _ = _parse_last_event_id(stale)
    resumed = drive(context, f"{instance}:1", 3)
    assert b"resume_gap" in resumed[2]


def test_the_fingerprint_notices_a_new_session(populated, context: UIContext) -> None:  # type: ignore[no-untyped-def]
    """Regression: sessions are graph nodes but the fingerprint only covered the
    ledger, so a session opening or expiring never refreshed the view."""

    from agent_commons.services import CommonsManager

    context.rebuild_graph()
    before = context.fingerprint()
    manager = CommonsManager(populated["repo"], state_root=populated["state_root"])
    manager.sessions.open_session(
        stable_instance_id="second-window",
        principal="local-operator",
        client="codex",
        software="codex-cli",
        role="independent-reviewer",
    )
    assert context.fingerprint() != before
    assert context.refresh_if_changed() is True
    sessions = [node for node in context.graph()["nodes"] if node["kind"] == "session"]
    assert len(sessions) == 2


def test_only_acceptance_may_render_as_a_green_tick() -> None:
    """The plan states this as a rule: a tick means a human accepted the work,
    never that a process exited zero.  Parsed from the asset so a frontend edit
    cannot quietly reintroduce the conflation."""

    body = read_spa()
    table = body.split("const GLYPHS = {", 1)[1].split("};", 1)[0]
    ok_states = set(re.findall(r"(\w+):\s*\[\"[^\"]+\",\s*\"ok\"\]", table))
    assert ok_states == {"accepted", "approved"}, ok_states
    for state in ("succeeded", "completed", "done"):
        assert re.search(rf"{state}:\s*\[\"[^\"]+\",\s*\"info\"\]", table), state


def test_the_two_panel_languages_translate_exactly_the_same_keys() -> None:
    """The chrome is translated from one STRINGS table with an English fallback,
    so a key present in one language and missing in the other would silently
    show English inside a Russian panel.  Parsed from the asset, like GLYPHS,
    so a frontend edit cannot quietly desynchronise the two languages."""

    body = read_spa()
    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    en_block = table.split("en: {", 1)[1].split("\n  },", 1)[0]
    ru_block = table.split("ru: {", 1)[1].split("\n  },", 1)[0]
    # Anchored to the line start: a value may itself end with a colon.
    en_keys = set(re.findall(r'^\s*([a-z0-9_]+): "', en_block, re.MULTILINE))
    ru_keys = set(re.findall(r'^\s*([a-z0-9_]+): "', ru_block, re.MULTILINE))
    assert en_keys, "the STRINGS table lost its English catalogue"
    assert en_keys == ru_keys, en_keys ^ ru_keys


def test_the_spa_carries_the_whole_acceptance_chain() -> None:
    """Round 3's blocker: both blind testers reached a `succeeded` run and found
    nowhere to accept the work.  Acceptance is a chain -- request a review, run
    an independent reviewer, then accept or send back -- so the drawer must call
    all three routes, not offer one button."""

    body = read_spa()
    assert 'id="task-acceptance"' in body
    for route in ("/review-request", "/accept", "/reopen"):
        assert '"/api/tasks/" + encodeURIComponent(task.id) + "' + route + '"' in body, route
    # Every step names the task revision it was written against, so a stale
    # drawer gets a conflict instead of overwriting a newer record.
    assert body.count("expected_revision: taskRevision(task)") == 3
    # The state drives what is offered; nothing is advanced client-side.
    assert 'state === "review" ? "accept_state_review"' in body
    assert "async function repaintAfterAcceptanceWrite" in body


def test_the_panel_never_records_an_acceptance_without_a_summary() -> None:
    """Acceptance is the human decision the whole design protects; the line
    saying what was accepted stays in the ledger forever, so the panel refuses
    to post without one rather than sending an empty summary."""

    body = read_spa()
    accept = body.split("async function acceptTask() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("task-accept-summary").value.trim()' in accept
    guard = accept.index("accept_needs_summary")
    assert guard < accept.index('"/accept"'), "the summary guard must precede the write"
    assert "return;" in accept[:guard + 200]


def test_the_acceptance_refusals_are_humanised_without_losing_the_canonical_text() -> None:
    """The domain's refusals arrived raw and in English under a Russian panel
    (round 3, PM).  Each one is mapped to a sentence naming the next action, in
    both languages, with the canonical message kept one glance away."""

    body = read_spa()
    hints = body.split("const REFUSAL_HINTS = [", 1)[1].split("\n];", 1)[0]
    for needle in (
        "an independent_review delegation requires an open independent review",
        "task acceptance requires a current approved independent review",
        "task acceptance requires an independent review",
        "acceptance review does not bind the current task revision",
        "task acceptance requires a review completed outside the work-author principals",
        "is not allowed from task state",
    ):
        assert needle in hints, needle
    # The narrower refusal must be matched before the family it belongs to.
    assert hints.index("a current approved independent review") < hints.index(
        '["task acceptance requires an independent review"'
    )
    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    for key in re.findall(r'"(err_[a-z0-9_]+)"\]?,?\n?', hints):
        for block in ("en: {", "ru: {"):
            language = table.split(block, 1)[1].split("\n  },", 1)[0]
            assert re.search(rf'^\s*{key}: "', language, re.MULTILINE), (key, block)
    assert 'return t(key) + " (" + message + ")";' in body


def test_every_entity_panel_leads_with_a_summary_and_puts_the_record_behind_it() -> None:
    """Round 3, both testers: opening a node met a raw ledger record rather than
    an answer.  The task drawer already led with its runs and its acceptance
    chain; this generalises that, so every kind gets a short per-kind summary
    and the JSON moves behind a toggle that is collapsed on each opening."""

    body = read_spa()
    assert 'id="record-summary"' in body
    assert 'id="record-json-toggle"' in body
    # Collapsed in the markup, and re-collapsed by every `inspect`: the drawer
    # opens on the answer, never on what the previous node was left showing.
    assert '<pre id="inspector-body" hidden></pre>' in body
    assert "showRecordJson(false);" in body
    summaries = body.split("const RECORD_SUMMARIES = {", 1)[1].split("};", 1)[0]
    for kind in ("task", "agent", "delegation", "review", "agent_link"):
        assert f"{kind}: summarize" in summaries, kind
    # Any kind without its own builder still gets an answer rather than JSON.
    assert "(RECORD_SUMMARIES[node.kind] || summarizeGeneric)(host, node, record);" in body
    # Nothing is invented: a field the server did not send loses its line rather
    # than printing a placeholder down the whole block.
    row = body.split("function summaryRow(", 1)[1].split("\n}\n", 1)[0]
    assert "if (!text.trim()) { return; }" in row


def test_a_summary_glosses_a_canonical_value_and_never_replaces_it() -> None:
    """States, grant levels, context modes and profile ids are canonical names
    shared with the CLI and the ledger, so the panel puts its sentence BESIDE
    them (plan item 20's recorded compromise) instead of translating them away.
    The gloss tables are keyed by the canonical token for exactly that reason."""

    body = read_spa()
    assert 'return glossKey ? value + " — " + t(glossKey) : value;' in body
    values = body.split("const VALUE_GLOSS = {", 1)[1].split("};", 1)[0]
    for token in ("deny", "ask", "auto", "fresh", "accumulated"):
        assert f"{token}: " in values, token
    states = body.split("const STATE_GLOSS = {", 1)[1].split("};", 1)[0]
    for token in ("ready", "review", "accepted", "succeeded", "approved"):
        assert f"{token}: " in states, token
    # `stateLabel` stays the single place that decides how a state is spelled;
    # the gloss is layered on top of it rather than beside a second vocabulary.
    assert "glossed(stateLabel(node), perKind[node.state] || STATE_GLOSS[node.state])" in body


def test_the_hire_modal_marks_its_required_fields_and_closes_on_success() -> None:
    """Round 3, both testers: the rationale was required by the server and
    unmarked, and a successful hire left the modal open with a line inside it.
    The markers come from a class the CSP allows, not an inline style."""

    body = read_spa()
    hire = body.split('id="hire-modal"', 1)[1].split('id="task-modal"', 1)[0]
    assert hire.count('class="req"') == 3
    assert 'data-i18n="required_note"' in hire
    assert ".req::after{content:" in body

    hire_fn = body.split("async function hireRole() {", 1)[1].split("\n}\n", 1)[0]
    assert "showHire(false);" in hire_fn
    # Only after the write landed: a modal that closes on a refusal would take
    # the refusal with it.
    assert hire_fn.index("showHire(false);") > hire_fn.index('post("/api/agents"')
    # The result outlives the modal, and the role stays findable.
    assert "reportOnBoard(" in hire_fn
    assert "openNodeById(" in hire_fn
    assert 'id="board-result"' in body


def test_a_field_shaped_refusal_lands_on_its_field_not_only_on_the_result_line() -> None:
    """Round 3, both testers, on both modals: the refusal rendered below the
    buttons at the bottom of a form tall enough to scroll, so the button read as
    dead and nothing said which field was wrong."""

    body = read_spa()
    show = body.split("function showFormError(", 1)[1].split("\n}\n", 1)[0]
    assert 'line.className = "field-error";' in show
    assert 'input.closest("label.field")' in show
    assert 'field.scrollIntoView({ block: "nearest" });' in show
    assert "input.focus();" in show
    # A refusal the panel cannot attribute to a field still shows -- and is
    # scrolled to, because being off-screen was the complaint either way.
    assert 'result.scrollIntoView({ block: "nearest" });' in show
    assert "showFormError(error, TASK_FIELDS, result)" in body
    assert "showFormError(error, fields, result)" in body
    # Stale errors are cleared on the next submit and on every modal opening.
    assert body.count("clearFieldErrors(") >= 5


def test_a_named_field_refusal_stops_repeating_the_canonical_text() -> None:
    """The designer flagged the canonical server text leaking in brackets.  It
    stays everywhere it is the actionable detail; it goes only where the
    humanised sentence has already named the very field it is about."""

    body = read_spa()
    human = body.split("function humanizeError(", 1)[1].split("\n}\n", 1)[0]
    assert 'return t("err_field_empty").replace("{field}", t(fieldLabels[field]));' in human
    assert 'return t(key) + " (" + message + ")";' in human


def test_the_panels_field_refusal_shapes_still_match_what_the_domain_says(
    writable,  # type: ignore[no-untyped-def]
) -> None:
    """An empty field is refused in three different sentences depending on which
    guard fires first -- the payload's string guard, the manager's non-empty-list
    guard, and the payload schema's own.  Matching only the first, which is all
    the panel used to do, left New task unable to say which field was wrong.  The
    shapes belong to the domain, so they are checked against it rather than
    transcribed into the frontend and left to drift."""

    from agent_commons.errors import ValidationError

    block = read_spa().split("const FIELD_REFUSALS = [", 1)[1].split("\n];", 1)[0]
    patterns = [re.compile(source) for source in re.findall(r"^\s*/(.+)/,$", block, re.MULTILINE)]
    assert len(patterns) == 3

    def field_named_by(call) -> str | None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValidationError) as caught:
            call()
        for pattern in patterns:
            found = pattern.match(str(caught.value))
            if found:
                return found.group(1)
        return str(caught.value)

    cases = {
        "title": lambda: writable.create_task(
            title="", description="d", acceptance_criteria=("c",)
        ),
        "description": lambda: writable.create_task(
            title="t", description="", acceptance_criteria=("c",)
        ),
        "acceptance_criteria": lambda: writable.create_task(
            title="t", description="d", acceptance_criteria=()
        ),
        "name": lambda: writable.create_agent(
            name="", profile_id="claude-builder", rationale="r"
        ),
        "rationale": lambda: writable.create_agent(
            name="n", profile_id="claude-builder", rationale=""
        ),
    }
    for expected, call in cases.items():
        assert field_named_by(call) == expected


def test_new_task_marks_exactly_the_fields_the_domain_refuses_blank(
    writable,  # type: ignore[no-untyped-def]
) -> None:
    """A required marker is a claim about the server, so it is checked against
    the server: every field the modal marks is one `create_task` actually
    refuses empty, including the criteria, whose guard lives in the manager
    rather than in the payload schema."""

    from agent_commons.errors import ValidationError

    body = read_spa()
    modal = body.split('id="task-modal"', 1)[1].split('id="link-modal"', 1)[0]
    marked = set(re.findall(r'<span class="req" data-i18n="([a-z0-9_]+)"', modal))
    fields = body.split("const TASK_FIELDS = {", 1)[1].split("};", 1)[0]
    blank = {
        "title": {"title": "", "description": "d", "acceptance_criteria": ("c",)},
        "description": {"title": "t", "description": "", "acceptance_criteria": ("c",)},
        "acceptance_criteria": {"title": "t", "description": "d", "acceptance_criteria": ()},
    }
    for wire, arguments in blank.items():
        label = re.search(rf'{wire}: \["([a-z0-9_]+)"', fields)
        assert label is not None, wire
        assert label.group(1) in marked, wire
        with pytest.raises(ValidationError):
            writable.create_task(**arguments)
    assert len(marked) == len(blank), marked


def test_every_dialog_shares_one_focus_trap_and_guards_esc_over_a_dirty_form() -> None:
    """Round 3, designer: Tab walked out of an open dialog into the page behind
    it, and because that page keeps its focus outlines the dialog looked as if
    it had vanished while it was still open.  All four dialogs share ONE
    mechanism -- trap, opening focus, restore, Esc -- rather than four copies,
    and Esc over a form whose fields have moved asks before discarding, through
    the same `window.confirm` the retire buttons already use."""

    body = read_spa()
    scrims = ("hire-modal", "task-modal", "link-modal", "close-link-modal")

    # One mechanism, not four: a single opener, a single closer, one entry per
    # dialog in the table that says how each is dismissed.
    assert body.count("function openModal(") == 1
    assert body.count("function closeModal(") == 1
    closers = body.split("const MODAL_CLOSERS = {", 1)[1].split("};", 1)[0]
    for scrim in scrims:
        assert f'"{scrim}":' in closers, scrim
        # Nothing may reach around the mechanism to hide a dialog by hand; that
        # is how focus was left stranded on a control the operator cannot see.
        assert f'document.getElementById("{scrim}").hidden = ' not in body, scrim

    # Each dialog says it is one, and is named by its own heading.
    for scrim, heading in (
        ("hire-modal", "hire-title"),
        ("task-modal", "task-modal-title"),
        ("link-modal", "link-modal-title"),
        ("close-link-modal", "close-link-modal-title"),
    ):
        markup = body.split(f'id="{scrim}"', 1)[1].split("</div>", 1)[0]
        assert 'role="dialog"' in markup, scrim
        assert 'aria-modal="true"' in markup, scrim
        assert f'aria-labelledby="{heading}"' in markup, scrim
        assert f'id="{heading}"' in body, heading

    # Tab and Shift+Tab both wrap inside the dialog instead of running on.
    assert 'if (event.key !== "Tab") { return; }' in body
    assert "event.shiftKey && (!inside || active === first)" in body
    assert "!event.shiftKey && (!inside || active === last)" in body
    # Focus moves in on opening and goes back to whatever opened the dialog.
    assert "if (openModalId !== scrimId) { modalOpener = document.activeElement; }" in body
    assert "opener.focus();" in body
    # Esc closes, but not over work: the guard compares the fields against what
    # they held as the dialog opened, and the sample is taken last, after the
    # caller has filled the form in.
    assert 'window.confirm(t("confirm_discard"))' in body
    assert "modalOpenedAs = modalFieldState(scrim);" in body
    # The guard lives in one place, because Esc is no longer the only way out:
    # a guide "?" inside a dialog leaves it too, and both departures ask the
    # same question (wave 4, item 32).
    dismiss = body.split("function dismissOpenModal() {", 1)[1].split("\n}\n", 1)[0]
    assert "modalFieldState(scrim) !== modalOpenedAs" in dismiss
    assert "{ return false; }" in dismiss
    trap = body.split('if (event.key === "Escape") {', 1)[1].split("return;", 1)[0]
    assert "dismissOpenModal();" in trap
    # And an open dialog owns Esc: one press must not also un-isolate the graph.
    assert 'event.key === "Escape" && !openModalId && focused' in body


def test_confirmations_say_what_happened_and_keep_the_id_one_click_away() -> None:
    """Round 3, designer: the panel confirmed its writes with the ids it had
    just minted -- `нанята agent.61JBN…`, `launched delegation.6S7B…` -- so the
    operator was told a ULID instead of what had happened, and the name they had
    typed a second earlier appeared nowhere.  Every confirmation now leads with
    a sentence naming the thing, and the canonical id follows it as a chip that
    stays selectable."""

    body = read_spa()
    assert "function reportResult(" in body
    assert 'chip.className = "idchip"' in body
    assert ".idchip{" in body
    # The chip must stay readable where the line is width-constrained: the board
    # bar gives the sentence its own truncating row and puts the id under it,
    # rather than squeezing both onto one row until the words vanish.
    assert "#board-result .saidline{" in body
    assert "#board-result .idchip{" in body
    assert "display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" in body

    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    english = table.split("en: {", 1)[1].split("\n  },", 1)[0]
    russian = table.split("ru: {", 1)[1].split("\n  },", 1)[0]
    for key in (
        "hired_named",
        "preset_saved_named",
        "task_created_named",
        "link_opened_named",
        "link_closed_named",
        "run_started",
        "settings_saved_named",
        "retired_named",
        "message_recorded_named",
        "accept_review_requested_named",
        "accept_recorded_named",
        "reopen_recorded_named",
    ):
        values = []
        for block in (english, russian):
            line = re.search(rf'^\s*{key}: "(.*)",$', block, re.MULTILINE)
            assert line is not None, key
            # A sentence that names the thing: every one carries a slot for the
            # human name or title, which is what the id was standing in for.
            assert "{" in line.group(1), key
            values.append(line.group(1))
        assert values[0] != values[1], f"{key} was not actually translated"

    # The id-leading forms these replaced are gone.
    assert 't("launched") + " " + payload.delegation_id' not in body
    assert 't("hired")' not in body
    assert 't("task_created")' not in body


def test_a_run_card_states_its_status_once_unless_the_two_values_disagree() -> None:
    """`SUCCEEDED · SUCCEEDED` (round 3, designer): the attempt's operational
    phase and the canonical delegation's state were concatenated bare.  Agreeing
    values are said once; disagreeing ones are real information about a run in
    trouble and are shown as two labelled values, never two bare words."""

    body = read_spa()
    status = body.split("function runStatusText(run) {", 1)[1].split("\n}\n", 1)[0]
    assert "if (!state || state === phase) { return lead + glossed(phase" in status
    assert 't("run_phase_word")' in status
    assert 't("run_state_word")' in status
    # Both card renderers -- the Runs view and the task drawer -- go through it,
    # and neither still concatenates the two values itself.
    assert body.count("kind.textContent = runStatusText(run);") == 2
    assert '" · " + run.delegation_state' not in body
    # Both values keep the ledger's spelling: only the labels are the panel's.
    assert "String(run.phase" in status
    assert "String(run.delegation_state" in status


def test_the_onboarding_card_waits_for_a_hire_not_for_an_empty_graph(
    writable,  # type: ignore[no-untyped-def]
) -> None:
    """The card hid the moment ANY node existed, so a workspace handed over with
    a task -- or merely an operator session -- in it hid the first step before it
    had been taken, and the round-3 PM never saw the card once.  Hiring IS the
    first step, so the card waits for a hired role; a template is shelf stock,
    which the graph marks on the node's own attrs."""

    writable.create_task(title="Handed over", description="d", acceptance_criteria=("c",))
    writable.create_agent(
        name="Reviewer template",
        profile_id="claude-builder",
        rationale="shelf stock, not a hire",
        template=True,
    )
    writable.rebuild_graph()
    nodes = writable.graph()["nodes"]
    # The old condition would have hidden the card on this workspace...
    assert len(nodes) > 0
    # ...and the panel's new one keeps it, because nobody has hired yet.  The
    # template marker the panel reads is really carried on the node.
    templates = [node for node in nodes if (node["attrs"] or {}).get("template")]
    assert templates, "the graph stopped marking templates on the node's attrs"
    hired = [
        node
        for node in nodes
        if node["kind"] == "agent" and not (node["attrs"] or {}).get("template")
    ]
    assert hired == []

    body = read_spa()
    assert "graph.nodes.length > 0 || !writesEnabled" not in body
    render = body.split("function render(graph) {", 1)[1].split(
        "function paintFocusChrome", 1
    )[0]
    assert 'node.kind === "agent" && !(node.attrs && node.attrs.template)' in render
    assert "hasHire || onboardingSetAside || !writesEnabled" in render
    # The card can now land over a board that already carries work, so it must
    # be possible to put aside -- an opaque overlay with no exit would trade one
    # finding for a worse one.
    assert 'id="onb-dismiss"' in body


def test_an_empty_template_catalogue_offers_the_route_that_creates_the_first_one() -> None:
    """Round 3, PM: choosing "From the agent catalogue" with an empty catalogue
    showed a select holding "— none —" and nothing else.  The dead select gives
    way to what a template is and to the path that creates one -- the
    from-scratch form with "save as a template" on, which this modal already
    had -- and the dead-end option leaves the list rather than merely refusing
    to be chosen."""

    body = read_spa()
    assert 'id="hire-preset-empty"' in body
    assert 'data-i18n="hire_preset_empty"' in body
    assert 'id="hire-preset-create"' in body

    mode = body.split("function applyHireMode() {", 1)[1].split("\n}\n", 1)[0]
    assert "const hasPresets = Boolean(catalog && (catalog.presets || []).length);" in mode
    assert 'document.getElementById("hire-preset-field").hidden = !fromPreset;' in mode
    assert (
        'document.getElementById("hire-preset-empty").hidden = !(wantsPreset && !hasPresets);'
        in mode
    )
    # Hidden, not merely disabled: a disabled option still advertises a path.
    paint = body.split("function paintHire() {", 1)[1].split("\n}\n", 1)[0]
    assert "presetMode.hidden = !hasPresets;" in paint
    assert "presetMode.disabled = !hasPresets;" in paint
    # No new route was invented: the shelf's own creation path is reused.
    assert body.count("showHire(true, { template: true })") == 2

    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    for key in ("hire_preset_empty", "hire_preset_create"):
        for block in ("en: {", "ru: {"):
            language = table.split(block, 1)[1].split("\n  },", 1)[0]
            assert re.search(rf'^\s*{key}: "', language, re.MULTILINE), (key, block)


def test_an_empty_catalogue_explains_the_skills_box_instead_of_showing_an_empty_one() -> None:
    """Round 3, designer: the skills multiselect rendered as an empty box with no
    empty state and read as broken.  The box gives way to a line saying what a
    skill is and where skills come from, in the same words the Catalogue view
    already uses about the editing gate.  The tools checklist had the same hole
    for a profile whose tools are all fixed."""

    body = read_spa()
    assert 'id="settings-skills-empty"' in body
    paint = body.split("function paintSettings(record) {", 1)[1].split(
        "\nasync function loadCatalog", 1
    )[0]
    assert 'document.getElementById("setting-skills-field").hidden = !skills.length;' in paint
    assert 'document.getElementById("settings-skills-empty").hidden = skills.length > 0;' in paint
    # Whether the line SHOWS is data; what it SAYS is the panel's own words, so
    # the sentence lives with the other labels that a language switch repaints
    # without touching the form around them (item 18).
    labels = body.split("function paintSettingsLabels(record) {", 1)[1].split("\n}\n", 1)[0]
    # Consistent with the Catalogue view: the same sentence about the same gate.
    assert 'catalogEditing ? t("catalog_add_here") : t("catalog_readonly")' in labels
    catalogue = body.split("function paintCatalog() {", 1)[1].split("\n}\n", 1)[0]
    assert 't("catalog_readonly")' in catalogue
    # The empty state is written from JS, so it must not also claim a data-i18n
    # marker -- applyI18n runs on every snapshot and would overwrite it.
    assert '<p class="note" id="settings-skills-empty" hidden></p>' in body
    # The tools checklist gets the same treatment.
    assert "if (!summary.narrowable.length) {" in paint
    assert 't("tools_none_narrowable")' in paint

    # And so does the hire form's copy of the picker (round 4, finding 3): the
    # same box with the same hole, so the same empty state, in the same words --
    # written from JS there too, because the second half of the sentence still
    # depends on the catalogue-editing gate.
    hire = body.split("function paintHire() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("hire-skills-field").hidden = !skills.length;' in hire
    assert 'document.getElementById("hire-skills-empty").hidden = skills.length > 0;' in hire
    assert 'catalogEditing ? t("catalog_add_here") : t("catalog_readonly")' in hire
    assert '<p class="note" id="hire-skills-empty" hidden></p>' in body

    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    for key in ("skills_empty", "catalog_add_here", "tools_none_narrowable"):
        for block in ("en: {", "ru: {"):
            language = table.split(block, 1)[1].split("\n  },", 1)[0]
            assert re.search(rf'^\s*{key}: "', language, re.MULTILINE), (key, block)


def test_a_repaint_cannot_change_the_hire_modal_under_the_operator() -> None:
    """Both round-3 testers saw the modal self-close or prefill unexpectedly, and
    it did, three ways.  `applyI18n` runs on every stream snapshot and rewrote a
    heading a JS write had claimed, against this file's own rule that an element
    is owned by data-i18n or by JS and never both.  `fillSelect` drops every
    option and re-selects the default, so any repaint reaching the open form put
    the profile, grants, context mode and template back.  And a `click` is
    delivered to the nearest common ancestor of press and release, so a drag that
    began in a field and ended on the backdrop dismissed the dialog."""

    body = read_spa()
    assert 'title.dataset.i18n = hireTemplate ? "preset_create_title" : "tab_hire";' in body

    paint = body.split("function paintHire() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("hire-modal").hidden ? fallback' in paint
    for picker in ("hire-profile", "hire-context_mode", "hire-preset", "hire-skills"):
        assert f'held("{picker}"' in paint, picker
    # The skills picker is a multiple select, so what it holds is a SET.  `.value`
    # on a multiple select is only its first selected option: handing that back
    # would re-select one skill and silently drop the rest -- the same clobber,
    # in the one control where it is hardest to notice.
    assert "element.multiple ? selectedValues(element) : element.value" in paint
    assert 'held("hire-skills", [])' in paint

    assert "function dismissOnBackdrop(" in body
    assert 'scrim.addEventListener("pointerdown"' in body
    assert "const outside = pressedBackdrop && event.target === scrim;" in body
    for scrim in ("hire-modal", "task-modal"):
        assert f'dismissOnBackdrop("{scrim}"' in body, scrim
    # The Run tab's task picker had the same clobber, and `loadLaunch` runs on
    # every snapshot -- it took the operator's chosen task off the tab mid-run.
    assert "picker.value || null, false);" in body
    # A typed rationale is the operator's; only the template's own text is
    # replaced when another template is chosen.
    prefill = body.split("function prefillPresetRationale() {", 1)[1].split("\n}\n", 1)[0]
    assert "if (field.value.trim() && field.value !== presetRationale) { return; }" in prefill


def test_returned_work_reaches_the_attention_queue_and_opens_its_task() -> None:
    """A finished run leaves the task where it was, so the queue is the only
    place that can say the work is waiting on a person (round 3, designer)."""

    body = read_spa()
    assert 'item.kind === "work_returned"' in body
    assert 'work_returned_head").replace("{role}"' in body
    # Not a dead button when the graph was trimmed: it says so instead.
    returned = body.split('if (item.kind === "work_returned") {', 1)[1].split("\n  }\n", 1)[0]
    assert "not_on_graph" in returned
    assert "inspect(node);" in returned


def test_the_first_guide_page_states_that_success_is_not_acceptance() -> None:
    """The most practically important rule was buried in the second tab."""

    body = read_spa()
    assert 'data-i18n="guide_accept_p"' in body
    table = body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
    english = table.split("en: {", 1)[1].split("\n  },", 1)[0]
    line = re.search(r'^\s*guide_accept_p: "(.*)",$', english, re.MULTILINE)
    assert line is not None
    # The canonical state is named, not translated away.
    assert "succeeded" in line.group(1)


def test_the_panel_carries_a_language_toggle_that_persists() -> None:
    """The header offers en/ru, static chrome re-renders through data-i18n
    markers, and the choice survives a reload via localStorage."""

    body = read_spa()
    assert 'id="lang"' in body
    assert 'data-i18n="' in body
    assert 'data-i18n-placeholder="' in body
    assert "function applyI18n" in body
    assert 'localStorage.getItem(LANG_KEY)' in body
    assert "localStorage.setItem(LANG_KEY, lang)" in body


def test_stale_acceptance_loses_its_acceptance_tone() -> None:
    body = read_spa()
    assert 'if (node.stale && tone === "ok") { return ["▨", "warn"]; }' in body


def test_the_client_does_not_re_prefix_the_event_id() -> None:
    """Regression: the id already carries instance:seq, so re-prefixing produced
    instance:instance:seq, which never matched and made every reconnect report a
    server restart -- draining the only staleness signal of meaning."""

    body = read_spa()
    assert 'server_instance_id + ":" + id' not in body
    assert body.count("lastEventId = id;") == 2


def test_the_spa_offers_a_way_to_isolate_a_node_and_to_leave_it() -> None:
    body = read_spa()
    assert 'id="focus-toggle"' in body
    assert "function neighboursOf(" in body
    # Faded, not hidden: unrelated work stays on screen as context.
    assert ".node.faded{opacity:" in body
    assert ".edge.faded{opacity:" in body
    # Two independent exits so nobody is stranded in a mostly faded graph.
    assert 'event.key === "Escape"' in body
    assert "event.target === canvas" in body


def test_a_graph_built_while_the_ledger_moves_does_not_freeze_the_view(
    context: UIContext,
) -> None:
    """Regression: the fingerprint was sampled after the snapshot, so a write
    landing in between was recorded as seen but not rendered.  The next
    comparison then matched and the view stayed frozen while the stream kept
    reporting itself live."""

    import agent_commons.ui.context as module

    original = module.ledger_fingerprint
    samples = iter(["sha256:before", "sha256:after"])

    def moving(target: Any) -> str:
        try:
            return next(samples)
        except StopIteration:
            return original(target)

    module.ledger_fingerprint = moving
    try:
        context.rebuild_graph()
    finally:
        module.ledger_fingerprint = original

    # The two samples disagreed, so nothing was recorded as seen and the very
    # next check rebuilds rather than trusting a graph that missed the write.
    assert context.refresh_if_changed() is True


def test_the_stream_pairs_a_sequence_with_the_graph_it_describes(context: UIContext) -> None:
    seq, graph = context.snapshot_frame()
    assert graph["seq"] == seq


def test_every_stream_connection_receives_each_update_not_only_the_first(
    context: UIContext, workspace, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    """Round 2: refresh_if_changed is a one-shot consumer of a shared fingerprint,
    so whichever connection polled first after a write got the frame and the rest
    stayed stale while showing 'live'. Every connection must get each update."""

    import asyncio

    from agent_commons.services import CommonsManager
    from agent_commons.ui import server as server_module

    monkeypatch.setattr(server_module, "_POLL_SECONDS", 0.01)

    async def drive() -> tuple[bytes, bytes]:
        gen_a = server_module._events(context, None)
        gen_b = server_module._events(context, None)
        try:
            for gen in (gen_a, gen_b):
                await anext(gen)  # hello
                await anext(gen)  # initial snapshot
            # A real canonical write moves the ledger fingerprint.
            writer = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
            session = writer.start_session(
                stable_instance_id="stream-writer-window-1",
                principal="local-operator",
                client="claude",
                software="claude-code",
                role="operator",
            )
            writer.session_id = session["session_id"]
            writer.create_objective(
                title="A change to broadcast",
                description="every connection should see this",
                acceptance_criteria=("delivered to all",),
                idempotency_key="stream-broadcast",
            )
            frame_a = await anext(gen_a)
            frame_b = await anext(gen_b)
            return frame_a, frame_b
        finally:
            await gen_a.aclose()
            await gen_b.aclose()

    frame_a, frame_b = asyncio.run(drive())
    assert b"event: snapshot" in frame_a
    assert b"event: snapshot" in frame_b


def test_the_board_shows_the_team_and_holds_the_runtime_behind_a_toggle() -> None:
    """Round 3, designer 5: one launch adds a delegation and a child session per
    step, and the manager looking at their team saw plumbing.  The runtime is
    filtered in the single seam the board lays out from, so no edge is left
    dangling, the toggle says how many nodes it covers, and the choice persists
    the way the language does."""

    body = read_spa()
    assert 'id="board-runtime"' in body
    # The filter belongs in visibleNodesOf: render() positions only what comes
    # back from there and skips any edge whose endpoint has no position, so a
    # hidden node takes its edges with it.
    seam = body.split("function visibleNodesOf(graph) {", 1)[1].split("\n}", 1)[0]
    assert "showRuntime || !isRuntimeNode(node)" in seam
    assert "if (!from || !to) { continue; }" in body
    # The operator's own window is the person, not machinery, so it stays -- and
    # "own" means the panel's writer session, not any window that happens to
    # answer to a human, because every other CLI window opens one of those too.
    predicate = body.split("function isRuntimeNode(node) {", 1)[1].split("\n}", 1)[0]
    assert 'node.kind === "delegation"' in predicate
    assert 'node.kind === "session" && !isOperatorSession(node)' in predicate
    own = body.split("function isOperatorSession(node) {", 1)[1].split("\n}", 1)[0]
    assert "metaInfo && metaInfo.writer_session_id" in own
    assert "Boolean(node.reports_to_operator)" in own
    # Nothing disappears silently: the count is part of the label.
    assert 't("runtime_nodes") + " (" + runtime + ")"' in body
    assert "localStorage.getItem(RUNTIME_KEY)" in body
    assert 'localStorage.setItem(RUNTIME_KEY, showRuntime ? "1" : "0")' in body
    # The depth captions are the runtime's own vocabulary and go with it; rank 0
    # names the operator, which is domain.
    assert "if (position.band !== 0 && !showRuntime) { continue; }" in body
    # ... and the run's own record stays reachable with the node off the board:
    # both entry points read the whole graph, never the board's filtered view,
    # and fall back to the record when the node is not there at all.
    opener = body.split("function openRunRecord(delegationId) {", 1)[1].split("\n}\n", 1)[0]
    assert "visibleNodesOf" not in opener
    assert 'inspect({ kind: "delegation"' in opener
    assert body.count("openRunRecord(run.delegation_id)") == 2


def test_fit_measures_the_area_the_operator_can_see_and_stops_at_readable() -> None:
    """Round 3, designer 4: Fit scaled to the whole canvas element, parking part
    of the board under the chrome, and shrank without a floor.  The frame is
    measured off the chrome elements themselves -- not off the grid's declared
    columns -- so the responsive work still to come cannot silently un-fix it."""

    body = read_spa()
    frame = body.split("function visibleBoardRect() {", 1)[1].split("\n}", 1)[0]
    assert "canvas.getBoundingClientRect()" in frame
    assert 'for (const id of ["sidebar", "dock"])' in frame
    assert "element.getBoundingClientRect()" in frame
    # Nothing past the window is visible either, however the shell arranges
    # itself -- measured in a live panel, where the canvas hung the toolbar's
    # own height below the fold.
    assert "Math.min(rect.bottom, window.innerHeight" in frame
    # ... and the cause of that overhang: the board's flex rule lost to
    # `#center .view.open`, so the toolbar never took layout space.
    assert "#center #view-board.view.open{display:flex" in body
    assert "#view-board #canvas{flex:1; min-height:0; height:auto}" in body
    fit = body.split("function fitView() {", 1)[1].split("\n}", 1)[0]
    assert "const frame = visibleBoardRect();" in fit
    assert "canvas.getBoundingClientRect" not in fit, "fit is back on the full element"
    assert "(frame.width - 2 * pad) / width" in fit
    assert "frame.left + pad" in fit, "the fitted content must clear the left chrome"
    # A floor, and one a card survives: 62 board units of card height at the
    # floor must still clear the 24px hit target the link ports are built to.
    assert "Math.max(FIT_MIN_SCALE" in fit
    match = re.search(r"const FIT_MIN_SCALE = ([0-9.]+);", body)
    assert match, "the fit scale has no named floor"
    assert float(match.group(1)) * 62 >= 24, "a card would shrink past its own hit target"


def test_a_wide_rank_wraps_into_a_block_shaped_like_the_frame() -> None:
    """A flat eight per row is 1650 board units against a centre rarely 900 CSS
    px wide, so Fit had to shrink the whole board to the width of its widest
    rank -- which is how nine nodes became unreadable (round 3, designer 4)."""

    body = read_spa()
    assert "PER_ROW" not in body, "the wrap is still a fixed eight"
    columns = body.split("function bandColumns(count) {", 1)[1].split("\n}", 1)[0]
    # Derived from the band's own size and the frame's proportions, cell
    # geometry included, rather than from a constant.
    assert "frame.width / frame.height" in columns
    assert "Math.sqrt(count * aspect * ROW / COLUMN)" in columns
    placement = body.split("function layout(nodes) {", 1)[1].split("\n}", 1)[0]
    assert "const columns = bandColumns(members.length);" in placement
    assert "(index % columns) * COLUMN" in placement
    assert "Math.floor(index / columns)" in placement


def test_a_link_port_stays_hittable_at_any_zoom_and_says_what_it_does() -> None:
    """Round 3, designer 17 and PM 12: after the autofit the ports were about six
    pixels across and both testers failed to open a link by dragging.  The grab
    area is sized in SCREEN space -- it counter-scales with the view -- so the
    24px target holds at every scale."""

    body = read_spa()
    match = re.search(r"const PORT_HIT = (\d+);", body)
    assert match, "the port hit area has no named radius"
    assert int(match.group(1)) * 2 >= 24, "the grab area is under 24 CSS px at 1:1"
    sizing = body.split("function sizePorts() {", 1)[1].split("\n}", 1)[0]
    assert "const inverse = 1 / view.scale;" in sizing
    assert 'scale(" + inverse + ")' in sizing
    # applyView is the one place the zoom ever changes, so it is where the
    # ports are re-sized; a wheel zoom must not leave them behind.
    applied = body.split("function applyView() {", 1)[1].split("\n}", 1)[0]
    assert "sizePorts();" in applied
    # A transparent grab circle behind a proportionate dot, a hover affordance
    # and a cursor hint, so the port reads as a handle before the drag.
    assert ".porthit{fill:transparent" in body
    assert ".port{cursor:crosshair}" in body
    assert ".port:hover .portc{" in body
    # The tooltip says what dragging does -- and names the non-drag path, since
    # the port itself is not keyboard-operable and will not pretend to be.
    assert 'portTip.textContent = t("port_tip");' in body
    english = body.split("const STRINGS = {", 1)[1].split("en: {", 1)[1].split("\n  },", 1)[0]
    port_tip = re.search(r'\n    port_tip: "([^"]+)"', english)
    assert port_tip and "Links tab" in port_tip.group(1)
    # The drop matters as much as the grab: the reach is in screen pixels and
    # was measured against a card at 1:1.
    assert "const DROP_REACH = 64;" in body
    assert "distance <= DROP_REACH" in body


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


# Every surface the panel paints from JS, and therefore every surface a language
# switch has to repaint.  Named here as well as in the asset so that adding one
# without repainting it fails a test rather than shipping a half-Russian panel.
LANGUAGE_SURFACES = {
    "chrome",
    "status",
    "workspace",
    "stream",
    "board",
    "legend",
    "catalogue",
    "hire",
    # The help popover keeps the key it was opened with, so a language switch
    # rewrites the sentence in place instead of closing it under the reader.
    "help_popover",
    "links",
    "settings",
    "drawer",
    "task_runs",
    "acceptance",
    "record_summary",
    "runs",
    "chat",
    "attention",
    "search",
    "launch",
}


def test_switching_language_repaints_every_surface_the_panel_paints_itself() -> None:
    """Round 3, PM 9 and designer 10: switching language left the links list in
    the previous locale.  The reported symptom was one surface; the cause was
    that `applyLanguage` was a hand-written call list, and half the panel was
    missing from it.  The surfaces are enumerated in the asset now, and this
    pins the enumeration -- mechanically, so a new surface cannot be quietly
    left out of it (item 18)."""

    body = read_spa()
    table = body.split("const LANGUAGE_SURFACES = [", 1)[1].split("\n];", 1)[0]
    named = set(re.findall(r'^\s*\["([a-z_]+)"', table, re.MULTILINE))
    assert named == LANGUAGE_SURFACES, named ^ LANGUAGE_SURFACES

    # It walks the table rather than repeating it: a call list beside a table
    # would drift from it, which is the bug all over again.
    switch = body.split("function applyLanguage() {", 1)[1].split("\n}\n", 1)[0]
    assert "for (const [name, repaint] of LANGUAGE_SURFACES)" in switch

    # A surface whose paint function takes an argument keeps the last one rather
    # than sitting the repaint out -- that omission is what stranded the links.
    assert "shownNode = node;" in body
    assert 'if (shownNode && shownNode.kind === "agent") { paintLinks(shownNode.id); }' in table
    assert "taskRunCount = paintTaskRuns(shownNode);" in table
    # ...and is forgotten when there is nothing on show, so a closed drawer is
    # not repainted behind the operator's back.
    closing = body.split("function closeDrawer() {", 1)[1].split("\n}\n", 1)[0]
    assert "shownNode = null;" in closing
    assert "shownRecord = null;" in closing

    # The settings form is open over operator input, so the repaint is
    # labels-only.  `paintSettings` refills every picker and blanks the reason
    # field; calling it here would take a half-finished edit with it.
    assert "paintSettingsLabels(currentRole)" in table
    assert "paintSettings(currentRole)" not in table
    # The pickers' option labels still change language -- rewritten in place, so
    # the selected value survives while its gloss is translated.
    labels = body.split("function paintSettingsLabels(record) {", 1)[1].split("\n}\n", 1)[0]
    # The picker's own shorter gloss, and the same one `fillSelect` put there:
    # relabelling from VALUE_GLOSS here would swap the short sentence for the
    # long one on a language switch, which is the clipped option all over again.
    assert "option.textContent = glossed(option.value, PICKER_GLOSS[option.value]);" in labels
    assert "fillSelect(" not in labels

    # The search view repaints only while it is the view on screen: re-running a
    # query behind a board the operator is reading would move them off it.
    repaint = body.split("function repaintSearch() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("view-search").classList.contains("open")' in repaint

    # The onboarding card and the guide are not in the table because they are
    # not painted from JS at all -- `chrome` (applyI18n) owns them, which is the
    # file's standing rule about who owns an element.
    assert '["chrome", () => applyI18n()]' in table
    for marker in ('id="onboarding"', 'id="gpage-brief"'):
        section = body.split(marker, 1)[1][:2000]
        assert "data-i18n=" in section, marker


def test_the_panel_uses_one_word_per_concept_in_each_language() -> None:
    """Round 3, designer 11 and PM 8: one thing had four names -- "Запуски" in
    the nav, "Запуск" on the tab, "прогоны" in the guide, "делегация" in the
    drawer -- and "Скиллы"/"Туллы" sat beside "Инструменты".  The glossary is
    written into the asset so the next person cannot drift from it, and this
    pins both the glossary and the words themselves (item 21)."""

    body = read_spa()
    english, russian = _language_tables()

    # The glossary is a comment block ahead of the table it governs.
    glossary = body.split("// One thing, one word.", 1)[1].split("const STRINGS = {", 1)[0]
    for concept in ("role           роль", "run            прогон", "skill          навык",
                    "tool           инструмент", "task           задача", "board          доска"):
        assert concept in glossary, concept
    # And it states the one distinction that keeps a canonical name canonical:
    # the record keeps the ledger's word, the activity gets the human one.
    assert "`delegation` names the record a run writes; a person watches a run." in glossary
    assert "`agent`      names the ledger kind" in glossary

    # No transliteration, and no second name for anything the glossary settles.
    for forbidden in ("скилл", "тулл", "борд", "агент", "делегац"):
        offenders = [line.strip() for line in russian.split("\n") if forbidden in line.lower()]
        assert offenders == [], (forbidden, offenders)

    # The run has exactly one Russian noun, and it is the same one everywhere.
    assert _value(russian, "tab_runs") == "Прогоны"
    assert _value(russian, "tab_run") == "Прогон"
    assert _value(russian, "count_delegations") == "прогонов"
    assert _value(english, "tab_runs") == "Runs"
    assert _value(english, "count_delegations") == "runs"
    # "запуск" survives only as the verb: no Russian string may use it as a noun
    # for a run again.
    for line in russian.split("\n"):
        for noun in ("запуски", "запуска ", "запуске", "запуском", "запуску"):
            assert noun not in line.lower(), line.strip()

    # The record itself keeps the ledger's name, in both languages.
    assert _value(english, "run_state_word") == "delegation"
    assert _value(russian, "run_state_word") == "delegation"

    # A skill is a навык and a tool is an инструмент wherever they are named.
    # Anchored on keys the panel actually renders: `skills_heading` and
    # `tools_heading` were carried here from a surface that no longer exists,
    # and pinning vocabulary to a string nothing displays proves nothing.
    assert _value(russian, "nav_skills") == "Навыки"
    assert _value(russian, "nav_tools") == "Инструменты"
    assert _value(russian, "guide_tab_catalog") == "Навыки и инструменты"


def test_the_russian_table_never_translates_a_canonical_value() -> None:
    """Item 20 was rejected as reported -- translating `deny`/`fresh`/`succeeded`
    would make one state read two ways between the panel, the CLI and the
    ledger.  The recorded compromise is a gloss BESIDE the canonical value, so
    the canonical value has to still be there, spelled the ledger's way."""

    body = read_spa()
    english, russian = _language_tables()

    # The link type was the one control that had translated its canonical value
    # away: the radio said "Спросить", and nothing on screen said `ask`.
    for block in (english, russian):
        assert _value(block, "link_type_ask").startswith("ask — ")
        assert _value(block, "link_type_handoff").startswith("handoff_work — ")

    # The gloss tables stay keyed by the canonical token, and the gloss is
    # appended to it rather than substituted for it.
    assert 'return glossKey ? value + " — " + t(glossKey) : value;' in body
    options = body.split("function glossedOptions(values, table) {", 1)[1].split("\n}\n", 1)[0]
    assert "{ id: value, title: glossed(value, table[value]) }" in options

    # No Russian string may spell a canonical state or grant level in Russian.
    # The glosses SAY what those words mean; none of them is offered as a name.
    for canonical in ("deny", "ask", "auto", "fresh", "accumulated", "succeeded", "review"):
        assert f'"{canonical}"' not in russian, canonical


def test_a_canonical_value_shown_to_a_person_carries_its_gloss() -> None:
    """The other half of item 20: the gloss `stateWithGloss` built for the record
    summaries had to reach every surface that shows a canonical value to a
    person, not only the drawer."""

    body = read_spa()

    # The runs list and the task's runs both go through one status line.
    status = body.split("function runStatusText(run) {", 1)[1].split("\n}\n", 1)[0]
    assert "glossed(phase, STATE_GLOSS[phase])" in status
    assert "glossed(state, STATE_GLOSS[state])" in status

    # The attention card, the acceptance line and the launch picker.
    assert 'glossed(item.task_state, STATE_GLOSS[item.task_state])' in body
    assert 'glossed(state, STATE_GLOSS[state]) : "—"' in body
    assert "glossed(task.state, STATE_GLOSS[task.state])" in body

    # The grant and context pickers, in both the settings panel and the hire
    # modal: the option's value stays the bare canonical token either way.
    # One definition and four call sites: both grant rows and both context
    # pickers.  A fifth picker of a canonical value would have to join them.
    assert body.count("glossedOptions(") == 5
    for fragment in (
        'fillSelect(document.getElementById("grant-" + name),\n'
        "      glossedOptions(levels, PICKER_GLOSS)",
        'glossedOptions((catalog && catalog.context_modes)',
        'glossedOptions(catalog.context_modes',
        'glossedOptions(levels, PICKER_GLOSS),\n      held("hire-" + name, "deny"), false);',
    ):
        assert fragment in body, fragment

    # Round 4, finding 2: the full sentence arrived clipped inside the closed
    # select -- "deny -- никогда, и даже не..." -- so every picker reads the
    # SHORT table and the long one keeps the surfaces with room for it.  The
    # compromise itself is unchanged: both tables are keyed by the canonical
    # token and both append to it, so the option's value is still bare.
    picker = body.split("const PICKER_GLOSS = {", 1)[1].split("};", 1)[0]
    value = body.split("const VALUE_GLOSS = {", 1)[1].split("};", 1)[0]
    canonical = set(re.findall(r"(\w+): \"g[lp]_", picker))
    assert canonical == {"deny", "ask", "auto", "fresh", "accumulated"}, canonical
    assert canonical <= set(re.findall(r"(\w+): \"g[lp]_", value)), canonical
    english, russian = _language_tables()
    for token in sorted(canonical):
        for block in (english, russian):
            short = _value(block, f"gp_{token}")
            assert short, token
            # Short enough to survive a one-line control, and never longer than
            # the sentence it was cut down from.
            assert len(short) <= 32, (token, short)
            assert len(short) < len(_value(block, f"gl_{token}")), (token, short)
    # And the long gloss still reaches the surfaces that have room: the summary
    # rows and the drawer read VALUE_GLOSS through `valueWithGloss`.
    assert "return value ? glossed(value, VALUE_GLOSS[value]) : \"\";" in body

    # A node card has no room for a sentence, so its gloss is the tooltip and the
    # visible line keeps the bare canonical state.  Both halves are pinned: a
    # gloss on the card's own line would push the label or the id off it.
    render = body.split("for (const node of visibleNodes) {", 1)[1].split(
        "\n    nodeLayer.appendChild(group);", 1
    )[0]
    assert 'const line = node.kind + " · " + stateLabel(node) +' in render
    assert '"\\n" + node.kind + " · " + stateWithGloss(node);' in render


def test_the_board_explains_its_marks_and_says_which_card_is_you() -> None:
    """Round 3, designer 18 and PM 11 and 13: the glyphs `◑ ○ ⊘ ●` were the
    board's entire status language and nothing anywhere explained them, and the
    operator's own card said "this is you" only in a tooltip nobody hovers
    (item 22)."""

    body = read_spa()

    # The legend lives on the board, where a person meets the marks, and folds
    # away so it does not spend the work surface.
    assert 'id="board-legend"' in body
    assert 'id="legend-panel" hidden' in body
    assert 'aria-controls="legend-panel"' in body
    assert 'data-i18n="legend_open"' in body

    # It is BUILT from the GLYPHS table rather than written out beside it, so a
    # state added there cannot go missing from its own explanation.
    legend = body.split("function paintGlyphLegend() {", 1)[1].split("\n}\n", 1)[0]
    assert "for (const [state, pair] of Object.entries(GLYPHS))" in legend
    # Every distinct glyph the table can produce has a meaning written for it.
    glyphs = body.split("const GLYPHS = {", 1)[1].split("};", 1)[0]
    produced = set(re.findall(r'\["([^"]+)",\s*"\w+"\]', glyphs))
    meanings = body.split("const GLYPH_MEANING = {", 1)[1].split("};", 1)[0]
    explained = set(re.findall(r'"([^"]+)": "lg_', meanings))
    assert produced <= explained, produced - explained
    # `▨` is substituted by `glyphFor` for stale evidence, so no state produces
    # it and it is listed with the two states it can stand in for.
    assert 'const STALE_GLYPH = ["▨", "warn", "lg_stale", ["accepted", "approved"]];' in body
    assert "rows.push(STALE_GLYPH);" in legend

    # The canonical states in the legend are printed, never translated: this is
    # the panel teaching that vocabulary, which is the opposite of replacing it.
    assert 'canonical.textContent = states.join(" · ");' in legend
    assert "t(states" not in legend

    # "This is you" becomes visible on the card, and the tooltip stays.
    assert 't("session_you_badge")' in body
    assert 't("session_you_tip")' in body
    render = body.split("for (const node of visibleNodes) {", 1)[1].split(
        "\n    nodeLayer.appendChild(group);", 1
    )[0]
    assert 'content.appendChild(text(W - 8, 34, t("session_you_badge"), "youbadge"));' in render
    assert ".youbadge{" in body

    english, russian = _language_tables()
    for key in ("legend_open", "legend_title", "legend_note", "lg_stale", "session_you_badge"):
        for block in (english, russian):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


def test_form_microcopy_says_what_the_operator_is_deciding() -> None:
    """Round 3, designer 21: "Бюджет ротации", "Контекст: fresh", "Отставка
    ролей" are the system's words, not a person's.  Each label now says what the
    operator is choosing, with the canonical value still beside it (item 31)."""

    english, russian = _language_tables()

    # The three the designer named, gone in both languages.
    assert "Бюджет ротации" not in russian
    assert "Turnover budget" not in english
    assert _value(russian, "retire_roles_label") != "Отставка ролей"
    assert _value(russian, "hire_context_label") != "Контекст"
    assert _value(english, "hire_context_label") != "Context"

    # What replaced them says what the role may DO, in a person's words.
    for block in (english, russian):
        for key in ("create_roles_label", "retire_roles_label", "open_links_label",
                    "hire_budget_label", "hire_context_label", "setting_tools_label",
                    "setting_skills_label", "run_wall_label", "link_type_label"):
            assert len(_value(block, key)) > 12, (key, _value(block, key))
    assert _value(english, "create_roles_label").startswith("May hire")
    assert _value(english, "retire_roles_label").startswith("May take")
    assert _value(english, "hire_context_label") == "Memory between runs"
    assert _value(russian, "hire_context_label") == "Память между прогонами"

    # The tooltips that already explained these fields are still there, and are
    # reconciled with the new labels rather than repeating them.
    body = read_spa()
    for tip in ("tip_hire_budget", "tip_hire_context", "tip_hire_profile"):
        assert f'data-i18n-title="{tip}"' in body, tip
    for block in (english, russian):
        assert _value(block, "tip_hire_budget") != _value(block, "hire_budget_label")
        # The label says how many; the tooltip says how the number is counted.
        assert _value(block, "tip_hire_budget") not in _value(block, "hire_budget_label")


def test_every_user_facing_string_in_the_markup_reaches_the_tables() -> None:
    """Round 3, designer 10 and PM 7: untranslated strings remained.  The one
    they reported has since been translated; these are the rest -- two aria
    labels a screen reader reads aloud, and a heading with no marker at all
    (item 19)."""

    body = read_spa()

    # aria-label is a label like any other, and is translated like one.
    assert 'data-i18n-aria="lang_label"' in body
    assert 'data-i18n-aria="board_aria"' in body
    i18n = body.split("function applyI18n() {", 1)[1].split("\n}\n", 1)[0]
    assert 'element.setAttribute("aria-label", t(element.dataset.i18nAria));' in i18n

    # Every heading in the markup is either translated or a proper name.  The
    # product and the two protocol acronyms are the only things spelled the same
    # in both languages; anything else with a bare English heading is a miss.
    proper = {"Agent Commons", "MCP"}
    for heading in re.findall(r"<h1([^>]*)>([^<]*)</h1>", body):
        attributes, shown = heading
        assert "data-i18n=" in attributes or shown.strip() in proper, shown

    english, russian = _language_tables()
    for key in ("lang_label", "board_aria", "sgr_title"):
        for block in (english, russian):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


# --- wave 3C: the board, the dock and a longer language -------------------


def test_the_dock_survives_a_language_a_third_longer_than_english() -> None:
    """Round 3, designer 9: Russian overflowed the dock.  The drawer title
    clipped to "nt · Верстальщик", a horizontal scrollbar appeared across the
    dock, and "+ Задача" wrapped inside its own button and broke the toolbar
    row.  The margin the panel is designed to survive is +30% on every string,
    and this measures the Russian table against it before pinning the three
    rules that make the layout independent of a length (item 23)."""

    body = read_spa()
    english, russian = _language_tables()

    # A real margin, not a hypothetical one: the Russian table has to still be
    # exercising it.  A table that stopped would make every rule below untested
    # rather than unnecessary.
    pairs = [
        (len(_value(english, key)), len(_value(russian, key)))
        for key in re.findall(r'^\s*([a-z0-9_]+): "', english, re.MULTILINE)
        if re.search(rf'^\s*{key}: "', russian, re.MULTILINE)
    ]
    ratios = [ru / en for en, ru in pairs if en >= 3]
    assert ratios, "the two tables no longer share a measurable key"
    assert max(ratios) >= 1.3, "the Russian table no longer reaches the +30% margin"
    assert 1.0 <= sum(ratios) / len(ratios) <= 1.6, sum(ratios) / len(ratios)

    # Rule one: a title that must stay one line ellipsizes AND carries the whole
    # of itself as a tooltip, so being cut never makes it unreadable.
    assert (
        "#drawer .drawer-head h2{white-space:nowrap; overflow:hidden;"
        " text-overflow:ellipsis; min-width:0}"
    ) in body
    assert "title.title = title.textContent;" in body

    # Rule two: no dock container scrolls sideways.  The width is fixed, so the
    # content is what gives way -- every container in the chain says so, and
    # none of them may declare a width the content cannot wrap inside.
    for rule in (
        "#dock{overflow-x:hidden; min-width:0}",
        # `break-word`, not `anywhere`: both break an over-long id, but
        # `anywhere` also collapses an element's min-content width to one
        # character, which flattened every flex row in these panels into a
        # vertical alphabet.
        "#dock .panel.open{overflow-x:hidden; overflow-wrap:break-word}",
        "#drawer{overflow-x:hidden; min-width:0}",
        "#drawer .panel.open{overflow-x:hidden; overflow-wrap:break-word}",
        "#sidebar{overflow-x:hidden}",
    ):
        assert rule in body, rule

    # Rule three: a toolbar wraps BETWEEN its controls, never inside one.
    assert ".board-bar button{white-space:nowrap; flex:none}" in body
    assert ".board-bar{row-gap:8px}" in body
    tabs = body.split(".dock-tabs{", 1)[1].split("}", 1)[0]
    assert "flex-wrap:wrap" in tabs, tabs
    assert "min-width:0" in tabs, tabs

    # And the margin those three rules are sized to is stated beside them, so
    # the next person testing this file knows what to test it at.
    assert "+30% IS THE DESIGN MARGIN" in body

    # Rule four, added in round 4 (finding 2): the widest dialog the panel has
    # must hold its grant grid at the same margin, at the two widths the
    # operator ran it at.  The geometry is arithmetic on the rules above, so it
    # is computed here rather than asserted as a screenshot.
    modal_padding, modal_border, gap, track_minimum = 18, 1, 6, 220
    for viewport in (1024, 900):
        modal = min(560, 0.92 * viewport)
        # The dialog never outgrows the viewport, so the page itself cannot
        # gain a second axis -- `min()` keeps the existing 92vw cap in force.
        assert modal <= 0.92 * viewport, viewport
        # box-sizing is border-box for everything, so the padding and the
        # border come out of the declared width rather than adding to it.
        content = modal - 2 * modal_padding - 2 * modal_border
        # `auto-fit` fits as many tracks of at least 220px as the gaps allow.
        columns = int((content + gap) // (track_minimum + gap))
        assert columns == 2, (viewport, columns)
        track = (content - gap * (columns - 1)) / columns
        assert track >= track_minimum, (viewport, track)

        # What a closed <select> can show: the track less its own padding,
        # border and the platform's dropdown arrow.  6.0px per character is a
        # deliberately generous average advance for the 12px face these controls
        # are set in, and the Russian label has to fit with the panel's +30%
        # still in hand.
        text_box = track - 2 * 6 - 2 * 1 - 20
        for token in ("deny", "ask", "auto", "fresh", "accumulated"):
            label = f"{token} — " + _value(russian, f"gp_{token}")
            assert len(label) * 1.3 * 6.0 <= text_box, (viewport, label)
            # The full sentence is what did NOT fit -- that is the clipped
            # "deny — никогда, и даже не…" the operator photographed, and the
            # reason the picker reads the short table.
            english_label = f"{token} — " + _value(english, f"gp_{token}")
            assert len(english_label) * 6.0 <= text_box, (viewport, english_label)

    # A child of the grid may never be wider than its track: a grid item's
    # automatic minimum is its MIN-CONTENT width, and a select's min-content is
    # its longest option, so without this one rule the longest gloss -- not the
    # font, not the language -- is what would push the row sideways.
    assert ".grants > *{min-width:0}" in body
    assert "grid-template-columns:repeat(auto-fit,minmax(220px,1fr))" in body
    assert "#hire-modal .modal{width:min(560px,92vw)}" in body
    assert "*{box-sizing:border-box}" in body
    fields = body.split(".field > input,.field > select,.field > textarea{", 1)[1].split(
        "}", 1
    )[0]
    assert "width:100%" in fields, fields


def test_a_radio_row_keeps_its_dot_beside_its_label() -> None:
    """Round 3, designer 13, confirmed in code: `.field span{display:block}` was
    a descendant rule and caught the caption inside a radio row nested in a
    field, so the dot rendered above its label in the agent settings.  The child
    combinator keeps the caption behaviour ordinary fields rely on, and every
    radio and checkbox in the file now sits in a row of its own (item 24)."""

    body = read_spa()
    assert ".field > span{display:block" in body
    assert ".field span{display:block" not in body
    assert ".choice{display:flex; align-items:baseline" in body

    # Every radio and checkbox in the markup: the settings tool-access modes,
    # the link modal's two types, the Runs view's grouping switch.
    rows = re.findall(r"<label([^>]*)><input type=\"(radio|checkbox)\"", body)
    assert len(rows) == 5, rows
    for attributes, control in rows:
        assert 'class="choice"' in attributes, (control, attributes)

    # And the checklist the settings panel builds from JS, both halves of it.
    assert body.count('row.className = "choice";') == 2
    # The <br> spacers those rows used are gone with the class that replaced
    # them; a flex row does not need one, and one left behind would open a blank
    # line between every tool.
    checklist = body.split("const checklist = document.getElementById", 1)[1].split(
        "\n  }\n", 1
    )[0]
    assert 'createElement("br")' not in checklist


def test_the_settings_tab_wears_its_glyph_in_css_not_in_its_label() -> None:
    """Round 3, designer 12: the "⚙ Settings" tab sat off the baseline of its
    siblings.  The glyph was inside the translated string, where it moved with
    the language and could not be given a line box of its own -- an element to
    hold it would have been overwritten by applyI18n's textContent write.  So
    the glyph is drawn by CSS and the string is only words (item 25)."""

    body = read_spa()
    english, russian = _language_tables()

    for block in (english, russian):
        assert "⚙" not in block
    assert _value(english, "tab_settings") == "Settings"
    assert _value(russian, "tab_settings") == "Настройки"
    assert 'data-i18n="tab_settings">Settings</button>' in body

    glyph = body.split('.dock-tabs button[data-panel="settings"]::before{', 1)[1].split(
        "}", 1
    )[0]
    assert 'content:"\\2699"' in glyph
    assert "line-height:1.2" in glyph
    assert "vertical-align:baseline" in glyph
    # The siblings state the same line box, so no tab depends on what is in it.
    tab = body.split(".dock-tabs button{", 1)[1].split("}", 1)[0]
    assert "line-height:1.2" in tab, tab


def test_every_scroll_container_takes_the_panels_own_scrollbar() -> None:
    """Round 3, designer 15: with no scrollbar styling at all, every scroll
    container rendered the platform's light scrollbar -- the highest-contrast
    object on a dark screen.  Both mechanisms are styled, on the universal
    selector, because between them they cover the dock, the drawer, the sidebar,
    the centre views, `pre` blocks, the guide and the lists (item 26)."""

    body = read_spa()

    # The page declares itself dark, so the user agent stops handing it light
    # chrome for scrollbars, selects and number spinners.
    assert "color-scheme:dark" in body

    assert "*{scrollbar-width:thin; scrollbar-color:var(--edge) var(--surface)}" in body
    for rule in (
        "::-webkit-scrollbar{width:10px; height:10px}",
        "::-webkit-scrollbar-track{background:var(--surface)}",
        "::-webkit-scrollbar-thumb{",
        "::-webkit-scrollbar-thumb:hover{background:var(--faint)}",
        "::-webkit-scrollbar-corner{background:var(--surface)}",
    ):
        assert rule in body, rule

    # Painted from the palette, never from a literal: a hardcoded grey here is
    # how a surface drifts out of the theme it belongs to.
    thumb = body.split("::-webkit-scrollbar-thumb{", 1)[1].split("}", 1)[0]
    assert "var(--edge)" in thumb
    assert "var(--surface)" in thumb
    assert not re.search(r"#[0-9a-fA-F]{3,6}", thumb), thumb


def test_the_footer_is_readable_and_says_what_a_stale_warning_is() -> None:
    """Round 3, designer 22: the footer was 11px, unseparated from the board
    above it, and said "устаревших предупреждений" without explaining what that
    meant.  The explanation here is read off `_mark_bound_evidence_stale` in the
    projection, not approximated: it names the five kinds that can go stale and
    says what going stale does and does not do (item 27)."""

    body = read_spa()
    english, russian = _language_tables()

    foot = body.split("\nfooter{", 1)[1].split("}", 1)[0]
    assert "font-size:12px" in foot, foot
    assert "border-top:1px solid var(--edge)" in foot, foot
    assert "padding:9px 12px" in foot, foot

    # The term travels with its own explanation rather than waiting inside a
    # tooltip nobody hovers, and the tooltip carries the whole of it.
    assert 'gloss.textContent = "— " + t("stale_warnings_gloss");' in body
    assert 'stale.title = t("stale_warnings_tip");' in body
    assert 'errors.title = t("issues_tip");' in body

    for block in (english, russian):
        assert len(_value(block, "stale_warnings_gloss")) > 30
        assert len(_value(block, "stale_warnings_tip")) > 120

    # Truthful, not approximate: what the projection actually marks stale, and
    # what it does not do to it.
    tip = _value(english, "stale_warnings_tip").lower()
    for named in ("review", "verification", "artifact", "finding", "decision", "revision"):
        assert named in tip, named
    assert "deleted" in tip
    assert "acceptance" in tip
    russian_tip = _value(russian, "stale_warnings_tip").lower()
    for named in ("обзор", "проверка", "артефакт", "находка", "решение", "ревизии"):
        assert named in russian_tip, named


def test_a_run_card_says_when_it_ran_and_never_invents_a_spend() -> None:
    """Round 3, designer 16: runs carried no start time, duration or spend, and
    could be neither filtered nor grouped.  Three of those four are now on the
    card.  The fourth is deliberately absent: nothing in this codebase records a
    consumed provider unit, so the card shows the LIMIT a run was launched under
    and the view says plainly that consumption is not recorded (item 28)."""

    body = read_spa()
    english, russian = _language_tables()

    meta = body.split("function runMetaLine(card, run) {", 1)[1].split("\n}\n", 1)[0]
    for field in ("run.started_at", "run.duration_seconds", "run.purpose", "run.limits.budget"):
        assert field in meta, field

    # The budget is a mapping -- a limit and the unit it counts -- so it is
    # spelled as one.  Printed bare it read "бюджет: [object Object]" in a live
    # panel, and the unit is canonical, so it stays as the ledger writes it.
    assert "budgetText(budget)" in meta
    formatter = body.split("function budgetText(budget) {", 1)[1].split("\n}\n", 1)[0]
    assert "budget.limit" in formatter
    assert "budget.unit" in formatter
    # A shape it does not recognise is spelled out, never silently dropped.
    assert "Object.entries(budget)" in formatter
    # A canonical purpose keeps the ledger's word and gains the panel's beside it.
    assert "valueWithGloss(run.purpose)" in meta
    # One line, both surfaces: the Runs view and the drawer's runs on a task.
    assert body.count("runMetaLine(") == 3, body.count("runMetaLine(")

    # The readable form is what a person reads; the ISO string stays canonical
    # and travels as the tooltip, the same split the id chip makes.
    assert "toLocaleString" in body
    assert 'formatWhen(run.started_at), run.started_at || ""' in meta

    # No spend-shaped label, and no arithmetic that would turn a cap into one.
    assert _value(english, "run_budget_word") == "budget"
    assert _value(russian, "run_budget_word") == "бюджет"
    for operator in ("budget *", "budget /", "budget +", "budget -"):
        assert operator not in body, operator
    for block in (english, russian):
        assert len(_value(block, "runs_no_spend")) > 80
    honesty = _value(english, "runs_no_spend").lower()
    for word in ("recorded", "token", "money", "cap"):
        assert word in honesty, word
    assert "cap" in _value(english, "run_budget_tip").lower()
    # It is said where the operator looks for a cost: the Runs view and the
    # task drawer's runs block, both.
    assert body.count('data-i18n="runs_no_spend"') == 2

    # Filtering: live/finished and by role, from plain form controls, so the
    # view stays keyboard-usable and dependency-free.
    assert '<select id="runs-filter-status">' in body
    assert '<select id="runs-filter-role">' in body
    assert '<input type="checkbox" id="runs-group-task">' in body
    filters = body.split("function filteredRuns() {", 1)[1].split("\n}\n", 1)[0]
    assert 'status === "live" && !run.live' in filters
    assert 'status === "finished" && run.live' in filters
    assert "run.agent_id !== role" in filters

    # Grouping by task.
    grouped = body.split("function paintRunsList() {", 1)[1].split("\n}\n", 1)[0]
    assert 'document.getElementById("runs-group-task").checked' in grouped
    assert "groups.get(key).push(run)" in grouped

    # And a snapshot repaint -- which arrives about every two seconds -- refills
    # both pickers AROUND the operator's choice rather than onto it.
    refill = body.split("function paintRunFilters() {", 1)[1].split("\n}\n", 1)[0]
    assert 'status.value || "all"' in refill
    assert 'role.value || ""' in refill


def test_the_header_names_the_workspace_and_keeps_both_ulids_one_click_away() -> None:
    """Round 3, designer 18: the header spent half its width on two ULIDs and
    neither said which workspace this was.  There is no workspace name in this
    system, so the honest human handle is the directory it runs in; the ids stay
    retrievable behind it, because they are what an operator pastes into a CLI
    command (item 29)."""

    body = read_spa()

    # The name is the repo directory's basename, and nothing else is invented.
    naming = body.split("function workspaceDirName(repo) {", 1)[1].split("\n}\n", 1)[0]
    assert "split(/[\\\\/]+/)" in naming
    assert "workspace_name" not in body, "the panel must not invent a workspace name field"

    identity = body.split("function paintWorkspaceIdentity() {", 1)[1].split("\n}\n", 1)[0]
    assert "metaInfo.repo" in identity
    # And it says that is what it is, in words, with the whole path.
    assert 'said("workspace_dir_tip", { path: repo })' in identity

    # Both ULIDs stay reachable, as the same chips the confirmations use.
    for chip in ("ws-repo", "ws-id", "ws-writer", "ws-instance"):
        assert f'class="idchip" id="{chip}"' in body, chip
        assert f'"{chip}"' in identity, chip
    assert "metaInfo.workspace_id" in identity
    assert "metaInfo.writer_session_id" in identity
    assert 'aria-controls="workspace-ids"' in body
    assert 'document.getElementById("workspace-ids").hidden = !open;' in body

    # The second ULID is off the header: the writes pill answers whether writes
    # are on, which is the whole question it exists for.
    status = body.split("function paintStatus() {", 1)[1].split("\n}\n", 1)[0]
    assert "writer_session_id" not in status, status


def test_the_shell_collapses_below_the_breakpoint_and_fit_still_measures_it() -> None:
    """Round 3, designer 24: below about 900px the sidebar would not collapse
    and the dock still took 360 of the width, leaving the board a strip.  Under
    the breakpoint both side columns leave the grid and become overlays -- and
    they are toggled with the `hidden` ATTRIBUTE, which is exactly what wave
    3A's `visibleBoardRect` reads, so the fit keeps measuring the area the
    operator can actually see (item 30)."""

    body = read_spa()
    css = body.split("<style", 1)[1].split("</style>", 1)[0]

    assert "@media (max-width:900px){" in css
    narrow = css.split("@media (max-width:900px){", 1)[1].split("\n}\n", 1)[0]
    assert "main{grid-template-columns:1fr}" in narrow
    assert "#sidebar,#dock{" in narrow
    assert "position:absolute" in narrow
    assert "#shell-toggles{display:flex}" in narrow
    # Narrowest LAST: both queries match an 800px window, and a shell still
    # declaring three columns there would quietly undo the collapse.
    assert css.index("@media (max-width:1100px)") < css.index("@media (max-width:900px)")
    # The overlays are positioned against the shell row, so they stop at the
    # footer rather than crossing it.
    shell_row = css.split("\nmain{", 1)[1].split("}", 1)[0]
    assert "position:relative" in shell_row

    # `hidden`, not a class -- this is the join with the fit computation.
    applied = body.split("function applyShell() {", 1)[1].split("\n}\n", 1)[0]
    assert "document.getElementById(id).hidden = !open;" in applied
    assert 'setAttribute("aria-expanded"' in applied
    rect = body.split("function visibleBoardRect() {", 1)[1].split("\n}\n", 1)[0]
    assert 'for (const id of ["sidebar", "dock"])' in rect
    assert "element.hidden" in rect
    assert "getBoundingClientRect()" in rect

    # A window widened back past the breakpoint gets its columns returned to it.
    assert 'window.addEventListener("resize", applyShell);' in body
    for toggle in ("shell-nav", "shell-dock"):
        assert f'id="{toggle}"' in body, toggle


# --- wave 4: the guide's framing, its picture, and the forthcoming sections ---


def _guide_markup() -> str:
    """The Overview section, from its `<section>` to the next one."""

    return read_spa().split('<section class="view" id="view-guide">', 1)[1].split(
        "\n    </section>", 1
    )[0]


GUIDE_DEEP_PAGES = ("agents", "tasks", "links", "limits", "catalog")


def test_the_guide_says_out_loud_which_of_its_pages_are_reference() -> None:
    """Item 32: the designer scored "In short" 9/10 as onboarding and the other
    five pages 8/10 as reference and 3/10 as onboarding.  They are reference --
    the dishonesty was never the content, only that nothing said so, leaving
    five specifications to be read as a tutorial that does not teach."""

    body = read_spa()
    guide = _guide_markup()

    # The strip separates the two kinds, with a label, not a bare gap.
    strip = guide.split('id="guide-tabs"', 1)[1].split("</div>", 1)[0]
    assert '<span class="tabsplit" data-i18n="guide_ref_label">' in strip
    assert strip.index('data-gpage="brief"') < strip.index("tabsplit")
    for page in GUIDE_DEEP_PAGES:
        assert strip.index("tabsplit") < strip.index(f'data-gpage="{page}"'), page
    # A span rather than a button, so the tab handler steps straight over it.
    tabs = body.split('document.getElementById("guide-tabs").addEventListener', 1)[1]
    assert 'const button = event.target.closest("button");' in tabs.split("});", 1)[0]

    # And each of the five says it again at its own top, because a reader who
    # arrives by deep link never passes the strip.
    for page in GUIDE_DEEP_PAGES:
        section = guide.split(f'<div id="gpage-{page}"', 1)[1].split("\n        </div>", 1)[0]
        assert 'data-i18n="guide_ref_lead"' in section, page
    brief = guide.split('<div id="gpage-brief">', 1)[1].split("\n        </div>", 1)[0]
    assert "guide_ref_lead" not in brief, "the onboarding page is not a reference page"

    english, russian = _language_tables()
    for key in ("guide_ref_label", "guide_ref_lead"):
        for block in (english, russian):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key


def test_every_guide_deep_link_lands_on_a_heading_that_actually_exists() -> None:
    """The other half of item 32: terms an operator meets in the panel and
    cannot guess are linked to the paragraph that explains them.  One mechanism
    -- stable heading ids plus `openGuide` -- so this compares the two sets and
    a renamed heading breaks the build rather than the link."""

    body = read_spa()
    guide = _guide_markup()

    ids = set(re.findall(r'<h3 id="(g-[a-z-]+)"', guide))
    assert len(ids) >= 19, ids
    links = re.findall(
        r'data-guide-page="([a-z]+)"\s*\n?\s*data-guide-anchor="([a-z-]+)"', body
    )
    assert links, "no term in the panel links into the guide"
    assert {anchor for _, anchor in links} <= ids, {a for _, a in links} - ids

    # And the page each link names is the page the heading is actually on: a
    # correct id on the wrong tab lands the reader on a hidden element.
    for page, anchor in links:
        section = guide.split(f'<div id="gpage-{page}"', 1)[1].split("\n        </div>", 1)[0]
        assert f'id="{anchor}"' in section, (page, anchor)

    # One mechanism, not a handler per link: `openGuide` is defined once and
    # called from exactly two places -- the single delegated listener, and the
    # popover's "read the whole paragraph" button, which is the same departure
    # taken deliberately (round 4, finding 1).
    assert body.count("openGuide(") == 3
    assert 'const link = event.target.closest("[data-guide-anchor]");' in body
    opener = body.split("function openGuide(page, anchorId) {", 1)[1].split("\n}\n", 1)[0]
    assert "viewShow(\"guide\");" in opener
    assert "guideShowPage(page);" in opener
    assert "heading.scrollIntoView({ block: \"start\" });" in opener
    # A dialog is left through the guard Esc uses, never over typed work.
    assert "if (openModalId && !dismissOpenModal()) { return; }" in opener
    # The heading it lands on is marked, in CSS, for long enough to be seen.
    assert 'heading.classList.add("flash");' in opener
    assert "@keyframes guideflash{" in body
    # And keyboard focus goes with the reader rather than staying on a mark that
    # may now be inside a dialog this very call closed (seen in a live panel).
    assert "tab.focus({ preventScroll: true });" in opener


def test_the_terms_an_operator_cannot_guess_carry_their_link_where_they_appear() -> None:
    """Item 32 names the terms: the grant levels, `context_mode`, the turnover
    budget, the acceptance chain, link types, skills against tools, and the run
    states.  Each is linked at the control where it is met, not only from a
    contents page nobody opens."""

    body = read_spa()
    regions = {
        "hire": body.split('id="hire-modal"', 1)[1].split('id="task-modal"', 1)[0],
        "settings": body.split('id="panel-settings"', 1)[1].split("\n    </section>", 1)[0],
        "link": body.split('id="link-modal"', 1)[1].split('id="close-link-modal"', 1)[0],
        "acceptance": body.split('id="task-acceptance"', 1)[1].split('id="task-runs"', 1)[0],
        "runs": body.split('id="view-runs"', 1)[1].split("\n    </section>", 1)[0],
        "skills": body.split('id="view-skills"', 1)[1].split("\n    </section>", 1)[0],
        "tools": body.split('id="view-tools"', 1)[1].split("\n    </section>", 1)[0],
    }
    for region, anchor in (
        ("hire", "g-ag-memory"),      # context_mode
        ("hire", "g-ag-lineage"),     # turnover_budget
        ("settings", "g-ag-memory"),
        ("settings", "g-ct-skill"),
        ("settings", "g-ct-tools"),
        ("link", "g-ln-record"),      # ask / handoff_work
        ("acceptance", "g-tk-accept"),
        ("runs", "g-tk-run"),         # the run states
        ("skills", "g-ct-skill"),
        ("tools", "g-ct-tools"),
    ):
        assert f'data-guide-anchor="{anchor}"' in regions[region], (region, anchor)
    # Three grants, one explanation: every grant row in both forms carries it.
    for region in ("hire", "settings"):
        assert regions[region].count('data-guide-anchor="g-ag-grants"') == 3, region

    # The mark sits in the caption's right-hand gutter, and the caption reserves
    # it: seen in a live panel, "May take other roles out of service" ran
    # straight under its "?" before the gutter existed.
    assert ".field > .gref{position:absolute" in body
    assert "padding-right" in body.split(".field > span{", 1)[1].split("}", 1)[0]

    # Each mark is a control a screen reader can name, and its glyph is drawn in
    # CSS rather than carried as an untranslated character inside the chrome.
    assert ".gref::before{content:\"?\"}" in body
    english, russian = _language_tables()
    for key in re.findall(r'class="gref"[^>]*data-i18n-title="([a-z_]+)"', body):
        for block in (english, russian):
            assert re.search(rf'^\s*{key}: "', block, re.MULTILINE), key
    for element in re.findall(r"<button[^>]*class=\"gref\"[^>]*>", body):
        assert "data-i18n-aria=" in element, element


def test_the_guide_carries_one_picture_and_every_label_in_it_is_translated() -> None:
    """Item 33: the guide described a loop and drew nothing.  The picture is
    inline SVG -- no asset, no data URI -- it takes its colours from the palette
    through classes, and every label goes through the same i18n pass as the rest
    of the chrome rather than being hardcoded English under a Russian panel."""

    body = read_spa()
    guide = _guide_markup()
    assert '<svg class="guidemap"' in guide
    figure = guide.split('<svg class="guidemap"', 1)[1].split("</svg>", 1)[0]

    # Drawn, not fetched, and not smuggled in as an encoded image either.
    for forbidden in ("<image", "data:", "xlink:href", "<use "):
        assert forbidden not in figure, forbidden

    # Colours through classes: a presentation attribute would put the palette
    # out of the stylesheet's reach, and a `style` attribute the CSP would drop.
    for forbidden in ('fill="', 'stroke="', "style="):
        assert forbidden not in figure, forbidden
    for rule in (".guidemap .gm-box{", ".guidemap .gm-label{", ".guidemap .gm-edge{"):
        assert rule in body, rule
    assert "var(--" in body.split(".guidemap .gm-box{", 1)[1].split("}", 1)[0]

    # Every label is a marker, and every marker reaches both tables.  `applyI18n`
    # writes textContent on anything carrying `data-i18n`, which is what an SVG
    # `<text>` node needs and all it needs.
    labels = re.findall(r"<text[^>]*>", figure)
    assert len(labels) >= 8, labels
    english, russian = _language_tables()
    for label in labels:
        key = re.search(r'data-i18n="([a-z0-9_]+)"', label)
        assert key, label
        for block in (english, russian):
            assert re.search(rf'^\s*{key.group(1)}: "', block, re.MULTILINE), key.group(1)
    # The figure names itself for a screen reader, in both languages too.
    assert 'role="img" data-i18n-aria="guide_map_aria"' in guide
    for block in (english, russian):
        assert re.search(r'^\s*guide_map_aria: "', block, re.MULTILINE)

    # Review sits between the run and acceptance, which is the one thing both
    # blind testers got wrong; a picture that skipped it would be worse than
    # none.  The return edge is drawn too: sent back is an ordinary outcome.
    order = [
        figure.index(f'data-i18n="guide_map_{node}"')
        for node in ("role", "task", "run", "review", "accepted")
    ]
    assert order == sorted(order), order
    assert 'class="gm-back"' in figure


def test_the_guides_examples_read_as_examples_instead_of_dim_grey_on_dark() -> None:
    """Item 33's other half: the guide's monospace examples -- its most concrete
    sentences -- were `--muted` on the page's own ground, the dimmest text in a
    dark panel.  Every `.dim` in the guide is accounted for here, not only the
    ones that happened to be noticed."""

    body = read_spa()
    guide = _guide_markup()

    rule = body.split(".doc p.mono.dim{", 1)[1].split("}", 1)[0]
    assert "color:var(--fg)" in rule, "an example still has no readable foreground"
    assert "background:var(--surface-2)" in rule, "an example still has no surface of its own"
    # Specific enough to beat the muted `.doc p.dim` above it.
    assert body.index(".doc p.dim{") < body.index(".doc p.mono.dim{")

    # And the rest of the guide's dim text, counted: one subtitle under the
    # title, one caption under the picture, and the five reference leads.  Those
    # are meta-lines about the page rather than the page's content, so they stay
    # quiet on purpose -- but they are pinned, so the next `.dim` added to the
    # guide has to be a deliberate choice rather than an inherited default.
    classes = re.findall(r'<p class="([^"]*\bdim\b[^"]*)"', guide)
    assert classes.count("dim") == 2, classes
    assert classes.count("dim guidelead") == len(GUIDE_DEEP_PAGES), classes
    assert classes.count("mono dim") == len(re.findall(r'class="mono dim"', guide))
    assert set(classes) == {"dim", "dim guidelead", "mono dim"}, set(classes)


def test_the_forthcoming_sections_are_grouped_yet_neither_hidden_nor_disabled() -> None:
    """Item 34 was rejected as reported -- SGR and MCP carry real content and
    each names its unlock condition, so hiding them would be less honest, not
    more.  The recorded compromise is to group them as forthcoming."""

    body = read_spa()
    nav = body.split('<nav id="sidebar">', 1)[1].split("</nav>", 1)[0]
    assert '<div class="navgroup soon">' in nav
    forthcoming = nav.split('<div class="navgroup soon">', 1)[1].split("</div>", 1)[0]

    # Their own group, under its own translated heading.
    assert 'class="navlbl" data-i18n="nav_soon"' in forthcoming
    english, russian = _language_tables()
    for block in (english, russian):
        assert re.search(r'^\s*nav_soon: "', block, re.MULTILINE)

    # Both are in it, and neither is left among the sections that work today.
    library = nav.split('data-i18n="nav_library"', 1)[1].split("</div>", 1)[0]
    for view in ("sgr", "mcp"):
        assert f'data-view="{view}"' in forthcoming, view
        assert f'data-view="{view}"' not in library, view

    # Grouped, not withdrawn: nothing here is hidden and nothing is disabled,
    # and each entry keeps the chip that says when it arrives.
    buttons = re.findall(r"<button[^>]*>", forthcoming)
    assert len(buttons) == 2, buttons
    for button in buttons:
        assert "hidden" not in button, button
        assert "disabled" not in button, button
    assert 'data-i18n="chip_soon"' in forthcoming
    assert 'data-i18n="chip_later"' in forthcoming

    # The marker that makes the grouping legible is drawn in CSS, so it can
    # never join a translated label or be mistaken for a disabled state.
    assert ".navgroup.soon button::before{" in body
    assert ".navgroup.soon{" in body

    # And the pages still open, and still say what unlocks them.
    for view, note in (("sgr", "sgr_note"), ("mcp", "mcp_note")):
        assert f'id="view-{view}"' in body, view
        assert f'data-i18n="{note}"' in body, note
        for block in (english, russian):
            assert re.search(rf'^\s*{note}: "', block, re.MULTILINE), note
