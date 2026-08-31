from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_commons.domain.context_pack import (
    ContextPackDraft,
    ContextPackRecord,
)
from agent_commons.errors import ValidationError

PACK_ID = "context_pack." + "0" * 25 + "1"
EVENT_ID = "evt." + "0" * 25 + "1"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"


def _payload() -> dict[str, object]:
    return {
        "summary": "A bounded baseline.",
        "facts": [
            {
                "statement": "The exact artifact passed verification.",
                "source_refs": [
                    {
                        "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                        "revision": EVENT_ID,
                    }
                ],
            }
        ],
        "decision_refs": [],
        "open_questions": ["Which cohort runs first?"],
    }


def test_context_pack_draft_and_record_deeply_own_nested_input() -> None:
    payload = _payload()
    draft = ContextPackDraft.from_payload(payload)
    record = ContextPackRecord.create(
        context_pack_id=PACK_ID,
        revision=EVENT_ID,
        source_event_id=EVENT_ID,
        draft=draft,
        recorded_at="2026-08-30T00:00:00Z",
        author_session_ids=("session.builder",),
    )

    facts = payload["facts"]
    assert isinstance(facts, list)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact["statement"] = "mutated"
    source_refs = fact["source_refs"]
    assert isinstance(source_refs, list)
    source_ref = source_refs[0]
    assert isinstance(source_ref, dict)
    source_ref["revision"] = "evt." + "0" * 25 + "2"

    assert record.draft.facts[0].statement == "The exact artifact passed verification."
    assert record.draft.facts[0].source_refs[0].revision == EVENT_ID
    exposed = record.to_dict()
    exposed_facts = exposed["facts"]
    assert isinstance(exposed_facts, list)
    exposed_facts[0]["statement"] = "also mutated"
    assert record.to_dict()["facts"][0]["statement"] == ("The exact artifact passed verification.")
    with pytest.raises(FrozenInstanceError):
        record.revision = "evt." + "0" * 25 + "3"  # type: ignore[misc]


def test_context_pack_domain_rejects_unbound_or_wrong_kind_sources() -> None:
    payload = _payload()
    payload["facts"] = [{"statement": "Unsupported", "source_refs": []}]
    with pytest.raises(ValidationError, match="must contain 1 to 8"):
        ContextPackDraft.from_payload(payload)

    payload = _payload()
    facts = payload["facts"]
    assert isinstance(facts, list)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact["source_refs"] = [
        {
            "ref": {"kind": "delegation", "id": "delegation." + "0" * 25 + "1"},
            "revision": EVENT_ID,
        }
    ]
    with pytest.raises(ValidationError, match="unsupported source kind"):
        ContextPackDraft.from_payload(payload)


def test_context_pack_domain_enforces_section_bounds() -> None:
    payload = _payload()
    payload["summary"] = "x" * 4097
    with pytest.raises(ValidationError, match="at most 4096"):
        ContextPackDraft.from_payload(payload)

    payload = _payload()
    payload["open_questions"] = ["question"] * 33
    with pytest.raises(ValidationError, match="at most 32"):
        ContextPackDraft.from_payload(payload)

    payload = _payload()
    payload["summary"] = "invalid-surrogate-\ud800"
    with pytest.raises(ValidationError, match="valid UTF-8"):
        ContextPackDraft.from_payload(payload)


def test_context_pack_owns_mutating_container_subclasses_before_validation() -> None:
    class MutatingList(list[object]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            original = list.copy(self)
            for index, item in enumerate(original):
                if index == 0:
                    self.clear()
                yield item

    payload = _payload()
    facts = payload["facts"]
    assert isinstance(facts, list)
    hostile_facts = MutatingList(facts)
    payload["facts"] = hostile_facts

    draft = ContextPackDraft.from_payload(payload)

    assert hostile_facts == []
    assert draft.facts[0].statement == "The exact artifact passed verification."
    assert type(draft.to_payload()["facts"]) is list
    assert type(draft.to_payload()["facts"][0]) is dict


def test_context_pack_stops_unbounded_hostile_list_without_echoing_values() -> None:
    class UnboundedList(list[object]):
        yielded = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            while True:
                self.yielded += 1
                yield {"statement": "never echo secret-ownership-value", "source_refs": []}

    payload = _payload()
    hostile = UnboundedList()
    payload["facts"] = hostile

    with pytest.raises(ValidationError) as refused:
        ContextPackDraft.from_payload(payload)

    assert hostile.yielded == 66
    assert "safe ownership limit" in str(refused.value)
    assert "secret-ownership-value" not in str(refused.value)


def test_context_pack_sanitizes_hostile_mapping_exceptions() -> None:
    class HostileMapping(dict[str, object]):
        def items(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("secret-from-hostile-container")

    with pytest.raises(ValidationError) as refused:
        ContextPackDraft.from_payload(HostileMapping(_payload()))

    assert str(refused.value) == "context pack mapping could not be safely copied"
    assert refused.value.__cause__ is None
