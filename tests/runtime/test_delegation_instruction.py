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
    )

    # This is provider-visible behavior. Any change requires an explicit
    # compatibility decision and a behavioral canary, not a silent refactor.
    assert "first use commons_list_verifications" in instruction
    assert "inspect only their evidence refs" in instruction
    assert "use commons_list_tasks once to locate the exact target id" in instruction
    assert "commons_read_artifact exactly once for each necessary artifact_ref" in instruction
    assert "do not call commons_show_artifact first" in instruction
    assert "Do not inventory unrelated workspace files" in instruction
    assert "Begin\nthe required terminal sequence no later than 40 seconds" in instruction
    assert "mandatory soft deadline" in instruction
    assert "commons_finalize_review" in instruction
    assert "commons_succeed_delegation" not in instruction
    assert "Supply only verdict and summary" in instruction
    assert "satisfy every evidence\nprecondition" in instruction
    assert "attempt finalization first" in instruction
    assert "server derives the operation identity" in instruction
    assert "If the call succeeds, stop immediately" in instruction
    assert hashlib.sha256(instruction.encode()).hexdigest() == (
        "a537244dcdd118cb9e9ada2a3eba78276443d4872236d2b1b2eb75531df7adb0"
    )


def test_verification_instruction_requires_exact_verification_terminal_protocol() -> None:
    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.verification",
            target_kind="task",
            target_id="task.verification",
            target_revision="evt.verification",
            purpose="verification",
            target_profile="claude-independent-reviewer",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
    )

    record = instruction.index("commons_record_verification")
    refresh = instruction.index("commons_show_delegation", record)
    succeed = instruction.index("commons_succeed_delegation", refresh)
    assert record < refresh < succeed
    assert "verification:<id>" in instruction
    assert "commons_show_review" not in instruction
    assert "prose-only answer or successful process exit" in instruction
    assert "typed needs-operator or input-needed outcome" in instruction


def test_implementation_instruction_gives_one_exact_terminal_sequence() -> None:
    instruction = compose_delegation_instruction(
        DelegationInstructionInput(
            delegation_id="delegation.implementation",
            target_kind="task",
            target_id="task.implementation",
            target_revision="evt.implementation",
            purpose="implementation",
            target_profile="claude-builder",
            max_depth=0,
            wall_time_seconds=60,
            max_attempts=1,
            max_concurrency=1,
            budget_limit=1,
            budget_unit="provider_units",
        ),
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
    )

    refresh = instruction.index("first call commons_show_delegation")
    succeed = instruction.index("commons_succeed_delegation", refresh)
    assert refresh < succeed
    assert 'delegation_id="delegation.implementation"' in instruction
    assert 'result_refs=["task:task.implementation"]' in instruction
    assert "immediately preceding read" in instruction
    assert "Do not try a stale revision or incomplete argument set" in instruction
