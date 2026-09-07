"""Closed operator HTTP surface for the service-owned library."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.library import LibraryError, LibraryStore


class LibraryRouteRegistrar(Protocol):
    def get(self, path: str, **kwargs: object) -> Callable[..., object]: ...

    def post(self, path: str, **kwargs: object) -> Callable[..., object]: ...


async def _response(operation: Callable[[], dict[str, Any]]) -> Response:
    try:
        result = await asyncio.to_thread(operation)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except LibraryError as exc:
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status
        )
    except (ConfigurationError, ValidationError, OSError):
        return JSONResponse(
            {
                "error": {
                    "code": "library_unavailable",
                    "message": "The service library is unavailable. Check configuration and retry.",
                }
            },
            status_code=409,
        )


async def _body(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 524_288:
            raise LibraryError("library_too_large", "Library edit exceeds its request limit.", 413)
    value = loads_json_strict(bytes(body))
    if not isinstance(value, dict):
        raise LibraryError("library_invalid", "Library edit must be an object.")
    return value


def register_library_routes(
    routes: LibraryRouteRegistrar,
    *,
    dependencies: list[object],
    write_dependencies: list[object],
    store_factory: Callable[[], LibraryStore],
    authorize_edit: Callable[[], None],
    editing_enabled: Callable[[], bool],
    authorize_content: Callable[[], None] | None = None,
    register_writes: bool = True,
) -> None:
    """Register safe metadata, privileged content reads and operator-only edits.

    The composition root retains authentication, operator-session checks and
    write-route allowlists. Storage paths are never request parameters.
    """
    content_gate = authorize_content or authorize_edit

    @routes.get("/api/library", dependencies=dependencies)
    async def library_catalog() -> Response:
        return await _response(lambda: store_factory().catalog(include_editable=editing_enabled()))

    @routes.get("/api/library/{kind}/{source}/{id}/{version}", dependencies=write_dependencies)
    async def library_detail(kind: str, source: str, id: str, version: str) -> Response:
        def operation() -> dict[str, Any]:
            content_gate()
            return store_factory().detail(
                {"kind": kind, "source": source, "id": id, "version": version}, editable=True
            )

        return await _response(operation)

    @routes.get(
        "/api/library/skill/{source}/{id}/{version}/files/{path:path}",
        dependencies=write_dependencies,
    )
    async def library_file(source: str, id: str, version: str, path: str) -> Response:
        def operation() -> dict[str, Any]:
            content_gate()
            return store_factory().read_file(
                {"kind": "skill", "source": source, "id": id, "version": version}, path
            )

        return await _response(operation)

    if not register_writes:
        return

    @routes.post("/api/library/{kind}", dependencies=write_dependencies)
    async def save_library(kind: str, request: Request) -> Response:
        try:
            value = await _body(request)
        except (LibraryError, ValidationError) as exc:
            status = exc.status if isinstance(exc, LibraryError) else 422
            return JSONResponse(
                {
                    "error": {
                        "code": "library_invalid",
                        "message": "Library edit has an invalid or oversized body.",
                    }
                },
                status_code=status,
            )

        def operation() -> dict[str, Any]:
            authorize_edit()
            required = {"id", "content", "expected_version", "idempotency_key"}
            if set(value) - (required | {"fork_ref"}) or not required <= set(value):
                raise LibraryError("library_invalid", "Library edit has an invalid shape.")
            return store_factory().save(kind=kind, **value)

        return await _response(operation)
