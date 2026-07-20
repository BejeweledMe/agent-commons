from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.runtime import ProcessResult, RunOutcome, RunReason, default_profile_registry
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


def _workspace(
    tmp_path: Path, *, parent_ttl_seconds: int = 8 * 60 * 60
) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="runtime-orchestration")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="runtime-parent-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
        ttl_seconds=parent_ttl_seconds,
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Review bounded runtime orchestration",
        description="Exercise one exact target without provider content persistence.",
        acceptance_criteria=("independent review is canonical",),
        priority="high",
        idempotency_key="runtime-target-task",
    )
    return manager, task


def _delegation(
    manager: CommonsManager,
    task: dict[str, Any],
    *,
    max_attempts: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    review = manager.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect exact target",),
        independent=True,
        idempotency_key=f"runtime-review-{max_attempts}",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": max_attempts,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        idempotency_key=f"runtime-delegation-{max_attempts}",
    )
    return review, delegation


class FakeRunner:
    def __init__(
        self,
        *,
        outcome: RunOutcome = RunOutcome.SUCCEEDED,
        reason: RunReason = RunReason.COMPLETED,
        after_start: Callable[[str], None] | None = None,
        crash_after_start: bool = False,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.after_start = after_start
        self.crash_after_start = crash_after_start
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation
        self.calls += 1
        if self.reason is not RunReason.START_FAILED:
            values["on_started"](7000 + self.calls)
            if self.crash_after_start:
                raise RuntimeError("simulated broker crash")
            if self.after_start is not None:
                self.after_start(values["child_session_id"])
        return ProcessResult(
            outcome=self.outcome,
            reason=self.reason,
            exit_code=0 if self.outcome is RunOutcome.SUCCEEDED else 1,
            pid=None if self.reason is RunReason.START_FAILED else 7000 + self.calls,
            duration_seconds=0.25,
            stdout=b"provider content must remain ephemeral",
            stderr=b"",
            stdout_bytes_seen=38,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def test_child_review_and_delegation_result_are_canonical_but_output_is_not(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    review, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            manager.repo_root,
            session_id=child_session_id,
            state_root=manager.paths.state_root,
        )
        completed = child.complete_review(
            review["entity_ref"]["id"],
            review["revision"],
            target_revision=task["revision"],
            verdict="approved",
            summary="The exact target satisfies the requested criterion.",
            idempotency_key="runtime-child-review-complete",
        )
        current = child.get_delegation(delegation_id)
        child.succeed_delegation(
            delegation_id,
            current["revision"],
            summary="Independent expert review recorded.",
            result_refs=({"kind": "review", "id": review["entity_ref"]["id"]},),
            idempotency_key="runtime-child-delegation-succeed",
        )
        assert completed["revision"]

    runner = FakeRunner(after_start=complete_as_child)
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )
    result = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-success",
    )

    assert result["delegation"]["state"] == "succeeded"
    assert result["delegation"]["result_refs"] == [review["entity_ref"]]
    assert manager.list_reviews(state="approved")[0]["id"] == review["entity_ref"]["id"]
    assert "provider content" not in json.dumps(result)
    operational = "".join(
        path.read_text()
        for path in (manager.paths.state_root / "runtime").rglob("*.json")
        if path.is_file()
    )
    assert "provider content" not in operational
    child_session = result["attempt"]["correlation"]["child_session_id"]
    assert len(result["attempt"]["correlation"]["trace_id"]) == 32
    assert manager.show_session(child_session)["status"] == "closed"


def test_prestart_failure_can_retry_only_until_attempt_limit(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task, max_attempts=2)
    delegation_id = delegation["entity_ref"]["id"]
    runner = FakeRunner(outcome=RunOutcome.FAILED, reason=RunReason.START_FAILED)
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    first = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert first["delegation"]["state"] == "requested"
    assert first["attempt"]["number"] == 1

    replay = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert replay["reused"] is True
    assert runner.calls == 1

    exhausted = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
        retry=True,
    )
    assert exhausted["attempt"]["number"] == 2
    assert exhausted["delegation"]["state"] == "failed"
    assert exhausted["delegation"]["reason_code"] == "launch_failed"

    terminal_replay = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="runtime-launch-retry",
    )
    assert terminal_replay["reused"] is True
    assert terminal_replay["delegation"]["state"] == "failed"
    with pytest.raises(LifecycleConflictError, match="requested state"):
        service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="runtime-launch-retry",
            retry=True,
        )


def test_reconcile_maps_ambiguous_running_attempt_to_canonical_needs_operator(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]
    runner = FakeRunner(crash_after_start=True)
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    with pytest.raises(RuntimeError, match="simulated broker crash"):
        service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="runtime-launch-crash",
        )
    assert manager.get_delegation(delegation_id)["state"] == "active"

    reconciled = service.reconcile()
    assert reconciled[0]["attempt"]["state"] == "needs_operator"
    assert reconciled[0]["delegation"]["state"] == "needs_operator"
    assert reconciled[0]["delegation"]["reason_code"] == "orphaned"


def test_parent_session_ttl_must_cover_provider_and_finalization(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path, parent_ttl_seconds=60)
    _, delegation = _delegation(manager, task)
    runner = FakeRunner()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=default_profile_registry(
            claude_executable="/bin/echo", mcp_executable="/bin/echo"
        ),
    )

    with pytest.raises(LifecycleConflictError, match="TTL must cover"):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="runtime-parent-ttl-too-short",
        )

    assert runner.calls == 0
    assert service.list_attempts() == []
