"""Thin authenticated HTTP registration for typed Design Gallery reads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from fastapi.responses import JSONResponse, Response

from agent_commons.services.design_authoring import build_authoring_snapshot
from agent_commons.services.design_gallery import (
    DesignGalleryReads,
    GalleryManager,
    GalleryReadRefusal,
    GalleryRefusalCode,
    GallerySnapshot,
)
from agent_commons.ui.gallery_dtos import GalleryResponseDTO


class GalleryRouteRegistrar(Protocol):
    """The narrow composition seam supplied by the UI server."""

    def get(
        self, path: str, **kwargs: object
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...


def register_gallery_routes(
    routes: GalleryRouteRegistrar,
    *,
    dependencies: list[object],
    manager_factory: Callable[[], GalleryManager],
    authoring_session_factory: Callable[[], str | None] = lambda: None,
) -> None:
    """Register list/detail reads; authentication remains server-owned.

    The composition root supplies its existing workspace dependency and manager
    factory.  This module neither creates a session nor adds a canonical write
    path.  Artifact bytes continue through the separately authenticated,
    hardened preview route.
    """

    @routes.get("/api/gallery", dependencies=dependencies)
    async def gallery_list() -> Response:
        return await _read(lambda reads: reads.list(), manager_factory)

    @routes.get("/api/gallery/authoring", dependencies=dependencies)
    async def gallery_authoring() -> Response:
        def _run() -> dict[str, object]:
            try:
                manager = manager_factory()
                producer_session_id = authoring_session_factory()
                return build_authoring_snapshot(
                    manager,
                    producer_session_id=producer_session_id,
                )
            except Exception:
                return {
                    "schema": "agent_commons.gallery-authoring.v1",
                    "state": "unavailable",
                    "writes_enabled": False,
                    "candidates": [],
                    "error": {
                        "code": "gallery_authoring_unavailable",
                        "message": "Gallery authoring could not build a safe current snapshot.",
                        "safe_next_actions": [
                            "Run workspace diagnostics, then reload the Gallery."
                        ],
                    },
                }

        payload = await asyncio.to_thread(_run)
        return JSONResponse(
            payload,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @routes.get("/api/gallery/{design_package_id}", dependencies=dependencies)
    async def gallery_detail(design_package_id: str) -> Response:
        return await _read(lambda reads: reads.get(design_package_id), manager_factory)


async def _read(
    operation: Callable[[DesignGalleryReads], GallerySnapshot],
    manager_factory: Callable[[], GalleryManager],
) -> Response:
    def _run() -> GalleryResponseDTO:
        try:
            # The manager is deliberately accepted only at this composition
            # edge; DesignGalleryReads immediately narrows it to snapshot().
            reads = DesignGalleryReads(manager_factory())
            result = operation(reads)
            return GalleryResponseDTO.from_snapshot(result)
        except GalleryReadRefusal as exc:
            return GalleryResponseDTO.from_refusal(exc)
        except Exception:
            # Projection/manager exception text may contain local paths or
            # ledger data.  Collapse it before the browser boundary.
            return GalleryResponseDTO.from_refusal(
                GalleryReadRefusal(
                    GalleryRefusalCode.PROJECTION_UNAVAILABLE,
                    409,
                    "The canonical Gallery projection is not currently available.",
                    ("run workspace diagnostics", "rebuild the derived projection and retry"),
                )
            )

    dto = await asyncio.to_thread(_run)
    status_code = 200
    if dto.state == "error":
        # Every error DTO is produced from a typed refusal.  The lookup is kept
        # local and closed rather than forwarding exception-derived status.
        code = dto.error.code if dto.error is not None else ""
        status_code = {
            GalleryRefusalCode.INVALID_ID.value: 400,
            GalleryRefusalCode.NOT_FOUND.value: 404,
            GalleryRefusalCode.PROJECTION_UNAVAILABLE.value: 409,
            GalleryRefusalCode.BOUNDS_EXCEEDED.value: 409,
        }.get(code, 409)
    return JSONResponse(
        dto.to_wire(),
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
