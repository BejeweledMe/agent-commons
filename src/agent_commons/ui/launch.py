"""Provider-launch coordination for the local UI.

The coordinator is an in-memory composition seam: it keeps the existing panel
request and response shapes while moving background-run ownership out of the
``UIContext`` compatibility facade.  It never defines a new persisted event or
provider protocol; delegation creation and runtime execution remain the
existing manager and runtime-service calls.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict, cast

from agent_commons.domain.envelopes import DelegationBudget, DelegationLimits
from agent_commons.errors import CommonsError, ConfigurationError, ValidationError
from agent_commons.services.manager import CommonsManager

if TYPE_CHECKING:
    from agent_commons.services.delegation_runtime import DelegationRuntimeService
    from agent_commons.ui.context import UIContext

_LOG = logging.getLogger("agent_commons.ui")

#: The one wording for "this panel cannot launch yet", shared by the direct-call
#: refusal here and the typed ``launch_not_configured`` HTTP refusal in
#: `ui.server`, so the two can never drift into two explanations of one state.
LAUNCH_NOT_CONFIGURED = (
    "no runtime environment is configured for this panel: launching a provider "
    "needs an operator session and an operator profile config"
)


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """The complete in-memory input of the existing launch call."""

    agent_id: str
    task_id: str
    wall_time_seconds: int | None = None
    idempotency_key: str | None = None
    background: bool = True


class LaunchResult(TypedDict):
    """The existing `/api/delegations` success body, in wire-field order."""

    delegation_id: str
    target_profile: str
    purpose: str
    launched: bool


class UILaunchCoordinator:
    """Create and run one bounded UI delegation through the configured runtime."""

    #: A UI-launched run is one bounded leaf with one provider attempt.
    _DEFAULT_RUN_LIMITS: Final = DelegationLimits(
        max_depth=0,
        wall_time_seconds=600,
        max_attempts=1,
        max_concurrency=1,
        budget=DelegationBudget(unit="provider_units", limit=1),
    )

    def __init__(self, context: UIContext) -> None:
        self._context = context
        self._launch_threads: list[threading.Thread] = []

    def await_launches(self, timeout: float = 30.0) -> None:
        """Join any background launch threads. For tests and clean shutdown."""

        for thread in list(self._launch_threads):
            thread.join(timeout=timeout)

    def _runtime_service(self, manager: CommonsManager) -> DelegationRuntimeService:
        """Build the same runtime service the CLI uses, under the writer session."""

        if self._context._runtime_factory is not None:
            return cast("DelegationRuntimeService", self._context._runtime_factory(manager))
        from agent_commons.services.delegation_runtime import (
            DelegationRuntimeService,
            load_runtime_configuration,
        )

        config = load_runtime_configuration(
            self._context._profile_config,
            workspace_root=self._context.repo,
        )
        runner = None
        if config.demo:
            # An internal/dev config can bind the runner seam without the panel
            # advertising demo capability.
            from agent_commons.runtime.demo import DemoRunner

            runner = DemoRunner(manager.paths.state_root)
        return DelegationRuntimeService(
            manager,
            profiles=config.profiles,
            operator_limits=config.limits,
            catalog=config.catalog,
            runner=runner,
        )

    def run(self, request: LaunchRequest) -> LaunchResult:
        """Record a bounded delegation for a role, then run it through the broker."""

        context = self._context
        if not context.launch_enabled:
            raise ConfigurationError(LAUNCH_NOT_CONFIGURED)
        limits = self._DEFAULT_RUN_LIMITS.to_payload()
        if request.wall_time_seconds:
            limits["wall_time_seconds"] = int(request.wall_time_seconds)
        if context._session_owner is not None:
            context._session_owner.ensure_run_ttl(int(limits["wall_time_seconds"]))
        writer = context.writer()
        role = writer.get_agent(request.agent_id)
        if role.get("state") != "active":
            raise ValidationError("only an active role can be given work")
        if role.get("template"):
            raise ValidationError("a role preset is a template and is never employed")
        task = writer.snapshot().tasks.get(request.task_id)
        if task is None:
            raise ValidationError(f"no such task: {request.task_id}")
        profile_id = str(role["profile_id"])
        purpose = "implementation"
        if profile_id.endswith("independent-reviewer"):
            purpose = "independent_review"
        delegation = writer.create_delegation(
            target_ref={"kind": "task", "id": request.task_id},
            target_revision=str(task.get("effective_revision") or task["revision"]),
            target_profile=profile_id,
            purpose=purpose,
            limits=limits,
            on_behalf_of_agent_id=request.agent_id,
            idempotency_key=request.idempotency_key,
        )
        delegation_id = str(delegation["entity_ref"]["id"])
        launch_key = f"ui-launch-{delegation_id}"

        def launch() -> None:
            try:
                self._runtime_service(context.writer()).run(
                    delegation_id, delegation["revision"], idempotency_key=launch_key
                )
            except Exception as exc:  # a launch failure is reported, never silent
                _LOG.warning("UI launch of %s failed: %s", delegation_id, exc)
                try:
                    context.writer().mark_delegation_needs_operator(
                        delegation_id,
                        str(delegation["revision"]),
                        reason_code="launch_failed",
                        summary=f"the panel could not start this run: {exc}",
                        idempotency_key=f"{launch_key}:launch-failed",
                    )
                    context.invalidate()
                except CommonsError as write_failure:  # pragma: no cover - defence
                    _LOG.warning(
                        "UI launch failure of %s could not be recorded: %s",
                        delegation_id,
                        write_failure,
                    )
            finally:
                context.invalidate()

        if request.background:
            thread = threading.Thread(target=launch, name=launch_key, daemon=True)
            self._launch_threads.append(thread)
            thread.start()
        else:
            launch()
        context.invalidate()
        return {
            "delegation_id": delegation_id,
            "target_profile": profile_id,
            "purpose": purpose,
            "launched": True,
        }
