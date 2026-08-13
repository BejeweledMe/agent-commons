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
