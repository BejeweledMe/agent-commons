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
import secrets
import socket
import webbrowser
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from agent_commons.errors import CommonsError
from agent_commons.ui import ENTITY_SCHEMA, read_spa
from agent_commons.ui.context import LAUNCH_NOT_CONFIGURED, UIContext
from agent_commons.ui.security import (
    PUBLIC_PATHS,
    SECURITY_HEADERS,
    allowed_hosts,
    allowed_origins,
    bearer_token,
    content_security_policy,
    new_token,
    token_matches,
)
from agent_commons.ui.setup import (
    SETUP_NOT_A_REPOSITORY,
    SETUP_UNINITIALIZED,
    SetupError,
    setup_state,
)

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

#: The complete mutating surface, named once so a route cannot be added without
#: the invariant test noticing.  Every one of these is a thin adapter over a
#: `CommonsManager` method; the UI is a third adapter beside the CLI and MCP,
#: not a second write path.
MUTATING_ROUTES = (
    ("POST", "/api/operations/{operation_id}/answer"),
    ("POST", "/api/chat"),
    ("POST", "/api/chat/{thread_id}/messages"),
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
LAUNCH_ROUTES = (("POST", "/api/delegations"),)

#: First run, declared apart from every other privilege because it is the only
#: surface that writes outside the ledger: one route creates the workspace
#: through the same initializer `agent-commons init` calls, the other writes the
#: operator's own runtime config and adopts it into this running panel.  Both
#: are registered by any operator panel -- a read-only one registers neither, so
#: the zero-non-GET invariant is untouched -- and the reading half of the same
#: surface (`GET /api/setup`, `GET /api/setup/preflight`) is in no tuple at all.
#: These two are also the only non-GET routes not bound to an existing
#: workspace: initialization is what makes the workspace exist.
SETUP_ROUTES = (
    ("POST", "/api/setup/initialize"),
    ("POST", "/api/setup/runtime-config"),
)

_HEARTBEAT_SECONDS = 15.0
_POLL_SECONDS = 2.0

#: What the operator can do about a panel opened on a directory that is not a
#: workspace yet. The panel serves the first-run screen; the routes that record
#: canonical events are there, and they say this until it exists.
_NOT_INITIALIZED_ACTIONS = ["run first run from this panel", "or run `agent-commons init` here"]


class _NotInitialized(Exception):
    """No workspace yet, so nothing can be recorded. Rendered by ``create_app``.

    Raised from a route dependency rather than from a handler on purpose: a
    dependency runs *before* the request body is read, which is the same
    discipline `launch_not_configured` already follows. Nothing in a body can
    make an absent workspace recordable, so nothing in it is worth parsing.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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

    def __init__(self, app: FastAPI, *, requires: list[Any] | None = None) -> None:
        self._app = app
        self._requires = list(requires or ())

    def post(self, path: str) -> Callable[[Any], Any]:
        return self._app.post(path, dependencies=self._requires)


def _workspace_bound(app: FastAPI, context: UIContext) -> _RouteGroup:
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
        state = await asyncio.to_thread(setup_state, context.repo)
        if state != SETUP_UNINITIALIZED and await asyncio.to_thread(lambda: context.writes_enabled):
            return
        if state == SETUP_NOT_A_REPOSITORY:
            raise _NotInitialized(
                SETUP_NOT_A_REPOSITORY,
                "this directory is not a git repository, so there is nothing for a "
                "workspace to attach to and nothing to record against",
            )
        raise _NotInitialized(
            SETUP_UNINITIALIZED,
            "this directory has no workspace yet, so this panel holds no operator "
            "session and records no canonical event",
        )

    return _RouteGroup(app, requires=[Depends(_require_workspace)])


def _error(status: int, code: str, message: str, actions: list[str] | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if actions:
        payload["error"]["safe_next_actions"] = actions
    response = JSONResponse(payload, status_code=status)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="agent-commons-ui"'
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


def create_app(context: UIContext, *, token: str, port: int) -> FastAPI:
    app = FastAPI(title="Agent Commons UI", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = allowed_hosts(port)
    origins = allowed_origins(port)

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
            elif request.url.path not in PUBLIC_PATHS and not _authorized(request):
                response = _error(
                    401,
                    "unauthorized",
                    "a bearer token is required",
                    ["reopen the URL printed by `agent-commons ui`"],
                )
            else:
                response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    def _authorized(request: Request) -> bool:
        presented = bearer_token(request.headers.get("authorization"))
        return presented is not None and token_matches(presented, token)

    async def _not_initialized(_: Request, exc: Exception) -> Response:
        # The frozen refusal table travels as a code, exactly as the first-run
        # screen's other refusals do; the class name would say nothing.
        assert isinstance(exc, _NotInitialized)
        return _error(409, exc.code, exc.message, _NOT_INITIALIZED_ACTIONS)

    app.add_exception_handler(_NotInitialized, _not_initialized)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        nonce = secrets.token_urlsafe(16)
        body = read_spa().replace("__CSP_NONCE__", nonce)
        response = HTMLResponse(body)
        response.headers["Content-Security-Policy"] = content_security_policy(nonce)
        return response

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/meta")
    async def meta() -> Response:
        return JSONResponse(await asyncio.to_thread(context.meta))

    @app.get("/api/graph")
    async def graph() -> Response:
        await asyncio.to_thread(context.refresh_if_changed)
        return JSONResponse(await asyncio.to_thread(context.graph))

    @app.get("/api/entities/{kind}/{entity_id}")
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

    @app.get("/api/search")
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

    @app.get("/api/operations")
    async def operations() -> Response:
        return JSONResponse(await asyncio.to_thread(context.pending_operations))

    @app.get("/api/chat")
    async def chat() -> Response:
        return JSONResponse(await asyncio.to_thread(context.engagements))

    @app.get("/api/proposals")
    async def proposals() -> Response:
        return JSONResponse(await asyncio.to_thread(context.agent_proposals))

    @app.get("/api/attention")
    async def attention() -> Response:
        # One canonical queue: the same source as the amber ring and the footer
        # count, so the list can never be empty while the graph says N are
        # waiting on you.
        return JSONResponse(await asyncio.to_thread(context.attention))

    @app.get("/api/catalog")
    async def catalog() -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(context.catalog))
        except CommonsError as exc:
            # A catalogue that fails to load is a misconfiguration, not a server
            # fault: name it rather than returning an opaque 500 (round 2).
            return _error(422, type(exc).__name__, str(exc))

    @app.get("/api/launch")
    async def launch_options() -> Response:
        # The roles and tasks the panel needs to offer a run, plus whether
        # launching is enabled at all. Readable in any mode; acting on it is not.
        return JSONResponse(await asyncio.to_thread(context.launch_options))

    @app.get("/api/runs")
    async def runs() -> Response:
        # Live and recent run phases, metadata only. Readable in any mode.
        return JSONResponse(await asyncio.to_thread(context.runs))

    @app.get("/api/setup")
    async def setup_status() -> Response:
        # Readable in any mode; acting on it needs a writing panel. This is the
        # one route that names operator paths, and only until the runtime is
        # configured -- see `UIContext.setup_status`.
        return JSONResponse(await asyncio.to_thread(context.setup_status))

    @app.get("/api/setup/preflight")
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
        recording = _workspace_bound(app, context)
        _register_writes(recording, context)
        _register_launch(recording, context)
        _register_catalog_writes(recording, context)
        # First run is the one surface that must answer before the workspace
        # exists -- it is what makes it exist -- so it is bound to nothing.
        _register_setup(_RouteGroup(app), context)

    @app.get("/api/stream")
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


async def _guarded(action: Callable[..., Any], context: UIContext, **kwargs: Any) -> Response:
    """Run one write and surface the guard that refused it, if any."""

    try:
        result = await asyncio.to_thread(action, **kwargs)
    except CommonsError as exc:
        # Refusals are the interesting output here: the guard that fired is
        # what the operator needs on the node, not a generic failure.
        return _error(409, type(exc).__name__, str(exc))
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
    """Attach the two first-run writes, declared apart in ``SETUP_ROUTES``.

    Neither takes a parameter, and that is the security property of this
    surface, not an omission: the directory a workspace is created in is the one
    the panel was opened on, and the operator config always lands on the frozen
    XDG path.  A bearer token therefore cannot aim either write at a path of its
    choosing, which is also why no manual "type the binary path here" field
    exists anywhere on the first-run screen.
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
    token = new_token()
    app = create_app(context, token=token, port=bound_port)
    if emit is not None:
        emit(bound_port, token)
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{bound_port}/#t={token}")
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[listener])
