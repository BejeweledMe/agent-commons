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
