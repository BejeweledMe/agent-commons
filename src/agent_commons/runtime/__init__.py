"""Optional local delegation runtime.

The canonical ledger remains owned by :class:`CommonsManager`; this package
contains only operational launch state, bounded provider adapters, and
non-authoritative metadata telemetry.
"""

from .attempts import (
    ATTEMPT_SCHEMA,
    REQUEST_SCHEMA,
    Attempt,
    AttemptReason,
    AttemptReservation,
    AttemptSpec,
    AttemptState,
    AttemptStore,
    checkout_fingerprint,
)
from .broker import (
    BrokerLifecycleHook,
    BrokerRequest,
    BrokerResult,
    LocalBroker,
    NoopBrokerLifecycleHook,
)
from .model import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    CodexApprovalPolicy,
    CodexRunnerProfile,
    CodexSandbox,
    CorrelationIds,
    ProfileRegistry,
    Provider,
    RunnerInvocation,
    RunnerProfile,
    default_profile_registry,
)
from .policy import PolicyViolationError, RuntimePolicy, RuntimeUsage
from .subprocess_runner import (
    CancellationToken,
    ProcessFactory,
    ProcessGroupTerminator,
    ProcessHandle,
    ProcessResult,
    RunOutcome,
    RunReason,
    SafeEnvironment,
    SubprocessRunner,
)
from .telemetry import (
    JsonlTelemetrySink,
    NoopTelemetrySink,
    OpenTelemetrySink,
    TelemetryEvent,
    TelemetryKind,
    TelemetrySink,
)

__all__ = [
    "ATTEMPT_SCHEMA",
    "REQUEST_SCHEMA",
    "Attempt",
    "AttemptReason",
    "AttemptReservation",
    "AttemptSpec",
    "AttemptState",
    "AttemptStore",
    "BrokerLifecycleHook",
    "BrokerRequest",
    "BrokerResult",
    "BuiltinProfileId",
    "CancellationToken",
    "ClaudePermissionMode",
    "ClaudeRunnerProfile",
    "CodexApprovalPolicy",
    "CodexRunnerProfile",
    "CodexSandbox",
    "CorrelationIds",
    "JsonlTelemetrySink",
    "LocalBroker",
    "NoopBrokerLifecycleHook",
    "NoopTelemetrySink",
    "OpenTelemetrySink",
    "PolicyViolationError",
    "ProcessFactory",
    "ProcessGroupTerminator",
    "ProcessHandle",
    "ProcessResult",
    "ProfileRegistry",
    "Provider",
    "RunOutcome",
    "RunReason",
    "RunnerInvocation",
    "RunnerProfile",
    "RuntimePolicy",
    "RuntimeUsage",
    "SafeEnvironment",
    "SubprocessRunner",
    "TelemetryEvent",
    "TelemetryKind",
    "TelemetrySink",
    "checkout_fingerprint",
    "default_profile_registry",
]
