from __future__ import annotations

import re

import pytest

from agent_commons.domain.context_pack import (
    ContextPackDraft,
    ContextPackRecord,
    ContextPackRefusal,
    ContextPackRefusalCode,
)
from agent_commons.services.context_compiler import ContextCompiler

PACK_ID = "context_pack." + "0" * 25 + "1"
EVENT_ID = "evt." + "0" * 25 + "1"


def _record(summary: str = "Stable baseline") -> ContextPackRecord:
    return ContextPackRecord.create(
        context_pack_id=PACK_ID,
        revision=EVENT_ID,
        source_event_id=EVENT_ID,
        draft=ContextPackDraft.from_payload(
            {
                "summary": summary,
                "facts": [],
                "decision_refs": [],
                "open_questions": ["What remains open?"],
            }
        ),
        recorded_at="2026-08-30T00:00:00Z",
        author_session_ids=("session.researcher",),
    )


def test_context_compiler_is_byte_deterministic_and_fingerprinted() -> None:
    compiler = ContextCompiler()

    first = compiler.compile(_record())
    second = compiler.compile(_record())

    assert first.text.encode("utf-8") == second.text.encode("utf-8")
    assert first.binding == second.binding
    assert first.size_bytes == len(first.text.encode("utf-8"))
    assert re.fullmatch(r"[a-f0-9]{64}", first.compiled_context_fingerprint)
    assert first.binding.context_pack_revision == EVENT_ID
    assert "provider" not in first.text.lower()


def test_context_compiler_fingerprint_changes_with_content_or_compiler_version() -> None:
    original = ContextCompiler().compile(_record("First"))
    changed_content = ContextCompiler().compile(_record("Second"))
    changed_compiler = ContextCompiler(compiler_version="context-pack-compiler.v2").compile(
        _record("First")
    )

    assert original.compiled_context_fingerprint != changed_content.compiled_context_fingerprint
    assert original.compiled_context_fingerprint != changed_compiler.compiled_context_fingerprint


def test_context_compiler_refuses_oversized_render_before_returning_output() -> None:
    with pytest.raises(ContextPackRefusal) as caught:
        ContextCompiler(max_bytes=32).compile(_record())

    assert caught.value.code is ContextPackRefusalCode.OVERSIZED
    assert "Stable baseline" not in str(caught.value)
