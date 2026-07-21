"""Metadata-only runtime telemetry with optional OpenTelemetry export."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.security import SecurityPolicy

from .attempts import _canonical_bytes, _ensure_private_directory, _exclusive_lock
from .diagnostics import DiagnosticCode
from .model import BuiltinProfileId, CorrelationIds, Provider, _safe_identifier


class TelemetryKind(StrEnum):
    REQUEST_RESERVED = "request_reserved"
    PROCESS_STARTING = "process_starting"
    PROCESS_STARTED = "process_started"
    PROCESS_FINISHED = "process_finished"
    ATTEMPT_RECONCILED = "attempt_reconciled"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """A deliberately closed metadata schema with no content-bearing fields."""

    kind: TelemetryKind
    recorded_at: str
    correlation: CorrelationIds
    request_id: str
    attempt_id: str
    provider: Provider
    profile_id: BuiltinProfileId
    state: str
    reason: str
    diagnostic_code: DiagnosticCode = DiagnosticCode.NONE
    pid: int | None = None
    exit_code: int | None = None
    duration_milliseconds: int | None = None
    stdout_bytes_seen: int = 0
    stderr_bytes_seen: int = 0
    output_truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", TelemetryKind(self.kind))
        object.__setattr__(self, "provider", Provider(self.provider))
        object.__setattr__(self, "profile_id", BuiltinProfileId(self.profile_id))
        if self.profile_id.provider is not self.provider:
            raise ValidationError("telemetry profile and provider do not match")
        _safe_identifier("request_id", self.request_id)
        _safe_identifier("attempt_id", self.attempt_id)
        _safe_identifier("telemetry state", self.state)
        _safe_identifier("telemetry reason", self.reason)
        object.__setattr__(self, "diagnostic_code", DiagnosticCode(self.diagnostic_code))
        try:
            datetime.fromisoformat(self.recorded_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValidationError("telemetry recorded_at is not an ISO timestamp") from exc
        if self.pid is not None and (
            isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid < 1
        ):
            raise ValidationError("telemetry pid is invalid")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise ValidationError("telemetry exit_code is invalid")
        for name in ("stdout_bytes_seen", "stderr_bytes_seen"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"{name} cannot be negative")
        if not isinstance(self.output_truncated, bool):
            raise ValidationError("telemetry output_truncated must be a boolean")
        if self.duration_milliseconds is not None and (
            isinstance(self.duration_milliseconds, bool)
            or not isinstance(self.duration_milliseconds, int)
            or self.duration_milliseconds < 0
        ):
            raise ValidationError("telemetry duration cannot be negative")

    @classmethod
    def create(
        cls,
        *,
        kind: TelemetryKind,
        correlation: CorrelationIds,
        request_id: str,
        attempt_id: str,
        provider: Provider,
        profile_id: BuiltinProfileId,
        state: str,
        reason: str,
        clock: Callable[[], float] = time.time,
        **metrics: Any,
    ) -> TelemetryEvent:
        recorded_at = datetime.fromtimestamp(clock(), tz=UTC).isoformat().replace("+00:00", "Z")
        return cls(
            kind=kind,
            recorded_at=recorded_at,
            correlation=correlation,
            request_id=request_id,
            attempt_id=attempt_id,
            provider=provider,
            profile_id=profile_id,
            state=state,
            reason=reason,
            **metrics,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "agent_commons.runtime_telemetry.v1",
            "kind": self.kind.value,
            "recorded_at": self.recorded_at,
            "correlation": self.correlation.as_dict(),
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "provider": self.provider.value,
            "profile_id": self.profile_id.value,
            "state": self.state,
            "reason": self.reason,
            "diagnostic_code": self.diagnostic_code.value,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "duration_milliseconds": self.duration_milliseconds,
            "stdout_bytes_seen": self.stdout_bytes_seen,
            "stderr_bytes_seen": self.stderr_bytes_seen,
            "output_truncated": self.output_truncated,
        }

    def as_otel_attributes(self) -> dict[str, str | int | bool]:
        values: dict[str, str | int | bool | None] = {
            "agent_commons.delegation.id": self.correlation.delegation_id,
            "agent_commons.target.kind": self.correlation.target_kind,
            "agent_commons.target.id": self.correlation.target_id,
            "agent_commons.target.revision": self.correlation.target_revision,
            "agent_commons.session.parent_id": self.correlation.parent_session_id,
            "agent_commons.session.child_id": self.correlation.child_session_id,
            "agent_commons.trace.correlation_id": self.correlation.trace_id,
            "agent_commons.request.id": self.request_id,
            "agent_commons.attempt.id": self.attempt_id,
            "agent_commons.provider": self.provider.value,
            "agent_commons.profile.id": self.profile_id.value,
            "agent_commons.attempt.state": self.state,
            "agent_commons.attempt.reason": self.reason,
            "agent_commons.attempt.diagnostic_code": self.diagnostic_code.value,
            "agent_commons.process.pid": self.pid,
            "agent_commons.process.exit_code": self.exit_code,
            "agent_commons.duration_ms": self.duration_milliseconds,
            "agent_commons.output.stdout_bytes_seen": self.stdout_bytes_seen,
            "agent_commons.output.stderr_bytes_seen": self.stderr_bytes_seen,
            "agent_commons.output.truncated": self.output_truncated,
            "agent_commons.capture_content": False,
        }
        return {key: value for key, value in values.items() if value is not None}


class TelemetrySink(Protocol):
    capture_content: bool

    def emit(self, event: TelemetryEvent) -> None: ...


class NoopTelemetrySink:
    capture_content = False

    def emit(self, event: TelemetryEvent) -> None:
        del event


class JsonlTelemetrySink:
    """Append private, non-authoritative metadata events under operational state."""

    capture_content = False

    def __init__(
        self,
        state_root: str | Path,
        *,
        security_policy: SecurityPolicy | None = None,
    ) -> None:
        self.root = Path(state_root).expanduser().resolve() / "runtime" / "telemetry"
        self.path = self.root / "events.jsonl"
        self.lock_path = self.root / "events.lock"
        self.security_policy = security_policy or SecurityPolicy()
        _ensure_private_directory(self.root)

    def emit(self, event: TelemetryEvent) -> None:
        body = event.as_dict()
        self.security_policy.assert_safe(body, context="runtime telemetry")
        data = _canonical_bytes(body)
        with _exclusive_lock(self.lock_path):
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


class _Tracer(Protocol):
    def start_span(self, name: str): ...


class OpenTelemetrySink:
    """Lazy OTel adapter; importing Agent Commons does not require OTel."""

    capture_content = False

    def __init__(
        self,
        *,
        tracer: _Tracer | None = None,
        security_policy: SecurityPolicy | None = None,
    ) -> None:
        if tracer is None:
            try:
                trace_module = importlib.import_module("opentelemetry.trace")
            except ModuleNotFoundError as exc:
                raise ConfigurationError(
                    "OpenTelemetry telemetry was selected but opentelemetry-api is not installed"
                ) from exc
            tracer = trace_module.get_tracer("agent_commons.runtime")
        self.tracer = tracer
        self.security_policy = security_policy or SecurityPolicy()

    def emit(self, event: TelemetryEvent) -> None:
        self.security_policy.assert_safe(event.as_dict(), context="runtime OpenTelemetry")
        span = self.tracer.start_span(f"agent_commons.runtime.{event.kind.value}")
        try:
            for key, value in event.as_otel_attributes().items():
                span.set_attribute(key, value)
        finally:
            span.end()
