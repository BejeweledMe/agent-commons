from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import CATALOG_ROUTES, LAUNCH_ROUTES, MUTATING_ROUTES

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

    app = create_app(writable, token="test-token", port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        yield test_client


#: Every declared route tuple beside the context property that gates it.  The
#: tuples in `ui.server` are a declaration checked by test rather than a runtime
#: allowlist, so the check has to live here -- and it has to live here *once*:
#: a new tuple joins the writing surface by being added to this table and
#: nowhere else, instead of by editing every test that pins the surface.
_SURFACE_GATES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("writes_enabled", MUTATING_ROUTES),
    ("catalog_editing_enabled", CATALOG_ROUTES),
    # Registered by every writing panel, configured or not: the runtime config
    # can be written into an already-serving panel, and the route table is built
    # once.  `launch_enabled` still exists and still means "a run would start" --
    # it is just no longer what decides whether the route is there.
    ("writes_enabled", LAUNCH_ROUTES),
)


def mutating_surface(app: FastAPI) -> set[tuple[str, str]]:
    """The non-GET (method, path) pairs an assembled app actually registers.

    Read from the built router, not from the declaration, so the two can
    disagree and be caught disagreeing.
    """

    return {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", set()) or set())
        if method not in {"GET", "HEAD"}
    }


def expected_surface(context: UIContext) -> set[tuple[str, str]]:
    """The non-GET surface a panel in this state is supposed to register.

    Derived from the context's own gate properties and the declared tuples, so
    a read-only panel expects the empty set and every other panel expects the
    union of the tuples its gates open.
    """

    surface: set[tuple[str, str]] = set()
    for gate, routes in _SURFACE_GATES:
        if getattr(context, gate):
            surface |= set(routes)
    return surface


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
