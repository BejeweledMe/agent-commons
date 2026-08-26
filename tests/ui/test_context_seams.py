"""Structural guard for the A4 UI composition split."""

from agent_commons.ui.actions import LAUNCH_NOT_CONFIGURED as ACTIONS_LAUNCH_NOT_CONFIGURED
from agent_commons.ui.actions import UIActions
from agent_commons.ui.context import UIContext
from agent_commons.ui.launch import (
    LAUNCH_NOT_CONFIGURED,
    LaunchRequest,
    LaunchResult,
    UILaunchCoordinator,
)
from agent_commons.ui.reads import UIReads


class _LaunchCoordinatorSpy:
    request: LaunchRequest | None = None
    timeout: float | None = None

    def await_launches(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def run(self, request: LaunchRequest) -> LaunchResult:
        self.request = request
        return {
            "delegation_id": "delegation.test",
            "target_profile": "codex-builder",
            "purpose": "implementation",
            "launched": True,
        }


def test_context_keeps_read_and_write_workflows_in_dedicated_modules() -> None:
    """The compatibility facade must not regain panel workflows during new work."""

    assert issubclass(UIContext, UIReads)
    assert issubclass(UIContext, UIActions)
    assert UIContext.catalog is UIReads.catalog
    assert UIContext.attention is UIReads.attention
    assert UIContext.create_agent is UIActions.create_agent
    assert "run_role_on_task" not in UIActions.__dict__
    assert "await_launches" not in UIActions.__dict__


def test_context_delegates_launches_to_the_dedicated_coordinator() -> None:
    context = object.__new__(UIContext)
    coordinator = _LaunchCoordinatorSpy()
    context._launch_coordinator = coordinator  # type: ignore[assignment]

    result = context.run_role_on_task(
        agent_id="agent.test",
        task_id="task.test",
        wall_time_seconds=42,
        idempotency_key="launch-test",
        background=False,
    )
    context.await_launches(timeout=3.0)

    assert result == {
        "delegation_id": "delegation.test",
        "target_profile": "codex-builder",
        "purpose": "implementation",
        "launched": True,
    }
    assert coordinator.request == LaunchRequest(
        agent_id="agent.test",
        task_id="task.test",
        wall_time_seconds=42,
        idempotency_key="launch-test",
        background=False,
    )
    assert coordinator.timeout == 3.0
    assert ACTIONS_LAUNCH_NOT_CONFIGURED is LAUNCH_NOT_CONFIGURED
    assert "_launch_threads" not in UIContext.__dict__
    assert UIContext.await_launches is not UILaunchCoordinator.await_launches
    assert UIContext.run_role_on_task is not UILaunchCoordinator.run
