from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError
from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CorrelationIds,
    PolicyViolationError,
    Provider,
    RuntimePolicy,
    checkout_fingerprint,
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
