from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.mcp.live_preview_tools import register_live_preview_tools
from agent_commons.runtime.live_previews import LivePreviewRegistry
from tests.runtime.test_live_previews import AGENT, REV, RUN, SESSION, TASK, bound


def tool_fixture(tmp_path: Path):  # type: ignore[no-untyped-def]
    snapshot = bound()
    snapshot.delegations[RUN].update(state="active", purpose="implementation")
    manager = SimpleNamespace(
        read_only=False,
        session_id=SESSION,
        workspace_id=snapshot.workspace_id,
        sessions=SimpleNamespace(require_active=lambda sid: SimpleNamespace(session_id=sid)),
        paths=SimpleNamespace(state_root=tmp_path / "private"),
        repo_root=tmp_path / "project",
        snapshot=lambda: snapshot,
    )
    functions: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    def register(annotation: dict[str, bool], **options: Any):  # type: ignore[no-untyped-def]
        metadata.update(annotation=annotation, **options)

        def decorated(function):  # type: ignore[no-untyped-def]
            functions[function.__name__] = function
            return function

        return decorated

    worker = dict(snapshot.delegations[RUN])
    register_live_preview_tools(register, manager, lambda: worker, worker_binding=worker)
    return manager, snapshot, worker, functions["commons_publish_live_preview"], metadata


def call(tool, **updates):  # type: ignore[no-untyped-def]
    return tool(
        **{
            "title": "Frontend",
            "url": "http://127.0.0.1:5173/",
            "reported_state": "ready",
            "ttl_seconds": 900,
            "idempotency_key": "frontend-preview-1",
            **updates,
        }
    )


def test_worker_tool_derives_current_exact_refs_and_returns_no_navigation_or_private_metadata(
    tmp_path: Path,
) -> None:
    manager, snapshot, _, tool, metadata = tool_fixture(tmp_path)
    # The current revision is obtained at invocation, never from startup state.
    current = "evt." + "5" * 26
    snapshot.delegations[RUN]["effective_revision"] = current
    snapshot.entity_revision_actor_session_ids[("delegation", RUN, current)] = SESSION
    receipt = call(tool)
    assert metadata["worker_only"] is True
    assert metadata["worker_purposes"] == ("implementation", "verification")
    assert metadata["annotation"] == {
        "readOnlyHint": False,
        "idempotentHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    }
    assert set(inspect.signature(tool).parameters) == {
        "title",
        "url",
        "idempotency_key",
        "reported_state",
        "ttl_seconds",
    }
    assert set(receipt) == {
        "schema",
        "preview_id",
        "task_id",
        "task_revision",
        "delegation_id",
        "producer_agent_id",
        "reported_state",
        "state",
        "reachability",
        "expires_at",
    }
    assert receipt["task_id"] == TASK and receipt["task_revision"] == REV
    assert receipt["producer_agent_id"] == AGENT and receipt["delegation_id"] == RUN
    assert receipt["state"] == "reported_ready" and receipt["reachability"] == "unknown"
    assert "http" not in str(receipt) and str(tmp_path) not in str(receipt)
    reader = LivePreviewRegistry(
        manager.paths.state_root,
        project_root=manager.repo_root,
        workspace_id=manager.workspace_id,
        forbidden_ports=frozenset({8080}),
    )
    assert reader.list(snapshot, "task", TASK)[0].delegation_revision == current
    assert call(tool)["preview_id"] == receipt["preview_id"]
    with pytest.raises(TypeError):
        call(tool, producer_agent_id="agent." + "6" * 26)


def test_cli_publication_without_ui_port_cannot_authorize_same_port_navigation(
    tmp_path: Path,
) -> None:
    manager, snapshot, _, tool, _ = tool_fixture(tmp_path)
    call(tool, url="http://127.0.0.1:8080/")
    reader = LivePreviewRegistry(
        manager.paths.state_root,
        project_root=manager.repo_root,
        workspace_id=manager.workspace_id,
        forbidden_ports=frozenset({8080}),
    )
    row = reader.list(snapshot, "agent", AGENT)[0]
    assert row.state == "unavailable" and row.url is None


@pytest.mark.parametrize(
    "change",
    [
        "read_only",
        "session",
        "ended_session",
        "ended_delegation",
        "wrong_agent",
        "wrong_task",
        "stale_task",
        "purpose",
    ],
)
def test_worker_cannot_publish_outside_live_bound_scope_or_through_readonly_manager(
    tmp_path: Path, change: str
) -> None:
    manager, snapshot, worker, tool, _ = tool_fixture(tmp_path)
    if change == "read_only":
        manager.read_only = True
    elif change == "session":
        manager.session_id = "session." + "c" * 32
    elif change == "ended_session":

        def ended(_sid):  # type: ignore[no-untyped-def]
            raise RuntimeError("private session record")

        manager.sessions.require_active = ended
    elif change == "ended_delegation":
        snapshot.delegations[RUN]["state"] = "succeeded"
    elif change == "wrong_agent":
        snapshot.delegations[RUN]["agent_id"] = "agent." + "6" * 26
    elif change == "wrong_task":
        snapshot.delegations[RUN]["target_ref"] = {"kind": "task", "id": "task." + "6" * 26}
    elif change == "stale_task":
        snapshot.tasks[TASK]["effective_revision"] = "evt." + "6" * 26
    else:
        worker["purpose"] = "independent_review"
    with pytest.raises(LifecycleConflictError) as caught:
        call(tool)
    assert str(caught.value) == "Live preview publication is unavailable for this worker and task."
    assert not manager.paths.state_root.exists()


def test_invalid_requests_are_closed_and_rejected_without_storage(tmp_path: Path) -> None:
    manager, _, _, tool, _ = tool_fixture(tmp_path)
    with pytest.raises(ValidationError):
        call(tool, idempotency_key="bad/key")
    for updates in (
        {"url": "http://127.0.0.1:5173/?secret=private"},
        {"ttl_seconds": False},
        {"reported_state": "reachable"},
        {"title": "bad\nlabel"},
    ):
        with pytest.raises(LifecycleConflictError) as caught:
            call(tool, **updates)
        assert "private" not in str(caught.value)
    assert not manager.paths.state_root.exists()


def test_distinct_workers_can_reuse_natural_idempotency_key(tmp_path: Path) -> None:
    manager, snapshot, worker, tool, _ = tool_fixture(tmp_path)
    first = call(tool)
    other = "delegation." + "6" * 26
    snapshot.delegations[other] = {**snapshot.delegations[RUN], "id": other}
    snapshot.entity_revision_actor_session_ids[("delegation", other, REV)] = SESSION
    worker["id"] = other
    second_tools = []

    def register(_annotation, **_options):  # type: ignore[no-untyped-def]
        def capture(function):  # type: ignore[no-untyped-def]
            second_tools.append(function)
            return function

        return capture

    register_live_preview_tools(register, manager, lambda: worker, worker_binding=worker)
    second = call(second_tools[0])
    assert first["preview_id"] != second["preview_id"]
    assert second["delegation_id"] == other


def test_registration_freezes_worker_target_even_when_live_guard_returns_new_binding(
    tmp_path: Path,
) -> None:
    manager, snapshot, worker, tool, _ = tool_fixture(tmp_path)
    changed = "task." + "6" * 26
    snapshot.tasks[changed] = {"id": changed, "revision": REV}
    snapshot.entity_revision_actor_session_ids[("task", changed, REV)] = SESSION
    worker["target_ref"]["id"] = changed
    snapshot.delegations[RUN]["target_ref"] = worker["target_ref"]
    with pytest.raises(LifecycleConflictError):
        call(tool)
    assert not manager.paths.state_root.exists()
