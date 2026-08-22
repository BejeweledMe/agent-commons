"""The tab catches up with its own replaced session.

The panel's operator session can expire while the tab stays open -- a machine
asleep past the TTL -- and be replaced under the same identity.  The tab reads
`/api/meta` exactly once, at boot, and decides which card on the board is the
operator's own window by comparing ids against the `writer_session_id` it
cached there.  A tab that only printed a notice would go on calling the DEAD
session the operator's window and hide its own live one behind the runtime
toggle, with a header chip naming an id that no longer exists.

So the frozen informational code `session_expired_recovered` is handled as an
instruction to re-read `/api/meta`, and the real function runs here under node:
"it re-fetches" and "it does so once" are claims about behaviour.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agent_commons.ui import read_spa


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


def _recover(lang: str, frames: list[dict[str, object]]) -> dict[str, object]:
    """Feed the real `applySessionRecovery` a sequence of stream frames.

    Everything it touches beyond its own logic is stubbed and recorded: the
    meta read, the repaints, and the line it writes.  The string table and the
    two functions under test are the asset's own.
    """

    body = read_spa()
    harness = "\n".join(
        [
            "let lang = process.argv[2];",
            "const STRINGS = {"
            + body.split("const STRINGS = {", 1)[1].split("\n};", 1)[0]
            + "\n};",
            _function(body, "function t(key) {"),
            "const calls = [];",
            'let metaInfo = { writer_session_id: "session.OLD", writes_enabled: true };',
            "let writesEnabled = true;",
            "let lastGraph = null;",
            "let reported = null;",
            "async function api(path) {",
            "  calls.push(path);",
            '  return { writer_session_id: "session.NEW", writes_enabled: true };',
            "}",
            'function paintStatus() { calls.push("paintStatus"); }',
            # The refusal banner is repainted off the SAME re-read: a panel that
            # lost the singleness race can win it back when the first window
            # closes, and a banner drawn only at boot would outlive its cause.
            'function paintPanelRefusal() { calls.push("paintPanelRefusal"); }',
            "function paintChrome() {}",
            "function render() {}",
            "function setStream(state, key) { calls.push(key); }",
            "function reportResult(host, tone, sentence, id) {",
            "  reported = { tone: tone, sentence: sentence, id: id };",
            "}",
            "const document = { getElementById: function () { return {}; } };",
            "let sessionLineage = [];",
            _function(body, "function isOperatorSession(node) {"),
            "let recoveredSessionId = null;",
            _function(body, "async function applySessionRecovery(payload) {"),
            "const frames = JSON.parse(process.argv[3]);",
            "(async () => {",
            "  for (const frame of frames) { await applySessionRecovery(frame); }",
            "  console.log(JSON.stringify({",
            "    calls: calls,",
            "    meta: metaInfo.writer_session_id,",
            "    lineage: sessionLineage,",
            "    reported: reported,",
            '    old: isOperatorSession({ kind: "session", id: "session.OLD" }),',
            '    now: isOperatorSession({ kind: "session", id: "session.NEW" }),',
            '    other: isOperatorSession({ kind: "session", id: "session.OTHER" }),',
            "  }));",
            "})();",
        ]
    )
    done = subprocess.run(
        ["node", "-", lang, json.dumps(frames)],
        input=harness,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


FRAME = {
    "code": "session_expired_recovered",
    "writer_session_id": "session.NEW",
    "previous_session_id": "session.OLD",
    "writer_session_ids": ["session.OLD", "session.NEW"],
}


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="no node to run applySessionRecovery() in"
)


@needs_node
def test_the_frame_re_reads_meta_rather_than_only_printing_a_notice() -> None:
    """The requirement the wave contract states in as many words: an
    informational message is not enough, because the cached id is what decides
    which card is the operator's own."""

    answer = _recover("en", [FRAME])
    assert answer["calls"].count("/api/meta") == 1
    assert answer["meta"] == "session.NEW"
    assert "paintStatus" in answer["calls"]
    # And everything else that answer decides is repainted off it, including the
    # refusal banner: `session_refusal` travels in the same payload.
    assert "paintPanelRefusal" in answer["calls"]


@needs_node
def test_a_re_announced_frame_does_nothing_at_all() -> None:
    """The frame carries no event id and is re-announced on every new
    connection, so an open tab that reconnects twice would otherwise re-read
    meta and re-print the line each time."""

    answer = _recover("en", [FRAME, FRAME, FRAME])
    assert answer["calls"].count("/api/meta") == 1


@needs_node
def test_a_second_replacement_is_picked_up_rather_than_swallowed() -> None:
    """Idempotence is per lineage tip, not "once per tab": a machine that sleeps
    twice is replaced twice."""

    again = dict(FRAME)
    again["previous_session_id"] = "session.NEW"
    again["writer_session_id"] = "session.THIRD"
    again["writer_session_ids"] = ["session.OLD", "session.NEW", "session.THIRD"]
    answer = _recover("en", [FRAME, again])
    assert answer["calls"].count("/api/meta") == 2
    assert answer["lineage"] == ["session.OLD", "session.NEW", "session.THIRD"]


@needs_node
def test_the_whole_line_of_sessions_stays_the_operators_own_window() -> None:
    """The other half of the same mistake: an expired session is still on the
    board, and it was still this operator's window.  Drawing it as somebody
    else's machinery is as wrong as treating it as the live one."""

    answer = _recover("en", [FRAME])
    assert answer["old"] is True
    assert answer["now"] is True
    # A session this panel never owned is machinery like any other.
    assert answer["other"] is False


@needs_node
def test_the_line_it_says_carries_the_new_id_and_is_not_a_green_tick() -> None:
    """Nothing was accepted -- a session was replaced -- so the confirmation
    tone is deliberately blank, and the id rides beside the sentence rather
    than instead of it."""

    for lang, table in (("en", 0), ("ru", 1)):
        answer = _recover(lang, [FRAME])
        reported = answer["reported"]
        assert reported["tone"] == ""
        assert reported["id"] == "session.NEW"
        assert reported["sentence"] == _value(_language_tables()[table], "session_recovered_note")


@needs_node
def test_a_refused_meta_read_says_so_instead_of_looking_recovered() -> None:
    """The read is what makes the recovery real.  If it fails the cached id is
    still the stale one this frame exists to replace, so the panel reports
    itself out of touch rather than repainting as if it had caught up."""

    body = read_spa()
    recovery = body.split("async function applySessionRecovery(payload) {", 1)[1]
    recovery = recovery.split("\n}\n", 1)[0]
    assert 'setStream("gap", "stream_unauthorized");' in recovery
    assert recovery.index('setStream("gap"') < recovery.index("paintStatus();")


def test_the_frame_never_touches_the_resumption_cursor() -> None:
    """It is sent without an `id:` precisely so it cannot; the handler must not
    put one back."""

    body = read_spa()
    handler = body.split('if (event === "session_expired_recovered" && data) {', 1)[1]
    handler = handler.split("return;", 1)[0]
    code = "\n".join(line for line in handler.split("\n") if not line.strip().startswith("//"))
    assert "lastEventId" not in code
    assert "applySessionRecovery(JSON.parse(data));" in code
