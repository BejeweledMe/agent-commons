"""Authenticated read-only HTTP route for bundled Starter Pack examples."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from fastapi.responses import JSONResponse, Response

from agent_commons.ui.starter_pack_dtos import StarterPackCatalogRefusalDTO
from agent_commons.ui.starter_packs import (
    StarterPackCatalogUnavailable,
    read_starter_pack_catalog,
)


class StarterPackRouteRegistrar(Protocol):
    """The small route-composition seam needed by this read-only adapter."""

    def get(
        self, path: str, **kwargs: object
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...


def register_starter_pack_routes(
    routes: StarterPackRouteRegistrar, *, dependencies: list[object]
) -> None:
    """Register the authenticated workspace-bound non-mutating catalogue route."""

    @routes.get("/api/work/starter-packs", dependencies=dependencies)
    async def starter_pack_catalog() -> Response:
        try:
            catalog = await asyncio.to_thread(read_starter_pack_catalog)
        except StarterPackCatalogUnavailable:
            return JSONResponse(StarterPackCatalogRefusalDTO().to_wire(), status_code=409)
        return JSONResponse(catalog.to_wire())
