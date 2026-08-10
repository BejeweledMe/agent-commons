"""FastAPI application for the local read-only workspace view.

Only ``GET`` routes are registered.  The absence of a mutating route is a
structural property of this module, verified by test, rather than a rule some
middleware is trusted to enforce.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import webbrowser
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from agent_commons.errors import CommonsError
from agent_commons.ui import ENTITY_SCHEMA, read_spa
from agent_commons.ui.context import UIContext
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
    ("POST", "/api/chat"),
    ("POST", "/api/chat/{thread_id}/messages"),
    ("POST", "/api/agents"),
    ("POST", "/api/agents/proposals/{thread_id}/approve"),
    ("POST", "/api/agents/{agent_id}/reconfigure"),
    ("POST", "/api/agents/{agent_id}/retire"),
    ("POST", "/api/agents/{agent_id}/messages"),
)

#: Catalogue editing is behind its own gate, so it has its own allowlist.
#: Adding a skill and adding a role are different privileges and the test that
#: pins the mutating surface should say so.
CATALOG_ROUTES = (
    ("POST", "/api/catalog/entries"),
    ("POST", "/api/catalog/entries/remove"),
)

_HEARTBEAT_SECONDS = 15.0
_POLL_SECONDS = 2.0


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

    @app.get("/api/chat")
    async def chat() -> Response:
        return JSONResponse(await asyncio.to_thread(context.engagements))

    @app.get("/api/proposals")
    async def proposals() -> Response:
        return JSONResponse(await asyncio.to_thread(context.agent_proposals))

    @app.get("/api/catalog")
    async def catalog() -> Response:
        return JSONResponse(await asyncio.to_thread(context.catalog))

    if context.writes_enabled:
        _register_writes(app, context)
    if context.catalog_editing_enabled:
        _register_catalog_writes(app, context)

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


def _register_writes(app: FastAPI, context: UIContext) -> None:
    """Attach the mutating surface. Every handler ends in ``CommonsManager``."""

    async def _record(action: Callable[..., Any], **kwargs: Any) -> Response:
        return await _guarded(action, context, **kwargs)

    async def _body(request: Request) -> dict[str, Any]:
        return await _json_body(request)

    @app.post("/api/chat")
    async def open_chat(request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.open_engagement,
            subject=str(body.get("subject", "")),
            body=str(body.get("message", "")),
            objective_id=body.get("objective_id"),
            idempotency_key=body.get("idempotency_key"),
        )

    @app.post("/api/chat/{thread_id}/messages")
    async def say_in_chat(thread_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.say_in_engagement,
            thread_id=thread_id,
            expected_revision=str(body.get("expected_revision", "")),
            body=str(body.get("message", "")),
            idempotency_key=body.get("idempotency_key"),
        )

    @app.post("/api/agents")
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

    @app.post("/api/agents/proposals/{thread_id}/approve")
    async def approve_proposal(thread_id: str, request: Request) -> Response:
        body = await _body(request)
        return await _record(
            context.approve_agent_proposal,
            thread_id=thread_id,
            idempotency_key=body.get("idempotency_key"),
        )

    @app.post("/api/agents/{agent_id}/reconfigure")
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

    @app.post("/api/agents/{agent_id}/retire")
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

    @app.post("/api/agents/{agent_id}/messages")
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


def _register_catalog_writes(app: FastAPI, context: UIContext) -> None:
    """Attach the operator-catalogue surface, gated separately from role writes."""

    @app.post("/api/catalog/entries")
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

    @app.post("/api/catalog/entries/remove")
    async def remove_catalog_entry(request: Request) -> Response:
        body = await _json_body(request)
        return await _guarded(
            context.remove_catalog_entry,
            context,
            section=str(body.get("section", "")),
            entry_id=str(body.get("id", "")),
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

    since_heartbeat = 0.0
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        since_heartbeat += _POLL_SECONDS
        changed = await asyncio.to_thread(context.refresh_if_changed)
        if changed:
            seq, graph = await asyncio.to_thread(context.snapshot_frame)
            yield _sse(
                "snapshot",
                {"seq": seq, "graph": graph},
                event_id=seq,
                instance=context.server_instance_id,
            )
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
