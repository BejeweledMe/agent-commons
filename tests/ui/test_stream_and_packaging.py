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
    trap = body.split('if (event.key === "Escape") {', 1)[1].split("return;", 1)[0]
    assert "modalFieldState(scrim) !== modalOpenedAs" in trap
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
    assert "if (!state || state === phase) { return lead + phase; }" in status
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
    assert "skillsEmpty.hidden = skills.length > 0;" in paint
    # Consistent with the Catalogue view: the same sentence about the same gate.
    assert 'catalogEditing ? t("catalog_add_here") : t("catalog_readonly")' in paint
    catalogue = body.split("function paintCatalog() {", 1)[1].split("\n}\n", 1)[0]
    assert 't("catalog_readonly")' in catalogue
    # The empty state is written from JS, so it must not also claim a data-i18n
    # marker -- applyI18n runs on every snapshot and would overwrite it.
    assert '<p class="note" id="settings-skills-empty" hidden></p>' in body
    # The tools checklist gets the same treatment.
    assert "if (!summary.narrowable.length) {" in paint
    assert 't("tools_none_narrowable")' in paint

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
    for picker in ("hire-profile", "hire-context_mode", "hire-preset"):
        assert f'held("{picker}"' in paint, picker

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
