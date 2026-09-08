from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from agent_commons.ui.output_routes import register_output_routes
from tests.services.test_outputs import AGENT, _bound


def _client(factory):  # type: ignore[no-untyped-def]
    app = FastAPI()

    async def require_session(x_session: str | None = Header(default=None)) -> None:
        if x_session != "allowed":
            raise HTTPException(status_code=401, detail="session required")

    register_output_routes(app, dependencies=[Depends(require_session)], manager_factory=factory)
    return TestClient(app)


def test_routes_require_auth_and_return_scoped_allowlisted_dtos(tmp_path: Path) -> None:
    manager, package, artifacts = _bound(tmp_path)
    client = _client(lambda: manager)
    query = {"scope_kind": "agent", "scope_id": AGENT}
    headers = {"X-Session": "allowed"}
    assert client.get("/api/outputs", params=query).status_code == 401
    assert client.get("/api/outputs/summary", params=query).status_code == 401
    assert manager.calls == 0
    summary = client.get("/api/outputs/summary", params=query, headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {
        "schema": "agent_commons.outputs-summary.v1",
        "scope": {"kind": "agent", "id": AGENT},
        "versions": "latest",
        "total": 1,
        "counts": {"unchecked": 1, "stale": 0, "unavailable": 0},
        "previews_verified": False,
    }
    result = client.get("/api/outputs", params=query, headers=headers)
    assert result.status_code == 200
    payload = result.json()
    assert payload["schema"] == "agent_commons.outputs.v1"
    assert payload["scope"] == {"kind": "agent", "id": AGENT}
    assert payload["truncated"] is False
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["state"] == "ready"
    assert item["task_id"] == package.draft.screens[0].producer_task_binding.identifier
    assert item["producer_agent_id"] == AGENT
    assert item["kind"] == "design_image"
    assert not ({"path", "source", "url", "actor", "metadata"} & set(item))
    assert str(artifacts[0][1]) not in result.text
    assert result.headers["Cache-Control"] == "no-store"
    assert result.headers["X-Content-Type-Options"] == "nosniff"
    assert client.post("/api/outputs", headers=headers).status_code == 405


def test_missing_and_invalid_queries_are_closed(tmp_path: Path) -> None:
    manager, _, _ = _bound(tmp_path)
    client = _client(lambda: manager)
    headers = {"X-Session": "allowed"}
    for route in ("/api/outputs", "/api/outputs/summary"):
        missing = client.get(route, headers=headers)
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "outputs_invalid_scope"
        foreign = client.get(
            route,
            params={"scope_kind": "agent", "scope_id": "agent." + "0" * 26},
            headers=headers,
        )
        assert foreign.status_code == 404
    bad_versions = client.get(
        "/api/outputs",
        params={"scope_kind": "agent", "scope_id": AGENT, "versions": "raw"},
        headers=headers,
    )
    assert bad_versions.status_code == 400


def test_factory_exception_never_crosses_browser_boundary() -> None:
    def broken():  # type: ignore[no-untyped-def]
        raise RuntimeError("sensitive source detail")

    client = _client(broken)
    response = client.get(
        "/api/outputs",
        params={"scope_kind": "agent", "scope_id": AGENT},
        headers={"X-Session": "allowed"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "outputs_unavailable"
    assert "sensitive" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_live_publication_is_operator_gated_and_returns_closed_scoped_metadata(
    tmp_path: Path,
) -> None:
    from agent_commons.runtime.live_previews import LivePreviewRegistry
    from tests.runtime.test_live_previews import AGENT as LIVE_AGENT
    from tests.runtime.test_live_previews import TASK, bound, payload

    snapshot = bound()
    clock = [1000.0]
    store = LivePreviewRegistry(
        tmp_path / "private",
        project_root=tmp_path / "project",
        workspace_id=snapshot.workspace_id,
        forbidden_ports=frozenset({8080}),
        clock=lambda: clock[0],
    )

    class Manager:
        def snapshot(self):  # type: ignore[no-untyped-def]
            return snapshot

    app = FastAPI()
    gates = []
    register_output_routes(
        app,
        dependencies=[],
        manager_factory=Manager,
        register_writes=True,
        live_registry_factory=lambda: store,
        writer_factory=Manager,
        authorize_publish=lambda: gates.append(True),
    )
    client = TestClient(app)
    first = client.post("/api/outputs/live-previews", json=payload())
    assert first.status_code == 200 and gates == [True]
    item = first.json()["item"]
    assert item["kind"] == "live_preview" and item["state"] == "reported_ready"
    assert item["reachability"] == "unknown"
    assert set(item) == {
        "kind",
        "output_id",
        "title",
        "task_id",
        "task_revision",
        "producer_agent_id",
        "producer_session_id",
        "producer_delegation_id",
        "delegation_revision",
        "url",
        "origin",
        "published_at",
        "expires_at",
        "reported_state",
        "state",
        "reachability",
        "latest",
        "version_count",
    }
    assert str(tmp_path) not in first.text and "idempotency_key" not in first.text
    query = {"scope_kind": "agent", "scope_id": LIVE_AGENT}
    summary = client.get("/api/outputs/summary", params=query).json()
    assert summary["total"] == 1 and summary["counts"]["unchecked"] == 1
    result = client.get("/api/outputs", params=query).json()
    assert result["items"] == [item]
    clock[0] += 61
    expired = client.get("/api/outputs", params={"scope_kind": "task", "scope_id": TASK}).json()[
        "items"
    ][0]
    assert expired["state"] == "expired" and expired["url"] is None
    summary = client.get("/api/outputs/summary", params=query).json()
    assert summary["total"] == 1 and summary["counts"]["unavailable"] == 1
    assert first.headers["Cache-Control"] == "no-store"
    assert client.post("/api/outputs/live-previews", content="x" * 8193).status_code == 413


def test_live_publication_defaults_disabled_and_gate_failure_never_opens_writer(
    tmp_path: Path,
) -> None:
    from tests.runtime.test_live_previews import payload

    def forbidden():  # type: ignore[no-untyped-def]
        raise AssertionError("writer should not be opened")

    client = _client(forbidden)
    assert client.post("/api/outputs/live-previews", json=payload()).status_code == 404
    response = client.post(
        "/api/outputs/live-previews", json=payload(), headers={"X-Session": "allowed"}
    )
    assert response.status_code == 404
    app = FastAPI()

    def deny() -> None:
        raise RuntimeError("private operator identity must remain hidden")

    register_output_routes(
        app,
        dependencies=[],
        manager_factory=forbidden,
        writer_factory=forbidden,
        register_writes=True,
        live_registry_factory=forbidden,
        authorize_publish=deny,
    )
    response = TestClient(app).post("/api/outputs/live-previews", json=payload())
    assert response.status_code == 409
    assert "identity" not in response.text and "writer" not in response.text
