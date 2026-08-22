"""Characterization tests for provider-only delegation instruction composition."""

from __future__ import annotations

import hashlib

from agent_commons.runtime import BuiltinProfileId
from agent_commons.services.delegation_instruction import (
    DelegationInstructionInput,
    compose_delegation_instruction,
)


def test_independent_review_instruction_is_byte_stable() -> None:
    """A structural seam must not silently change the instruction workers receive."""

    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.characterization",
            target_kind="task",
            target_id="task.characterization",
            target_revision="evt.characterization",
            purpose="independent_review",
            target_profile="claude-independent-reviewer",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=50_000,
            budget_unit="micro_usd",
        ),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        skills=(("house-checklist", "Check error handling before style."),),
    )

    # Captured from DelegationRuntimeService._instruction before A4.5 moved its
    # text. A change here is a behavior change, not part of the structural move.
    assert hashlib.sha256(instruction.encode()).hexdigest() == (
        "a28d615b3b74b7ac659187bfbaf75408ce46df17efd320777db15ded0bbfeed9"
    )
