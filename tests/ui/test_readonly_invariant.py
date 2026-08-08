"""The UI records no canonical event.  That is mechanical, not a convention."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.services.manager import CommonsManager
from agent_commons.ui.context import UIContext
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
