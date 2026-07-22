from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError, SecurityPolicyError
from agent_commons.runtime import (
    AttemptStore,
    BrokerRequest,
    BuiltinProfileId,
    CorrelationIds,
    JsonlTelemetrySink,
    LocalBroker,
    OpenTelemetrySink,
    ProcessResult,
    RunOutcome,
    RunReason,
    RuntimePolicy,
    TelemetryEvent,
    TelemetryKind,
    default_profile_registry,
)
from agent_commons.runtime import telemetry as telemetry_module


def correlation() -> CorrelationIds:
    return CorrelationIds(
        delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        target_kind="review",
        target_id="review.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        target_revision="evt.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        parent_session_id="session.parent000000000000000000000001",
        child_session_id="session.child00000000000000000000000001",
        trace_id="0123456789abcdef0123456789abcdef",
    )


def event() -> TelemetryEvent:
    return TelemetryEvent.create(
        kind=TelemetryKind.PROCESS_FINISHED,
        correlation=correlation(),
        request_id="request.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        attempt_id="attempt.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        provider="codex",
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        state="succeeded",
        reason="completed",
        duration_milliseconds=42,
    )


def test_jsonl_telemetry_is_private_metadata_only_and_rejects_secrets(tmp_path: Path) -> None:
    sink = JsonlTelemetrySink(tmp_path / "state")
    sink.emit(event())
    body = json.loads(sink.path.read_text())
    serialized = json.dumps(body)
    assert stat.S_IMODE(sink.path.stat().st_mode) == 0o600
    assert body["duration_milliseconds"] == 42
    for forbidden in ("prompt", "instruction", "reasoning", "raw_output", "command", "env"):
        assert forbidden not in serialized
    assert sink.capture_content is False

    unsafe = TelemetryEvent.create(
        kind=TelemetryKind.PROCESS_FINISHED,
        correlation=correlation(),
        request_id="sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA",
        attempt_id="attempt.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        provider="codex",
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        state="failed",
        reason="start_failed",
    )
    with pytest.raises(SecurityPolicyError):
        sink.emit(unsafe)


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str) -> FakeSpan:
        self.names.append(name)
        span = FakeSpan()
        self.spans.append(span)
        return span


def test_optional_otel_adapter_exports_only_allowlisted_metadata(monkeypatch) -> None:
    tracer = FakeTracer()
    sink = OpenTelemetrySink(tracer=tracer)
    sink.emit(event())
    assert tracer.names == ["agent_commons.runtime.process_finished"]
    assert tracer.spans[0].ended
    assert tracer.spans[0].attributes["agent_commons.capture_content"] is False
    assert all(
        "prompt" not in key and "output.content" not in key for key in tracer.spans[0].attributes
    )

    unsafe = TelemetryEvent.create(
        kind=TelemetryKind.PROCESS_FINISHED,
        correlation=correlation(),
        request_id="sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA",
        attempt_id="attempt.01KXZZZZZZZZZZZZZZZZZZZZZZ",
        provider="codex",
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        state="failed",
        reason="start_failed",
    )
    with pytest.raises(SecurityPolicyError):
        sink.emit(unsafe)

    def missing(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(telemetry_module.importlib, "import_module", missing)
    with pytest.raises(ConfigurationError, match="opentelemetry-api is not installed"):
        OpenTelemetrySink()


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, invocation, **kwargs) -> ProcessResult:
        self.calls.append({"invocation": invocation, **kwargs})
        kwargs["on_started"](777)
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=777,
            duration_seconds=0.125,
            stdout=b'{"result":"ok"}\n',
            stderr=b"",
            stdout_bytes_seen=16,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


class CollectingSink:
    capture_content = False

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class CollectingLifecycleHook:
    def __init__(self) -> None:
        self.started = []

    def process_started(self, attempt) -> None:
        self.started.append(attempt)


def test_broker_runs_once_with_distinct_child_and_keeps_content_ephemeral(tmp_path: Path) -> None:
    parent = RuntimePolicy(remaining_depth=2, max_attempts=2)
    child = parent.derive_child()
    runner = FakeRunner()
    telemetry = CollectingSink()
    lifecycle = CollectingLifecycleHook()
    state_root = tmp_path / "state"
    broker = LocalBroker(
        profiles=default_profile_registry(codex_executable="/bin/echo", trusted_workspace=True),
        attempts=AttemptStore(state_root),
        runner=runner,  # type: ignore[arg-type]
        telemetry=telemetry,
        lifecycle_hook=lifecycle,
    )
    request = BrokerRequest(
        idempotency_key="broker-e2e-request",
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        instruction="Sensitive work instruction that must remain ephemeral",
        cwd=tmp_path,
        state_root=state_root,
        correlation=correlation(),
        parent_policy=parent,
        child_policy=child,
    )
    first = broker.run(request)
    second = broker.run(request)

    assert first.attempt.state.value == "succeeded"
    assert second.reused is True
    assert len(runner.calls) == 1
    assert len(lifecycle.started) == 1
    assert lifecycle.started[0].state.value == "running"
    assert lifecycle.started[0].pid == 777
    assert runner.calls[0]["child_session_id"] == correlation().child_session_id
    assert [item.kind for item in telemetry.events] == [
        TelemetryKind.REQUEST_RESERVED,
        TelemetryKind.PROCESS_STARTING,
        TelemetryKind.PROCESS_STARTED,
        TelemetryKind.PROCESS_FINISHED,
    ]
    operational_text = "".join(
        path.read_text() for path in (state_root / "runtime").rglob("*.json") if path.is_file()
    )
    assert "Sensitive work instruction" not in operational_text
    assert '"result":"ok"' not in operational_text
