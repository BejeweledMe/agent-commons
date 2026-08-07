from __future__ import annotations

import hashlib
import hmac
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_commons.core.ids import stable_id
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.runtime import (
    CommunicationAuthorizationError,
    CommunicationScope,
    CommunicationStore,
    OperationKind,
    OperationLimits,
    OperationRequestSpec,
    OperationState,
    checkout_fingerprint,
)


class FakeClock:
    """A manually advanced clock so deadline/expiry behavior is exact and hermetic."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_scope(
    tmp_path: Path,
    *,
    sender: str = "sender",
    recipients: tuple[str, ...] = ("recipient",),
    delegation: str = "delegation-1",
    task: str = "task-1",
    revision: str = "revision-1",
    attempt: str = "attempt-1",
) -> CommunicationScope:
    return CommunicationScope(
        workspace_fingerprint=checkout_fingerprint(tmp_path),
        delegation_id=stable_id("delegation", delegation),
        task_id=stable_id("task", task),
        target_revision=stable_id("evt", revision),
        attempt_id=stable_id("attempt", attempt),
        sender_session_id=stable_id("session", sender),
        allowed_recipient_session_ids=tuple(stable_id("session", name) for name in recipients),
    )


def make_spec(
    tmp_path: Path,
    *,
    key: str = "op-1",
    kind: OperationKind = OperationKind.REQUEST,
    metadata: dict[str, object] | None = None,
    deadline_seconds: int = 60,
    continuation_of: str | None = None,
    **scope_kwargs: object,
) -> OperationRequestSpec:
    return OperationRequestSpec(
        idempotency_key=key,
        kind=kind,
        scope=make_scope(tmp_path, **scope_kwargs),
        metadata=metadata if metadata is not None else {"question": "which branch?"},
        deadline_seconds=deadline_seconds,
        continuation_of=continuation_of,
    )


def _tamper(
    path: Path,
    *,
    reseal: bool = False,
    integrity_key: bytes | None = None,
    **updates: object,
) -> None:
    document = json.loads(path.read_bytes())
    document.update(updates)
    if reseal:
        semantic = dict(document)
        semantic.pop("semantic_sha256", None)
        semantic.pop("integrity_hmac_sha256", None)
        canonical_semantic = json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        semantic_bytes = (canonical_semantic + "\n").encode("utf-8")
        document["semantic_sha256"] = hashlib.sha256(semantic_bytes).hexdigest()
        if integrity_key is not None:
            document["integrity_hmac_sha256"] = hmac.new(
                integrity_key, semantic_bytes, hashlib.sha256
            ).hexdigest()
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_bytes((canonical + "\n").encode("utf-8"))


def test_request_check_reply_ack_lifecycle_is_private_and_atomic(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")

    opened = store.request(make_spec(tmp_path))
    assert opened.state is OperationState.OPEN
    operations_dir = tmp_path / "state" / "runtime" / "communication" / "operations"
    document_path = operations_dir / f"{opened.operation_id}.json"
    assert document_path.exists()
    assert stat.S_IMODE(document_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(operations_dir.stat().st_mode) == 0o700

    checked = store.check(opened.operation_id, requester_session_id=sender)
    assert checked.state is OperationState.OPEN

    replied = store.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )
    assert replied.state is OperationState.REPLIED
    assert replied.reply == {"branch": "main"}

    acked = store.ack(opened.operation_id, acker_session_id=sender, idempotency_key="ack-1")
    assert acked.state is OperationState.ACKED
    assert acked.state.terminal


def test_reply_is_exactly_once(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    recipient = stable_id("session", "recipient")
    opened = store.request(make_spec(tmp_path))

    first = store.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )
    repeated = store.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )
    assert repeated == first

    with pytest.raises(LifecycleConflictError, match="exactly-once reply"):
        store.reply(
            opened.operation_id,
            responder_session_id=recipient,
            idempotency_key="reply-2",
            answer={"branch": "develop"},
        )


def test_ack_is_exactly_once(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")
    opened = store.request(make_spec(tmp_path))
    store.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )

    first = store.ack(opened.operation_id, acker_session_id=sender, idempotency_key="ack-1")
    repeated = store.ack(opened.operation_id, acker_session_id=sender, idempotency_key="ack-1")
    assert repeated == first

    with pytest.raises(LifecycleConflictError, match="exactly-once ack"):
        store.ack(opened.operation_id, acker_session_id=sender, idempotency_key="ack-2")


@pytest.mark.parametrize(
    "kind",
    [
        OperationKind.PROGRESS,
        OperationKind.BLOCKER,
        OperationKind.GUIDANCE,
        OperationKind.CHECKPOINT,
    ],
)
def test_progress_and_blocker_ack_directly_without_a_reply(
    tmp_path: Path, kind: OperationKind
) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    recipient = stable_id("session", "recipient")
    opened = store.request(make_spec(tmp_path, kind=kind, metadata={"percent": 50}))
    assert opened.state is OperationState.OPEN

    with pytest.raises(LifecycleConflictError, match="only a request operation"):
        store.reply(
            opened.operation_id,
            responder_session_id=recipient,
            idempotency_key="reply-1",
            answer={"ok": True},
        )

    acked = store.ack(opened.operation_id, acker_session_id=recipient, idempotency_key="ack-1")
    assert acked.state is OperationState.ACKED


def test_request_is_idempotent_and_rejects_key_reuse_for_different_content(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)

    first = store.request(make_spec(tmp_path, key="shared-key"))
    repeated = store.request(make_spec(tmp_path, key="shared-key"))
    assert repeated == first

    with pytest.raises(IdempotencyConflictError):
        store.request(
            make_spec(tmp_path, key="shared-key", metadata={"question": "different question"})
        )

    with pytest.raises(IdempotencyConflictError):
        store.request(make_spec(tmp_path, key="shared-key", deadline_seconds=61))


def test_deadline_expiry_is_lazy_and_fails_closed(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")
    opened = store.request(make_spec(tmp_path, deadline_seconds=10))

    clock.advance(11)
    checked = store.check(opened.operation_id, requester_session_id=sender)
    assert checked.state is OperationState.EXPIRED

    with pytest.raises(LifecycleConflictError, match="illegal communication transition"):
        store.reply(
            opened.operation_id,
            responder_session_id=recipient,
            idempotency_key="reply-1",
            answer={"branch": "main"},
        )


def test_authorization_rejects_non_participants_and_wrong_roles(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")
    stranger = stable_id("session", "stranger")
    opened = store.request(make_spec(tmp_path))

    with pytest.raises(CommunicationAuthorizationError):
        store.check(opened.operation_id, requester_session_id=stranger)

    with pytest.raises(CommunicationAuthorizationError):
        store.reply(
            opened.operation_id,
            responder_session_id=sender,
            idempotency_key="reply-1",
            answer={"branch": "main"},
        )

    store.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )
    with pytest.raises(CommunicationAuthorizationError):
        store.ack(opened.operation_id, acker_session_id=recipient, idempotency_key="ack-1")


def test_foreign_and_missing_operations_share_one_non_disclosing_error(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path, attempt="attempt-1"))

    with pytest.raises(CommunicationAuthorizationError) as foreign:
        store.check(
            opened.operation_id,
            requester_session_id=sender,
            attempt_id=stable_id("attempt", "attempt-other"),
        )
    with pytest.raises(CommunicationAuthorizationError) as missing:
        store.check(
            stable_id("operation", "missing"),
            requester_session_id=sender,
        )

    assert str(foreign.value) == str(missing.value) == "communication operation is unavailable"


def test_invalid_operation_id_is_rejected_before_path_resolution(tmp_path: Path) -> None:
    store = CommunicationStore(tmp_path / "state")

    with pytest.raises(ValidationError, match="valid operation identifier"):
        store.check("../../outside", requester_session_id=stable_id("session", "sender"))


def test_oversized_metadata_is_rejected_before_any_write(tmp_path: Path) -> None:
    clock = FakeClock()
    limits = OperationLimits(max_metadata_bytes=32)
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock, limits=limits)

    with pytest.raises(ValidationError, match="exceeds the configured size limit"):
        store.request(make_spec(tmp_path, metadata={"question": "x" * 200}))

    operations_dir = tmp_path / "state" / "runtime" / "communication" / "operations"
    assert not operations_dir.exists() or not any(operations_dir.iterdir())


def test_secret_bearing_metadata_fails_closed_without_echo(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)

    with pytest.raises(SecurityPolicyError):
        store.request(make_spec(tmp_path, metadata={"password": "hunter2-secret-value"}))

    operations_dir = tmp_path / "state" / "runtime" / "communication" / "operations"
    assert not operations_dir.exists() or not any(operations_dir.iterdir())


def test_continuation_chain_depth_is_bounded(tmp_path: Path) -> None:
    clock = FakeClock()
    limits = OperationLimits(max_chain_depth=2)
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock, limits=limits)

    root = store.request(make_spec(tmp_path, key="root"))
    assert root.depth == 0
    child = store.request(make_spec(tmp_path, key="child", continuation_of=root.operation_id))
    assert child.depth == 1
    grandchild = store.request(
        make_spec(tmp_path, key="grandchild", continuation_of=child.operation_id)
    )
    assert grandchild.depth == 2

    with pytest.raises(ValidationError, match="exceeds the configured depth limit"):
        store.request(
            make_spec(tmp_path, key="great-grandchild", continuation_of=grandchild.operation_id)
        )


def test_tampered_continuation_chain_is_rejected_as_cyclic(tmp_path: Path) -> None:
    clock = FakeClock()
    limits = OperationLimits(max_chain_depth=8)
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock, limits=limits)

    root = store.request(make_spec(tmp_path, key="root"))
    child = store.request(make_spec(tmp_path, key="child", continuation_of=root.operation_id))

    _tamper(
        store._path(root.operation_id),
        reseal=True,
        integrity_key=store._integrity_key_bytes(),
        continuation_of=child.operation_id,
    )

    with pytest.raises(ValidationError, match="cyclic"):
        store.request(make_spec(tmp_path, key="grandchild", continuation_of=child.operation_id))


def test_tampered_document_fails_closed_on_read(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path))

    _tamper(store._path(opened.operation_id), state="not-a-real-state")

    with pytest.raises(IntegrityError):
        store.check(opened.operation_id, requester_session_id=sender)


def test_public_digest_reseal_cannot_authenticate_metadata_tamper(tmp_path: Path) -> None:
    store = CommunicationStore(tmp_path / "state")
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path))

    _tamper(
        store._path(opened.operation_id),
        reseal=True,
        metadata={"question": "silently changed"},
    )

    with pytest.raises(IntegrityError, match="authentication tag"):
        store.check(opened.operation_id, requester_session_id=sender)


def test_resealed_but_impossible_state_shape_fails_closed(tmp_path: Path) -> None:
    store = CommunicationStore(tmp_path / "state")
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path))

    _tamper(
        store._path(opened.operation_id),
        reseal=True,
        integrity_key=store._integrity_key_bytes(),
        state="acked",
    )

    with pytest.raises(IntegrityError, match="acknowledgement key"):
        store.check(opened.operation_id, requester_session_id=sender)


def test_restart_reopens_the_store_and_preserves_exactly_once_state(tmp_path: Path) -> None:
    clock = FakeClock()
    state_root = tmp_path / "state"
    store_before_restart = CommunicationStore(state_root, clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")
    opened = store_before_restart.request(make_spec(tmp_path))
    store_before_restart.reply(
        opened.operation_id,
        responder_session_id=recipient,
        idempotency_key="reply-1",
        answer={"branch": "main"},
    )

    restarted_clock = FakeClock(start=clock.value + 5)
    store_after_restart = CommunicationStore(
        state_root, clock=restarted_clock, wall_clock=restarted_clock
    )
    reloaded = store_after_restart.check(opened.operation_id, requester_session_id=sender)
    assert reloaded.state is OperationState.REPLIED
    assert reloaded.reply == {"branch": "main"}

    acked = store_after_restart.ack(
        opened.operation_id, acker_session_id=sender, idempotency_key="ack-1"
    )
    assert acked.state is OperationState.ACKED


def test_integrity_key_is_private_and_read_only_restart_can_verify(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    store = CommunicationStore(state_root)
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path))

    assert stat.S_IMODE(store.integrity_key_path.stat().st_mode) == 0o600
    assert len(store.integrity_key_path.read_bytes()) == 32

    read_only = CommunicationStore(state_root, read_only=True)
    assert read_only.check(opened.operation_id, requester_session_id=sender) == opened


def test_missing_or_changed_integrity_key_fails_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    store = CommunicationStore(state_root)
    sender = stable_id("session", "sender")
    opened = store.request(make_spec(tmp_path))

    store.integrity_key_path.write_bytes(b"x" * 32)
    store.integrity_key_path.chmod(0o600)
    with pytest.raises(IntegrityError, match="changed during store lifetime"):
        store.check(opened.operation_id, requester_session_id=sender)
    changed_key_store = CommunicationStore(state_root, read_only=True)
    with pytest.raises(IntegrityError, match="authentication tag"):
        changed_key_store.check(opened.operation_id, requester_session_id=sender)

    store.integrity_key_path.unlink()
    missing_key_store = CommunicationStore(state_root, read_only=True)
    with pytest.raises(IntegrityError, match="key is unavailable"):
        missing_key_store.check(opened.operation_id, requester_session_id=sender)


def test_concurrent_first_use_converges_on_one_integrity_key(tmp_path: Path) -> None:
    state_root = tmp_path / "state"

    def open_store(_: int) -> bytes:
        return CommunicationStore(state_root)._integrity_key_bytes()

    with ThreadPoolExecutor(max_workers=8) as executor:
        keys = list(executor.map(open_store, range(24)))

    assert len(set(keys)) == 1
    key_path = state_root / "runtime" / "communication" / "integrity.key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_cancellation_is_two_phase_and_requires_a_distinct_confirmer(tmp_path: Path) -> None:
    clock = FakeClock()
    store = CommunicationStore(tmp_path / "state", clock=clock, wall_clock=clock)
    sender = stable_id("session", "sender")
    recipient = stable_id("session", "recipient")
    opened = store.request(make_spec(tmp_path))

    with pytest.raises(CommunicationAuthorizationError, match="request cancellation"):
        store.request_cancel(opened.operation_id, by_session_id=recipient)

    requested = store.request_cancel(opened.operation_id, by_session_id=sender)
    assert requested.state is OperationState.CANCEL_REQUESTED
    assert store.request_cancel(opened.operation_id, by_session_id=sender) == requested

    with pytest.raises(CommunicationAuthorizationError):
        store.confirm_cancel(opened.operation_id, by_session_id=sender)

    confirmed = store.confirm_cancel(opened.operation_id, by_session_id=recipient)
    assert confirmed.state is OperationState.CANCELLED
    assert confirmed.state.terminal
    assert store.confirm_cancel(opened.operation_id, by_session_id=recipient) == confirmed


def test_continuation_requires_the_exact_parent_scope(tmp_path: Path) -> None:
    store = CommunicationStore(tmp_path / "state")
    parent = store.request(make_spec(tmp_path, key="parent"))

    with pytest.raises(ValidationError, match="foreign delegation scope"):
        store.request(
            make_spec(
                tmp_path,
                key="child",
                task="different-task",
                continuation_of=parent.operation_id,
            )
        )


def test_list_operations_only_returns_records_visible_to_requester(tmp_path: Path) -> None:
    store = CommunicationStore(tmp_path / "state")
    first = store.request(make_spec(tmp_path, key="first"))
    second = store.request(
        make_spec(
            tmp_path,
            key="second",
            sender="other-sender",
            recipients=("other-recipient",),
        )
    )

    sender = stable_id("session", "sender")
    other_sender = stable_id("session", "other-sender")
    assert store.list_operations(requester_session_id=sender) == (first,)
    assert store.list_operations(requester_session_id=other_sender) == (second,)
