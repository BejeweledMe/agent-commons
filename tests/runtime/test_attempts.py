from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

import pytest

from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CorrelationIds,
    DiagnosticCode,
    OperatorLimits,
    PolicyViolationError,
    ProcessResult,
    Provider,
    RunOutcome,
    RunReason,
    RuntimePolicy,
    checkout_fingerprint,
    classify_process_result,
)


class Clock:
    def __init__(self) -> None:
        self.value = 1_750_000_000.0

    def __call__(self) -> float:
        self.value += 1
        return self.value


def policies() -> tuple[RuntimePolicy, RuntimePolicy]:
    parent = RuntimePolicy(
        remaining_depth=2,
        max_fanout=4,
        max_attempts=2,
        max_concurrency=4,
    )
    return parent, parent.derive_child()


def spec(
    cwd: Path,
    *,
    key: str = "delegate-task-one",
    profile_id: BuiltinProfileId = BuiltinProfileId.CODEX_BUILDER,
    revision: str = "evt.01KXZZZZZZZZZZZZZZZZZZZZZZ",
    child_session_id: str = "session.child00000000000000000000000001",
) -> AttemptSpec:
    _, child = policies()
    return AttemptSpec(
        idempotency_key=key,
        profile_id=profile_id,
        provider=profile_id.provider,
        correlation=CorrelationIds(
            delegation_id="delegation.01KXZZZZZZZZZZZZZZZZZZZZZZ",
            target_kind="review",
            target_id="review.01KXZZZZZZZZZZZZZZZZZZZZZZ",
            target_revision=revision,
            parent_session_id="session.parent000000000000000000000001",
            child_session_id=child_session_id,
        ),
        parent_policy=policies()[0],
        child_policy=child,
        checkout_fingerprint=checkout_fingerprint(cwd),
    )


def test_attempt_reservation_is_private_atomic_and_idempotent(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    store = AttemptStore(state_root, clock=Clock())
    parent, _ = policies()

    first = store.reserve(spec(tmp_path), parent_policy=parent)
    repeated = store.reserve(spec(tmp_path), parent_policy=parent)
    assert first.created is True
    assert repeated.created is False
    assert repeated.attempt == first.attempt
    assert stat.S_IMODE((state_root / "runtime").stat().st_mode) == 0o700
    request_path = next((state_root / "runtime" / "requests").glob("*.json"))
    assert stat.S_IMODE(request_path.stat().st_mode) == 0o600

    persisted = request_path.read_text()
    assert "prompt" not in persisted
    assert "instruction" not in persisted
    assert "stdout" not in persisted.replace("stdout_bytes_seen", "")

    with pytest.raises(IdempotencyConflictError):
        store.reserve(
            spec(tmp_path, revision="evt.01KXYYYYYYYYYYYYYYYYYYYYYY"),
            parent_policy=parent,
        )


def test_attempt_lifecycle_reconcile_and_retry_are_bounded(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "state", clock=Clock())
    parent, _ = policies()
    attempt = store.reserve(spec(tmp_path), parent_policy=parent).attempt
    launching = store.transition(
        attempt.attempt_id,
        AttemptState.LAUNCHING,
        reason="process_starting",
    )
    running = store.transition(
        launching.attempt_id,
        AttemptState.RUNNING,
        reason="process_started",
        pid=123,
    )
    assert running.pid == 123

    reconciled = store.reconcile()
    assert reconciled[0].state is AttemptState.NEEDS_OPERATOR
    assert reconciled[0].reason == "broker_restart_ambiguous"
    retry = store.reserve(spec(tmp_path), parent_policy=parent, retry=True)
    assert retry.created is True
    assert retry.attempt.number == 2

    failed = store.transition(
        retry.attempt.attempt_id,
        AttemptState.FAILED,
        reason="start_failed",
    )
    assert failed.state is AttemptState.FAILED
    with pytest.raises(PolicyViolationError, match="attempt"):
        store.reserve(spec(tmp_path), parent_policy=parent, retry=True)
    with pytest.raises(LifecycleConflictError, match="illegal"):
        store.transition(
            failed.attempt_id,
            AttemptState.RUNNING,
            reason="process_started",
            pid=999,
        )


def test_retry_cannot_amplify_the_parent_policy_bound_to_the_request(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "state", clock=Clock())
    parent, _ = policies()
    initial_spec = spec(tmp_path)
    attempt = store.reserve(initial_spec, parent_policy=parent).attempt
    store.transition(attempt.attempt_id, AttemptState.FAILED, reason="start_failed")

    expanded_parent = RuntimePolicy(
        remaining_depth=3,
        max_fanout=10,
        max_attempts=10,
        max_concurrency=10,
    )
    with pytest.raises(IdempotencyConflictError, match="parent policy"):
        store.reserve(initial_spec, parent_policy=expanded_parent, retry=True)


def test_only_one_writable_worker_can_be_active_per_checkout(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "state", clock=Clock())
    parent, _ = policies()
    store.reserve(spec(tmp_path, key="first-builder"), parent_policy=parent)

    with pytest.raises(PolicyViolationError, match="writable worker"):
        store.reserve(
            spec(
                tmp_path,
                key="second-builder",
                profile_id=BuiltinProfileId.CLAUDE_BUILDER,
                child_session_id="session.child00000000000000000000000002",
            ),
            parent_policy=parent,
        )

    reviewer = store.reserve(
        spec(
            tmp_path,
            key="read-only-review",
            profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
            child_session_id="session.child00000000000000000000000003",
        ),
        parent_policy=parent,
    )
    assert reviewer.created


def test_shared_operator_queue_is_bounded_and_admits_in_order(tmp_path: Path) -> None:
    limits = OperatorLimits(
        global_concurrency=1,
        queue_capacity=1,
        queue_wait_seconds=2,
        provider_concurrency={"codex": 1},
        profile_concurrency={"codex-independent-reviewer": 1},
    )
    store = AttemptStore(tmp_path / "state", operator_limits=limits)
    parent, _ = policies()
    first = store.reserve(
        spec(
            tmp_path,
            key="queued-first-reviewer",
            profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        ),
        parent_policy=parent,
    ).attempt
    result: list[object] = []

    def reserve_second() -> None:
        result.append(
            store.reserve(
                spec(
                    tmp_path,
                    key="queued-second-reviewer",
                    profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
                    child_session_id="session.child00000000000000000000000002",
                ),
                parent_policy=parent,
            )
        )

    thread = threading.Thread(target=reserve_second)
    thread.start()
    deadline = time.monotonic() + 1
    while not store.queue_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.queue_path.exists()

    with pytest.raises(PolicyViolationError, match="queue is full"):
        store.reserve(
            spec(
                tmp_path,
                key="queued-third-reviewer",
                profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
                child_session_id="session.child00000000000000000000000003",
            ),
            parent_policy=parent,
        )

    store.transition(first.attempt_id, AttemptState.CANCELLED, reason="cancelled")
    thread.join(timeout=2)
    assert not thread.is_alive()
    reservation = result[0]
    assert reservation.queue_depth == 1
    assert reservation.queued_milliseconds > 0


def test_operator_provider_units_are_aggregate_across_requests(tmp_path: Path) -> None:
    limits = OperatorLimits(parent_provider_units=1)
    store = AttemptStore(tmp_path / "state", operator_limits=limits)
    parent, _ = policies()
    first = store.reserve(spec(tmp_path, key="budget-first"), parent_policy=parent).attempt
    store.transition(first.attempt_id, AttemptState.FAILED, reason="start_failed")

    with pytest.raises(PolicyViolationError, match="provider_units budget"):
        store.reserve(
            spec(
                tmp_path,
                key="budget-second",
                child_session_id="session.child00000000000000000000000002",
            ),
            parent_policy=parent,
        )


def test_tampered_request_body_is_detected(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    store = AttemptStore(state_root, clock=Clock())
    parent, _ = policies()
    store.reserve(spec(tmp_path), parent_policy=parent)
    path = next((state_root / "runtime" / "requests").glob("*.json"))
    value = json.loads(path.read_text())
    value["spec"]["provider"] = Provider.CLAUDE.value
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(Exception, match="semantic digest"):
        store.list_attempts()


def test_failure_diagnostic_is_closed_and_raw_provider_content_is_not_persisted(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    store = AttemptStore(state_root, clock=Clock())
    parent, _ = policies()
    attempt = store.reserve(spec(tmp_path), parent_policy=parent).attempt
    store.transition(attempt.attempt_id, AttemptState.LAUNCHING, reason="process_starting")
    store.transition(
        attempt.attempt_id,
        AttemptState.RUNNING,
        reason="process_started",
        pid=123,
    )

    secret = "sk-ant-api03-never-persist-this"
    finished = store.finish(
        attempt.attempt_id,
        ProcessResult(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            exit_code=1,
            pid=123,
            duration_seconds=0.1,
            stdout=b"",
            stderr=(
                f"MCP handshake failed; credential={secret}; internal path=/private/tmp/x"
            ).encode(),
            stdout_bytes_seen=0,
            stderr_bytes_seen=96,
            output_truncated=False,
        ),
    )

    assert finished.diagnostic_code is DiagnosticCode.MCP_HANDSHAKE_FAILED
    persisted = next((state_root / "runtime" / "requests").glob("*.json")).read_text()
    assert secret not in persisted
    assert "/private/tmp/x" not in persisted
    assert "MCP handshake failed" not in persisted
    assert '"diagnostic_code":"mcp_handshake_failed"' in persisted


def test_v2_attempt_is_upgraded_in_memory_and_rewritten_on_next_transition(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    store = AttemptStore(state_root, clock=Clock())
    parent, _ = policies()
    reserved = store.reserve(spec(tmp_path), parent_policy=parent).attempt
    path = next((state_root / "runtime" / "requests").glob("*.json"))
    document = json.loads(path.read_text())
    document["schema"] = "agent_commons.runtime_request.v2"
    for attempt in document["attempts"]:
        attempt["schema"] = "agent_commons.runtime_attempt.v2"
        attempt.pop("diagnostic_code")
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    loaded = store.list_attempts()[0]
    assert loaded.schema == "agent_commons.runtime_attempt.v3"
    assert loaded.diagnostic_code is DiagnosticCode.LEGACY_UNCLASSIFIED

    store.transition(reserved.attempt_id, AttemptState.FAILED, reason="start_failed")
    rewritten = json.loads(path.read_text())
    assert rewritten["schema"] == "agent_commons.runtime_request.v3"
    assert rewritten["attempts"][0]["schema"] == "agent_commons.runtime_attempt.v3"
    assert rewritten["attempts"][0]["diagnostic_code"] == "legacy_unclassified"


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("Please run /login; token=sk-secret", DiagnosticCode.PROVIDER_AUTH_FAILED),
        ("Maximum budget exceeded; token=sk-secret", DiagnosticCode.PROVIDER_BUDGET_EXHAUSTED),
        ("Unknown option --future-flag; token=sk-secret", DiagnosticCode.UNSUPPORTED_PROVIDER_FLAG),
        ("Invalid MCP config; token=sk-secret", DiagnosticCode.MCP_CONFIG_INVALID),
        ("Failed to spawn MCP server; token=sk-secret", DiagnosticCode.MCP_SPAWN_FAILED),
        ("MCP binding timeout; token=sk-secret", DiagnosticCode.MCP_BINDING_TIMEOUT),
        (
            "agent-commons-exec-gate: provider exec failed",
            DiagnosticCode.PROVIDER_START_FAILED,
        ),
        (
            "agent-commons-exec-gate: invalid control frame",
            DiagnosticCode.BROKER_CONTROL_ERROR,
        ),
    ),
)
def test_provider_failure_corpus_maps_only_to_closed_codes(
    message: str,
    expected: DiagnosticCode,
) -> None:
    diagnostic = classify_process_result(
        ProcessResult(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            exit_code=1,
            pid=123,
            duration_seconds=0.1,
            stdout=b"",
            stderr=message.encode(),
            stdout_bytes_seen=0,
            stderr_bytes_seen=len(message.encode()),
            output_truncated=False,
        )
    )

    assert diagnostic.code is expected
    assert "sk-secret" not in diagnostic.hint
