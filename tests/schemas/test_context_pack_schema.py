from __future__ import annotations

import pytest

from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.domain.validation import validate_payload
from agent_commons.errors import ValidationError

PACK_ID = "context_pack." + "0" * 25 + "1"
EVENT_ID = "evt." + "0" * 25 + "1"
ARTIFACT_ID = "artifact." + "0" * 25 + "1"


def _payload() -> dict[str, object]:
    return {
        "context_pack_id": PACK_ID,
        "summary": "Bounded baseline",
        "facts": [
            {
                "statement": "The artifact is the exact reviewed revision.",
                "source_refs": [
                    {
                        "ref": {"kind": "artifact", "id": ARTIFACT_ID},
                        "revision": EVENT_ID,
                    }
                ],
            }
        ],
        "decision_refs": [],
        "open_questions": [],
    }


def test_packaged_context_pack_schema_and_domain_validator_agree() -> None:
    registry = SchemaRegistry()
    payload = _payload()

    assert "commons.payload.context_pack.v1" in registry.schema_names
    registry.validate("commons.payload.context_pack.v1", payload)
    validate_payload("context_pack.created", payload)


def test_context_pack_schema_rejects_unknown_and_oversized_nested_fields() -> None:
    registry = SchemaRegistry()
    payload = _payload()
    payload["transcript"] = "must never be canonical"
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate("commons.payload.context_pack.v1", payload)

    payload = _payload()
    facts = payload["facts"]
    assert isinstance(facts, list)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact["statement"] = "x" * 1025
    with pytest.raises(ValidationError, match="too long"):
        registry.validate("commons.payload.context_pack.v1", payload)

    payload = _payload()
    payload.pop("summary")
    with pytest.raises(ValidationError, match="'summary' is a required property"):
        registry.validate("commons.payload.context_pack.v1", payload)


def test_context_pack_domain_rejects_provider_and_reasoning_fields() -> None:
    payload = _payload()
    payload["provider_argv"] = ["claude"]
    with pytest.raises(ValidationError, match="unsupported or missing"):
        validate_payload("context_pack.created", payload)
