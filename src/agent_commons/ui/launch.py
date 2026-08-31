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
from agent_commons.runtime import (
    ContextBindingMode,
    ContextBindingRequest,
)
from agent_commons.runtime.model import BuiltinProfileId
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.provider_auth import (
    UIProviderAuthCoordinator,
    provider_auth_launch_refusal,
)
from agent_commons.ui.read_dtos import ProviderAuthPayload

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

_GENERIC_LAUNCH_FAILURE_SUMMARY = (
    "the panel could not start this run because runtime configuration or process "
    "startup failed; provider details were suppressed"
)
_KNOWN_PROFILE_FAILURE_SUMMARIES = {
    f"runner profile is not configured: {profile_id.value}": (
        f"the panel could not start this run: runner profile is not configured: {profile_id.value}"
    )
    for profile_id in BuiltinProfileId
}


def _safe_launch_failure_summary(exc: Exception) -> str:
    """Map an exception to fixed text without returning its arbitrary detail."""

    try:
        key = str(exc)
    except Exception:  # pragma: no cover - hostile exception defensive boundary
        return _GENERIC_LAUNCH_FAILURE_SUMMARY
    return _KNOWN_PROFILE_FAILURE_SUMMARIES.get(key, _GENERIC_LAUNCH_FAILURE_SUMMARY)


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """The complete in-memory input of the existing launch call."""

    agent_id: str
    task_id: str
    wall_time_seconds: int | None = None
    idempotency_key: str | None = None
    background: bool = True
    context: ContextBindingRequest = ContextBindingRequest.fresh()


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
        # A test/embedder runtime factory is a construction-time dependency.
        # Capturing it prevents later mutable context state from swapping the
        # auth probe independently of the coordinator that owns its flights.
        auth_runtime_factory = context._runtime_factory
        if auth_runtime_factory is not None:

            def build_auth_runtime() -> DelegationRuntimeService:
                return cast(
                    "DelegationRuntimeService",
                    auth_runtime_factory(self._context.manager()),
                )

        else:

            def build_auth_runtime() -> DelegationRuntimeService:
                return self._runtime_service(self._context.manager())

        self._provider_auth = UIProviderAuthCoordinator(build_auth_runtime)

    def await_launches(self, timeout: float = 30.0) -> None:
        """Join any background launch threads. For tests and clean shutdown."""

        for thread in list(self._launch_threads):
            thread.join(timeout=timeout)

    def provider_auth_status(self, profile_id: str) -> ProviderAuthPayload:
        return self._provider_auth.status(profile_id)

    def check_provider_auth(self, profile_id: str) -> ProviderAuthPayload:
        return self._provider_auth.check_again(profile_id)

    def start_provider_login(self, profile_id: str) -> ProviderAuthPayload:
        return self._provider_auth.start_login(profile_id)

    def cancel_provider_login(self, profile_id: str) -> ProviderAuthPayload:
        return self._provider_auth.cancel_login(profile_id)

    def shutdown(self) -> None:
        """Stop provider-auth processes owned by this panel."""

        self._provider_auth.shutdown()

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
        try:
            role_context_mode = ContextBindingMode(str(role.get("context_mode", "fresh")))
        except ValueError as exc:
            raise ValidationError("the selected role has an invalid context mode") from exc
        if role_context_mode is ContextBindingMode.ACCUMULATED:
            if request.context.mode is not ContextBindingMode.ACCUMULATED:
                raise ValidationError(
                    "an accumulated role requires one exact published Context Pack revision"
                )
        elif request.context.mode is not ContextBindingMode.FRESH:
            raise ValidationError("a fresh role cannot receive a Context Pack")
        task = writer.snapshot().tasks.get(request.task_id)
        if task is None:
            raise ValidationError(f"no such task: {request.task_id}")
        profile_id = str(role["profile_id"])
        purpose = "implementation"
        if profile_id.endswith("independent-reviewer"):
            purpose = "independent_review"
        # Probe the exact configured profile before creating a canonical
        # delegation.  Authentication refusal is therefore pure: no child,
        # attempt, receipt, or requested delegation exists yet.
        auth_status = self.provider_auth_status(profile_id)
        if auth_status["blocks_launch"]:
            raise provider_auth_launch_refusal(auth_status)
        runtime: DelegationRuntimeService | None = None
        if request.context.mode is ContextBindingMode.ACCUMULATED:
            runtime = self._runtime_service(writer)
            # Refuse a stale, missing, unauthorized, or oversized pack while
            # the HTTP action is still synchronous and before a canonical
            # delegation, child session, or attempt exists.  The runtime
            # repeats this exact validation under the per-delegation lock.
            runtime.validate_context_selection(request.context)
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
                launch_runtime = runtime or self._runtime_service(context.writer())
                launch_runtime.run(
                    delegation_id,
                    delegation["revision"],
                    idempotency_key=launch_key,
                    context=request.context,
                )
            except Exception as exc:  # a launch failure is reported, never silent
                # The runtime repeats the auth gate immediately before opening
                # the child.  If credentials changed after the UI precheck,
                # leave this exact delegation requested and retryable; calling
                # it terminal `launch_failed` would make the visible recovery
                # action a lie.
                if getattr(exc, "code", None) in {
                    "provider_auth_required",
                    "provider_auth_unknown",
                    "credential_store_unavailable",
                }:
                    _LOG.warning(
                        "UI launch of %s stopped at the provider-auth gate; "
                        "provider details were suppressed",
                        delegation_id,
                    )
                    context.invalidate()
                    return
                safe_summary = _safe_launch_failure_summary(exc)
                _LOG.warning(
                    "UI launch of %s failed before canonical finalization; "
                    "provider details were suppressed",
                    delegation_id,
                )
                try:
                    context.writer().mark_delegation_needs_operator(
                        delegation_id,
                        str(delegation["revision"]),
                        reason_code="launch_failed",
                        summary=safe_summary,
                        idempotency_key=f"{launch_key}:launch-failed",
                    )
                    context.invalidate()
                except CommonsError:  # pragma: no cover - defence
                    _LOG.warning(
                        "UI launch failure of %s could not be recorded; details suppressed",
                        delegation_id,
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
