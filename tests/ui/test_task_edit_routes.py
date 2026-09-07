from __future__ import annotations

import asyncio

from starlette.requests import Request

from agent_commons.ui.security import SESSION_COOKIE_NAME
from agent_commons.ui.task_edit_routes import MAX_TASK_EDIT_BYTES, _bounded_body

HEADERS = {"Cookie": f"{SESSION_COOKIE_NAME}=test-token", "Origin": "http://127.0.0.1:51234"}


def _task(writable):  # type: ignore[no-untyped-def]
    return writable.writer().create_task(
        title="Complete text",
        description="Long description " * 200,
        acceptance_criteria=("Complete criterion",),
        idempotency_key="editor-task",
    )


def test_editor_routes_require_authentication_and_return_complete_owned_content(
    writable, writable_client
):  # type: ignore[no-untyped-def]
    created = _task(writable)
    identifier = created["entity_ref"]["id"]
    path = f"/api/work/tasks/{identifier}/edit-detail"
    assert writable_client.get(path).status_code == 401
    response = writable_client.get(path, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "Long description " * 200
    assert body["revision"] == created["revision"]
    assert body["editable"] is True and body["cancellable"] is True
    assert set(body) == {
        "schema",
        "task_id",
        "revision",
        "title",
        "description",
        "acceptance_criteria",
        "dependencies",
        "state",
        "editable",
        "cancellable",
        "refusal_code",
    }


def test_edit_route_preserves_cas_and_cancel_keeps_record(writable, writable_client):  # type: ignore[no-untyped-def]
    created = _task(writable)
    identifier = created["entity_ref"]["id"]
    path = f"/api/work/tasks/{identifier}"
    request = {
        "expected_revision": created["revision"],
        "idempotency_key": "ui-edit",
        "changes": {"title": "Revised title"},
    }
    first = writable_client.post(path + "/edit", headers=HEADERS, json=request)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["action"] == "revised"
    assert set(result) == {"schema", "task_id", "revision", "action"}
    stale = writable_client.post(
        path + "/edit",
        headers=HEADERS,
        json={**request, "idempotency_key": "stale-edit", "changes": {"title": "Stale overwrite"}},
    )
    assert stale.status_code == 409
    retry = writable_client.post(path + "/edit", headers=HEADERS, json=request)
    assert retry.json() == result
    cancel = writable_client.post(
        path + "/cancel",
        headers=HEADERS,
        json={
            "expected_revision": result["revision"],
            "idempotency_key": "ui-cancel",
            "reason": "This work is no longer needed.",
        },
    )
    assert cancel.status_code == 200, cancel.text
    detail = writable_client.get(path + "/edit-detail", headers=HEADERS).json()
    assert detail["state"] == "cancelled" and detail["title"] == "Revised title"
    assert detail["editable"] is False and detail["cancellable"] is False


def test_editor_rejects_oversized_and_ambiguous_bodies_before_writing(writable, writable_client):  # type: ignore[no-untyped-def]
    created = _task(writable)
    identifier = created["entity_ref"]["id"]
    path = f"/api/work/tasks/{identifier}"
    for action in ("edit", "cancel"):
        oversized = writable_client.post(
            path + "/" + action, headers=HEADERS, content=b"x" * (MAX_TASK_EDIT_BYTES + 1)
        )
        assert oversized.status_code == 413
        for raw in (b'{"changes":{},"changes":{}}', b"[]", b'{"changes":NaN}', b"{broken"):
            invalid = writable_client.post(path + "/" + action, headers=HEADERS, content=raw)
            assert invalid.status_code == 422, invalid.text
    assert writable.manager().snapshot().tasks[identifier]["revision"] == created["revision"]


def test_unrelated_authority_fields_cannot_be_edited(writable, writable_client):  # type: ignore[no-untyped-def]
    created = _task(writable)
    identifier = created["entity_ref"]["id"]
    response = writable_client.post(
        f"/api/work/tasks/{identifier}/edit",
        headers=HEADERS,
        json={
            "expected_revision": created["revision"],
            "idempotency_key": "authority",
            "changes": {"owner_session_id": "session.fake", "state": "accepted"},
        },
    )
    assert response.status_code == 409
    assert writable.manager().snapshot().tasks[identifier]["revision"] == created["revision"]


def test_stream_limit_stops_before_reading_remaining_chunks() -> None:
    chunks = [b"x" * MAX_TASK_EDIT_BYTES, b"x", b"must not be read"]
    consumed = 0

    async def receive() -> dict:
        nonlocal consumed
        chunk = chunks[consumed]
        consumed += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    request = Request({"type": "http"}, receive)
    result = asyncio.run(_bounded_body(request))
    assert result.status_code == 413
    assert consumed == 2
