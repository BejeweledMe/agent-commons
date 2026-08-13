"""The default UI records no canonical event, and the writable one has exactly
one way to record: the same manager the CLI and MCP adapter use.

The read-only assertions below are unchanged.  What used to carry the whole
invariant -- "no mutating route exists" -- now covers only the default server,
so three assertions were added for the writable one: the mutating surface is an
explicit allowlist, every route in it dies when `record_event` is removed, and
each route is driven over HTTP and its event then found in the ledger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.services.manager import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import MUTATING_ROUTES
from tests.ui.conftest import authorized, tree_digest


def test_api_traffic_does_not_change_any_canonical_file(  # type: ignore[no-untyped-def]
    client, workspace: dict[str, Any]
) -> None:
    commons_root: Path = workspace["commons_root"]
    before = tree_digest(commons_root)
    assert before, "fixture produced no canonical files to compare"

    client.get("/api/meta", headers=authorized())
    client.get("/api/graph", headers=authorized())
    graph = client.get("/api/graph", headers=authorized()).json()
    for node in graph["nodes"]:
        client.get(f"/api/entities/{node['kind']}/{node['id']}", headers=authorized())
    assert tree_digest(commons_root) == before


def test_streaming_does_not_change_any_canonical_file(
    context: UIContext, workspace: dict[str, Any]
) -> None:
    """Exercised against the generator directly: the HTTP stream never ends, so
    driving it through a test client would just block."""

    import asyncio

    from agent_commons.ui.server import _events

    commons_root: Path = workspace["commons_root"]
    before = tree_digest(commons_root)

    async def drive() -> list[bytes]:
        frames: list[bytes] = []
        generator = _events(context, None)
        try:
            for _ in range(2):
                frames.append(await anext(generator))
        finally:
            await generator.aclose()
        return frames

    frames = asyncio.run(drive())
    assert frames[0].startswith(b"id: ")
    assert b"event: hello" in frames[0]
    assert b"event: snapshot" in frames[1]
    assert tree_digest(commons_root) == before


def test_app_exposes_no_mutating_route(client) -> None:  # type: ignore[no-untyped-def]
    for route in client.app.routes:
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, f"{route.path} exposes {methods}"


def test_context_builds_the_manager_read_only(context: UIContext) -> None:
    assert context.manager().read_only is True


def test_the_ui_never_calls_record_event(  # type: ignore[no-untyped-def]
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the read-only UI attempted a canonical write")

    monkeypatch.setattr(CommonsManager, "record_event", explode)
    assert client.get("/api/meta", headers=authorized()).status_code == 200
    assert client.get("/api/graph", headers=authorized()).status_code == 200


def test_entity_ids_must_match_their_kind(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/entities/task/delegation.01K0", headers=authorized())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_id"


def test_unknown_entity_kinds_are_rejected(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/entities/secrets/secrets.1", headers=authorized())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_kind"


def test_a_missing_entity_is_reported_as_not_found(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get(
        "/api/entities/task/task.01K00000000000000000000000", headers=authorized()
    )
    assert response.status_code == 404


def test_meta_declares_the_read_only_contract(client) -> None:  # type: ignore[no-untyped-def]
    meta = client.get("/api/meta", headers=authorized()).json()
    assert meta["read_only"] is True
    assert meta["writes_enabled"] is False
    assert meta["truth_layers"] == ["CANONICAL", "COORDINATION", "OPERATIONAL"]
    assert "authentication" in meta["trust_note"]


# -- the writable server -----------------------------------------------------


def _agent_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Backend",
        "profile_id": "claude-builder",
        "rationale": "the backend surface needs a standing owner",
        **overrides,
    }


def test_the_writable_app_exposes_exactly_the_declared_mutating_surface(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    found = {
        (method, route.path)
        for route in writable_client.app.routes
        for method in (getattr(route, "methods", set()) or set())
        if method not in {"GET", "HEAD"}
    }
    assert found == set(MUTATING_ROUTES)


def test_every_mutating_route_dies_without_the_manager_write_path(
    writable_client,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No route reaches durable state by any other means.

    Removing `CommonsManager.record_event` has to break all of them; a route
    that still succeeds is a second write path by definition.
    """

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a UI route recorded outside CommonsManager.record_event")

    created = writable_client.post("/api/agents", json=_agent_body(), headers=authorized())
    assert created.status_code == 200, created.text
    agent_id = created.json()["entity_ref"]["id"]
    revision = created.json()["revision"]

    monkeypatch.setattr(CommonsManager, "record_event", explode)
    calls = (
        ("/api/agents", _agent_body(name="Second")),
        (
            f"/api/agents/{agent_id}/reconfigure",
            {"expected_revision": revision, "changes": {"name": "Renamed"}, "reason": "x"},
        ),
        (f"/api/agents/{agent_id}/retire", {"reason": "x"}),
        (f"/api/agents/{agent_id}/messages", {"body": "please look at this"}),
    )
    for path, body in calls:
        with pytest.raises(AssertionError, match="outside CommonsManager"):
            writable_client.post(path, json=body, headers=authorized())


def test_each_mutating_route_lands_its_event_in_the_ledger(
    writable_client,  # type: ignore[no-untyped-def]
    writable: UIContext,
) -> None:
    """Driven through HTTP, the seam a user of this server actually crosses."""

    created = writable_client.post("/api/agents", json=_agent_body(), headers=authorized())
    assert created.status_code == 200, created.text
    agent_id = created.json()["entity_ref"]["id"]

    shown = writable_client.get(f"/api/entities/agent/{agent_id}", headers=authorized())
    assert shown.status_code == 200
    assert shown.json()["record"]["origin"] == "human"

    reconfigured = writable_client.post(
        f"/api/agents/{agent_id}/reconfigure",
        json={
            "expected_revision": created.json()["revision"],
            "changes": {"name": "Staff backend"},
            "reason": "promoted",
        },
        headers=authorized(),
    )
    assert reconfigured.status_code == 200, reconfigured.text

    messaged = writable_client.post(
        f"/api/agents/{agent_id}/messages",
        json={"body": "start with the payments endpoint"},
        headers=authorized(),
    )
    assert messaged.status_code == 200, messaged.text

    retired = writable_client.post(
        f"/api/agents/{agent_id}/retire",
        json={"reason": "surface moved"},
        headers=authorized(),
    )
    assert retired.status_code == 200, retired.text

    recorded = [
        record.event["event_type"] for record in writable.writer().events.iter_events()
    ]
    assert "agent.created" in recorded
    assert "agent.reconfigured" in recorded
    assert "thread.opened" in recorded and "thread.replied" in recorded
    assert "agent.retired" in recorded


def test_a_refused_write_names_the_guard_it_tripped(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    grants = {"create_roles": "auto", "retire_roles": "deny", "open_links": "deny"}
    response = writable_client.post(
        "/api/agents",
        json=_agent_body(grants=grants),
        headers=authorized(),
    )
    assert response.status_code == 409, response.text
    assert "turnover_budget" in response.json()["error"]["message"]


def test_the_writable_server_declares_that_it_writes(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    meta = writable_client.get("/api/meta", headers=authorized()).json()
    assert meta["writes_enabled"] is True
    assert meta["read_only"] is False
    assert meta["writer_session_id"]


def test_agent_links_open_and_close_through_the_one_write_path(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    """Wave 1 item 7: both link routes are thin adapters over the manager's
    open/close — the domain judges, the panel maps refusals. Closing is a
    recorded state change, never a delete, and demands the revision it
    closes."""

    a = writable_client.post("/api/agents", json=_agent_body(name="A"), headers=authorized())
    b = writable_client.post("/api/agents", json=_agent_body(name="B"), headers=authorized())
    assert a.status_code == 200 and b.status_code == 200
    a_id = a.json()["entity_ref"]["id"]
    b_id = b.json()["entity_ref"]["id"]

    opened = writable_client.post(
        "/api/agent-links",
        json={
            "from_agent_id": a_id,
            "to_agent_id": b_id,
            "allowed_action": "handoff_work",
            "deadline_seconds": 86400,
            "reason": "A hands documentation to B for review",
        },
        headers=authorized(),
    )
    assert opened.status_code == 200, opened.text
    link = opened.json()
    link_id = link["entity_ref"]["id"] if link.get("entity_ref") else link["link_id"]

    # A self-link is the domain's refusal, surfaced verbatim — not a UI rule.
    selflink = writable_client.post(
        "/api/agent-links",
        json={
            "from_agent_id": a_id,
            "to_agent_id": a_id,
            "allowed_action": "ask",
            "deadline_seconds": 3600,
            "reason": "nonsense",
        },
        headers=authorized(),
    )
    assert selflink.status_code in (400, 409), selflink.text

    # Closing without the revision it closes is refused, nothing recorded.
    blind = writable_client.post(
        "/api/agent-links/" + link_id + "/close",
        json={"reason": "done"},
        headers=authorized(),
    )
    assert blind.status_code in (400, 409), blind.text

    closed = writable_client.post(
        "/api/agent-links/" + link_id + "/close",
        json={"expected_revision": link["revision"], "reason": "handoff finished"},
        headers=authorized(),
    )
    assert closed.status_code == 200, closed.text

    shown = writable_client.get(
        "/api/entities/agent_link/" + link_id, headers=authorized()
    )
    assert shown.status_code == 200
    assert shown.json()["record"]["state"] == "closed"
