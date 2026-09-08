"""Fresh projects use the same private collaboration store in HTTP and MCP."""

from __future__ import annotations

import io
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_commons.runtime.collaboration_storage import collaboration_state_root
from agent_commons.runtime.live_previews import LivePreviewRegistry
from agent_commons.services.manager import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from tests.mcp.test_live_preview_tools import call, tool_fixture
from tests.services.test_conversations import (
    test_addressed_mcp_delivers_real_image_and_explicit_ack as exercise_mcp_image,
)
from tests.ui.conftest import PORT, authorized
from tests.ui.test_conversation_routes import BASE, ensure, reserve, send, upload


@pytest.fixture
def default_manager(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_COMMONS_STATE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_COMMONS_STATE_BASE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "user-state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    CommonsManager.initialize(repo, integrations=(), workspace_name="default-storage-test")
    manager = CommonsManager(repo)
    assert manager.paths.state_root.is_relative_to(repo)
    session = manager.start_session(
        stable_instance_id="private-root-operator",
        principal="operator",
        client="codex",
        software="tests",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def test_fresh_project_http_text_results_and_real_image_upload(default_manager):
    manager = default_manager
    context = UIContext(manager.repo_root, writer_session_id=manager.session_id)
    app = create_app(
        context, token="test-token", exchange_code="test-exchange-code", port=PORT, api_base="/api"
    )
    task = manager.create_task(
        title="Results", description="Inspect outputs", acceptance_criteria=("Read outputs",)
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        thread = ensure(client)
        text = send(client, thread)
        assert text.status_code == 200, text.text
        for path in ("/api/outputs", "/api/outputs/summary"):
            result = client.get(
                path,
                params={"scope_kind": "task", "scope_id": task["entity_ref"]["id"]},
                headers=authorized(),
            )
            assert result.status_code == 200, result.text
        thread["revision"] = text.json()["revision"]
        draft = reserve(client, thread)
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), (23, 57, 91)).save(stream, format="PNG")
        data = stream.getvalue()
        uploaded = upload(client, thread, draft, data=data, name="fixture.png", media="image/png")
        assert uploaded.status_code == 200, uploaded.text
        sent = send(client, thread, key="image", body="", draft=draft)
        assert sent.status_code == 200, sent.text
        attachment_url = (
            f"{BASE}/{thread['thread_id']}/messages/{sent.json()['message_id']}"
            f"/attachments/{uploaded.json()['attachment_id']}"
        )
        downloaded = client.get(attachment_url, headers=authorized())
        assert downloaded.status_code == 200 and downloaded.content == data
        assert (
            client.get(f"{BASE}/{thread['thread_id']}/messages", headers=authorized()).status_code
            == 200
        )
    private = collaboration_state_root(manager)
    assert private == collaboration_state_root(context.manager())
    assert not private.is_relative_to(manager.repo_root)
    assert any(p.read_bytes() == data for p in private.rglob("*") if p.is_file())
    assert all(p.read_bytes() != data for p in manager.repo_root.rglob("*") if p.is_file())
    assert not (manager.paths.state_root / "runtime/message-delivery").exists()


def test_default_project_mcp_image_and_receipts_share_http_storage(default_manager):
    exercise_mcp_image(default_manager)
    private = collaboration_state_root(default_manager)
    assert (private / "runtime/message-delivery").is_dir()
    assert not (default_manager.paths.state_root / "runtime/message-attachments").exists()


def test_default_worker_preview_publication_is_readable_from_ui_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "user-state"))
    manager, snapshot, _, tool, _ = tool_fixture(tmp_path)
    manager.paths.state_root = manager.repo_root / ".git/state"
    receipt = call(tool)
    reader = LivePreviewRegistry(
        collaboration_state_root(manager),
        project_root=manager.repo_root,
        workspace_id=manager.workspace_id,
        forbidden_ports=frozenset({8080}),
    )
    assert reader.list(snapshot, "task", receipt["task_id"])[0].output_id == receipt["preview_id"]
    assert not manager.paths.state_root.exists()
