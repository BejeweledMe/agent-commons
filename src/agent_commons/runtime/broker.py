"""Provider-neutral synchronous broker orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from agent_commons.errors import ConfigurationError

from .attempts import (
    Attempt,
    AttemptSpec,
    AttemptState,
    AttemptStore,
    checkout_fingerprint,
)
from .launch import ValidatedLaunchPlan, invocation_fingerprint
from .model import BuiltinProfileId, CorrelationIds, ProfileRegistry
from .policy import RuntimePolicy
from .subprocess_runner import CancellationToken, ProcessResult, SubprocessRunner
from .telemetry import (
    NoopTelemetrySink,
    TelemetryEvent,
    TelemetryKind,
    TelemetrySink,
)


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    """One launch request; ``instruction`` is never written by the runtime."""

    idempotency_key: str
    profile_id: BuiltinProfileId
    instruction: str
    cwd: Path
    state_root: Path
    correlation: CorrelationIds
    parent_policy: RuntimePolicy
    child_policy: RuntimePolicy
    purpose: str = "implementation"
    launch_key_sha256: str = "0" * 64
    retry: bool = False
    #: Tool names the standing role this run acts for is allowed to use.  Empty
    #: means the profile's own fixed set; a non-empty value can only narrow it.
    role_tools: tuple[str, ...] = ()
    #: Standing permissions of that role.  A staff-changing tool reaches argv
    #: only when its grant is above `deny`, so a run with no role gets none.
    role_grants: Mapping[str, str] = field(default_factory=dict)
    #: Exact one-build production plan.  Legacy direct broker callers may omit
    #: it and retain the existing profile-build compatibility path.
    validated_launch_plan: ValidatedLaunchPlan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", BuiltinProfileId(self.profile_id))
        object.__setattr__(self, "role_tools", tuple(self.role_tools))
        object.__setattr__(self, "role_grants", MappingProxyType(dict(self.role_grants)))
        object.__setattr__(self, "cwd", Path(self.cwd).expanduser().resolve())
        object.__setattr__(self, "state_root", Path(self.state_root).expanduser().resolve())
        if self.purpose not in {"implementation", "independent_review", "verification"}:
            raise ConfigurationError("broker request purpose is unsupported")
        if self.validated_launch_plan is not None:
            validated = self.validated_launch_plan
            if (
                validated.plan.profile_id is not self.profile_id
                or validated.plan.purpose.value != self.purpose
                or validated.invocation.profile_id is not self.profile_id
            ):
                raise ConfigurationError("broker request does not match its validated launch plan")
        self.child_policy.assert_reduction_of(self.parent_policy)
        if len(self.launch_key_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.launch_key_sha256
        ):
            raise ConfigurationError("broker launch key digest is invalid")


@dataclass(frozen=True, slots=True)
class BrokerResult:
    attempt: Attempt
    process: ProcessResult | None
    reused: bool
    telemetry_failures: int = 0


class BrokerLifecycleHook(Protocol):
    """Canonical integration seam called after durable process identification."""

    def process_started(self, attempt: Attempt) -> None: ...


class NoopBrokerLifecycleHook:
    def process_started(self, attempt: Attempt) -> None:
        del attempt


class LocalBroker:
    """Reserve, launch, observe, and terminally classify one provider process."""

    def __init__(
        self,
        *,
        profiles: ProfileRegistry,
        attempts: AttemptStore,
        runner: SubprocessRunner,
        telemetry: TelemetrySink | None = None,
        lifecycle_hook: BrokerLifecycleHook | None = None,
    ) -> None:
        self.profiles = profiles
        self.attempts = attempts
        self.runner = runner
        self.telemetry = telemetry or NoopTelemetrySink()
        self.lifecycle_hook = lifecycle_hook or NoopBrokerLifecycleHook()

    def _emit(self, event: TelemetryEvent) -> int:
        try:
            self.telemetry.emit(event)
        except Exception:
            # Telemetry is explicitly non-authoritative and must not own execution.
            return 1
        return 0

    @staticmethod
    def _event(
        kind: TelemetryKind,
        attempt: Attempt,
        *,
        duration_milliseconds: int | None = None,
        queue_depth: int | None = None,
        queued_milliseconds: int | None = None,
    ) -> TelemetryEvent:
        return TelemetryEvent.create(
            kind=kind,
            correlation=attempt.correlation,
            request_id=attempt.request_id,
            attempt_id=attempt.attempt_id,
            provider=attempt.provider,
            profile_id=attempt.profile_id,
            state=attempt.state.value,
            reason=attempt.reason,
            diagnostic_code=attempt.diagnostic_code,
            pid=attempt.pid,
            exit_code=attempt.exit_code,
            duration_milliseconds=duration_milliseconds,
            queue_depth=queue_depth,
            queued_milliseconds=queued_milliseconds,
            stdout_bytes_seen=attempt.stdout_bytes_seen,
            stderr_bytes_seen=attempt.stderr_bytes_seen,
            output_truncated=attempt.output_truncated,
        )

    def reconcile(self) -> tuple[Attempt, ...]:
        attempts = self.attempts.reconcile()
        for attempt in attempts:
            self._emit(self._event(TelemetryKind.ATTEMPT_RECONCILED, attempt))
        return attempts

    def run(
        self,
        request: BrokerRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> BrokerResult:
        profile = self.profiles.get(request.profile_id)
        if request.child_policy.max_budget_microusd is not None and not profile.supports_budget:
            raise ConfigurationError(
                f"profile {request.profile_id.value} cannot enforce a monetary budget"
            )
        if request.validated_launch_plan is not None:
            # Production passes the exact immutable object built once by the
            # adapter.  Do not reconstruct or normalize any invocation byte.
            invocation = request.validated_launch_plan.invocation
            launch_plan_sha256 = request.validated_launch_plan.invocation_fingerprint
        else:
            # Compatibility for direct broker tests/callers.  Service-owned
            # production launches always use ``validated_launch_plan``.
            invocation = profile.build_invocation(
                request.instruction,
                workspace_root=request.cwd,
                state_root=request.state_root,
                delegation_id=request.correlation.delegation_id,
                child_session_id=request.correlation.child_session_id,
                max_budget_microusd=request.child_policy.max_budget_microusd,
                worker_purpose=request.purpose,
                role_tools=request.role_tools,
                role_grants=request.role_grants,
            )
            launch_plan_sha256 = invocation_fingerprint(invocation)
        spec = AttemptSpec(
            idempotency_key=request.idempotency_key,
            profile_id=request.profile_id,
            provider=profile.provider,
            correlation=request.correlation,
            parent_policy=request.parent_policy,
            child_policy=request.child_policy,
            checkout_fingerprint=checkout_fingerprint(request.cwd),
            launch_plan_sha256=launch_plan_sha256,
            launch_key_sha256=request.launch_key_sha256,
        )
        reservation = self.attempts.reserve(
            spec,
            parent_policy=request.parent_policy,
            retry=request.retry,
        )
        if not reservation.created:
            return BrokerResult(attempt=reservation.attempt, process=None, reused=True)

        telemetry_failures = 0
        if reservation.queue_depth > 0:
            telemetry_failures += self._emit(
                self._event(
                    TelemetryKind.REQUEST_QUEUED,
                    reservation.attempt,
                    queue_depth=reservation.queue_depth,
                    queued_milliseconds=reservation.queued_milliseconds,
                )
            )
        telemetry_failures += self._emit(
            self._event(
                TelemetryKind.REQUEST_RESERVED,
                reservation.attempt,
                queue_depth=reservation.queue_depth,
                queued_milliseconds=reservation.queued_milliseconds,
            )
        )
        launching = self.attempts.transition(
            reservation.attempt.attempt_id,
            AttemptState.LAUNCHING,
            reason="process_starting",
        )
        telemetry_failures += self._emit(self._event(TelemetryKind.PROCESS_STARTING, launching))

        def on_started(pid: int) -> None:
            nonlocal telemetry_failures
            running = self.attempts.transition(
                launching.attempt_id,
                AttemptState.RUNNING,
                reason="process_started",
                pid=pid,
            )
            # Canonical delegation.started may now bind this durably known process.
            # Hook failure propagates to the runner, which terminates the process
            # group and returns a normalized control_error result.
            self.lifecycle_hook.process_started(running)
            telemetry_failures += self._emit(self._event(TelemetryKind.PROCESS_STARTED, running))

        process = self.runner.run(
            invocation,
            cwd=request.cwd,
            child_session_id=request.correlation.child_session_id,
            delegation_id=request.correlation.delegation_id,
            state_root=request.state_root,
            timeout_seconds=request.child_policy.timeout_seconds,
            max_output_bytes=request.child_policy.max_output_bytes,
            cancellation=cancellation,
            on_started=on_started,
        )
        finished = self.attempts.finish(launching.attempt_id, process)
        telemetry_failures += self._emit(
            self._event(
                TelemetryKind.PROCESS_FINISHED,
                finished,
                duration_milliseconds=round(process.duration_seconds * 1_000),
            )
        )
        return BrokerResult(
            attempt=finished,
            process=process,
            reused=False,
            telemetry_failures=telemetry_failures,
        )
