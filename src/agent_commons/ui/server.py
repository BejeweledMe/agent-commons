"""FastAPI application for the local workspace panel.

One structural property, verified by test rather than trusted to middleware: a
read-only panel registers no route of any method but ``GET``, and an operator
panel registers exactly the union of the four declared tuples -- unconditionally,
whatever else is or is not configured about it.  There is no gate formula left
in the route table, because everything a panel can gain (a workspace, an
operator runtime config, a catalogue beside it) can now appear while it is
already serving, and the table is built once.  What is not yet true is refused
by the handler with a named code the first-run screen draws by.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import webbrowser
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_commons.core.canonical import loads_json_strict
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.context_pack import ContextPackRefusal
from agent_commons.errors import CommonsError
from agent_commons.runtime import AttemptStore
from agent_commons.services.artifact_content import ArtifactPreviewReader, ArtifactPreviewRefusal
from agent_commons.services.design_authoring import publish_from_selection, revise_from_selection
from agent_commons.ui import ENTITY_SCHEMA, gallery_static_directory, read_gallery_shell, read_spa
from agent_commons.ui.context import (
    LAUNCH_NOT_CONFIGURED,
    PANEL_ALREADY_OPEN_ACTIONS,
    UIContext,
)
from agent_commons.ui.gallery_routes import register_gallery_routes
from agent_commons.ui.security import (
    AUTH_EXCHANGE_PATH,
    SECURITY_HEADERS,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    LocalBrowserSession,
    allowed_hosts,
    allowed_origins,
    content_security_policy,
    gallery_content_security_policy,
    is_public_path,
    new_api_base,
    new_token,
)
from agent_commons.ui.session_owner import PanelAlreadyOpenError
from agent_commons.ui.setup import (
    SETUP_NOT_A_REPOSITORY,
    SETUP_UNINITIALIZED,
    SetupError,
    missing_workspace_state,
)
from agent_commons.ui.starter_pack_routes import register_starter_pack_routes
from agent_commons.ui.tracker_reads import build_tracker_snapshot
from agent_commons.ui.tracker_routes import register_tracker_routes
from agent_commons.ui.work_routes import register_work_routes


class _ExpectedShutdownCancellationFilter(logging.Filter):
    """Hide only uvicorn's traceback for tasks it cancelled during shutdown."""

    def __init__(self, shutting_down: Callable[[], bool]) -> None:
        super().__init__()
        self._shutting_down = shutting_down

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info is not None else None
        expected = (
            self._shutting_down()
            and record.getMessage().startswith("Exception in ASGI application")
            and isinstance(error, asyncio.CancelledError)
        )
        return not expected


_ENTITY_KINDS = frozenset(
    {
        "objective",
        "task",
        "thread",
        "review",
        "verification",
        "finding",
        "decision",
        "artifact",
        "handoff",
        "delegation",
        "session",
        "agent",
        "agent_link",
    }
)

# A consumed exchange code cannot be made valid again.  Keep the recovery
# action truthful in both the middleware refusal and the exchange endpoint.
BROWSER_SESSION_RECOVERY_ACTIONS = (
    "stop the local panel, start it again, then open the newly printed "
    "Work URL once in this browser",
)

#: The complete mutating surface, named once so a route cannot be added without
#: the invariant test noticing.  Every one of these is a thin adapter over a
#: `CommonsManager` method; the UI is a third adapter beside the CLI and MCP,
#: not a second write path.
MUTATING_ROUTES = (
    ("POST", "/api/operations/{operation_id}/answer"),
    ("POST", "/api/chat"),
    ("POST", "/api/chat/{thread_id}/messages"),
    ("POST", "/api/gallery/{design_package_id}/screens/{screen_id}/feedback"),
    ("POST", "/api/gallery/packages"),
    ("POST", "/api/gallery/{design_package_id}/revisions"),
    ("POST", "/api/agents"),
    ("POST", "/api/agents/proposals/{thread_id}/approve"),
    ("POST", "/api/agents/proposals/{thread_id}/decline"),
    ("POST", "/api/agents/{agent_id}/reconfigure"),
    ("POST", "/api/agents/{agent_id}/retire"),
    ("POST", "/api/agents/{agent_id}/messages"),
    ("POST", "/api/agent-links"),
    ("POST", "/api/agent-links/{link_id}/close"),
    ("POST", "/api/tasks"),
    ("POST", "/api/tasks/{task_id}/revise"),
    ("POST", "/api/tasks/{task_id}/review-request"),
    ("POST", "/api/tasks/{task_id}/accept"),
    ("POST", "/api/tasks/{task_id}/reopen"),
    ("POST", "/api/work/starter-packs/{pack_id}/blueprints/{blueprint_id}/apply"),
    ("POST", "/api/work/context-packs"),
    ("POST", "/api/work/context-packs/{context_pack_id}/revisions"),
)

#: Catalogue editing keeps its own allowlist: adding a skill and adding a role
#: are different privileges and the test that pins the mutating surface should
#: say so. It is no longer its own registration gate -- the generated runtime
#: config seeds a catalogue beside itself and the panel adopts both while it is
#: already serving, so a gated table answered 404 to editing the first-run
#: screen had just switched on. `_require_catalog_editing` refuses instead.
CATALOG_ROUTES = (
    ("POST", "/api/catalog/entries"),
    ("POST", "/api/catalog/entries/remove"),
)

#: Launching a provider is a larger privilege than recording a role: bounded
#: metadata against a billable subscription process. It is registered by every
#: operator panel all the same, because the operator config that makes a launch
#: possible can be written from the panel's own first-run screen while the
#: server is already up, and FastAPI does not rebuild its route table. The
#: handler refuses with `launch_not_configured` until the environment exists.
#: Keeping it a separately declared tuple is the compensation for that: the
#: mutating-surface test still names launching as its own privilege rather than
#: folding it into the write allowlist.
LAUNCH_ROUTES = (
    ("POST", "/api/delegations"),
    ("POST", "/api/provider-auth/{profile_id}/login"),
    ("POST", "/api/provider-auth/{profile_id}/cancel"),
    ("POST", "/api/provider-auth/{profile_id}/check"),
)

#: First run, declared apart from every other privilege because it is the only
#: surface that writes outside the ledger: one route creates the workspace
#: through the same initializer `agent-commons init` calls, one writes the
#: initial operator runtime config and adopts it into this running panel, and
#: one may add profiles for a provider subsequently found by trusted discovery.
#: The latter derives both its input and ownership proof from the current file;
#: neither route accepts a parameter, so an authenticated browser session
#: cannot choose a path or a mode. All are registered by any operator panel --
#: a read-only one registers none of these canonical writes, while its narrow
#: auth exchange stays separate -- and the
#: reading half of the same surface (`GET /api/setup`, `GET /api/setup/preflight`)
#: is in no tuple at all.  These are also the only non-GET routes not bound to
#: an existing workspace: initialization is what makes the workspace exist.
SETUP_ROUTES = (
    ("POST", "/api/setup/initialize"),
    ("POST", "/api/setup/runtime-config"),
    ("POST", "/api/setup/add-discovered-providers"),
)

_HEARTBEAT_SECONDS = 15.0
_POLL_SECONDS = 2.0

#: What the operator can do about a panel opened on a directory that is not a
#: workspace yet. The panel serves the first-run screen; the routes that record
#: canonical events are there, and they say this until it exists.
_NOT_INITIALIZED_ACTIONS = ["run first run from this panel", "or run `agent-commons init` here"]


class _NotInitialized(Exception):
    """Nothing can be recorded here yet. Rendered by ``create_app``.

    Raised from a route dependency rather than from a handler on purpose: a
    dependency runs *before* the request body is read, which is the same
    discipline `launch_not_configured` already follows. Nothing in a body can
    make an absent workspace recordable, so nothing in it is worth parsing.
    Mostly this carries the first-run codes; a panel that lost the singleness
    race carries ``panel_already_open`` with its own actions instead, because
    sending that operator to first run would not help.
    """

    def __init__(self, code: str, message: str, actions: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.actions = list(actions) if actions is not None else list(_NOT_INITIALIZED_ACTIONS)


def _missing_workspace_refusal(missing: str | None) -> _NotInitialized:
    """The one refusal both halves of the surface raise before a workspace.

    Reading routes and writing routes refuse the same two states with the same
    code, the same sentence, and the same safe next actions, because the tab
    draws them by the code and a second shape would be a second contract.  A
    ``None`` still lands on ``setup_uninitialized``: the caller that reaches
    here with a workspace present is a writing route whose panel could not
    obtain a session, and "not set up for that yet" remains the honest name
    the first-run screen can act on.
    """

    if missing == SETUP_NOT_A_REPOSITORY:
        return _NotInitialized(
            SETUP_NOT_A_REPOSITORY,
            "this directory is not a git repository, so there is nothing for a "
            "workspace to attach to and nothing to read or record here",
        )
    return _NotInitialized(
        SETUP_UNINITIALIZED,
        "this directory has no workspace yet, so this panel has nothing to read "
        "and records no canonical event until first run creates one",
    )


@dataclass(frozen=True)
class _BoundApiRoutes:
    """Register logical ``/api`` routes under one opaque process-local base."""

    app: FastAPI
    api_base: str

    def _bound_path(self, path: str) -> str:
        if not path.startswith("/api/"):
            raise ValueError(f"API routes must start with /api/: {path}")
        return self.api_base + path.removeprefix("/api")

    def get(self, path: str, **kwargs: Any) -> Callable[[Any], Any]:
        return self.app.get(self._bound_path(path), **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable[[Any], Any]:
        return self.app.post(self._bound_path(path), **kwargs)


class _RouteGroup:
    """Where one group of routes attaches, and what must hold before any runs.

    The routes stay flat on the application -- an included ``APIRouter`` is one
    nested object in ``app.routes``, and the invariant test reads that list to
    check what was registered against what was declared, so nesting would hide
    the surface from the only check that guards it.  What the group carries is
    the precondition shared by every route in it, as a FastAPI dependency:
    dependencies resolve *before* the handler runs and therefore before the
    request body is read.
    """

    def __init__(
        self, app: FastAPI | _BoundApiRoutes, *, requires: list[Any] | None = None
    ) -> None:
        self._app = app
        self._requires = list(requires or ())

    def post(self, path: str) -> Callable[[Any], Any]:
        return self._app.post(path, dependencies=self._requires)


def _workspace_bound(app: FastAPI | _BoundApiRoutes, context: UIContext) -> _RouteGroup:
    """The route group that needs a workspace existing right now.

    The panel's non-GET surface is registered structurally -- see ``create_app``
    -- so this is where "registered" stops meaning "usable".  It reads
    ``writes_enabled``, which is deliberately a question answered per request:
    the same panel answers no before `POST /api/setup/initialize` and yes after
    it, in the same process and without the route table changing.
    """

    async def _require_workspace() -> None:
        # Two questions, and the state is asked first: there is nothing to
        # record into before the workspace exists, so a panel that believes it
        # holds a session there is wrong rather than lucky.
        missing = await asyncio.to_thread(missing_workspace_state, context.repo)
        # One look at the session and its refusal: two reads could pair "no
        # session" with a reason a concurrent request had already cleared.
        session_id, refusal = await asyncio.to_thread(context.session_or_refusal)
        if missing is None and session_id is not None:
            return
        if isinstance(refusal, PanelAlreadyOpenError):
            # A panel whose deferred lock lost the singleness race: the
            # workspace exists, another panel owns it, and calling that
            # "not set up yet" would send the operator to a first-run screen
            # that cannot help.  The refusal keeps its own frozen code, its
            # text, and the first panel's address.
            raise _NotInitialized(
                str(refusal.code),
                str(refusal),
                actions=list(PANEL_ALREADY_OPEN_ACTIONS),
            )
        raise _missing_workspace_refusal(missing)

    return _RouteGroup(app, requires=[Depends(_require_workspace)])


def _error(status: int, code: str, message: str, actions: list[str] | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if actions:
        payload["error"]["safe_next_actions"] = actions
    response = JSONResponse(payload, status_code=status)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Session realm="agent-commons-ui"'
    return response


def _sse(
    event: str, data: Any, *, event_id: int | None = None, instance: str | None = None
) -> bytes:
    lines = []
    if event_id is not None:
        # The id is composite so a reconnect after a restart is detectable: a
        # bare counter would make a stale client look caught up.
        lines.append(f"id: {instance}:{event_id}" if instance else f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def create_app(
    context: UIContext,
    *,
    token: str,
    port: int,
    exchange_code: str | None = None,
    api_base: str | None = None,
) -> FastAPI:
    """Build the local UI with a private session token and exchange code.

    ``token`` remains the private session credential argument for test and
    embedding compatibility. ``serve`` always supplies a distinct, short-lived
    ``exchange_code``; callers which omit it receive a fresh code unavailable
    to unauthenticated requests. Browser-facing callers also receive an opaque
    API base, so a cookie cannot authenticate another loopback port's ordinary
    ``/api`` route. The only compatibility exception is a direct constructor
    with *no* ``exchange_code``: it selects ``/api`` for existing in-process
    tests and cannot produce a browser launch capability. ``serve`` always
    supplies an exchange code and therefore never selects that exception.
    """

    app = FastAPI(title="Agent Commons UI", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = allowed_hosts(port)
    origins = allowed_origins(port)
    selected_api_base = (
        api_base
        if api_base is not None
        else new_api_base()
        if exchange_code is not None
        else "/api"
    )
    if selected_api_base != "/api" and (
        not selected_api_base.startswith("/api/")
        or selected_api_base.endswith("/")
        or "?" in selected_api_base
        or "#" in selected_api_base
    ):
        raise ValueError("api_base must be a non-empty /api/<opaque-path> prefix")
    browser_session = LocalBrowserSession(
        exchange_code=exchange_code if exchange_code is not None else new_token(),
        session_token=token,
        api_base=selected_api_base,
    )
    app.state.api_base = browser_session.api_base
    api_routes = _BoundApiRoutes(app, browser_session.api_base)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        host = request.headers.get("host", "")
        if host not in hosts:
            response: Response = _error(
                403, "forbidden_host", "this server accepts loopback Host headers only"
            )
        else:
            origin = request.headers.get("origin")
            if origin is not None and origin not in origins:
                response = _error(403, "forbidden_origin", "cross-origin requests are refused")
            elif (
                request.url.path.startswith("/api/")
                and request.url.path != AUTH_EXCHANGE_PATH
                and not _is_bound_api_path(request)
            ):
                response = _error(
                    404, "not_found", "this API route is not available in this process"
                )
            elif not is_public_path(request.url.path) and not _authorized(request):
                response = _error(
                    401,
                    "unauthorized",
                    "an authenticated local browser session is required",
                    list(BROWSER_SESSION_RECOVERY_ACTIONS),
                )
            else:
                response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def _authorized(request: Request) -> bool:
        return browser_session.session_matches(request.cookies.get(SESSION_COOKIE_NAME))

    def _is_bound_api_path(request: Request) -> bool:
        path = request.url.path
        return path == browser_session.api_base or path.startswith(browser_session.api_base + "/")

    def _same_origin(request: Request) -> bool:
        """Require the exchange fetch to come from this exact loopback origin."""

        host = request.headers.get("host", "")
        return request.headers.get("origin") == f"{request.url.scheme}://{host}"

    @app.post(AUTH_EXCHANGE_PATH)
    async def exchange_browser_code(request: Request) -> Response:
        """Turn the printed single-use fragment code into an HTTP-only cookie.

        This is the sole unauthenticated non-GET route. It writes no canonical
        or operational record, never echoes its request body, and requires the
        same loopback Host and exact-origin protections as all UI requests.
        """

        if not _same_origin(request):
            return _error(403, "forbidden_origin", "cross-origin requests are refused")
        body = await _json_body(request)
        if browser_session.consume_exchange_code(body.get("code")) is None:
            return _error(
                401,
                "unauthorized",
                "the local browser session could not be established",
                list(BROWSER_SESSION_RECOVERY_ACTIONS),
            )
        response = JSONResponse({"api_base": browser_session.api_base})
        # The product deliberately binds HTTP loopback only. A Secure cookie
        # would be dropped on required http://127.0.0.1, so this process-bound
        # cookie relies on loopback Host/origin checks plus HttpOnly and
        # SameSite=Strict, without a Domain attribute.
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=browser_session.session_token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="strict",
            path=browser_session.api_base,
        )
        return response

    async def _require_workspace_to_read() -> None:
        # The reading half of the precondition the writing routes already
        # carry, minus the session question a read never asks.  Before this
        # guard, a reading route on a directory with no workspace built the
        # manager and let its ConfigurationError escape as a 500 -- or, on the
        # catalogue route, as a 422 named after the exception class -- while
        # the writing half refused with a frozen code.  The tab that opened on
        # a bare repository therefore could not load at all.  Every reading
        # route that touches the ledger now refuses with the same code, the
        # same sentence, and the same safe next actions as the writes; the
        # routes that must answer in every state -- the SPA itself, `/api/meta`
        # (the tab's boot request), and `GET /api/setup` (the one entry point
        # that names the state) -- deliberately do not carry it.
        missing = await asyncio.to_thread(missing_workspace_state, context.repo)
        if missing is not None:
            raise _missing_workspace_refusal(missing)

    reads_workspace = [Depends(_require_workspace_to_read)]

    async def _not_initialized(_: Request, exc: Exception) -> Response:
        # The frozen refusal table travels as a code, exactly as the first-run
        # screen's other refusals do; the class name would say nothing.
        assert isinstance(exc, _NotInitialized)
        return _error(409, exc.code, exc.message, exc.actions)

    app.add_exception_handler(_NotInitialized, _not_initialized)

    # The Gallery bundle holds no workspace data, so it is served as a public
    # shell like the legacy root. Its API bootstrap uses the same HTTP-only
    # browser session established from the one-time fragment code.
    gallery_directory = gallery_static_directory()
    app.mount(
        "/gallery/assets",
        StaticFiles(directory=gallery_directory / "assets"),
        name="gallery-assets",
    )
    register_work_routes(app)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        nonce = secrets.token_urlsafe(16)
        body = read_spa().replace("__CSP_NONCE__", nonce)
        response = HTMLResponse(body)
        response.headers["Content-Security-Policy"] = content_security_policy(nonce)
        return response

    @app.get("/gallery", response_class=HTMLResponse)
    @app.get("/gallery/", response_class=HTMLResponse)
    async def gallery() -> Response:
        response = HTMLResponse(read_gallery_shell())
        response.headers["Content-Security-Policy"] = gallery_content_security_policy()
        return response

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @api_routes.get("/api/meta")
    async def meta() -> Response:
        return JSONResponse(await asyncio.to_thread(context.meta))

    register_gallery_routes(
        api_routes,
        dependencies=reads_workspace,
        manager_factory=context.manager,
        authoring_session_factory=lambda: context.writer_session_id,
    )

    def tracker_source(*, resume_after: int | None = None):
        # The cursor is enforced by the SSE route. This composition function
        # returns only the latest disposable snapshot from canonical truth plus
        # the existing operational attempt store.
        del resume_after
        context.refresh_if_changed()
        sequence, graph = context.snapshot_frame()
        manager = context.manager()
        attempts = AttemptStore(manager.paths.state_root, read_only=True).list_attempts()
        return build_tracker_snapshot(
            manager.snapshot(),
            attempts,
            generated_at=str(graph["generated_at"]),
            sequence=sequence,
            graph=graph,
        )

    register_tracker_routes(
        api_routes,
        dependencies=reads_workspace,
        source=tracker_source,
    )

    @api_routes.get("/api/artifacts/{artifact_id}/preview", dependencies=reads_workspace)
    async def artifact_preview(artifact_id: str) -> Response:
        # The response contains raw workspace bytes, so it goes through the
        # same browser-session middleware as every data route. The reader deliberately
        # receives only an artifact id: manifest resolution owns the source
        # path and rejects a replaced or unsafe file before bytes reach HTTP.
        def _read_preview():
            return ArtifactPreviewReader(context.manager()).read(artifact_id)

        try:
            preview = await asyncio.to_thread(_read_preview)
        except ArtifactPreviewRefusal as exc:
            return _error(exc.status_code, exc.code, str(exc))
        return Response(content=preview.content, media_type=preview.media_type)

    @api_routes.get("/api/graph", dependencies=reads_workspace)
    async def graph() -> Response:
        await asyncio.to_thread(context.refresh_if_changed)
        return JSONResponse(await asyncio.to_thread(context.graph))

    @api_routes.get("/api/entities/{kind}/{entity_id}", dependencies=reads_workspace)
    async def entity(kind: str, entity_id: str) -> Response:
        if kind not in _ENTITY_KINDS:
            return _error(400, "unknown_kind", "unsupported entity kind")
        if not entity_id.startswith(f"{kind}."):
            return _error(400, "malformed_id", "entity id does not match its kind")
        record = await asyncio.to_thread(context.entity, kind, entity_id)
        if record is None:
            return _error(404, "not_found", "no such entity in this workspace")
        return JSONResponse(
            {
                "schema": ENTITY_SCHEMA,
                "kind": kind,
                "id": entity_id,
                "record": record,
            }
        )

    @api_routes.get("/api/search", dependencies=reads_workspace)
    async def search(request: Request) -> Response:
        query = request.query_params.get("q", "")
        kind = request.query_params.get("kind") or None
        if kind is not None and kind not in _ENTITY_KINDS:
            return _error(400, "unknown_kind", "unsupported entity kind")
        try:
            limit = int(request.query_params.get("limit", "25"))
        except ValueError:
            return _error(400, "invalid_request", "limit must be an integer")
        return JSONResponse(
            await asyncio.to_thread(context.search, query=query, limit=limit, subject_kind=kind)
        )

    @api_routes.get("/api/operations", dependencies=reads_workspace)
    async def operations() -> Response:
        return JSONResponse(await asyncio.to_thread(context.pending_operations))

    @api_routes.get("/api/chat", dependencies=reads_workspace)
    async def chat() -> Response:
        return JSONResponse(await asyncio.to_thread(context.engagements))

    @api_routes.get("/api/proposals", dependencies=reads_workspace)
    async def proposals() -> Response:
        return JSONResponse(await asyncio.to_thread(context.agent_proposals))

    @api_routes.get("/api/attention", dependencies=reads_workspace)
    async def attention() -> Response:
        # One canonical queue: the same source as the amber ring and the footer
        # count, so the list can never be empty while the graph says N are
        # waiting on you.
        return JSONResponse(await asyncio.to_thread(context.attention))

    @api_routes.get("/api/catalog", dependencies=reads_workspace)
    async def catalog() -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(context.catalog))
        except CommonsError as exc:
            # A catalogue that fails to load is a misconfiguration, not a server
            # fault: name it rather than returning an opaque 500 (round 2).
            return _error(422, type(exc).__name__, str(exc))

    @api_routes.get("/api/launch", dependencies=reads_workspace)
    async def launch_options() -> Response:
        # The roles and tasks the panel needs to offer a run, plus whether
        # launching is enabled at all. Readable in any mode; acting on it is not.
        return JSONResponse(await asyncio.to_thread(context.launch_options))

    @api_routes.get("/api/provider-auth/{profile_id}", dependencies=reads_workspace)
    async def provider_auth_status(profile_id: str) -> Response:
        """Return only the closed availability DTO for one fixed profile."""

        try:
            return JSONResponse(
                await asyncio.to_thread(
                    context.provider_auth_status,
                    profile_id=profile_id,
                )
            )
        except CommonsError as exc:
            return _error(409, getattr(exc, "code", type(exc).__name__), str(exc))

    @api_routes.get("/api/work/provider-availability", dependencies=reads_workspace)
    async def provider_availability() -> Response:
        """Return the unified closed provider/profile availability projection."""

        try:
            return JSONResponse(await asyncio.to_thread(context.provider_availability))
        except CommonsError as exc:
            return _error(409, getattr(exc, "code", type(exc).__name__), str(exc))

    @api_routes.get("/api/runs", dependencies=reads_workspace)
    async def runs() -> Response:
        # Live and recent run phases, metadata only. Readable in any mode.
        return JSONResponse(await asyncio.to_thread(context.runs))

    @api_routes.get("/api/setup")
    async def setup_status() -> Response:
        # Readable in any mode; acting on it needs a writing panel. This is the
        # one route that names operator paths, and only until the runtime is
        # configured -- see `UIContext.setup_status`.
        return JSONResponse(await asyncio.to_thread(context.setup_status))

    @api_routes.get("/api/work/setup-guidance", dependencies=reads_workspace)
    async def work_setup_guidance(reveal_location: bool = False) -> Response:
        """Serve Work's redacted setup contract, never the legacy setup read."""

        return JSONResponse(
            await asyncio.to_thread(
                context.work_setup_guidance,
                reveal_location_label=reveal_location,
            )
        )

    @api_routes.get("/api/work/context-packs", dependencies=reads_workspace)
    async def work_context_packs() -> Response:
        return JSONResponse(await asyncio.to_thread(context.work_context_packs))

    @api_routes.get("/api/work/context-packs/{context_pack_id}", dependencies=reads_workspace)
    async def work_context_pack(context_pack_id: str) -> Response:
        if not is_typed_id(context_pack_id, "context_pack"):
            return _error(400, "invalid_request", "context_pack_id must be exact")
        try:
            return JSONResponse(
                await asyncio.to_thread(
                    context.work_context_pack,
                    context_pack_id=context_pack_id,
                )
            )
        except ContextPackRefusal as exc:
            return _error(
                409,
                str(exc.code),
                str(exc),
                [exc.remediation],
            )

    # The bundled examples contain no workspace data, but the existing Work
    # contract reserves all data reads for initialized projects.  After first
    # run, the route gives the normal role screen a non-empty example catalogue.
    register_starter_pack_routes(api_routes, dependencies=reads_workspace)

    @api_routes.get("/api/setup/preflight", dependencies=reads_workspace)
    async def setup_preflight() -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(context.setup_preflight))
        except SetupError as exc:
            return _error(409, exc.code, str(exc))
        except CommonsError as exc:
            return _error(422, type(exc).__name__, str(exc))

    if context.operator_panel:
        # One condition for the whole non-GET surface, and it is the only
        # structural one left.  Every capability this panel might gain -- a
        # workspace, an operator runtime config, a catalogue beside it -- can
        # now appear while the server is already running, and FastAPI builds its
        # route table exactly once; a table built from those states would answer
        # 404 to the very surface the first-run screen had just switched on.
        # What is still false at request time is refused by the handler, with a
        # code the panel can draw: `setup_uninitialized`, `launch_not_configured`,
        # or the catalogue's own named refusal.
        recording = _workspace_bound(api_routes, context)
        _register_writes(recording, context)
        _register_launch(recording, context)
        _register_catalog_writes(recording, context)
        # First run is the one surface that must answer before the workspace
        # exists -- it is what makes it exist -- so it is bound to nothing.
        _register_setup(_RouteGroup(api_routes), context)

    app.router.add_event_handler("shutdown", context._launch_coordinator.shutdown)

    @api_routes.get("/api/stream", dependencies=reads_workspace)
    async def stream(request: Request) -> Response:
        last_event_id = request.headers.get("last-event-id")
        return StreamingResponse(
            _events(context, last_event_id),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    return app


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


_MAX_CONTEXT_PACK_REQUEST_BYTES = 72 * 1024
_MAX_IDEMPOTENCY_KEY_CHARS = 256


async def _context_pack_body(request: Request, *, required: frozenset[str]) -> dict[str, Any]:
    """Own a small closed write envelope before canonical domain validation."""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                raise ValueError("content-length must be a non-negative integer")
            if parsed_content_length > _MAX_CONTEXT_PACK_REQUEST_BYTES:
                raise ValueError("context pack request exceeds the 73728-byte limit")
        except ValueError as exc:
            if "exceeds" in str(exc):
                raise
            raise ValueError("content-length must be a non-negative integer") from exc
    raw = await request.body()
    if len(raw) > _MAX_CONTEXT_PACK_REQUEST_BYTES:
        raise ValueError("context pack request exceeds the 73728-byte limit")
    value = loads_json_strict(raw)
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("context pack request contains missing or unsupported fields")
    draft = value.get("draft")
    key = value.get("idempotency_key")
    if not isinstance(draft, Mapping):
        raise ValueError("draft must be an object")
    if not isinstance(key, str) or not key.strip() or len(key) > _MAX_IDEMPOTENCY_KEY_CHARS:
        raise ValueError("idempotency_key must be a non-empty string of at most 256 characters")
    return dict(value)


async def _guarded(action: Callable[..., Any], context: UIContext, **kwargs: Any) -> Response:
    """Run one write and surface the guard that refused it, if any."""

    try:
        result = await asyncio.to_thread(action, **kwargs)
    except CommonsError as exc:
        # Refusals are the interesting output here: the guard that fired is
        # what the operator needs on the node, not a generic failure.
        actions = list(getattr(exc, "safe_next_actions", ()))
        remediation = getattr(exc, "remediation", None)
        if not actions and isinstance(remediation, str) and remediation:
            actions = [remediation]
        return _error(
            409,
            str(getattr(exc, "code", type(exc).__name__)),
            str(exc),
            actions,
        )
    except (TypeError, ValueError, KeyError) as exc:
        return _error(400, "invalid_request", str(exc))
    context.invalidate()
    return JSONResponse(result)


def _register_writes(router: _RouteGroup, context: UIContext) -> None:
    """Attach the mutating surface. Every handler ends in ``CommonsManager``."""

    async def _record(action: Callable[..., Any], **kwargs: Any) -> Response:
        return await _guarded(action, context, **kwargs)

    async def _body(request: Request) -> dict[str, Any]:
        return await _json_body(request)

    @router.post("/api/work/context-packs")
    async def publish_context_pack(request: Request) -> Response:
        try:
            body = await _context_pack_body(
                request,
                required=frozenset({"draft", "idempotency_key"}),
            )
        except (CommonsError, TypeError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc))
        return await _record(
            context.publish_context_pack,
            draft=body["draft"],
            idempotency_key=body["idempotency_key"],
        )

    @router.post("/api/work/context-packs/{context_pack_id}/revisions")
    async def revise_context_pack(context_pack_id: str, request: Request) -> Response:
        try:
            if not is_typed_id(context_pack_id, "context_pack"):
                raise ValueError("context_pack_id must be exact")
            body = await _context_pack_body(
                request,
                required=frozenset({"expected_revision", "draft", "idempotency_key"}),
            )
            expected_revision = body["expected_revision"]
            if not is_typed_id(expected_revision, "evt"):
                raise ValueError("expected_revision must be an exact evt identifier")
        except (CommonsError, TypeError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc))
        return await _record(
            context.revise_context_pack,
            context_pack_id=context_pack_id,
            expected_revision=expected_revision,
            draft=body["draft"],
            idempotency_key=body["idempotency_key"],
        )

    @router.post("/api/work/starter-packs/{pack_id}/blueprints/{blueprint_id}/apply")
    async def apply_starter_pack_blueprint(
        pack_id: str, blueprint_id: str, request: Request
    ) -> Response:
        body = await _body(request)
        return await _record(
            context.apply_starter_pack_blueprint,
            pack_id=pack_id,
            blueprint_id=blueprint_id,
            confirmed=body.get("confirmed") is True,
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/operations/{operation_id}/answer")
    async def answer_operation(operation_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.answer_operation,
            operation_id=operation_id,
            answer=body.get("answer") or {},
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/chat")
    async def open_chat(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.open_engagement,
            subject=str(body.get("subject", "")),
            body=str(body.get("message", "")),
            objective_id=body.get("objective_id"),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/chat/{thread_id}/messages")
    async def say_in_chat(thread_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.say_in_engagement,
            thread_id=thread_id,
            expected_revision=str(body.get("expected_revision", "")),
            body=str(body.get("message", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/gallery/{design_package_id}/screens/{screen_id}/feedback")
    async def open_gallery_feedback(
        design_package_id: str, screen_id: str, request: Request
    ) -> Response:
        body = await _body(request)
        return await _record(
            context.open_gallery_feedback,
            design_package_id=design_package_id,
            design_package_revision=str(body.get("design_package_revision", "")),
            screen_id=screen_id,
            artifact_revision=str(body.get("artifact_revision", "")),
            producer_task_revision=str(body.get("producer_task_revision", "")),
            body=str(body.get("message", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/gallery/packages")
    async def publish_design_package(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            lambda *, body: publish_from_selection(context.writer(), body),
            body=body,
        )

    @router.post("/api/gallery/{design_package_id}/revisions")
    async def revise_design_package(design_package_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            lambda *, design_package_id, body: revise_from_selection(
                context.writer(), design_package_id, body
            ),
            design_package_id=design_package_id,
            body=body,
        )

    @router.post("/api/agents")
    async def create_agent(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.create_agent,
            name=str(body.get("name", "")),
            profile_id=str(body.get("profile_id", "")),
            rationale=str(body.get("rationale", "")),
            context_mode=str(body.get("context_mode", "fresh")),
            grants=body.get("grants"),
            turnover_budget=body.get("turnover_budget"),
            lifetime=body.get("lifetime"),
            skills=tuple(body.get("skills") or ()),
            tool_allowlist=tuple(body.get("tool_allowlist") or ()),
            template=bool(body.get("template", False)),
            # The one moment a model is chosen. Absent, empty, or whitespace
            # means the profile's model stands; a hired role never changes it,
            # so `reconfigure` below has no such field and will not get one.
            model=body.get("model"),
            created_by_agent_id=body.get("created_by_agent_id"),
            from_preset_id=body.get("from_preset_id"),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agents/proposals/{thread_id}/approve")
    async def approve_proposal(thread_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.approve_agent_proposal,
            thread_id=thread_id,
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agents/proposals/{thread_id}/decline")
    async def decline_proposal(thread_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.decline_agent_proposal,
            thread_id=thread_id,
            reason=str(body.get("reason", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agents/{agent_id}/reconfigure")
    async def reconfigure_agent(agent_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.reconfigure_agent,
            agent_id=agent_id,
            expected_revision=str(body.get("expected_revision", "")),
            changes=body.get("changes") or {},
            reason=str(body.get("reason", "")),
            isolation_downgrade_reason=body.get("isolation_downgrade_reason"),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/tasks")
    async def create_task(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.create_task,
            title=str(body.get("title", "")),
            description=str(body.get("description", "")),
            acceptance_criteria=tuple(
                str(item) for item in (body.get("acceptance_criteria") or ())
            ),
            dependencies=tuple(str(item) for item in (body.get("dependencies") or ())),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/tasks/{task_id}/revise")
    async def revise_task(task_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.revise_task,
            task_id=task_id,
            expected_revision=str(body.get("expected_revision", "")),
            changes=body.get("changes") or {},
            idempotency_key=body.get("idempotency_key"),
        )

    # The acceptance chain, in the order a person walks it: send the work for an
    # independent review, then accept the verdict or send the work back.  Each
    # is a thin adapter over the manager; the refusal when no qualifying review
    # exists is the domain's, and reaches the panel as the guard that fired.
    @router.post("/api/tasks/{task_id}/review-request")
    async def request_task_review(task_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.request_task_review,
            task_id=task_id,
            expected_revision=str(body.get("expected_revision", "")),
            criteria=tuple(str(item) for item in (body.get("criteria") or ())),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/tasks/{task_id}/accept")
    async def accept_task(task_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.accept_task,
            task_id=task_id,
            expected_revision=str(body.get("expected_revision", "")),
            summary=str(body.get("summary", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/tasks/{task_id}/reopen")
    async def reopen_task(task_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.reopen_task,
            task_id=task_id,
            expected_revision=str(body.get("expected_revision", "")),
            reason=str(body.get("reason", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agent-links")
    async def open_agent_link(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.open_agent_link,
            from_agent_id=str(body.get("from_agent_id", "")),
            to_agent_id=str(body.get("to_agent_id", "")),
            allowed_action=str(body.get("allowed_action", "ask")),
            deadline_seconds=body.get("deadline_seconds"),
            reason=str(body.get("reason", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agent-links/{link_id}/close")
    async def close_agent_link(link_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.close_agent_link,
            link_id=link_id,
            # Required by the domain signature: closing races an open ledger,
            # so the caller must say which revision of the link it is closing.
            expected_revision=str(body.get("expected_revision", "")),
            reason=str(body.get("reason", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agents/{agent_id}/retire")
    async def retire_agent(agent_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.retire_agent,
            agent_id=agent_id,
            expected_revision=body.get("expected_revision"),
            reason=str(body.get("reason", "")),
            cascade=bool(body.get("cascade", False)),
            idempotency_key=body.get("idempotency_key"),
        )

    @router.post("/api/agents/{agent_id}/messages")
    async def message_agent(agent_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.message_agent,
            agent_id=agent_id,
            body_text=str(body.get("body", "")),
            subject=body.get("subject"),
            thread_id=body.get("thread_id"),
            expected_revision=body.get("expected_revision"),
            idempotency_key=body.get("idempotency_key"),
        )


def _register_catalog_writes(router: _RouteGroup, context: UIContext) -> None:
    """Attach the operator-catalogue surface, declared apart from role writes.

    Declared apart, not gated apart: every operator panel carries these two
    routes and `_require_catalog_editing` names which half of the state is
    missing -- no catalogue, or no session -- when one is called without them.
    """

    @router.post("/api/catalog/entries")
    async def save_catalog_entry(request: Request) -> Response:
        body = await _json_body(request)
        return await _guarded(
            context.save_catalog_entry,
            context,
            section=str(body.get("section", "")),
            entry_id=str(body.get("id", "")),
            title=str(body.get("title", "")),
            description=str(body.get("description", "")),
            instruction=body.get("instruction"),
        )

    @router.post("/api/catalog/entries/remove")
    async def remove_catalog_entry(request: Request) -> Response:
        body = await _json_body(request)
        return await _guarded(
            context.remove_catalog_entry,
            context,
            section=str(body.get("section", "")),
            entry_id=str(body.get("id", "")),
        )


def _register_setup(router: _RouteGroup, context: UIContext) -> None:
    """Attach the first-run writes, declared apart in ``SETUP_ROUTES``.

    Not one of them takes a parameter, and that is the security property of
    this surface, not an omission: the directory a workspace is created in is
    the one the panel was opened on, the operator config always lands on the
    frozen XDG path, and discovered-provider regeneration derives its input
    from the current config and trusted discovery. An authenticated browser
    session therefore cannot aim any of these writes at a path or a mode of its
    choosing, which
    is also why no manual "type the binary path here" field exists anywhere on
    the first-run screen.
    """

    async def _setup(action: Callable[..., Any]) -> Response:
        # Setup refusals are the frozen table the first-run screen draws by, so
        # the code travels typed rather than as the exception's class name.
        try:
            result = await asyncio.to_thread(action)
        except SetupError as exc:
            return _error(409, exc.code, str(exc))
        except CommonsError as exc:
            return _error(409, type(exc).__name__, str(exc))
        except (OSError, TypeError, ValueError) as exc:
            return _error(400, "invalid_request", str(exc))
        context.invalidate()
        return JSONResponse(result)

    @router.post("/api/setup/initialize")
    async def initialize_workspace() -> Response:
        return await _setup(context.initialize_workspace)

    @router.post("/api/setup/runtime-config")
    async def write_runtime_config() -> Response:
        return await _setup(context.configure_runtime)

    @router.post("/api/setup/add-discovered-providers")
    async def add_discovered_providers() -> Response:
        return await _setup(context.add_discovered_providers)


def _register_launch(router: _RouteGroup, context: UIContext) -> None:
    """Attach the launch surface, declared separately from role writes.

    One route: put a role to work on a task. It records a delegation through the
    same manager as every other write, then runs it through the same broker the
    CLI uses — one launch path, not a second one.

    Every operator panel gets this route, configured or not, and the refusal for
    "not configured" is typed rather than structural: `launch_not_configured`.
    That is a deliberate weakening — the guarantee moves from "the route does
    not exist" to "the route refuses" — bought by the fact that the operator
    profile config is written from the panel's own first-run screen, after the
    route table has been built and can no longer change.
    """

    @router.post("/api/delegations")
    async def run_role_on_task(request: Request) -> Response:
        if not context.launch_enabled:
            # Named ahead of the body read: nothing about the request can make
            # an unconfigured runtime launchable, and the panel needs the code,
            # not a generic conflict, to offer setup instead of a retry.
            return _error(409, "launch_not_configured", LAUNCH_NOT_CONFIGURED)
        body = await _json_body(request)
        return await _guarded(
            context.run_role_on_task,
            context,
            agent_id=str(body.get("agent_id", "")),
            task_id=str(body.get("task_id", "")),
            wall_time_seconds=body.get("wall_time_seconds"),
            idempotency_key=body.get("idempotency_key"),
            context_pack_id=body.get("context_pack_id"),
            context_pack_revision=body.get("context_pack_revision"),
        )

    @router.post("/api/provider-auth/{profile_id}/login")
    async def start_provider_login(profile_id: str) -> Response:
        return await _guarded(
            context.start_provider_login,
            context,
            profile_id=profile_id,
        )

    @router.post("/api/provider-auth/{profile_id}/cancel")
    async def cancel_provider_login(profile_id: str) -> Response:
        return await _guarded(
            context.cancel_provider_login,
            context,
            profile_id=profile_id,
        )

    @router.post("/api/provider-auth/{profile_id}/check")
    async def check_provider_auth(profile_id: str) -> Response:
        return await _guarded(
            context.check_provider_auth,
            context,
            profile_id=profile_id,
        )


async def _events(context: UIContext, last_event_id: str | None) -> AsyncIterator[bytes]:
    yield _sse(
        "hello",
        {
            "seq": context.seq,
            "server_instance_id": context.server_instance_id,
            "heartbeat_seconds": _HEARTBEAT_SECONDS,
            "poll_seconds": _POLL_SECONDS,
        },
        event_id=context.seq,
        instance=context.server_instance_id,
    )
    await asyncio.to_thread(context.refresh_if_changed)
    seq, graph = await asyncio.to_thread(context.snapshot_frame)
    yield _sse(
        "snapshot",
        {"seq": seq, "graph": graph},
        event_id=seq,
        instance=context.server_instance_id,
    )

    parsed = _parse_last_event_id(last_event_id)
    if parsed is not None:
        instance, seq = parsed
        if instance != context.server_instance_id:
            yield _sse(
                "resume_gap",
                {"from": seq, "to": context.seq, "reason": "server_restarted"},
                event_id=context.seq,
                instance=context.server_instance_id,
            )
        elif seq < context.seq:
            # There is no durable event history yet, so a caught-up client is
            # told plainly rather than left to assume it missed nothing.
            yield _sse(
                "resume_gap",
                {"from": seq, "to": context.seq, "reason": "no_event_history"},
                event_id=context.seq,
                instance=context.server_instance_id,
            )

    # The panel's operator session can be replaced under the same identity when
    # its TTL expired -- a laptop asleep past eight hours -- and the tab cached
    # `writer_session_id` exactly once, at boot.  The stream is the only channel
    # an open tab keeps reading, so the replacement is announced here, as the
    # frozen informational code `session_expired_recovered`.  A connection
    # announces the current id of any lineage that has grown past its first
    # session and it has not announced yet: an open connection reports the
    # recovery within one poll, and a tab reconnecting after one is told
    # immediately instead of trusting its stale cache.  The frame carries no
    # event id, so it can never disturb Last-Event-ID resumption.
    announced_session: str | None = None

    def _recovery_frame() -> bytes | None:
        nonlocal announced_session
        lineage = context.session_lineage()
        if len(lineage) < 2 or lineage[-1] == announced_session:
            return None
        announced_session = lineage[-1]
        return _sse(
            "session_expired_recovered",
            {
                "code": "session_expired_recovered",
                "writer_session_id": lineage[-1],
                "previous_session_id": lineage[-2],
                "writer_session_ids": list(lineage),
            },
        )

    recovered = await asyncio.to_thread(_recovery_frame)
    if recovered is not None:
        yield recovered

    # Each connection tracks the last sequence it sent.  refresh_if_changed is a
    # one-shot consumer of a shared fingerprint, so whichever connection polls
    # first after a write triggers the rebuild and the others saw `changed ==
    # False` and never sent the frame -- one watcher got every update while the
    # rest stayed stale showing "live" (round 2, design).  Emitting whenever the
    # shared seq advances past this connection's own last-sent value delivers the
    # frame to every connection regardless of which one triggered the rebuild.
    last_sent = seq
    since_heartbeat = 0.0
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        since_heartbeat += _POLL_SECONDS
        recovered = await asyncio.to_thread(_recovery_frame)
        if recovered is not None:
            yield recovered
            since_heartbeat = 0.0
        await asyncio.to_thread(context.refresh_if_changed)
        if context.seq > last_sent:
            seq, graph = await asyncio.to_thread(context.snapshot_frame)
            yield _sse(
                "snapshot",
                {"seq": seq, "graph": graph},
                event_id=seq,
                instance=context.server_instance_id,
            )
            last_sent = seq
            since_heartbeat = 0.0
        elif since_heartbeat >= _HEARTBEAT_SECONDS:
            # A comment carries no id, so it cannot disturb Last-Event-ID.
            yield b": keepalive\n\n"
            since_heartbeat = 0.0


def _parse_last_event_id(value: str | None) -> tuple[str, int] | None:
    if not value or ":" not in value:
        return None
    instance, _, raw = value.rpartition(":")
    try:
        return instance, int(raw)
    except ValueError:
        return None


def serve(
    context: UIContext,
    *,
    port: int = 0,
    open_browser: bool = True,
    emit: Callable[[int, str], None] | None = None,
) -> None:
    """Bind loopback, print the one-time URL, then run the server."""

    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(64)
    bound_port = listener.getsockname()[1]
    session_token = new_token()
    exchange_code = new_token()
    app = create_app(
        context,
        token=session_token,
        exchange_code=exchange_code,
        port=bound_port,
    )
    if emit is not None:
        emit(bound_port, exchange_code)
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{bound_port}/work#c={exchange_code}")
    config = uvicorn.Config(
        app,
        log_level="warning",
        access_log=False,
        # An SSE connection never finishes on its own, so a graceful shutdown
        # that waits for open responses waits for the browser tab instead of
        # the person at the keyboard: Ctrl-C left this call blocked, the
        # session and the panel lock alive, and the next panel refused.  One
        # second lets an in-flight ordinary request land; then the streams are
        # cancelled and this returns, which is what lets the caller's finally
        # block actually close the session.
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    cancellation_filter = _ExpectedShutdownCancellationFilter(lambda: server.should_exit)
    error_logger = logging.getLogger("uvicorn.error")
    error_logger.addFilter(cancellation_filter)
    try:
        server.run(sockets=[listener])
    finally:
        error_logger.removeFilter(cancellation_filter)
