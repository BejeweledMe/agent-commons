"""Regression coverage for the authenticated multi-project UI host."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_commons.services.manager import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.project_host import ProjectHost
from agent_commons.ui.project_operations import ProjectOperations
from agent_commons.ui.project_registry import ProjectRegistry
from agent_commons.ui.security import AUTH_EXCHANGE_PATH
from agent_commons.ui.server import create_app
from agent_commons.ui.session_owner import ProjectSessionOwner

PORT = 51234
_COOKIE = {"Cookie": "agent_commons_ui_session=test-token"}


def _project(operations: ProjectOperations, path: Path, name: str) -> dict[str, object]:
    inspection = operations.inspect("new", str(path), name)
    return operations.create(inspection["inspection_id"], f"create-{name}")["project"]


def _host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, read_only: bool = False
) -> tuple[ProjectHost, dict[str, object], dict[str, object]]:
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    registry = ProjectRegistry(tmp_path / "private-registry")
    operations = ProjectOperations(registry)
    tmp_path.mkdir(parents=True, exist_ok=True)
    first = _project(operations, tmp_path / "first", "First")
    second = _project(operations, tmp_path / "second", "Second")
    first_repo = tmp_path / "first"

    def writer_context(repo: Path, state: Path | None = None) -> UIContext:
        manager = CommonsManager(repo, state_root=state)
        session = manager.start_session(
            stable_instance_id=f"project-host-test-{repo.name}",
            principal="operator",
            client="codex",
            software="test",
            role="operator",
        )
        return UIContext(repo, state_root=state, writer_session_id=str(session["session_id"]))

    host = ProjectHost(
        registry,
        operations,
        writer_context(first_repo),
        read_only=read_only,
        context_factory=writer_context,
    )
    return host, first, second


def test_host_requires_one_outer_session_and_never_falls_back_for_unknown_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, first, second = _host(tmp_path, monkeypatch)
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.get("/api/projects").status_code == 401
        listing = client.get("/api/projects", headers=_COOKIE)
        assert listing.status_code == 200
        assert listing.json()["default_project_id"] == first["id"]

        second_meta = client.get(f"/api/projects/{second['id']}/meta", headers=_COOKIE)
        assert second_meta.status_code == 200
        assert second_meta.json()["workspace_id"] == second["workspace_id"]

        unknown = client.get("/api/projects/project." + "0" * 32 + "/meta", headers=_COOKIE)
        assert unknown.status_code == 404
        encoded = client.get(f"/api/projects/{second['id']}%2Fother/meta", headers=_COOKIE)
        assert encoded.status_code == 404


def test_host_keeps_non_repository_startup_in_first_run_state(tmp_path: Path) -> None:
    repo = tmp_path / "not-a-repository"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "registry")
    host = ProjectHost(
        ProjectRegistry(tmp_path / "registry"), ProjectOperations(registry), UIContext(repo)
    )

    app = host.create_app(token="test-token", port=PORT, api_base="/api")

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        response = client.get("/api/meta", headers=_COOKIE)
    assert response.status_code == 200
    assert host.default_project_id is None


def test_host_registry_writes_are_read_only_guarded_and_closed_to_known_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, _, _ = _host(tmp_path, monkeypatch, read_only=True)
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        refused = client.post(
            "/api/projects/inspect",
            headers=_COOKIE,
            json={"mode": "new", "path": str(tmp_path / "third"), "name": "Third"},
        )
        assert refused.status_code == 403

    writable, _, _ = _host(tmp_path / "writable", monkeypatch)
    writable_app = writable.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(writable_app, base_url=f"http://127.0.0.1:{PORT}") as client:
        refused = client.post(
            "/api/projects/inspect",
            headers=_COOKIE,
            json={"mode": "new", "path": str(tmp_path / "third"), "name": "Third", "extra": 1},
        )
        assert refused.status_code == 400
        too_large = client.post(
            "/api/projects/inspect",
            headers={**_COOKIE, "content-length": str(16 * 1024 + 1)},
            content=b"{" + b" " * (16 * 1024) + b"}",
        )
        assert too_large.status_code == 400


def test_read_only_host_removes_project_write_routes_even_with_a_writer_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    project = _project(operations, tmp_path / "repo", "Read only")
    repo = tmp_path / "repo"
    session = CommonsManager(repo).start_session(
        stable_instance_id="read-only-host-writer",
        principal="operator",
        client="codex",
        software="test",
        role="operator",
    )
    host = ProjectHost(
        registry,
        operations,
        UIContext(repo, writer_session_id=str(session["session_id"])),
        read_only=True,
    )
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    body = {
        "title": "Must not write",
        "description": "The host contract overrides a supplied writer context.",
        "acceptance_criteria": ["No event is recorded."],
    }
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.post("/api/tasks", headers=_COOKIE, json=body).status_code == 404
        assert (
            client.post(
                f"/api/projects/{project['id']}/tasks", headers=_COOKIE, json=body
            ).status_code
            == 404
        )
    handle = host._handles[str(project["id"])]
    assert all(
        "POST" not in getattr(route, "methods", set())
        for route in host.project_app(handle).router.routes
    )
    assert not any(
        "POST" in getattr(route, "methods", set())
        and str(getattr(route, "path", "")).startswith("/api/library")
        for route in app.router.routes
    )
    assert not CommonsManager(repo, read_only=True).snapshot().tasks


def test_host_child_route_inventory_has_no_second_auth_exchange_or_static_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, _, second = _host(tmp_path, monkeypatch)
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.get(f"/api/projects/{second['id']}/meta", headers=_COOKIE).status_code == 200
    handle = host._handles[str(second["id"])]
    paths = {getattr(route, "path", "") for route in host.project_app(handle).router.routes}
    legacy = create_app(handle.context, token="test-token", port=PORT, api_base="/api")
    expected_logical_paths = {
        str(route.path).removeprefix("/api")
        for route in legacy.router.routes
        if str(getattr(route, "path", "")).startswith("/api/")
    }
    expected_logical_paths.discard(AUTH_EXCHANGE_PATH.removeprefix("/api"))
    assert paths == expected_logical_paths
    assert "/meta" in paths and "/stream" in paths
    assert "/api/auth/exchange" not in paths
    assert "/" not in paths
    assert len(host._children) == 1


def test_host_shutdown_waits_for_owned_bounded_thread_before_closing_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    repo = tmp_path / "workspace"
    repo.mkdir()
    import subprocess

    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=repo, check=True)
    CommonsManager.initialize(repo, integrations=())
    owner = ProjectSessionOwner(repo, heartbeat_interval_seconds=3600)
    context = UIContext(repo, session_owner=owner)
    registry = ProjectRegistry(tmp_path / "registry")
    operations = ProjectOperations(registry)
    host = ProjectHost(registry, operations, context)
    host.create_app(token="test-token", port=PORT, api_base="/api")
    owner.ensure_active()
    gate = threading.Event()
    worker = threading.Thread(target=gate.wait, daemon=False)
    context._launch_coordinator._launch_threads.append(worker)
    worker.start()
    stopped = threading.Thread(target=host.shutdown)
    stopped.start()
    time.sleep(0.05)
    assert stopped.is_alive()
    assert owner.session_id is not None
    gate.set()
    stopped.join(timeout=2)
    assert not stopped.is_alive()
    assert owner.session_id is None


def test_lifespan_closes_all_host_admission_gates_before_any_project_joins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _, second = _host(tmp_path, monkeypatch)
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    gates: set[str] = set()
    joined: list[str] = []

    class Coordinator:
        def __init__(self, project_id: str) -> None:
            self.project_id = project_id

        def begin_draining(self) -> None:
            gates.add(self.project_id)

        def shutdown(self, *, timeout: float | None = None) -> dict[str, int]:
            assert gates == set(host._handles)
            joined.append(self.project_id)
            return {"live": 0}

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert client.get(f"/api/projects/{second['id']}/meta", headers=_COOKIE).status_code == 200
        for project_id, handle in host._handles.items():
            handle.context._launch_coordinator = Coordinator(project_id)
    assert set(joined) == set(host._handles)


def test_host_shutdown_keeps_owner_heartbeat_and_lock_for_requested_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    repo = tmp_path / "requested-work"
    repo.mkdir()
    import subprocess

    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=repo, check=True)
    CommonsManager.initialize(repo, integrations=())
    owner = ProjectSessionOwner(repo, heartbeat_interval_seconds=3600)
    session_id = owner.ensure_active()
    writer = CommonsManager(repo, session_id=session_id)
    task = writer.create_task(
        title="Live request",
        description="Host shutdown must not orphan it.",
        acceptance_criteria=("Owner remains available.",),
    )
    requested = writer.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    registry = ProjectRegistry(tmp_path / "registry")
    host = ProjectHost(
        ProjectRegistry(tmp_path / "registry"),
        ProjectOperations(registry),
        UIContext(repo, session_owner=owner),
    )
    host.create_app(token="test-token", port=PORT, api_base="/api")

    outcome = host.shutdown()

    assert outcome["projects"]
    project_outcome = next(iter(outcome["projects"].values()))
    assert project_outcome["closed"] is False
    assert owner._panel_lock_fd is not None
    assert owner._thread is not None and owner._thread.is_alive()
    writer.mark_delegation_needs_operator(
        str(requested["entity_ref"]["id"]),
        str(requested["revision"]),
        reason_code="launch_failed",
        summary="Test cleanup after retained ownership.",
        idempotency_key="requested-work-cleanup",
    )
    assert owner.shutdown()["closed"] is True


def test_native_picker_is_authenticated_nonmutating_and_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.ui.conftest import tree_digest

    host, first, _ = _host(tmp_path, monkeypatch)
    calls = []
    result = {
        "schema": "agent_commons.project_folder_selection.v1",
        "status": "selected",
        "path": str(tmp_path / "first"),
        "name": "first",
    }

    def pick(purpose: object) -> dict[str, object]:
        calls.append(purpose)
        return result

    monkeypatch.setattr(host._folder_picker, "pick", pick)
    before = tree_digest(tmp_path / "first" / ".agent-commons")
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        assert (
            client.post("/api/projects/pick-folder", json={"purpose": "existing"}).status_code
            == 401
        )
        assert not calls
        assert (
            client.post(
                "/api/projects/pick-folder",
                headers=_COOKIE,
                json={"purpose": "existing", "path": "/untrusted"},
            ).status_code
            == 400
        )
        with host._picker_guard:
            busy = client.post(
                "/api/projects/pick-folder", headers=_COOKIE, json={"purpose": "existing"}
            )
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "project_picker_busy"
        assert not calls
        selected = client.post(
            "/api/projects/pick-folder", headers=_COOKIE, json={"purpose": "existing"}
        )
        assert selected.status_code == 200
        assert selected.json() == result
        assert selected.headers["cache-control"] == "no-store"
        assert client.get(f"/api/projects/{first['id']}/meta", headers=_COOKIE).status_code == 200
    assert calls == ["existing"]
    assert tree_digest(tmp_path / "first" / ".agent-commons") == before


def test_native_picker_readonly_refusal_precedes_host_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _, _ = _host(tmp_path, monkeypatch, read_only=True)

    def forbidden(_purpose: object) -> dict[str, object]:
        raise AssertionError("read-only host opened a chooser")

    monkeypatch.setattr(host._folder_picker, "pick", forbidden)
    app = host.create_app(token="test-token", port=PORT, api_base="/api")
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        response = client.post(
            "/api/projects/pick-folder", headers=_COOKIE, json={"purpose": "parent"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "read_only"
