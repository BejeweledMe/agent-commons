from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.security import AUTH_ROUTES, SESSION_COOKIE_NAME
from agent_commons.ui.server import (
    CATALOG_ROUTES,
    LAUNCH_ROUTES,
    MUTATING_ROUTES,
    SETUP_ROUTES,
)

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
    (repo / "README.md").write_text("example\n", encoding="utf-8")
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

    app = create_app(
        context,
        token="test-token",
        exchange_code="test-exchange-code",
        port=PORT,
        api_base="/api",
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        yield test_client


@pytest.fixture
def writable(workspace: dict[str, Any]) -> UIContext:
    """A UI opened for writes, bound to a real operator session."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="ui-writer-window-1234",
        principal="local-operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    return UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
    )


@pytest.fixture
def writable_client(writable: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from agent_commons.ui.server import create_app

    app = create_app(
        writable,
        token="test-token",
        exchange_code="test-exchange-code",
        port=PORT,
        api_base="/api",
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        yield test_client


#: The whole non-GET surface of an operator panel, named once.  There is no
#: table of gates any more and that is the point: every capability the panel can
#: gain -- a workspace, a runtime config, a catalogue -- now appears while it is
#: already serving, and FastAPI builds its route table once, so the table cannot
#: depend on any of them.  A new tuple joins the surface by being added here and
#: nowhere else.
OPERATOR_SURFACE: frozenset[tuple[str, str]] = frozenset(
    set(MUTATING_ROUTES) | set(LAUNCH_ROUTES) | set(SETUP_ROUTES) | set(CATALOG_ROUTES)
)


def mutating_surface(app: FastAPI) -> set[tuple[str, str]]:
    """The canonical-write pairs an assembled app actually registers.

    The one public auth exchange is intentionally excluded: it makes an
    in-memory HTTP-only session, never changes the workspace or operation
    stores, and must be present for read-only panels too.
    """

    return {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", set()) or set())
        if method not in {"GET", "HEAD"} and (method, route.path) not in AUTH_ROUTES
    }


def expected_surface(context: UIContext) -> set[tuple[str, str]]:
    """The non-GET surface a panel in this state is supposed to register.

    Two answers and no formula: a read-only panel registers nothing, and an
    operator panel registers the whole declared union, whatever else is or is
    not configured about it.  Because `operator_panel` is also the one property
    `create_app` reads, callers that want a check independent of the
    implementation compare against `OPERATOR_SURFACE` literally instead -- and
    the tests that pin the invariant do both.
    """

    return set(OPERATOR_SURFACE) if context.operator_panel else set()


def authorized() -> dict[str, str]:
    """The process-private local-browser session used by direct app fixtures."""

    return {"Cookie": f"{SESSION_COOKIE_NAME}=test-token"}


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
