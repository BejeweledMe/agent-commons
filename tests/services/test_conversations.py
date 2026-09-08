from copy import deepcopy

import pytest

from agent_commons.domain.conversations import attachment_refs, conversation_scope
from agent_commons.domain.revisions import structural_correction_changes
from agent_commons.errors import IdempotencyConflictError, LifecycleConflictError, ValidationError
from agent_commons.services.conversations import Conversations
from agent_commons.services.manager import CommonsManager


@pytest.fixture
def manager(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="conversation-tests")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="conversation-operator",
        principal="operator",
        client="codex",
        software="tests",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def attachment(number=0, **changes):
    return {
        "attachment_id": "attachment." + str(number).zfill(32),
        "digest": "sha256:" + "a" * 64,
        "media_type": "image/png",
        "size_bytes": 15_000_000,
        "display_name": "reference.png",
        "category": "image",
        **changes,
    }


def test_v2_attachment_only_reply_round_trips_and_retry_preserves_identity(manager):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    again = service.ensure(scope={"kind": "project"}, idempotency_key="another-open")
    assert thread == again
    args = {"body": "", "attachments": [attachment()], "idempotency_key": "send"}
    first = service.reply(thread["thread_id"], thread["revision"], **args)
    retry = service.reply(thread["thread_id"], thread["revision"], **args)
    assert first["revision"] == retry["revision"]
    with pytest.raises(IdempotencyConflictError):
        service.reply(thread["thread_id"], thread["revision"], **{**args, "body": "different"})
    data = service.messages(thread["thread_id"])
    assert len(data["messages"]) == 1
    assert data["messages"][0]["attachments"] == [attachment()]
    assert data["messages"][0]["delivery"]["read_confirmed"] is False
    assert manager.snapshot().semantics_required == 6


def test_legacy_text_keeps_legacy_floor_and_shape(manager):
    thread = manager.open_thread(
        thread_type="question",
        subject="Question",
        desired_outcome="Answer",
        to=("operator",),
        idempotency_key="legacy-open",
    )
    manager.reply_thread(
        thread["entity_ref"]["id"], thread["revision"], body="Hello", idempotency_key="legacy-reply"
    )
    snapshot = manager.snapshot()
    assert snapshot.semantics_required < 6
    assert "attachments" not in snapshot.threads[thread["entity_ref"]["id"]]["messages"][0]


def test_agent_reply_is_not_queued_back_to_its_author(manager):
    from types import SimpleNamespace

    message = {
        "message_id": "message." + "1" * 32,
        "actor": {"session_id": "session." + "1" * 32},
    }
    thread = {"id": "thread." + "1" * 26, "to": ["*"], "messages": [message]}
    author = {
        "id": "delegation." + "1" * 26,
        "child_session_id": message["actor"]["session_id"],
        "agent_id": "agent." + "1" * 26,
        "purpose": "implementation",
        "state": "active",
    }
    snapshot = SimpleNamespace(delegations={author["id"]: author})
    service = Conversations(manager)
    assert service.delivery_view(thread, message, snapshot) == {
        "state": "recorded",
        "read_confirmed": False,
        "recipients": [],
    }
    peer = {
        **author,
        "id": "delegation." + "2" * 26,
        "child_session_id": "session." + "2" * 32,
        "agent_id": "agent." + "2" * 26,
    }
    snapshot.delegations[peer["id"]] = peer
    delivery = service.delivery_view(thread, message, snapshot)
    assert delivery["state"] == "queued"
    assert [item["agent_id"] for item in delivery["recipients"]] == [peer["agent_id"]]
    assert delivery["read_confirmed"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"size_bytes": 15_000_001},
        {"size_bytes": True},
        {"display_name": "../secret"},
        {"display_name": "bad\\name"},
        {"display_name": "bad\x00name"},
        {"media_type": "image/svg+xml"},
        {"category": []},
        {"media_type": []},
        {"digest": "no"},
        {"attachment_id": "../file"},
        {"category": "file"},
    ],
)
def test_invalid_attachment_metadata_fails_closed(patch):
    with pytest.raises(ValidationError):
        attachment_refs([attachment(**patch)])


def test_image_and_file_count_limits_are_independent():
    images = [attachment(i) for i in range(10)]
    files = [attachment(i + 10, media_type="application/pdf", category="file") for i in range(10)]
    assert len(attachment_refs(images + files)) == 20
    with pytest.raises(ValidationError):
        attachment_refs(images + [attachment(30)])
    with pytest.raises(ValidationError):
        attachment_refs([attachment(), attachment()])


@pytest.mark.parametrize(
    "scope", [{"kind": []}, {"kind": "task", "id": "foreign"}, {"kind": "project", "id": "extra"}]
)
def test_scope_validation(scope):
    with pytest.raises(ValidationError):
        conversation_scope(scope)


def test_reply_and_subject_cannot_cross_conversations(manager):
    service = Conversations(manager)
    with pytest.raises(ValidationError):
        service.ensure(scope={"kind": "task", "id": "task." + "0" * 26}, idempotency_key="missing")
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    with pytest.raises(LifecycleConflictError):
        service.reply(
            thread["thread_id"],
            thread["revision"],
            body="Hello",
            reply_to_message_id="message." + "0" * 26,
            idempotency_key="outside",
        )
    with pytest.raises(ValidationError):
        service.reply(thread["thread_id"], thread["revision"], body="", idempotency_key="empty")


def test_correction_cannot_swap_attachment_or_scope():
    original = {"conversation_scope": {"kind": "project"}, "attachments": [attachment()]}
    changed = deepcopy(original)
    changed["attachments"][0]["digest"] = "sha256:" + "b" * 64
    assert structural_correction_changes(original, changed) == ("attachments",)


def test_private_attachment_send_binds_exact_metadata_and_restarts(manager):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    attachment = service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="brief.txt",
        media_type="text/plain",
        data=b"A private project brief",
    )
    sent = service.send(
        thread["thread_id"],
        thread["revision"],
        body="",
        draft_id=draft["draft_id"],
        idempotency_key="send",
    )
    retry = service.send(
        thread["thread_id"],
        thread["revision"],
        body="",
        draft_id=draft["draft_id"],
        idempotency_key="send",
    )
    assert sent == retry
    restarted = Conversations(manager)
    descriptor, data = restarted.read_attachment(
        thread["thread_id"], sent["message_id"], attachment["attachment_id"]
    )
    assert data == b"A private project brief"
    assert restarted._descriptor(descriptor) == attachment
    assert restarted.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "bound"


def test_invalid_send_never_freezes_draft(manager):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    with pytest.raises(ValidationError):
        service.send(
            thread["thread_id"],
            thread["revision"],
            body="",
            draft_id=draft["draft_id"],
            idempotency_key="send",
        )
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "ready"


def test_canonical_commit_then_private_finalize_crash_recovers_exact_message(manager, monkeypatch):
    from agent_commons.runtime.message_attachments import MessageAttachmentStore

    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    item = service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="notes.txt",
        media_type="text/plain",
        data=b"Notes",
    )
    original = MessageAttachmentStore.finalize_binding

    def fail(*args, **kwargs):
        raise OSError("simulated response loss")

    monkeypatch.setattr(MessageAttachmentStore, "finalize_binding", fail)
    with pytest.raises(OSError):
        service.send(
            thread["thread_id"],
            thread["revision"],
            body="Read this",
            draft_id=draft["draft_id"],
            idempotency_key="send",
        )
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "binding"
    monkeypatch.setattr(MessageAttachmentStore, "finalize_binding", original)
    sent = service.send(
        thread["thread_id"],
        thread["revision"],
        body="Read this",
        draft_id=draft["draft_id"],
        idempotency_key="send",
    )
    assert len(service.messages(thread["thread_id"])["messages"]) == 1
    assert (
        service.read_attachment(thread["thread_id"], sent["message_id"], item["attachment_id"])[1]
        == b"Notes"
    )


def test_addressed_mcp_delivers_real_image_and_explicit_ack(manager):
    import base64
    import io

    from PIL import Image

    from agent_commons.mcp.conversation_tools import register_conversation_tools

    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    task = manager.create_task(
        title="Image task",
        description="Inspect the fixture",
        acceptance_criteria=("Read image",),
        idempotency_key="task",
    )
    task = manager.start_task(
        task["entity_ref"]["id"], task["revision"], idempotency_key="start-task"
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="delegate",
    )
    child = manager.start_session(
        stable_instance_id="image-worker",
        principal="operator",
        client="codex",
        software="tests",
        role="builder",
    )
    manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child["session_id"],
        attempt=1,
        idempotency_key="delegation-start",
    )
    draft = service.reserve_draft(thread["thread_id"])
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(stream, format="PNG")
    data = stream.getvalue()
    item = service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="reference.png",
        media_type="image/png",
        data=data,
    )
    sent = service.send(
        thread["thread_id"],
        thread["revision"],
        body="Inspect the attached image.",
        draft_id=draft["draft_id"],
        idempotency_key="send",
    )
    worker = CommonsManager(
        manager.repo_root, session_id=child["session_id"], state_root=manager.paths.state_root
    )
    tools = {}

    def register(*args, **kwargs):
        def decorate(fn):
            tools[fn.__name__] = fn
            return fn

        return decorate

    def current():
        return worker.snapshot().delegations[delegation["entity_ref"]["id"]]

    register_conversation_tools(register, worker, current)
    with pytest.raises(ValidationError):
        tools["commons_acknowledge_message"](thread["thread_id"], sent["message_id"])
    tools["commons_read_message"](thread["thread_id"], sent["message_id"])
    result = tools["commons_read_message_image"](
        thread["thread_id"], sent["message_id"], item["attachment_id"]
    )
    assert result.content[0].type == "image"
    assert base64.b64decode(result.content[0].data) == data
    assert result.content[0].mimeType == "image/png"
    tools["commons_acknowledge_message"](thread["thread_id"], sent["message_id"])
    assert (
        service.messages(thread["thread_id"])["messages"][0]["delivery"]["state"] == "acknowledged"
    )
    with pytest.raises(LifecycleConflictError):
        tools["commons_read_message_image"](
            "thread." + "0" * 26, sent["message_id"], item["attachment_id"]
        )


def test_same_key_changed_intent_and_empty_reply_leave_new_draft_ready(manager):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    service.send(thread["thread_id"], thread["revision"], body="First", idempotency_key="send")
    for key, reply in [("send", None), ("new", "")]:
        draft = service.reserve_draft(thread["thread_id"])
        service.upload(
            thread["thread_id"],
            draft["draft_id"],
            filename="brief.txt",
            media_type="text/plain",
            data=b"brief",
        )
        with pytest.raises((IdempotencyConflictError, ValidationError)):
            service.send(
                thread["thread_id"],
                thread["revision"],
                body="Changed",
                draft_id=draft["draft_id"],
                idempotency_key=key,
                reply_to_message_id=reply,
            )
        assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "ready"
    assert len(service.messages(thread["thread_id"])["messages"]) == 1


def test_stale_revision_is_proven_precommit_and_exact_reply_still_recovers(manager):
    from agent_commons.services.conversations import ConversationRevisionConflict

    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    first = service.send(
        thread["thread_id"], thread["revision"], body="First", idempotency_key="first"
    )
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="brief.txt",
        media_type="text/plain",
        data=b"brief",
    )
    with pytest.raises(ConversationRevisionConflict):
        service.send(
            thread["thread_id"],
            thread["revision"],
            body="Second",
            idempotency_key="second",
            draft_id=draft["draft_id"],
        )
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "ready"
    assert (
        service.send(thread["thread_id"], thread["revision"], body="First", idempotency_key="first")
        == first
    )


def test_upload_http_retry_identity_reuses_exact_attachment(manager):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    args = {
        "filename": "brief.txt",
        "media_type": "text/plain",
        "data": b"brief",
        "operation_id": "upload-retry",
    }
    item = service.upload(thread["thread_id"], draft["draft_id"], **args)
    assert service.upload(thread["thread_id"], draft["draft_id"], **args) == item
    assert len(service.show_draft(thread["thread_id"], draft["draft_id"])["attachments"]) == 1


def test_failed_append_recovers_exact_frozen_content_after_another_message(manager, monkeypatch):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="brief.txt",
        media_type="text/plain",
        data=b"brief",
    )
    original = manager.events.append_event

    def fail(**kwargs):
        raise OSError("simulated interruption before event reservation")

    monkeypatch.setattr(manager.events, "append_event", fail)
    intent = {"body": "Frozen", "draft_id": draft["draft_id"], "idempotency_key": "send"}
    with pytest.raises(OSError):
        service.send(thread["thread_id"], thread["revision"], **intent)
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "binding"
    monkeypatch.setattr(manager.events, "append_event", original)
    service.send(
        thread["thread_id"], thread["revision"], body="Concurrent", idempotency_key="concurrent"
    )
    with pytest.raises(IdempotencyConflictError):
        service.send(thread["thread_id"], thread["revision"], **{**intent, "body": "Changed"})
    sent = service.send(thread["thread_id"], thread["revision"], **intent)
    assert service.send(thread["thread_id"], thread["revision"], **intent) == sent
    assert len(service.messages(thread["thread_id"])["messages"]) == 2
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "bound"


def test_history_tail_and_before_are_bounded_and_keep_legacy_forward_contract(manager, monkeypatch):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    service.send(thread["thread_id"], thread["revision"], body="Fixture", idempotency_key="one")
    snapshot = manager.snapshot()
    template = snapshot.threads[thread["thread_id"]]["messages"][0]
    messages = [
        {**template, "message_id": f"message.{i:026X}", "body": f"Message {i}"} for i in range(2001)
    ]
    from types import SimpleNamespace

    snapshot = SimpleNamespace(
        threads={
            thread["thread_id"]: {
                "id": thread["thread_id"],
                "revision": thread["revision"],
                "conversation_scope": thread["scope"],
                "subject": thread["subject"],
                "state": "open",
                "messages": messages,
            }
        },
        delegations=snapshot.delegations,
        agents=snapshot.agents,
    )
    monkeypatch.setattr(manager, "snapshot", lambda: snapshot)
    monkeypatch.setattr(
        service,
        "delivery_view",
        lambda *args: {"state": "recorded", "read_confirmed": False, "recipients": []},
    )
    tail = service.messages(thread["thread_id"], tail=True)
    assert [item["body"] for item in tail["messages"]] == [
        f"Message {i}" for i in range(1951, 2001)
    ]
    assert tail["next_cursor"] is None
    assert tail["previous_cursor"] == messages[1951]["message_id"]
    before = service.messages(thread["thread_id"], before=tail["previous_cursor"])
    assert before["messages"][-1]["body"] == "Message 1950"
    assert before["previous_cursor"] == messages[1901]["message_id"]
    first = service.messages(thread["thread_id"])
    assert first["messages"][0]["body"] == "Message 0"
    assert first["previous_cursor"] is None
    assert (
        service.messages(thread["thread_id"], after=first["next_cursor"])["messages"][0]["body"]
        == "Message 50"
    )
    assert service.messages(thread["thread_id"], before=messages[0]["message_id"])["messages"] == []
    for query in (
        {"tail": True, "after": messages[0]["message_id"]},
        {"tail": True, "before": messages[0]["message_id"]},
        {"after": messages[0]["message_id"], "before": messages[1]["message_id"]},
        {"tail": 1},
        {"before": "unknown"},
    ):
        with pytest.raises(ValidationError):
            service.messages(thread["thread_id"], **query)


def test_expired_ready_draft_refusal_proves_no_binding_or_message(manager, monkeypatch):
    from agent_commons.services.conversations import ConversationDraftExpired

    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="note.txt",
        media_type="text/plain",
        data=b"fixture",
    )
    store, operator = service._attachment_context(thread["thread_id"])
    store.clock = lambda: draft["expires_at"] + 1
    monkeypatch.setattr(service, "_attachment_context", lambda *args, **kwargs: (store, operator))
    monkeypatch.setattr(
        store,
        "begin_binding",
        lambda *args, **kwargs: pytest.fail("expired draft must not enter binding"),
    )
    for _ in range(2):
        with pytest.raises(ConversationDraftExpired):
            service.send(
                thread["thread_id"],
                thread["revision"],
                body="Kept",
                draft_id=draft["draft_id"],
                idempotency_key="expired",
            )
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "ready"
    assert service.messages(thread["thread_id"])["messages"] == []


def test_expiry_cannot_release_an_ambiguous_reserved_identity(manager, monkeypatch):
    from agent_commons.services.conversations import ConversationDraftExpired

    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="note.txt",
        media_type="text/plain",
        data=b"fixture",
    )
    store, operator = service._attachment_context(thread["thread_id"])
    store.clock = lambda: draft["expires_at"] + 1
    monkeypatch.setattr(service, "_attachment_context", lambda *args, **kwargs: (store, operator))
    monkeypatch.setattr(manager.events.idempotency, "lookup", lambda **kwargs: object())
    with pytest.raises(LifecycleConflictError) as error:
        service.send(
            thread["thread_id"],
            thread["revision"],
            body="Frozen",
            draft_id=draft["draft_id"],
            idempotency_key="ambiguous",
        )
    assert not isinstance(error.value, ConversationDraftExpired)


def test_expired_bound_retry_recovers_exact_message_without_refusal(manager, monkeypatch):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="note.txt",
        media_type="text/plain",
        data=b"fixture",
    )
    intent = {"body": "Recorded", "draft_id": draft["draft_id"], "idempotency_key": "send"}
    sent = service.send(thread["thread_id"], thread["revision"], **intent)
    store, operator = service._attachment_context(thread["thread_id"])
    store.clock = lambda: draft["expires_at"] + 1
    monkeypatch.setattr(service, "_attachment_context", lambda *args, **kwargs: (store, operator))
    assert service.send(thread["thread_id"], thread["revision"], **intent) == sent
    with pytest.raises(IdempotencyConflictError):
        service.send(thread["thread_id"], thread["revision"], **{**intent, "body": "Changed"})
    assert len(service.messages(thread["thread_id"])["messages"]) == 1


def test_expired_binding_without_append_recovers_only_frozen_content(manager, monkeypatch):
    service = Conversations(manager)
    thread = service.ensure(scope={"kind": "project"}, idempotency_key="open")
    draft = service.reserve_draft(thread["thread_id"])
    service.upload(
        thread["thread_id"],
        draft["draft_id"],
        filename="note.txt",
        media_type="text/plain",
        data=b"fixture",
    )
    original = manager.events.append_event

    def fail(**kwargs):
        raise OSError("fixture failure before reservation")

    monkeypatch.setattr(manager.events, "append_event", fail)
    intent = {"body": "Frozen", "draft_id": draft["draft_id"], "idempotency_key": "send"}
    with pytest.raises(OSError):
        service.send(thread["thread_id"], thread["revision"], **intent)
    assert service.show_draft(thread["thread_id"], draft["draft_id"])["state"] == "binding"
    monkeypatch.setattr(manager.events, "append_event", original)
    store, operator = service._attachment_context(thread["thread_id"])
    store.clock = lambda: draft["expires_at"] + 3600
    monkeypatch.setattr(service, "_attachment_context", lambda *args, **kwargs: (store, operator))
    with pytest.raises(IdempotencyConflictError):
        service.send(thread["thread_id"], thread["revision"], **{**intent, "body": "Changed"})
    sent = service.send(thread["thread_id"], thread["revision"], **intent)
    assert service.send(thread["thread_id"], thread["revision"], **intent) == sent
    assert len(service.messages(thread["thread_id"])["messages"]) == 1
