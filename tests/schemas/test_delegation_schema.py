from __future__ import annotations

from copy import deepcopy

import pytest

from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.domain.validation import validate_payload
from agent_commons.errors import ValidationError

DELEGATION_ID = "delegation.00000000000000000000000001"
TASK_ID = "task.00000000000000000000000001"
REVISION = "evt.00000000000000000000000001"
SESSION_ID = "session." + "a" * 32


def _request() -> dict:
    return {
        "delegation_id": DELEGATION_ID,
        "target_ref": {"kind": "task", "id": TASK_ID},
        "target_revision": REVISION,
        "target_profile": "claude-independent-reviewer",
        "purpose": "independent_review",
        "parent_session_id": SESSION_ID,
        "root_delegation_id": DELEGATION_ID,
        "depth": 0,
        "limits": {
            "max_depth": 1,
            "wall_time_seconds": 600,
            "max_attempts": 2,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 8000},
        },
    }


def test_delegation_payload_schema_is_closed_and_allowlisted() -> None:
    registry = SchemaRegistry()
    payload = _request()

    registry.validate("commons.payload.delegation.v1", payload)
    validate_payload("delegation.requested", payload)

    unknown_profile = {**payload, "target_profile": "shell-anything"}
    with pytest.raises(ValidationError, match="not one of|target_profile"):
        registry.validate("commons.payload.delegation.v1", unknown_profile)
    with pytest.raises(ValidationError, match="target_profile"):
        validate_payload("delegation.requested", unknown_profile)

    mismatched_purpose = {**payload, "target_profile": "codex-builder"}
    with pytest.raises(ValidationError, match="independent-reviewer profile"):
        validate_payload("delegation.requested", mismatched_purpose)

    extra = {**payload, "command": "claude --dangerously-skip-permissions"}
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate("commons.payload.delegation.v1", extra)


def test_delegation_limits_are_exact_positive_and_bounded() -> None:
    payload = _request()
    missing_budget = deepcopy(payload)
    del missing_budget["limits"]["budget"]
    with pytest.raises(ValidationError, match="limits must contain exactly"):
        validate_payload("delegation.requested", missing_budget)

    boolean_attempts = deepcopy(payload)
    boolean_attempts["limits"]["max_attempts"] = True
    with pytest.raises(ValidationError, match="must be an integer"):
        validate_payload("delegation.requested", boolean_attempts)

    excessive_depth = deepcopy(payload)
    excessive_depth["limits"]["max_depth"] = 9
    with pytest.raises(ValidationError, match="between 0 and 8"):
        validate_payload("delegation.requested", excessive_depth)


def test_success_requires_at_least_one_typed_result_reference() -> None:
    registry = SchemaRegistry()
    payload = {
        "delegation_id": DELEGATION_ID,
        "expected_revision": REVISION,
        "summary": "Completed.",
        "result_refs": [],
    }
    with pytest.raises(ValidationError, match="should be non-empty"):
        registry.validate("commons.payload.delegation.v1", payload)

    malformed = {**payload, "result_refs": [{"kind": "task", "id": TASK_ID, "x": 1}]}
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate("commons.payload.delegation.v1", malformed)
