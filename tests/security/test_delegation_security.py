from __future__ import annotations

import pytest

from agent_commons.errors import SecurityPolicyError
from agent_commons.security import SecurityPolicy


def test_delegation_metadata_is_safe_but_embedded_credentials_are_redacted() -> None:
    policy = SecurityPolicy(detect_free_text_pii=False)
    safe = {
        "event_type": "delegation.requested",
        "payload": {
            "target_profile": "claude-independent-reviewer",
            "purpose": "independent_review",
            "limits": {
                "max_depth": 1,
                "wall_time_seconds": 600,
                "max_attempts": 1,
                "max_concurrency": 1,
                "budget": {"unit": "tokens", "limit": 8000},
            },
        },
    }
    policy.assert_safe(safe, context="delegation metadata")

    secret = "sk-proj-" + "X" * 24
    unsafe = {
        "event_type": "delegation.input_needed",
        "payload": {"summary": f"provider_api_key={secret}"},
    }
    with pytest.raises(SecurityPolicyError) as caught:
        policy.assert_safe(unsafe, context="delegation metadata")

    assert secret not in str(caught.value)
    assert "secret" in str(caught.value)
