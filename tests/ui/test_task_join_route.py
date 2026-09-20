"""The manual join route refuses before it writes (ADR 0021, WP-11.2)."""

from __future__ import annotations

from typing import Any

from agent_commons.ui.context import ledger_fingerprint
from tests.ui.conftest import authorized


def _task(client: Any) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={
            "title": "Join provenance from the panel",
            "description": "An existing task the operator attaches to an application.",
            "acceptance_criteria": ["The join is auditable."],
            "idempotency_key": "join-route-task",
        },
        headers=authorized(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return {"id": str(payload["entity_ref"]["id"]), "revision": str(payload["revision"])}


def test_join_route_refuses_bad_requests_without_touching_the_ledger(
    writable, writable_client
) -> None:
    task = _task(writable_client)
    before = ledger_fingerprint(writable.paths())
    application_id = "application." + ("c" * 64)
    refusals = (
        {"application_id": application_id, "expected_revision": task["revision"]},
        {"application_id": application_id, "reason": "no revision"},
        {
            "application_id": "not-an-application",
            "expected_revision": task["revision"],
            "reason": "invalid identity",
        },
        {
            "application_id": application_id,
            "expected_revision": task["revision"],
            "reason": "unknown application",
        },
    )
    for body in refusals:
        response = writable_client.post(
            f"/api/tasks/{task['id']}/application", json=body, headers=authorized()
        )
        assert response.status_code >= 400, (body, response.text)
        assert "error" in response.json(), response.text
    assert ledger_fingerprint(writable.paths()) == before, "a refused join never appends an event"


def test_join_route_is_absent_from_the_read_only_panel(client, context) -> None:
    before = ledger_fingerprint(context.paths())
    response = client.post(
        f"/api/tasks/task.{'0' * 26}/application",
        json={
            "application_id": "application." + ("d" * 64),
            "expected_revision": "evt.01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "reason": "read-only panel",
        },
        headers=authorized(),
    )
    assert response.status_code in (403, 404, 405), response.text
    assert ledger_fingerprint(context.paths()) == before
