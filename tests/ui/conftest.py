from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext

PORT = 51234


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    state_root = tmp_path / "state"
    CommonsManager.initialize(repo, integrations=())
    return {"repo": repo, "state_root": state_root, "commons_root": repo / ".agent-commons"}


@pytest.fixture
def context(workspace: dict[str, Any]) -> UIContext:
    return UIContext(workspace["repo"], state_root=workspace["state_root"])


@pytest.fixture
def populated(workspace: dict[str, Any]) -> dict[str, Any]:
    """A workspace with a real session and task.

    An empty workspace silently skips the session-rendering path, which is
    exactly where a projection bug hides.
    """

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.sessions.open_session(
        stable_instance_id="ui-test-window",
        principal="local-operator",
        client="claude",
        software="claude-code",
        role="implementation-author",
        capabilities=("task:write",),
    )
    bound = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=session.session_id,
    )
    created = bound.create_task(
        title="Render the graph",
        description="Exercise the read-only projection",
        acceptance_criteria=("The graph contains this task",),
    )
    return {
        **workspace,
        "session_id": session.session_id,
        "task_id": created["entity_ref"]["id"],
    }


@pytest.fixture
def client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from agent_commons.ui.server import create_app

    app = create_app(context, token="test-token", port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        yield test_client


def authorized() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def tree_digest(root: Path) -> dict[str, str]:
    """Byte-level fingerprint of every canonical file under a root."""

    digests: dict[str, str] = {}
    if not root.exists():
        return digests
    for path in sorted(root.rglob("*")):
        if path.is_file():
            info = path.stat()
            digests[str(path.relative_to(root))] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                + f":{info.st_size}:{info.st_mtime_ns}"
            )
    return digests
