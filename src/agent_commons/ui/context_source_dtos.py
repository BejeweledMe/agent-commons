"""Current Context Pack source identities without generic entity payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.domain.collections import collection_for
from agent_commons.domain.context_pack import (
    ALLOWED_SOURCE_KINDS,
    ContextFact,
    ContextPackDraft,
    ContextPackRefusal,
    RevisionBoundRef,
)
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.services.context_packs import ContextPackCommands

MAX_CONTEXT_SOURCES = 256
MAX_SOURCE_LABEL_BYTES = 256
SOURCE_KINDS = ALLOWED_SOURCE_KINDS | {"decision"}
_LABEL_FIELDS = {
    "artifact": ("title", "name"),
    "task": ("title",),
    "thread": ("title", "subject"),
    "finding": ("title", "summary"),
    "decision": ("title", "summary", "proposal"),
    "verification": ("title", "verdict"),
}


def _label(kind: str, identifier: str, record: Mapping[str, object]) -> str:
    for field in _LABEL_FIELDS[kind]:
        value = record.get(field)
        if not isinstance(value, str):
            continue
        # Labels are display metadata, never a path/body fallback. Bound work
        # before normalizing controls, including bidi/zero-width characters.
        printable = "".join(char if char.isprintable() else " " for char in value[:1024])
        label = " ".join(printable.split()).encode("utf-8")[:MAX_SOURCE_LABEL_BYTES]
        text = label.decode("utf-8", errors="ignore").strip()
        if text:
            return text
    return identifier


def context_source_catalog_payload(snapshot: ProjectSnapshot) -> dict[str, Any]:
    """Reuse publication eligibility over one snapshot; never read source files.

    A current candidate is not accepted truth or a guarantee of later launch.
    Publication and execution revalidate the chosen exact references.
    """

    sources: list[dict[str, object]] = []
    truncated = False
    for kind in sorted(SOURCE_KINDS):
        records = getattr(snapshot, str(collection_for(kind)))
        for identifier, record in sorted(records.items()):
            revision = record.get("effective_revision") or record.get("revision")
            if not is_typed_id(identifier, kind) or not isinstance(revision, str):
                continue
            if not is_typed_id(revision, "evt"):
                continue
            ref = RevisionBoundRef(kind, identifier, revision)
            draft = ContextPackDraft(
                summary="Source eligibility",
                facts=() if kind == "decision" else (ContextFact("Source", (ref,)),),
                decision_refs=(ref,) if kind == "decision" else (),
                open_questions=(),
            )
            try:
                ContextPackCommands.validate_sources(draft, snapshot)
            except ContextPackRefusal:
                continue
            if len(sources) == MAX_CONTEXT_SOURCES:
                truncated = True
                break
            sources.append(
                {
                    "ref": {"kind": kind, "id": identifier, "revision": revision},
                    "label": _label(kind, identifier, record),
                    "freshness": "current",
                }
            )
        if truncated:
            break
    return {
        "schema": "agent-commons.ui.context-sources.v1",
        "state": "ready" if sources else "empty",
        "sources": sources,
        "truncated": truncated,
    }
