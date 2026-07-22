from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import (
    ConfigurationError,
    IdempotencyConflictError,
    LifecycleConflictError,
)
from agent_commons.runtime import (
    OperatorLimits,
    PolicyViolationError,
    ProcessResult,
    ProfileRegistry,
    RunOutcome,
    RunReason,
    default_profile_registry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


def _workspace(tmp_path: Path) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="runtime-security")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="runtime-security-parent-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="Review the bounded runtime",
        description="Exercise security properties at the canonical/runtime boundary.",
        acceptance_criteria=("the exact review is independently completed",),
        priority="high",
        idempotency_key="runtime-security-target",
    )
    return manager, task


def _delegation(
    manager: CommonsManager,
    task: dict[str, Any],
    *,
    max_attempts: int = 1,
    budget_unit: str = "micro_usd",
    budget_limit: int = 50_000,
) -> dict[str, Any]:
    return manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": max_attempts,
            "max_concurrency": 1,
            "budget": {"unit": budget_unit, "limit": budget_limit},
        },
        idempotency_key=(
            f"runtime-security-delegation-{max_attempts}-{budget_unit}-{budget_limit}"
        ),
    )


def _profiles(*, claude_executable: str = "/bin/echo") -> ProfileRegistry:
    return default_profile_registry(
        claude_executable=claude_executable,
        mcp_executable="/bin/echo",
    )


class FakeRunner:
    def __init__(
        self,
        *,
        outcome: RunOutcome = RunOutcome.FAILED,
        reason: RunReason = RunReason.NONZERO_EXIT,
        after_start: Callable[[str], None] | None = None,
        raise_after_start: bool = False,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.after_start = after_start
        self.raise_after_start = raise_after_start
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation
        self.calls += 1
        pid = None
        if self.reason is not RunReason.START_FAILED:
            pid = 9_000 + self.calls
            values["on_started"](pid)
            if self.raise_after_start:
                raise RuntimeError("provider transport crashed after process start")
            if self.after_start is not None:
                self.after_start(values["child_session_id"])
        return ProcessResult(
            outcome=self.outcome,
            reason=self.reason,
            exit_code=0 if self.outcome is RunOutcome.SUCCEEDED else 1,
            pid=pid,
            duration_seconds=0.1,
            stdout=b"ephemeral provider output",
            stderr=b"",
            stdout_bytes_seen=25,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


class BlockingRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._counter_lock = threading.Lock()

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        with self._counter_lock:
            self.calls += 1
            call_number = self.calls
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("test did not release the fake provider")
        pid = 9_500 + call_number
        values["on_started"](pid)
        return ProcessResult(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            exit_code=1,
            pid=pid,
            duration_seconds=0.1,
            stdout=b"",
            stderr=b"",
            stdout_bytes_seen=0,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def _service(
    manager: CommonsManager,
    runner: FakeRunner,
    *,
    claude_executable: str = "/bin/echo",
    operator_limits: OperatorLimits | None = None,
) -> DelegationRuntimeService:
    return DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        profiles=_profiles(claude_executable=claude_executable),
        operator_limits=operator_limits,
    )


def test_foreign_session_cannot_launch_another_parents_delegation(tmp_path: Path) -> None:
    parent, task = _workspace(tmp_path)
    delegation = _delegation(parent, task)
    foreign_session = parent.start_session(
        stable_instance_id="runtime-security-foreign-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    foreign = CommonsManager(
        parent.repo_root,
        session_id=foreign_session["session_id"],
        state_root=parent.paths.state_root,
    )
    runner = FakeRunner()
    service = _service(foreign, runner)

    with pytest.raises(LifecycleConflictError, match="requester"):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="foreign-launch-key",
        )

    assert runner.calls == 0
    assert service.list_attempts() == []
    assert parent.get_delegation(delegation["entity_ref"]["id"])["state"] == "requested"


def test_first_launch_key_owns_every_retry(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task, max_attempts=2)
    runner = FakeRunner(outcome=RunOutcome.FAILED, reason=RunReason.START_FAILED)
    service = _service(manager, runner)
    delegation_id = delegation["entity_ref"]["id"]

    first = service.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="bound-launch-key",
    )
    assert first["delegation"]["state"] == "requested"

    with pytest.raises(IdempotencyConflictError, match="different runtime launch key"):
        service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="alternate-launch-key",
            retry=True,
        )

    assert runner.calls == 1
    assert [attempt["number"] for attempt in service.list_attempts()] == [1]
    assert manager.get_delegation(delegation_id)["state"] == "requested"


def test_terminal_attempt_is_reconciled_after_canonical_finalize_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    runner = FakeRunner()
    crashed_service = _service(manager, runner)
    delegation_id = delegation["entity_ref"]["id"]

    original_fail = manager.fail_delegation

    def crash_before_canonical_finalize(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("canonical finalize crash")

    monkeypatch.setattr(
        manager,
        "fail_delegation",
        crash_before_canonical_finalize,
    )
    with pytest.raises(RuntimeError, match="canonical finalize crash"):
        crashed_service.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="terminal-gap-launch",
        )

    attempt = crashed_service.list_attempts()[0]
    assert attempt["state"] == "failed"
    assert manager.get_delegation(delegation_id)["state"] == "active"
    child_session_id = attempt["correlation"]["child_session_id"]
    assert manager.show_session(child_session_id)["status"] == "active"  # type: ignore[index]

    monkeypatch.setattr(manager, "fail_delegation", original_fail)
    recovered = _service(manager, FakeRunner()).reconcile()

    assert recovered[0]["attempt"]["state"] == "failed"
    assert recovered[0]["delegation"]["state"] == "failed"
    assert recovered[0]["delegation"]["reason_code"] == "runtime_error"


def test_concurrent_same_key_launches_one_provider_without_corrupting_state(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]
    runner = BlockingRunner()
    services = (_service(manager, runner), _service(manager, runner))
    start = threading.Barrier(3)
    results: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    def launch(service: DelegationRuntimeService) -> None:
        start.wait()
        try:
            results.append(
                service.run(
                    delegation_id,
                    delegation["revision"],
                    idempotency_key="concurrent-bound-key",
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=launch, args=(service,)) for service in services]
    for thread in threads:
        thread.start()
    start.wait()
    assert runner.entered.wait(timeout=5)
    runner.release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert runner.calls == 1
    assert len(results) == 2
    assert sorted(result["reused"] for result in results) == [False, True]
    assert {result["delegation"]["state"] for result in results} == {"failed"}
    assert len(services[0].list_attempts()) == 1
    assert manager.get_delegation(delegation_id)["state"] == "failed"
    assert manager.snapshot().warnings == []


@pytest.mark.parametrize(
    ("budget_unit", "budget_limit", "max_attempts", "message"),
    [
        ("tokens", 10_000, 1, "supports only"),
        ("provider_units", 1, 2, "must cover every permitted"),
    ],
)
def test_unsupported_budget_fails_before_session_reservation_or_spawn(
    tmp_path: Path,
    budget_unit: str,
    budget_limit: int,
    max_attempts: int,
    message: str,
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(
        manager,
        task,
        max_attempts=max_attempts,
        budget_unit=budget_unit,
        budget_limit=budget_limit,
    )
    runner = FakeRunner()
    service = _service(manager, runner)
    observer = CommonsManager(manager.repo_root, state_root=manager.paths.state_root)
    sessions_before = len(observer.show_session())  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match=message):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key=f"unsupported-budget-{budget_unit}",
        )

    assert runner.calls == 0
    assert service.list_attempts() == []
    assert len(observer.show_session()) == sessions_before  # type: ignore[arg-type]


def test_admission_rejection_closes_the_unbound_child_session(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    limits = OperatorLimits(parent_provider_units=1)
    runner = FakeRunner()
    service = _service(manager, runner, operator_limits=limits)
    first = _delegation(
        manager,
        task,
        budget_unit="provider_units",
        budget_limit=1,
    )
    service.run(
        first["entity_ref"]["id"],
        first["revision"],
        idempotency_key="consume-parent-provider-unit",
    )

    second_task = manager.create_task(
        title="Second bounded provider unit",
        description="Prove rejected admission releases its unbound child identity.",
        acceptance_criteria=("no session leak",),
        idempotency_key="second-provider-unit-target",
    )
    second = manager.create_delegation(
        target_ref=second_task["entity_ref"],
        target_revision=second_task["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="second-provider-unit-delegation",
    )
    observer = CommonsManager(manager.repo_root, state_root=manager.paths.state_root)
    sessions_before = len(observer.show_session())  # type: ignore[arg-type]

    with pytest.raises(PolicyViolationError, match="provider_units budget"):
        service.run(
            second["entity_ref"]["id"],
            second["revision"],
            idempotency_key="rejected-parent-provider-unit",
        )

    assert runner.calls == 1
    assert manager.get_delegation(second["entity_ref"]["id"])["state"] == "requested"
    assert len(observer.show_session()) == sessions_before  # type: ignore[arg-type]


def test_reconcile_surfaces_unavailable_foreign_owner_without_mutation(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task, max_attempts=2)
    service = _service(manager, FakeRunner(reason=RunReason.START_FAILED))
    service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="foreign-owner-prestart-attempt",
    )

    foreign_session = manager.start_session(
        stable_instance_id="runtime-reconcile-foreign-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator-recovery",
    )
    foreign = CommonsManager(
        manager.repo_root,
        session_id=foreign_session["session_id"],
        state_root=manager.paths.state_root,
    )
    foreign_service = _service(foreign, FakeRunner())
    assert foreign_service.reconcile() == []

    parent = manager.sessions.require_active(manager.session_id)
    manager.sessions.close(parent.session_id, nonce=parent.nonce)
    before = manager.get_delegation(delegation["entity_ref"]["id"])
    visible = foreign_service.reconcile()

    assert len(visible) == 1
    assert visible[0]["reconciled"] is False
    assert visible[0]["workflow_diagnostic_code"] == "requester_unavailable"
    assert visible[0]["safe_next_actions"]
    assert manager.get_delegation(delegation["entity_ref"]["id"]) == before


def test_profile_drift_cannot_change_a_bound_retry_plan(tmp_path: Path) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task, max_attempts=2)
    delegation_id = delegation["entity_ref"]["id"]
    runner = FakeRunner(outcome=RunOutcome.FAILED, reason=RunReason.START_FAILED)
    original = _service(manager, runner, claude_executable="/bin/echo")
    original.run(
        delegation_id,
        delegation["revision"],
        idempotency_key="profile-bound-launch",
    )

    changed = _service(manager, runner, claude_executable="/usr/bin/false")
    with pytest.raises(IdempotencyConflictError, match="different request"):
        changed.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="profile-bound-launch",
            retry=True,
        )

    assert runner.calls == 1
    assert [attempt["number"] for attempt in changed.list_attempts()] == [1]
    assert manager.get_delegation(delegation_id)["state"] == "requested"


def test_post_start_transport_exception_does_not_falsely_close_child(
    tmp_path: Path,
) -> None:
    manager, task = _workspace(tmp_path)
    delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]
    crashed = _service(manager, FakeRunner(raise_after_start=True))

    with pytest.raises(RuntimeError, match="transport crashed"):
        crashed.run(
            delegation_id,
            delegation["revision"],
            idempotency_key="post-start-crash",
        )

    attempt = crashed.list_attempts()[0]
    child_session_id = attempt["correlation"]["child_session_id"]
    assert attempt["state"] == "running"
    assert manager.get_delegation(delegation_id)["state"] == "active"
    assert manager.show_session(child_session_id)["status"] == "active"  # type: ignore[index]

    reconciled = _service(manager, FakeRunner()).reconcile()

    assert reconciled[0]["attempt"]["state"] == "needs_operator"
    assert reconciled[0]["delegation"]["state"] == "needs_operator"
    assert manager.show_session(child_session_id)["status"] == "active"  # type: ignore[index]
