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
from agent_commons.ui.server import (
    CATALOG_ROUTES,
    LAUNCH_ROUTES,
    MUTATING_ROUTES,
    SETUP_ROUTES,
)
from tests.ui.conftest import authorized, expected_surface, mutating_surface, tree_digest

# The acceptance chain's setup lives beside the acceptance tests rather than
# being copied here: one recipe for "a task with a qualifying review", so a
# change to the chain cannot leave this invariant asserting against a shape the
# panel no longer produces.
from tests.ui.test_acceptance_chain import (
    approve_independently,
    create_task,
    send_for_review,
)


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


def test_read_endpoints_preserve_their_top_level_json_shapes(  # type: ignore[no-untyped-def]
    client, populated
) -> None:
    meta = client.get("/api/meta", headers=authorized()).json()
    assert set(meta) == {
        "schema",
        "agent_commons_version",
        "workspace_id",
        "repo",
        "read_only",
        "writes_enabled",
        "writer_session_id",
        "session_refusal",
        "server_instance_id",
        "trust_note",
        "truth_layers",
    }
    # Present in every state and null in the ordinary one, so the tab tests it
    # unconditionally rather than probing for a key that may not be there.
    assert meta["session_refusal"] is None

    graph = client.get("/api/graph", headers=authorized()).json()
    assert set(graph) == {
        "schema",
        "workspace_id",
        "generated_at",
        "ledger_fingerprint",
        "server_instance_id",
        "seq",
        "nodes",
        "edges",
        "counts",
        "awaiting_human",
        "issues",
        "warnings",
        "read_diagnostics",
        "limits",
    }
    first = graph["nodes"][0]
    entity = client.get(f"/api/entities/{first['kind']}/{first['id']}", headers=authorized()).json()
    assert set(entity) == {"schema", "kind", "id", "record"}

    attention = client.get("/api/attention", headers=authorized()).json()
    assert set(attention) == {"items", "count", "writes_enabled"}
    assert isinstance(client.get("/api/runs", headers=authorized()).json(), list)


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
    writable: UIContext,
) -> None:
    # Both halves on purpose: the derived expectation reads the same property
    # `create_app` reads, so on its own it could only ever agree with itself.
    # The literal union of the four declared tuples is what actually pins the
    # surface -- a route silently dropped from registration *and* from the
    # declaration would pass the first comparison and fail this one.
    assert mutating_surface(writable_client.app) == expected_surface(writable)
    assert mutating_surface(writable_client.app) == (
        set(MUTATING_ROUTES) | set(LAUNCH_ROUTES) | set(SETUP_ROUTES) | set(CATALOG_ROUTES)
    )


def create_agent(client, *, name: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    response = client.post("/api/agents", json=_agent_body(name=name), headers=authorized())
    assert response.status_code == 200, response.text
    return {"id": response.json()["entity_ref"]["id"], "revision": response.json()["revision"]}


def test_every_mutating_route_dies_without_the_manager_write_path(
    writable_client,  # type: ignore[no-untyped-def]
    workspace: dict[str, Any],
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
    # Acceptance is the newest write and the one with the most machinery behind
    # it, so it is named here explicitly rather than trusted to be thin.  It has
    # to be driven all the way to a qualifying review first: refused early, it
    # would never reach the write path this test removes, and the route would
    # look sealed for the wrong reason.
    task = create_task(writable_client)
    chain = send_for_review(writable_client, task["id"], task["revision"]).json()
    approve_independently(
        workspace,
        review_id=chain["review_id"],
        review_revision=chain["review_revision"],
        target_revision=chain["task_revision"],
    )

    second_task = create_task(writable_client, title="Вторая задача")
    other_id = create_agent(writable_client, name="Other")["id"]
    link = writable_client.post(
        "/api/agent-links",
        json={
            "from_agent_id": agent_id,
            "to_agent_id": other_id,
            "allowed_action": "ask",
            "reason": "so the close route has something to close",
        },
        headers=authorized(),
    ).json()
    # The chat route names its field `message`, not `body`; a real thread id is
    # needed so the reply route below is exercised against something that exists.
    thread = writable_client.post(
        "/api/chat", json={"subject": "Kickoff", "message": "start"}, headers=authorized()
    ).json()
    thread_id = thread.get("entity_ref", {}).get("id") or thread.get("thread_id")
    thread_revision = str(thread.get("revision", ""))

    monkeypatch.setattr(CommonsManager, "record_event", explode)
    # Every route in the sealed tuple, not a sample of it: the docstring's claim
    # is only true if the list below is the list up there. A route missing from
    # here is a route that could stop being thin without this test noticing.
    calls = (
        ("/api/agents", _agent_body(name="Second")),
        (
            f"/api/agents/{agent_id}/reconfigure",
            {"expected_revision": revision, "changes": {"name": "Renamed"}, "reason": "x"},
        ),
        (f"/api/agents/{agent_id}/retire", {"reason": "x"}),
        (f"/api/agents/{agent_id}/messages", {"body": "please look at this"}),
        ("/api/chat", {"subject": "Second chat", "message": "hello"}),
        (
            f"/api/chat/{thread_id}/messages",
            {"expected_revision": thread_revision, "message": "another line"},
        ),
        (
            "/api/agent-links",
            {"from_agent_id": agent_id, "to_agent_id": other_id, "reason": "x"},
        ),
        (
            f"/api/agent-links/{link['entity_ref']['id']}/close",
            {"expected_revision": link["revision"], "reason": "done"},
        ),
        ("/api/tasks", {"title": "Third", "description": "d", "acceptance_criteria": ["c"]}),
        (
            f"/api/tasks/{second_task['id']}/revise",
            {
                "expected_revision": second_task["revision"],
                "changes": {"description": "revised description"},
            },
        ),
        (
            f"/api/tasks/{second_task['id']}/review-request",
            {"expected_revision": second_task["revision"]},
        ),
        (
            f"/api/tasks/{task['id']}/accept",
            {"expected_revision": chain["task_revision"], "summary": "looks right to me"},
        ),
        (
            f"/api/tasks/{task['id']}/reopen",
            {"expected_revision": chain["task_revision"], "reason": "one more pass"},
        ),
    )
    # Two routes refuse on a missing subject before they reach any write, so
    # driving them here would prove nothing about the write path; they are
    # exercised end to end elsewhere (a real proposal thread in
    # tests/ui/test_role_graph.py, a real pending operation in
    # tests/ui/test_blockers.py). Naming them keeps the equality below exact:
    # a NEW route cannot be added without being either exercised or exempted.
    exempt = {
        ("POST", "/api/agents/proposals/{thread_id}/approve"),
        ("POST", "/api/agents/proposals/{thread_id}/decline"),
        ("POST", "/api/operations/{operation_id}/answer"),
    }
    covered = {
        ("POST", "/api/agents"),
        ("POST", "/api/agents/{agent_id}/reconfigure"),
        ("POST", "/api/agents/{agent_id}/retire"),
        ("POST", "/api/agents/{agent_id}/messages"),
        ("POST", "/api/chat"),
        ("POST", "/api/chat/{thread_id}/messages"),
        ("POST", "/api/agent-links"),
        ("POST", "/api/agent-links/{link_id}/close"),
        ("POST", "/api/tasks"),
        ("POST", "/api/tasks/{task_id}/revise"),
        ("POST", "/api/tasks/{task_id}/review-request"),
        ("POST", "/api/tasks/{task_id}/accept"),
        ("POST", "/api/tasks/{task_id}/reopen"),
    }
    assert covered | exempt == set(MUTATING_ROUTES)
    assert len(calls) == len(covered)
    for path, body in calls:
        try:
            writable_client.post(path, json=body, headers=authorized())
        except AssertionError as exc:
            assert "outside CommonsManager" in str(exc), path
        else:
            raise AssertionError(f"{path} reached a terminal answer without the write path")


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

    recorded = [record.event["event_type"] for record in writable.writer().events.iter_events()]
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

    shown = writable_client.get("/api/entities/agent_link/" + link_id, headers=authorized())
    assert shown.status_code == 200
    assert shown.json()["record"]["state"] == "closed"


def test_a_task_created_from_the_panel_lands_on_the_board(
    writable_client,  # type: ignore[no-untyped-def]
) -> None:
    """PM cold-run blocker: the chat form records a thread, not a task — a
    manager could not put work on the board at all. POST /api/tasks is the
    door, a thin adapter over create_task, sealed into the mutating surface."""

    created = writable_client.post(
        "/api/tasks",
        json={
            "title": "Добавить карту проезда",
            "description": "Встроить карту на страницу-визитку",
            "acceptance_criteria": ["на странице есть карта с меткой адреса"],
        },
        headers=authorized(),
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["entity_ref"]["id"]

    shown = writable_client.get("/api/entities/task/" + task_id, headers=authorized())
    assert shown.status_code == 200
    record = shown.json()["record"]
    assert record["title"] == "Добавить карту проезда"
    assert record["state"] == "ready"

    graph = writable_client.get("/api/graph", headers=authorized()).json()
    assert task_id in {node["id"] for node in graph["nodes"]}
