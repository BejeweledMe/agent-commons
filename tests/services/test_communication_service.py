from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import LifecycleConflictError, SecurityPolicyError
from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CommunicationAuthorizationError,
    CorrelationIds,
    OperationState,
    RuntimePolicy,
    checkout_fingerprint,
)
from agent_commons.services import CommonsManager
from agent_commons.services.communication import CommunicationRuntimeService


def _workspace(
    tmp_path: Path,
    *,
    create_attempt: bool = True,
) -> tuple[
    CommonsManager,
    CommonsManager,
    dict[str, Any],
    dict[str, Any],
    AttemptStore,
]:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo.mkdir(parents=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="communication-service")

    parent = CommonsManager(repo, state_root=state_root)
    parent_session = parent.start_session(
        stable_instance_id="communication-parent-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="lead",
    )
    parent.session_id = parent_session["session_id"]
    child = CommonsManager(repo, state_root=state_root)
    child_session = child.start_session(
        stable_instance_id="communication-child-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    child.session_id = child_session["session_id"]
    task = parent.create_task(
        title="Implement task-scoped input",
        description="Exercise bounded parent and child communication.",
        acceptance_criteria=("A parent can answer without storing content canonically.",),
        priority="high",
        idempotency_key="communication-service-task",
    )
    requested = parent.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 900,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 10_000},
        },
        idempotency_key="communication-service-delegation",
    )
    delegation_id = requested["entity_ref"]["id"]
    parent.start_delegation(
        delegation_id,
        requested["revision"],
        child_session_id=child_session["session_id"],
        idempotency_key="communication-service-delegation-start",
    )

    attempts = AttemptStore(state_root)
    if create_attempt:
        parent_policy = RuntimePolicy(
            remaining_depth=1,
            max_fanout=1,
            max_attempts=1,
            max_concurrency=1,
            timeout_seconds=900,
        )
        child_policy = parent_policy.derive_child()
        attempt = attempts.reserve(
            AttemptSpec(
                idempotency_key="communication-service-attempt",
                profile_id=BuiltinProfileId.CODEX_BUILDER,
                provider=BuiltinProfileId.CODEX_BUILDER.provider,
                correlation=CorrelationIds(
                    delegation_id=delegation_id,
                    target_kind="task",
                    target_id=task["entity_ref"]["id"],
                    target_revision=task["revision"],
                    parent_session_id=parent_session["session_id"],
                    child_session_id=child_session["session_id"],
                ),
                parent_policy=parent_policy,
                child_policy=child_policy,
                checkout_fingerprint=checkout_fingerprint(repo),
            ),
            parent_policy=parent_policy,
        ).attempt
        attempts.transition(
            attempt.attempt_id,
            AttemptState.LAUNCHING,
            reason="process_starting",
        )
        attempts.transition(
            attempt.attempt_id,
            AttemptState.RUNNING,
            reason="process_started",
            pid=4242,
        )
    return parent, child, task, requested, attempts


def test_blocking_question_parent_reply_child_ack_and_resume(tmp_path: Path) -> None:
    parent, child, _, requested, attempts = _workspace(tmp_path)
    delegation_id = requested["entity_ref"]["id"]
    child_service = CommunicationRuntimeService(child, attempts=attempts)
    parent_service = CommunicationRuntimeService(parent, attempts=attempts)

    opened = child_service.request_input(
        delegation_id,
        idempotency_key="choose-branch",
        question="Which bounded implementation branch should I take?",
        why_needed="Both branches satisfy the current acceptance criteria.",
        safe_context={"choices": ["minimal", "extended"]},
        desired_outcome="Select one listed branch.",
        blocking=True,
        deadline_seconds=300,
    )
    operation_id = opened["operation"]["operation_id"]
    assert opened["operation"]["state"] == OperationState.OPEN.value
    assert opened["delegation"]["state"] == "input_needed"
    assert opened["delegation"]["summary"] == (
        "Delegated work is waiting for bounded parent input."
    )

    retried = child_service.request_input(
        delegation_id,
        idempotency_key="choose-branch",
        question="Which bounded implementation branch should I take?",
        why_needed="Both branches satisfy the current acceptance criteria.",
        safe_context={"choices": ["minimal", "extended"]},
        desired_outcome="Select one listed branch.",
        blocking=True,
        deadline_seconds=300,
    )
    assert retried["operation"]["operation_id"] == operation_id

    parent_inbox = parent_service.inbox()
    assert [item["operation_id"] for item in parent_inbox] == [operation_id]
    replied = parent_service.reply_to_input(
        operation_id,
        idempotency_key="choose-minimal",
        answer={"selection": "minimal", "canonical": True},
    )
    assert replied["operation"]["state"] == OperationState.REPLIED.value
    assert replied["delegation"]["state"] == "active"

    checked = child_service.check_input(operation_id)
    assert checked["reply"] == {"selection": "minimal", "canonical": True}
    acked = child_service.acknowledge(operation_id, idempotency_key="answer-received")
    assert acked["state"] == OperationState.ACKED.value

    canonical_text = "".join(
        json.dumps(record.event, sort_keys=True) for record in parent.events.iter_events()
    )
    assert "Which bounded implementation branch" not in canonical_text
    assert '"selection": "minimal"' not in canonical_text
    assert '"canonical": true' not in canonical_text
    assert "Bounded parent input was supplied through the private runtime channel." in (
        canonical_text
    )


def test_progress_and_blocker_are_child_to_parent_only(tmp_path: Path) -> None:
    parent, child, _, requested, attempts = _workspace(tmp_path)
    delegation_id = requested["entity_ref"]["id"]
    child_service = CommunicationRuntimeService(child, attempts=attempts)
    parent_service = CommunicationRuntimeService(parent, attempts=attempts)

    progress = child_service.share_progress(
        delegation_id,
        idempotency_key="halfway",
        summary="Focused tests are complete.",
        completed_units=1,
        total_units=2,
    )["operation"]
    blocker = child_service.report_blocker(
        delegation_id,
        idempotency_key="missing-fixture",
        summary="One bounded fixture is unavailable.",
        impact="The final negative test cannot run.",
        safe_next_action="Provide the documented synthetic fixture.",
    )["operation"]

    assert {item["kind"] for item in parent_service.inbox()} == {"progress", "blocker"}
    assert (
        parent_service.acknowledge(progress["operation_id"], idempotency_key="seen-progress")[
            "state"
        ]
        == OperationState.ACKED.value
    )
    assert blocker["state"] == OperationState.OPEN.value

    with pytest.raises(LifecycleConflictError, match="bound delegation child"):
        parent_service.share_progress(
            delegation_id,
            idempotency_key="parent-cannot-send",
            summary="This role is wrong.",
        )


def test_parent_guidance_and_checkpoint_are_child_acknowledged(tmp_path: Path) -> None:
    parent, child, _, requested, attempts = _workspace(tmp_path)
    delegation_id = requested["entity_ref"]["id"]
    parent_service = CommunicationRuntimeService(parent, attempts=attempts)
    child_service = CommunicationRuntimeService(child, attempts=attempts)

    guidance = parent_service.send_guidance(
        delegation_id,
        idempotency_key="prefer-small-patch",
        instruction="Keep the control surface limited to the current task.",
        rationale="The manual fallback must remain obvious.",
        expected_effect="No unrelated files are changed.",
    )["operation"]
    checkpoint = parent_service.request_checkpoint(
        delegation_id,
        idempotency_key="pause-before-review",
        reason="A review boundary is ready.",
        safe_boundary="After focused tests and before provider launch.",
        expected_ack="Confirm the child is at that boundary.",
    )["operation"]

    assert {item["kind"] for item in child_service.inbox()} == {"guidance", "checkpoint"}
    assert (
        child_service.acknowledge_control(
            guidance["operation_id"], idempotency_key="guidance-seen"
        )["state"]
        == OperationState.ACKED.value
    )
    assert (
        child_service.acknowledge_control(
            checkpoint["operation_id"], idempotency_key="checkpoint-seen"
        )["state"]
        == OperationState.ACKED.value
    )

    with pytest.raises(LifecycleConflictError, match="canonical delegation parent"):
        child_service.send_guidance(
            delegation_id,
            idempotency_key="wrong-role",
            instruction="This must fail.",
            rationale="The child cannot direct itself through the parent channel.",
            expected_effect="No mutation.",
        )
    with pytest.raises(CommunicationAuthorizationError):
        parent_service.acknowledge_control(
            guidance["operation_id"], idempotency_key="parent-cannot-ack"
        )

    canonical_text = "".join(
        json.dumps(record.event, sort_keys=True) for record in parent.events.iter_events()
    )
    assert "Keep the control surface" not in canonical_text
    assert "pause-before-review" not in canonical_text


def test_foreign_session_and_secret_question_fail_without_echo(tmp_path: Path) -> None:
    parent, child, _, requested, attempts = _workspace(tmp_path)
    delegation_id = requested["entity_ref"]["id"]
    child_service = CommunicationRuntimeService(child, attempts=attempts)
    opened = child_service.request_input(
        delegation_id,
        idempotency_key="safe-question",
        question="Which public option should I use?",
        why_needed="The task permits two options.",
        safe_context={"options": ["a", "b"]},
        desired_outcome="Choose a or b.",
        blocking=False,
    )

    stranger = CommonsManager(parent.repo_root, state_root=parent.paths.state_root)
    stranger_session = stranger.start_session(
        stable_instance_id="communication-stranger-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="observer",
    )
    stranger.session_id = stranger_session["session_id"]
    stranger_service = CommunicationRuntimeService(stranger, attempts=attempts)
    with pytest.raises(CommunicationAuthorizationError, match="unavailable"):
        stranger_service.check_input(opened["operation"]["operation_id"])

    secret = "password=do-not-echo-this-value"
    with pytest.raises(SecurityPolicyError) as rejected:
        child_service.request_input(
            delegation_id,
            idempotency_key="unsafe-question",
            question=secret,
            why_needed="A credential was mistakenly supplied.",
            safe_context={},
            desired_outcome="Reject it.",
            blocking=False,
        )
    assert secret not in str(rejected.value)


def test_missing_live_attempt_and_stale_target_fail_closed(tmp_path: Path) -> None:
    parent, child, task, requested, attempts = _workspace(tmp_path, create_attempt=False)
    service = CommunicationRuntimeService(child, attempts=attempts)
    delegation_id = requested["entity_ref"]["id"]
    with pytest.raises(LifecycleConflictError, match="no durable operational attempt"):
        service.share_progress(
            delegation_id,
            idempotency_key="no-attempt",
            summary="This cannot be attributed safely.",
        )

    # A fresh fixture gets a valid attempt, then changes the exact target revision.
    parent, child, task, requested, attempts = _workspace(
        tmp_path / "stale",
        create_attempt=True,
    )
    parent.start_task(
        task["entity_ref"]["id"],
        task["revision"],
        idempotency_key="make-communication-target-stale",
    )
    stale_service = CommunicationRuntimeService(child, attempts=attempts)
    with pytest.raises(LifecycleConflictError, match="target revision is stale"):
        stale_service.share_progress(
            requested["entity_ref"]["id"],
            idempotency_key="stale-target",
            summary="This target moved after delegation.",
        )
