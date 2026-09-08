"""Mounted HTTP contracts for project conversations and private attachments."""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_commons.errors import ConfigurationError
from agent_commons.runtime.message_attachments import MessageAttachmentStore
from agent_commons.ui.server import create_app
from tests.ui.conftest import PORT, authorized, tree_digest

BASE = "/api/conversations"


def ensure(client, scope=None, key="open"):
    response = client.post(
        BASE,
        json={"scope": scope or {"kind": "project"}, "idempotency_key": key},
        headers=authorized(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def reserve(client, thread):
    response = client.post(f"{BASE}/{thread['thread_id']}/drafts", json={}, headers=authorized())
    assert response.status_code == 200, response.text
    return response.json()


def upload(
    client,
    thread,
    draft,
    data=b"Private fixture bytes 827",
    key="upload",
    name="brief.txt",
    media="text/plain",
):
    return client.post(
        f"{BASE}/{thread['thread_id']}/drafts/{draft['draft_id']}/attachments",
        params={"filename": name},
        content=data,
        headers={**authorized(), "content-type": media, "idempotency-key": key},
    )


def send(client, thread, *, key="send", body="Hello", draft=None, **changes):
    value = {"body": body, "expected_revision": thread["revision"], "idempotency_key": key}
    if draft is not None:
        value["draft_id"] = draft["draft_id"]
    value.update(changes)
    return client.post(f"{BASE}/{thread['thread_id']}/messages", json=value, headers=authorized())


def show_draft(client, thread, draft):
    return client.get(
        f"{BASE}/{thread['thread_id']}/drafts/{draft['draft_id']}", headers=authorized()
    ).json()


def test_real_message_pagination_and_task_scope(writable, writable_client):
    thread = ensure(writable_client)
    assert ensure(writable_client, key="another") == thread
    first = send(writable_client, thread)
    assert first.status_code == 200
    second = send(
        writable_client,
        {**thread, "revision": first.json()["revision"]},
        key="second",
        body="Second",
        reply_to_message_id=first.json()["message_id"],
    )
    assert second.status_code == 200
    url = f"{BASE}/{thread['thread_id']}/messages"
    page = writable_client.get(url, params={"limit": 1}, headers=authorized())
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert len(page.json()["messages"]) == 1
    next_page = writable_client.get(
        url, params={"after": page.json()["next_cursor"]}, headers=authorized()
    ).json()
    assert [item["body"] for item in next_page["messages"]] == ["Second"]
    task = writable.writer().create_task(
        title="Scoped", description="A separate conversation", acceptance_criteria=("Read scope",)
    )
    scoped = ensure(writable_client, {"kind": "task", "id": task["entity_ref"]["id"]}, "task-open")
    assert scoped["thread_id"] != thread["thread_id"]
    listed = writable_client.get(
        BASE,
        params={"scope_kind": "task", "scope_id": task["entity_ref"]["id"]},
        headers=authorized(),
    ).json()
    assert [item["thread_id"] for item in listed["conversations"]] == [scoped["thread_id"]]
    assert (
        send(writable_client, scoped, reply_to_message_id=first.json()["message_id"]).status_code
        == 409
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", BASE),
        ("POST", BASE),
        ("GET", BASE + "/thread/messages"),
        ("POST", BASE + "/thread/messages"),
        ("POST", BASE + "/thread/drafts"),
        ("GET", BASE + "/thread/drafts/draft"),
        ("POST", BASE + "/thread/drafts/draft/attachments"),
        ("POST", BASE + "/thread/drafts/draft/attachments/attachment/remove"),
        ("GET", BASE + "/thread/messages/message/attachments/attachment"),
    ],
)
def test_every_conversation_endpoint_requires_browser_auth(writable_client, method, path):
    assert writable_client.request(method, path).status_code == 401


def test_foreign_origin_and_host_cannot_create_conversation(writable_client):
    body = {"scope": {"kind": "project"}, "idempotency_key": "open"}
    for header in ({"origin": "https://outside.invalid"}, {"host": "outside.invalid"}):
        response = writable_client.post(BASE, json=body, headers={**authorized(), **header})
        assert response.status_code == 403


@pytest.mark.parametrize("body", [b"[]", b"{", b'{"x":1,"x":2}', b" " * 100001])
def test_invalid_json_and_body_limit_fail_before_canonical_write(writable, writable_client, body):
    before = tree_digest(writable.repo / ".agent-commons")
    response = writable_client.post(BASE, content=body, headers=authorized())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "conversation_invalid_request"
    assert tree_digest(writable.repo / ".agent-commons") == before


def test_invalid_message_and_stale_revision_keep_draft_editable(writable_client):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    assert upload(writable_client, thread, draft).status_code == 200
    invalid = send(writable_client, thread, draft=draft, reply_to_message_id="")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "conversation_invalid_message"
    assert show_draft(writable_client, thread, draft)["state"] == "ready"
    assert send(writable_client, thread, key="other").status_code == 200
    stale = send(writable_client, thread, draft=draft)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conversation_revision_conflict"
    assert show_draft(writable_client, thread, draft)["state"] == "ready"


def test_finalize_uncertainty_keeps_one_canonical_message_and_same_retry(
    writable, writable_client, monkeypatch
):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    assert upload(writable_client, thread, draft).status_code == 200
    original = MessageAttachmentStore.finalize_binding

    def fail(*args, **kwargs):
        raise OSError("Synthetic private failure detail")

    monkeypatch.setattr(MessageAttachmentStore, "finalize_binding", fail)
    failed = send(writable_client, thread, draft=draft)
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "conversation_unavailable"
    assert "Synthetic" not in failed.text
    assert show_draft(writable_client, thread, draft)["state"] == "binding"
    assert len(writable.writer().snapshot().threads[thread["thread_id"]]["messages"]) == 1
    monkeypatch.setattr(MessageAttachmentStore, "finalize_binding", original)
    recovered = send(writable_client, thread, draft=draft)
    assert recovered.status_code == 200
    assert send(writable_client, thread, draft=draft).json() == recovered.json()
    assert len(writable.writer().snapshot().threads[thread["thread_id"]]["messages"]) == 1


def test_upload_identity_removal_and_no_resurrection(writable_client):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    first = upload(writable_client, thread, draft)
    assert first.status_code == 200
    assert upload(writable_client, thread, draft).json() == first.json()
    assert upload(writable_client, thread, draft, data=b"different").status_code == 409
    second = upload(writable_client, thread, draft, key="second", name="other.txt")
    assert second.status_code == 200
    url = (
        f"{BASE}/{thread['thread_id']}/drafts/{draft['draft_id']}"
        f"/attachments/{first.json()['attachment_id']}/remove"
    )
    removed = writable_client.post(url, json={}, headers=authorized())
    assert removed.status_code == 200
    assert removed.json()["attachments"] == [second.json()]
    assert writable_client.post(url, json={}, headers=authorized()).json() == removed.json()
    assert upload(writable_client, thread, draft).status_code == 409


@pytest.mark.parametrize(
    "data,name,media",
    [
        (b"", "empty.txt", "text/plain"),
        (b"x", "../bad.txt", "text/plain"),
        (b"not png", "fake.png", "image/png"),
    ],
)
def test_invalid_upload_does_not_destroy_previous_selection(writable_client, data, name, media):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    ready = upload(writable_client, thread, draft).json()
    rejected = upload(writable_client, thread, draft, data=data, name=name, media=media, key="bad")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "attachment_invalid"
    assert show_draft(writable_client, thread, draft)["attachments"] == [ready]


def test_http_upload_size_boundary(writable_client):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    accepted = upload(writable_client, thread, draft, data=b"x" * 15_000_000)
    assert accepted.status_code == 200
    rejected = upload(writable_client, thread, draft, data=b"x" * 15_000_001, key="too-large")
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "attachment_too_large"
    assert show_draft(writable_client, thread, draft)["attachments"] == [accepted.json()]


def test_bound_download_exact_bytes_headers_scope_and_private_content(writable, writable_client):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    content = b"Private fixture bytes 827"
    item = upload(writable_client, thread, draft, data=content, name="brief ü.txt").json()
    sent = send(writable_client, thread, draft=draft)
    assert sent.status_code == 200
    url = (
        f"{BASE}/{thread['thread_id']}/messages/{sent.json()['message_id']}"
        f"/attachments/{item['attachment_id']}"
    )
    response = writable_client.get(url, headers=authorized())
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "%C3%BC" in response.headers["content-disposition"]
    assert (
        writable_client.get(
            url.replace(sent.json()["message_id"], "message." + "0" * 26), headers=authorized()
        ).status_code
        == 409
    )
    for record in writable.writer().events.iter_events():
        assert content.decode() not in json.dumps(record.event)
    assert not {"width", "height", "path", "operator_id"}.intersection(item)


def test_actual_png_download(writable_client):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(stream, format="PNG")
    item = upload(
        writable_client, thread, draft, data=stream.getvalue(), name="image.png", media="image/png"
    ).json()
    sent = send(writable_client, thread, draft=draft).json()
    url = (
        f"{BASE}/{thread['thread_id']}/messages/{sent['message_id']}"
        f"/attachments/{item['attachment_id']}"
    )
    response = writable_client.get(url, headers=authorized())
    assert response.status_code == 200 and response.content == stream.getvalue()
    assert response.headers["content-type"] == "image/png"


def test_readonly_server_registers_only_conversation_reads(writable):
    app = create_app(
        writable,
        token="test-token",
        port=PORT,
        api_base="/api",
        read_only=True,
        manage_shutdown=False,
    )
    routes = [(route.path, route.methods) for route in app.routes if "conversations" in route.path]
    assert len(routes) == 3
    assert all(methods == {"GET"} for _, methods in routes)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.get(BASE, headers=authorized()).status_code == 200
        assert client.post(BASE, json={}, headers=authorized()).status_code == 405


def test_opaque_api_mount_owns_conversation_routes(writable):
    app = create_app(
        writable,
        token="test-token",
        exchange_code="test-code",
        port=PORT,
        api_base="/api/project-fixture",
        manage_shutdown=False,
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.get(BASE, headers=authorized()).status_code == 404
        response = client.post(
            "/api/project-fixture/conversations",
            json={"scope": {"kind": "project"}, "idempotency_key": "open"},
            headers=authorized(),
        )
        assert response.status_code == 200


def test_dynamic_writer_refusal_is_sanitized_for_upload(writable, writable_client, monkeypatch):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)

    def refuse():
        raise ConfigurationError("Synthetic private writer detail")

    monkeypatch.setattr(writable, "writer", refuse)
    response = upload(writable_client, thread, draft)
    assert response.status_code == 409
    assert "Synthetic" not in response.text


def test_http_failed_append_rebases_only_exact_frozen_content(
    writable, writable_client, monkeypatch
):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    assert upload(writable_client, thread, draft).status_code == 200
    events_type = type(writable.writer().events)
    original = events_type.append_event

    def fail(self, **kwargs):
        if kwargs.get("payload", {}).get("body") == "Frozen":
            raise OSError("Synthetic failure before reservation")
        return original(self, **kwargs)

    monkeypatch.setattr(events_type, "append_event", fail)
    uncertain = send(writable_client, thread, body="Frozen", draft=draft)
    assert uncertain.json()["error"]["code"] == "conversation_unavailable"
    assert show_draft(writable_client, thread, draft)["state"] == "binding"
    assert send(writable_client, thread, body="Concurrent", key="other").status_code == 200
    monkeypatch.setattr(events_type, "append_event", original)
    assert send(writable_client, thread, body="Changed", draft=draft).status_code == 409
    assert (
        send(writable_client, thread, body="Frozen", key="different-key", draft=draft).status_code
        == 409
    )
    recovered = send(writable_client, thread, body="Frozen", draft=draft)
    assert recovered.status_code == 200
    assert send(writable_client, thread, body="Frozen", draft=draft).json() == recovered.json()
    messages = writable.writer().snapshot().threads[thread["thread_id"]]["messages"]
    assert [item["body"] for item in messages] == ["Concurrent", "Frozen"]


def test_cross_thread_draft_and_attachment_access_refused(writable, writable_client):
    first = ensure(writable_client)
    task = writable.writer().create_task(
        title="Other", description="Other scope", acceptance_criteria=("Scoped",)
    )
    second = ensure(writable_client, {"kind": "task", "id": task["entity_ref"]["id"]}, "other")
    draft = reserve(writable_client, first)
    item = upload(writable_client, first, draft).json()
    assert (
        writable_client.get(
            f"{BASE}/{second['thread_id']}/drafts/{draft['draft_id']}", headers=authorized()
        ).status_code
        == 409
    )
    assert upload(writable_client, second, draft).status_code == 422
    sent = send(writable_client, first, draft=draft).json()
    wrong_url = (
        f"{BASE}/{second['thread_id']}/messages/{sent['message_id']}"
        f"/attachments/{item['attachment_id']}"
    )
    assert writable_client.get(wrong_url, headers=authorized()).status_code == 409
    assert show_draft(writable_client, first, draft)["state"] == "bound"


@pytest.mark.parametrize("change", [{"body": {}}, {"body": None}, {"expected_revision": []}])
def test_invalid_message_types_never_freeze_draft(writable_client, change):
    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    assert upload(writable_client, thread, draft).status_code == 200
    result = send(writable_client, thread, draft=draft, **change)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "conversation_invalid_message"
    assert show_draft(writable_client, thread, draft)["state"] == "ready"


def test_client_cannot_supply_canonical_attachments_or_storage_paths(writable, writable_client):
    thread = ensure(writable_client)
    before = tree_digest(writable.repo / ".agent-commons")
    response = send(writable_client, thread, attachments=[{"path": "../../synthetic-private"}])
    assert response.status_code == 409
    assert "synthetic-private" not in response.text
    assert tree_digest(writable.repo / ".agent-commons") == before


def test_missing_workspace_gates_reads_and_writes(tmp_path):
    from agent_commons.ui.context import UIContext
    from tests.ui.conftest import _git

    repo = tmp_path / "bare"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    context = UIContext(repo, state_root=tmp_path / "state", writer_session_id="unavailable")
    app = create_app(context, token="test-token", port=PORT, api_base="/api", manage_shutdown=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        for method, path in [
            ("GET", BASE),
            ("POST", BASE),
            ("POST", BASE + "/thread/drafts/draft/attachments"),
        ]:
            response = client.request(method, path, headers=authorized())
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "setup_uninitialized"


def test_upload_deadline_preserves_ready_files(writable_client, monkeypatch):
    from contextlib import asynccontextmanager

    from agent_commons.ui import conversation_routes

    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    first = upload(writable_client, thread, draft).json()

    @asynccontextmanager
    async def expired(seconds):
        raise TimeoutError("Synthetic bounded timeout")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(conversation_routes.asyncio, "timeout", expired)
        response = upload(writable_client, thread, draft, key="timeout")
    assert response.status_code == 408
    assert response.json()["error"]["code"] == "attachment_upload_timeout"
    assert show_draft(writable_client, thread, draft)["attachments"] == [first]


def test_other_operator_cannot_access_ready_draft(writable, writable_client):
    from agent_commons.ui.context import UIContext

    thread = ensure(writable_client)
    draft = reserve(writable_client, thread)
    first = upload(writable_client, thread, draft).json()
    manager = writable.writer()
    session = manager.start_session(
        stable_instance_id="other-operator",
        principal="other",
        client="codex",
        software="tests",
        role="operator",
    )
    context = UIContext(
        writable.repo, state_root=manager.paths.state_root, writer_session_id=session["session_id"]
    )
    app = create_app(context, token="test-token", port=PORT, api_base="/api", manage_shutdown=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        response = client.get(
            f"{BASE}/{thread['thread_id']}/drafts/{draft['draft_id']}", headers=authorized()
        )
        assert response.status_code == 409
        assert upload(client, thread, draft).status_code == 422
        assert send(client, thread, draft=draft).status_code == 409
    assert show_draft(writable_client, thread, draft)["attachments"] == [first]


def test_http_tail_before_and_proven_expired_draft(writable_client, monkeypatch):
    thread = ensure(writable_client)
    first = send(writable_client, thread).json()
    current = {**thread, "revision": first["revision"]}
    second = send(writable_client, current, key="second", body="Second").json()
    current["revision"] = second["revision"]
    url = f"{BASE}/{thread['thread_id']}/messages"
    tail = writable_client.get(url, params={"tail": True, "limit": 1}, headers=authorized())
    assert tail.status_code == 200
    assert tail.json()["messages"][0]["body"] == "Second"
    assert tail.json()["next_cursor"] is None
    before = writable_client.get(
        url, params={"before": tail.json()["previous_cursor"]}, headers=authorized()
    )
    assert [item["body"] for item in before.json()["messages"]] == ["Hello"]
    assert before.json()["previous_cursor"] is None
    assert (
        writable_client.get(
            url, params={"tail": True, "before": first["message_id"]}, headers=authorized()
        ).status_code
        == 409
    )
    draft = reserve(writable_client, current)
    assert upload(writable_client, current, draft).status_code == 200
    original = MessageAttachmentStore.__init__

    def expired(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.clock = lambda: draft["expires_at"] + 1

    monkeypatch.setattr(MessageAttachmentStore, "__init__", expired)
    response = send(writable_client, current, key="expired", draft=draft)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conversation_draft_expired"
    assert show_draft(writable_client, current, draft)["state"] == "ready"
    assert len(writable_client.get(url, headers=authorized()).json()["messages"]) == 2
