"""What of a canonical event is searchable, decided by allowlist.

A denylist here would be wrong twice over.  A payload field added later would be
indexed by accident, and the thing being protected -- prompts, transcripts, tool
arguments, provider output -- is exactly the material somebody would add without
thinking about the index.  So this is a positive list: a field is searchable
because it appears below, and a new field is invisible to search until someone
adds it here on purpose.

Everything listed is already canonical, bounded, and scanned by ``SecurityPolicy``
before it was ever written.  Nothing here reads operational state, telemetry, or
provider output, none of which is in the ledger to begin with.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Free-text payload fields worth matching on.  Names people would search for,
#: and the sentences that explain a decision after everyone has forgotten it.
SEARCHABLE_TEXT_FIELDS = (
    "title",
    "description",
    "subject",
    "desired_outcome",
    "summary",
    "body",
    "proposal",
    "rationale",
    "reason",
    "resolution",
    "claim",
    "note",
    "name",
    "scope",
    "verdict",
    "severity",
    "priority",
    "purpose",
    "target_profile",
    "profile_id",
    "state",
    "classification",
    "origin",
    "retired_by",
)

#: List-of-string payload fields.  Acceptance criteria and review criteria are
#: where the actual requirement usually lives.
SEARCHABLE_LIST_FIELDS = (
    "acceptance_criteria",
    "criteria",
    "alternatives",
    "dissent",
    "next_actions",
    "blockers",
    "risks",
    "open_questions",
    "completed",
    "active",
    "skills",
    "tool_allowlist",
)

_MAX_FIELD_CHARS = 4_000
_MAX_TOTAL_CHARS = 32_000


def _clean(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    return text[:_MAX_FIELD_CHARS]


def searchable_text(event: Mapping[str, Any]) -> str:
    """The indexable text of one event: allowlisted payload fields plus its type.

    Bounded, because an unbounded document would let one oversized event
    dominate the index and its ranking.
    """

    payload = event.get("payload")
    parts: list[str] = [str(event.get("event_type", ""))]
    if isinstance(payload, Mapping):
        for field in SEARCHABLE_TEXT_FIELDS:
            value = payload.get(field)
            if isinstance(value, str):
                cleaned = _clean(value)
                if cleaned:
                    parts.append(cleaned)
        for field in SEARCHABLE_LIST_FIELDS:
            values = payload.get(field)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, str):
                        cleaned = _clean(item)
                        if cleaned:
                            parts.append(cleaned)
        # A role's own settings read like prose to somebody searching the index.
        grants = payload.get("grants")
        if isinstance(grants, Mapping):
            parts.extend(f"{name}:{level}" for name, level in sorted(grants.items()))
    actor = event.get("actor")
    if isinstance(actor, Mapping):
        for field in ("role_id", "principal_id", "client"):
            value = actor.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)[:_MAX_TOTAL_CHARS]


def subject_of(event: Mapping[str, Any]) -> tuple[str, str]:
    """The entity an event is about, for filtering and for showing a result."""

    refs = event.get("subject_refs")
    if isinstance(refs, list) and refs:
        first = refs[0]
        if isinstance(first, Mapping):
            return str(first.get("kind", "")), str(first.get("id", ""))
    return "", ""
