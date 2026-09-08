"""Avoid replay and lock queues without weakening canonical conversation writes."""

from contextlib import contextmanager

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.services.conversations import Conversations
from agent_commons.services.manager import CommonsManager
from tests.services.test_conversations import manager as manager_fixture


@pytest.fixture
def manager(tmp_path):
    return manager_fixture.__wrapped__(tmp_path)


def test_existing_conversation_does_not_acquire_canonical_write_lock(manager, monkeypatch):
    service = Conversations(manager)
    first = service.ensure(scope={"kind": "project"}, idempotency_key="open")

    @contextmanager
    def forbidden():
        raise AssertionError("An existing conversation must not wait for a canonical writer")
        yield

    monkeypatch.setattr(manager, "_canonical_write_lock", forbidden)
    assert service.ensure(scope={"kind": "project"}, idempotency_key="open") == first


def test_creation_rechecks_scope_after_waiting_for_write_lock(manager, monkeypatch):
    other = CommonsManager(
        manager.repo_root, session_id=manager.session_id, state_root=manager.paths.state_root
    )
    original = manager._canonical_write_lock
    created = None

    @contextmanager
    def concurrent_writer():
        nonlocal created
        if created is None:
            created = Conversations(other).ensure(
                scope={"kind": "project"}, idempotency_key="other"
            )
        with original():
            yield

    monkeypatch.setattr(manager, "_canonical_write_lock", concurrent_writer)
    result = Conversations(manager).ensure(scope={"kind": "project"}, idempotency_key="mine")
    assert result == created
    assert len(manager.snapshot().threads) == 1


def test_created_conversation_response_equals_canonical_projection(manager):
    service = Conversations(manager)
    result = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    assert service.list({"kind": "project"})["conversations"] == [result]


def test_replayed_open_never_invents_open_state_for_resolved_thread(manager):
    service = Conversations(manager)
    result = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    manager.resolve_thread(
        result["thread_id"],
        result["revision"],
        resolution="resolved",
        summary="Finished",
        idempotency_key="close",
    )
    replayed = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    assert replayed["state"] == "resolved"
    assert replayed == service.list({"kind": "project"})["conversations"][0]


def test_existing_scope_still_rejects_ambiguous_canonical_threads(manager):
    service = Conversations(manager)
    service.ensure(scope={"kind": "project"}, idempotency_key="open")
    manager.open_thread(
        thread_type="engagement",
        subject="Other",
        desired_outcome="Other",
        to=("operator", "*"),
        conversation_scope={"kind": "project"},
        idempotency_key="second-thread",
    )
    with pytest.raises(LifecycleConflictError):
        service.ensure(scope={"kind": "project"}, idempotency_key="retry")


def test_private_attachment_operations_observe_canonical_scope_once(manager, monkeypatch):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    original = CommonsManager._records_and_snapshot
    count = 0

    def counted(self, *args, **kwargs):
        nonlocal count
        count += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CommonsManager, "_records_and_snapshot", counted)
    draft = service.reserve_draft(thread["thread_id"])
    assert count == 1
    count = 0
    item = service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="brief.txt",
        media_type="text/plain",
        data=b"brief",
    )
    assert count == 1
    count = 0
    service.show_draft(thread["thread_id"], draft["draft_id"])
    assert count == 1
    count = 0
    sent = service.send(
        thread["thread_id"],
        thread["revision"],
        body="Read",
        draft_id=draft["draft_id"],
        idempotency_key="send",
    )
    assert count <= 4
    count = 0
    assert (
        service.read_attachment(thread["thread_id"], sent["message_id"], item["attachment_id"])[1]
        == b"brief"
    )
    assert count == 1
