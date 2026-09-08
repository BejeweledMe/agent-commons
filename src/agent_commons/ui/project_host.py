"""One authenticated loopback host for several isolated project panels.

The registry keeps private checkout bindings.  This module is the only UI
composition layer allowed to turn one of those bindings into a mounted panel:
there is no selected-project global and a URL is always bound to one immutable
``UIContext`` instance for its lifetime.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agent_commons.errors import CommonsError
from agent_commons.ui.context import UIContext
from agent_commons.ui.project_operations import ProjectOperations
from agent_commons.ui.project_registry import ProjectRegistry, ProjectRegistryRefusal

_PROJECT_ID = re.compile(r"project\.[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True)
class ProjectHandle:
    """A fixed context binding owned by this host, never by browser selection."""

    project_id: str
    context: UIContext
    generation: int


class ProjectHost:
    """Mount project-local route tables below one opaque browser API base."""

    def __init__(
        self,
        registry: ProjectRegistry,
        operations: ProjectOperations,
        default_context: UIContext,
        *,
        context_factory: Callable[[Path, Path | None], UIContext] | None = None,
        read_only: bool = False,
        max_open_contexts: int = 16,
        max_concurrent_launches: int = 4,
    ) -> None:
        if max_open_contexts < 1 or max_concurrent_launches < 1:
            raise ValueError("project host capacity must be positive")
        self.registry = registry
        self.operations = operations
        self.default_context = default_context
        self._context_factory = context_factory or (
            lambda repo, state: UIContext(repo, state_root=state)
        )
        self.read_only = read_only
        self.max_open_contexts = max_open_contexts
        self._launch_admission = threading.BoundedSemaphore(max_concurrent_launches)
        self._handles: dict[str, ProjectHandle] = {}
        self._children: dict[str, FastAPI] = {}
        self._activation_refusals: dict[str, tuple[str, int, str]] = {}
        self._default_project_id: str | None = None
        self._app: FastAPI | None = None
        self._api_base: str | None = None
        self._generation = 0
        self._port: int | None = None
        self._draining = False
        self._guard = threading.RLock()

    @property
    def default_project_id(self) -> str | None:
        return self._default_project_id

    def attach_startup_default(self) -> str | None:
        """Attach the startup checkout only after it has a real workspace.

        A first-run panel intentionally starts before initialization, so an
        unavailable startup directory leaves the registry empty instead of
        making the host constructor fail.
        """

        if self.read_only or self._default_project_id is not None:
            return self._default_project_id
        try:
            # Register the resolved state root, not UIContext's optional
            # input.  A state base or inherited default can resolve to a
            # different root; persisting the input would split receipts and
            # runtime state from the workspace the panel actually reads.
            resolved_state_root = self.default_context.manager().paths.state_root
        except CommonsError:
            # A non-repository or an uninitialized checkout is the explicit
            # first-run state.  It must still serve the bootstrap UI; no
            # registry operation has been attempted in this branch.
            return None
        try:
            result = self.registry.register_existing(
                self.default_context.repo,
                resolved_state_root,
                name=self.default_context.repo.name,
            )
        except ProjectRegistryRefusal:
            # The workspace was already established above, so a registry
            # failure is not bootstrap state and must fail closed.
            raise
        project = result.get("project")
        if not isinstance(project, dict) or not isinstance(project.get("id"), str):
            return None
        project_id = str(project["id"])
        self._default_project_id = project_id
        self._activate(project_id, context=self.default_context)
        return project_id

    def list_projects(self) -> dict[str, Any]:
        self.attach_startup_default()
        payload = dict(self.operations.list_projects())
        payload["default_project_id"] = self._default_project_id
        payload["read_only"] = self.read_only
        return payload

    def create_app(
        self,
        *,
        token: str,
        port: int,
        exchange_code: str | None = None,
        api_base: str | None = None,
    ) -> FastAPI:
        """Create the single outer auth/static host and mount ready projects."""

        from agent_commons.ui.server import _BoundApiRoutes, _error, create_app

        app = create_app(
            self.default_context,
            token=token,
            port=port,
            exchange_code=exchange_code,
            api_base=api_base,
            read_only=self.read_only,
            manage_shutdown=False,
        )
        selected = str(app.state.api_base)
        if not selected:
            raise ValueError("project host requires an opaque outer API base")
        self._app = app
        self._api_base = selected
        self._port = port
        if not self.read_only and self.default_context._session_owner is not None:
            # First run is allowed: an owner defers this lock until setup has
            # created a state root.  Reacquiring an already-held startup lock
            # only refreshes its recorded port.
            self.default_context._session_owner.acquire_panel_lock(port)
            self.default_context._session_owner.start()
        routes = _BoundApiRoutes(app, selected)

        def refusal(exc: Exception) -> Response:
            status = int(getattr(exc, "status", 409))
            code = str(getattr(exc, "code", "project_unavailable"))
            return _error(status, code, str(exc))

        async def project_body(
            request: Request, *, fields: frozenset[str]
        ) -> dict[str, Any] | Response:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) < 0 or int(content_length) > 16 * 1024:
                        return _error(400, "project_invalid", "Project request is invalid.")
                except ValueError:
                    return _error(400, "project_invalid", "Project request is invalid.")
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > 16 * 1024:
                    return _error(400, "project_invalid", "Project request is invalid.")
                chunks.append(chunk)
            raw = b"".join(chunks)
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _error(400, "project_invalid", "Project request is invalid.")
            if not isinstance(value, dict) or set(value) != fields:
                return _error(400, "project_invalid", "Project request is invalid.")
            return value

        @routes.get("/api/projects")
        async def projects() -> Response:
            try:
                return JSONResponse(await asyncio.to_thread(self.list_projects))
            except ProjectRegistryRefusal as exc:
                return refusal(exc)

        @routes.post("/api/projects/inspect")
        async def inspect_project(request: Request) -> Response:
            if self.read_only:
                return _error(403, "read_only", "This host is read-only.")
            body = await project_body(request, fields=frozenset({"mode", "path", "name"}))
            if isinstance(body, Response):
                return body
            try:
                return JSONResponse(
                    await asyncio.to_thread(
                        self.operations.inspect,
                        body.get("mode"),
                        body.get("path"),
                        body.get("name"),
                    )
                )
            except ProjectRegistryRefusal as exc:
                return refusal(exc)

        @routes.post("/api/projects")
        async def create_project(request: Request) -> Response:
            if self.read_only:
                return _error(403, "read_only", "This host is read-only.")
            body = await project_body(
                request, fields=frozenset({"inspection_id", "idempotency_key"})
            )
            if isinstance(body, Response):
                return body
            try:
                result = await asyncio.to_thread(
                    self.operations.create,
                    body.get("inspection_id"),
                    body.get("idempotency_key"),
                )
                project = result.get("project")
                if isinstance(project, dict) and isinstance(project.get("id"), str):
                    self._activate(str(project["id"]))
                return JSONResponse(result)
            except ProjectRegistryRefusal as exc:
                return refusal(exc)

        @routes.post("/api/projects/{project_id}/update")
        async def update_project(project_id: str, request: Request) -> Response:
            if self.read_only:
                return _error(403, "read_only", "This host is read-only.")
            if _PROJECT_ID.fullmatch(project_id) is None:
                return _error(404, "project_unknown", "Project was not found.")
            body = await project_body(
                request,
                fields=frozenset({"expected_revision", "name", "archived", "idempotency_key"}),
            )
            if isinstance(body, Response):
                return body
            try:
                return JSONResponse(
                    await asyncio.to_thread(
                        self.operations.update,
                        project_id,
                        body.get("expected_revision"),
                        body.get("name"),
                        body.get("archived"),
                        body.get("idempotency_key"),
                    )
                )
            except ProjectRegistryRefusal as exc:
                return refusal(exc)

        # All project-management routes must precede the subtree dispatcher:
        # `/projects/<id>/update` belongs to this host, never to a project.
        self.attach_startup_default()
        app.mount(f"{selected}/projects", _ProjectDispatcher(self))
        app.router.add_event_handler("shutdown", self.shutdown)
        return app

    def _activate(
        self, project_id: str, *, context: UIContext | None = None
    ) -> ProjectHandle | None:
        """Create and mount one exact binding, bounded without evicting work."""

        if _PROJECT_ID.fullmatch(project_id) is None:
            return None
        with self._guard:
            if self._draining:
                self._activation_refusals[project_id] = (
                    "project_host_draining",
                    409,
                    "This local host is draining existing work.",
                )
                return None
            existing = self._handles.get(project_id)
            if existing is not None:
                return existing
            if len(self._handles) >= self.max_open_contexts:
                self._activation_refusals[project_id] = (
                    "project_capacity",
                    409,
                    "This host has reached its open-project capacity.",
                )
                return None
            try:
                entry = self.registry.project_binding(project_id)
            except ProjectRegistryRefusal:
                return None
            public = entry.public()
            if not public["available"]:
                state = str(public["state"])
                self._activation_refusals[project_id] = (
                    f"project_{state}",
                    409,
                    "This project is unavailable.",
                )
                return None
            state = Path(entry.state_binding) if entry.state_binding is not None else None
            try:
                bound = (
                    context if context is not None else self._context_factory(entry.checkout, state)
                )
            except CommonsError:
                self._activation_refusals[project_id] = (
                    "project_unavailable",
                    409,
                    "This project is unavailable.",
                )
                return None
            if not self._same_binding(bound.repo, entry.checkout):
                self._activation_refusals[project_id] = (
                    "project_identity_conflict",
                    409,
                    "This project is unavailable.",
                )
                return None
            if not self.read_only and bound._session_owner is not None:
                if self._port is None:
                    return None
                bound._session_owner.acquire_panel_lock(self._port)
                bound._session_owner.start()
            self._generation += 1
            handle = ProjectHandle(project_id, bound, self._generation)
            self._handles[project_id] = handle
            self._activation_refusals.pop(project_id, None)
            # The admission token is shared by host-owned contexts only.  The
            # coordinator pins its context and token before it creates a run.
            bound._launch_admission = self._launch_admission
            return handle

    @staticmethod
    def _same_binding(left: Path, right: Path) -> bool:
        try:
            return left.resolve(strict=True) == right.resolve(strict=True)
        except OSError:
            return False

    def project_app(self, handle: ProjectHandle) -> FastAPI:
        """Build one route-only project child after its exact binding is checked."""

        with self._guard:
            existing = self._children.get(handle.project_id)
            if existing is not None:
                return existing
            return self._build_project_app(handle)

    def _build_project_app(self, handle: ProjectHandle) -> FastAPI:
        from agent_commons.ui.server import _error, create_app

        child = create_app(
            handle.context,
            token="",
            port=0,
            api_base="",
            hosted=True,
            read_only=self.read_only,
        )

        @child.middleware("http")
        async def binding_guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
            try:
                entry = self.registry.project_binding(handle.project_id)
                if not entry.public()["available"] or not self._same_binding(
                    handle.context.repo, entry.checkout
                ):
                    return _error(409, "project_unavailable", "This project is unavailable.")
            except ProjectRegistryRefusal:
                return _error(404, "project_unknown", "Project was not found.")
            return await call_next(request)

        self._children[handle.project_id] = child
        return child

    def shutdown(self) -> dict[str, Any]:
        """Drain host-owned launch threads without claiming their completion."""

        outcomes: dict[str, Any] = {"projects": {}, "draining": []}
        # Close every project admission gate before draining one of them.  A
        # sequential close left later projects free to create work while an
        # earlier one was joining, which made shutdown non-atomic at host
        # scope.
        with self._guard:
            self._draining = True
            handles = tuple(self._handles.items())
        for _, handle in handles:
            handle.context._launch_coordinator.begin_draining()
            owner = handle.context._session_owner
            if owner is not None:
                owner.begin_draining()
        for project_id, handle in handles:
            owner = handle.context._session_owner
            launch = handle.context._launch_coordinator.shutdown(timeout=None)
            if launch.get("live", 0):
                outcomes["draining"].append(project_id)
            if owner is not None:
                outcomes["projects"][project_id] = owner.shutdown()
        return outcomes


class _ProjectDispatcher:
    """Resolve a project id at request time without a selected-project global."""

    def __init__(self, host: ProjectHost) -> None:
        self._host = host

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        raw = str(scope.get("path", ""))
        # Starlette versions differ on whether an ASGI mount hands its child a
        # stripped ``path`` or preserves it and advances ``root_path``.  Accept
        # either representation, while still deriving identity solely from the
        # component immediately below the project mount.
        mount_root = str(scope.get("root_path", ""))
        if mount_root and raw.startswith(mount_root + "/"):
            raw = raw.removeprefix(mount_root)
        project_id, separator, remainder = raw.lstrip("/").partition("/")
        if not separator or _PROJECT_ID.fullmatch(project_id) is None:
            await JSONResponse(
                {"error": {"code": "project_unknown", "message": "Project was not found."}},
                status_code=404,
            )(scope, receive, send)
            return
        handle = await asyncio.to_thread(self._host._activate, project_id)
        if handle is None:
            recorded = self._host._activation_refusals.get(project_id)
            if recorded is not None:
                code, status, message = recorded
                response = JSONResponse(
                    {"error": {"code": code, "message": message}}, status_code=status
                )
                await response(scope, receive, send)
                return
            try:
                self._host.registry.project_binding(project_id)
            except ProjectRegistryRefusal:
                code, status, message = "project_unknown", 404, "Project was not found."
            else:
                code, status, message = (
                    "project_capacity",
                    409,
                    "This host has reached its open-project capacity.",
                )
            await JSONResponse({"error": {"code": code, "message": message}}, status_code=status)(
                scope, receive, send
            )
            return
        child_scope = dict(scope)
        child_scope["path"] = "/" + remainder
        child_scope["raw_path"] = child_scope["path"].encode("utf-8")
        child_scope["root_path"] = str(scope.get("root_path", "")) + "/" + project_id
        await self._host.project_app(handle)(child_scope, receive, send)
