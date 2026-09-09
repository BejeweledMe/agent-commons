"""Authenticated composition seam and explicit DTOs for scoped output reads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import loads_json_strict
from agent_commons.runtime.live_previews import LivePreview, LivePreviewRefusal, LivePreviewRegistry
from agent_commons.services.outputs import (
    ImageOutput,
    OutputManager,
    OutputReadRefusal,
    OutputReads,
    OutputSnapshot,
    OutputSummary,
    unavailable,
)
from agent_commons.storage.opstate import iso_timestamp


class OutputRouteRegistrar(Protocol):
    def post(
        self, path: str, **kwargs: object
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...

    def get(
        self, path: str, **kwargs: object
    ) -> Callable[[Callable[..., object]], Callable[..., object]]: ...


@dataclass(frozen=True, slots=True)
class OutputResponseDTO:
    snapshot: OutputSnapshot

    def to_wire(self) -> dict[str, object]:
        value = self.snapshot
        return {
            "schema": "agent_commons.outputs.v1",
            "scope": {"kind": value.scope.kind, "id": value.scope.identifier},
            "versions": value.versions,
            "items": [
                _live_wire(item) if isinstance(item, LivePreview) else _image_wire(item)
                for item in value.items
            ],
            "truncated": False,
        }


@dataclass(frozen=True, slots=True)
class OutputSummaryDTO:
    summary: OutputSummary

    def to_wire(self) -> dict[str, object]:
        value = self.summary
        return {
            "schema": "agent_commons.outputs-summary.v1",
            "scope": {"kind": value.scope.kind, "id": value.scope.identifier},
            "versions": "latest",
            "total": value.total,
            "counts": {
                "unchecked": value.unchecked,
                "stale": value.stale,
                "unavailable": value.unavailable,
            },
            "previews_verified": False,
        }


def _image_wire(item: ImageOutput) -> dict[str, object]:
    # Explicit allowlist: service/manifest dictionaries never cross this boundary.
    value = {
        "kind": item.kind,
        "output_id": item.output_id,
        "series_id": item.series_id,
        "title": item.title,
        "package_id": item.package_id,
        "package_revision": item.package_revision,
        "screen_id": item.screen_id,
        "artifact_id": item.artifact_id,
        "artifact_revision": item.artifact_revision,
        "content_revision": item.content_revision,
        "task_id": item.task_id,
        "task_revision": item.task_revision,
        "producer_session_id": item.producer_session_id,
        "producer_agent_id": item.producer_agent_id,
        "producer_delegation_id": item.producer_delegation_id,
        "recorded_at": item.recorded_at,
        "media_type": item.media_type,
        "classification": item.classification,
        "state": item.state,
        "reason": item.reason,
        # Additive nullable review standing of the producing task. Absent server
        # knowledge is null, never an implied "awaiting" or an implied approval.
        "review_state": item.review_state,
        "latest": item.latest,
        "version_count": item.version_count,
        "width": item.width,
        "height": item.height,
    }

    if item.kind == "artifact_image":
        for field in ("package_id", "package_revision", "screen_id"):
            value.pop(field)
        value["delegation_revision"] = item.delegation_revision
        value["historical_preview_verified"] = item.historical_preview_verified
    return value


def _live_wire(item: LivePreview) -> dict[str, object]:
    return {
        "kind": "live_preview",
        "output_id": item.output_id,
        "title": item.title,
        "task_id": item.task_id,
        "task_revision": item.task_revision,
        "producer_agent_id": item.producer_agent_id,
        "producer_session_id": item.producer_session_id,
        "producer_delegation_id": item.producer_delegation_id,
        "delegation_revision": item.delegation_revision,
        "url": item.url if item.state == "reported_ready" else None,
        "origin": item.origin,
        "published_at": iso_timestamp(item.published_at),
        "expires_at": iso_timestamp(item.expires_at),
        "reported_state": item.reported_state,
        "state": item.state,
        "reachability": "unknown",
        "latest": item.latest,
        "version_count": item.version_count,
    }


def register_output_routes(
    routes: OutputRouteRegistrar,
    *,
    dependencies: list[object],
    manager_factory: Callable[[], OutputManager],
    live_registry_factory: Callable[[], LivePreviewRegistry] | None = None,
    writer_factory: Callable[[], OutputManager] | None = None,
    authorize_publish: Callable[[], None] | None = None,
    write_dependencies: list[object] | None = None,
    register_writes: bool = False,
) -> None:
    """Mount on a fixed project context using its existing auth dependencies.

    The composition root supplies the authenticated, workspace-bound registrar.
    Publication is disabled unless all operator writer/gate/store seams are supplied.
    Existing artifact preview routes remain the sole source of image bytes.
    """

    @routes.get("/api/outputs/summary", dependencies=dependencies)
    async def output_summary(scope_kind: str = "", scope_id: str = "") -> Response:
        return await _read(
            lambda reads: OutputSummaryDTO(reads.summary(scope_kind, scope_id)).to_wire(),
            manager_factory,
            live_registry_factory,
        )

    @routes.get("/api/outputs", dependencies=dependencies)
    async def output_list(
        scope_kind: str = "", scope_id: str = "", versions: str = "latest"
    ) -> Response:
        return await _read(
            lambda reads: OutputResponseDTO(
                reads.list(scope_kind, scope_id, versions=versions)
            ).to_wire(),
            manager_factory,
            live_registry_factory,
        )

    if not register_writes:
        return

    @routes.post(
        "/api/outputs/live-previews", dependencies=dependencies + (write_dependencies or [])
    )
    async def publish_live_preview(request: Request) -> Response:
        # Authenticate/authorize before accepting the body or opening private state.
        try:
            if authorize_publish is None or writer_factory is None or live_registry_factory is None:
                raise LivePreviewRefusal("live_preview_publication_disabled", 403)
            await asyncio.to_thread(authorize_publish)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 8192:
                    raise LivePreviewRefusal("live_preview_invalid_request", 413)
            payload = loads_json_strict(bytes(content))

            def publish() -> dict[str, object]:
                row = live_registry_factory().publish(writer_factory().snapshot(), payload)
                return {
                    "schema": "agent_commons.live-preview-published.v1",
                    "item": _live_wire(row),
                }

            result = await asyncio.to_thread(publish)
            return JSONResponse(
                result, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            )
        except Exception as exc:
            refusal = exc if isinstance(exc, LivePreviewRefusal) else LivePreviewRefusal()
            return JSONResponse(
                {
                    "error": {
                        "code": refusal.code,
                        "message": refusal.message,
                        "safe_next_actions": ["Refresh the task and its run before retrying."],
                    }
                },
                status_code=refusal.status_code,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )


async def _read(
    operation: Callable[[OutputReads], dict[str, object]],
    manager_factory: Callable[[], OutputManager],
    live_registry_factory: Callable[[], LivePreviewRegistry] | None = None,
) -> Response:
    def run() -> tuple[dict[str, object], int]:
        try:
            return operation(
                OutputReads(
                    manager_factory(),
                    live_previews=live_registry_factory() if live_registry_factory else None,
                )
            ), 200
        except OutputReadRefusal as exc:
            refusal = exc
        except Exception:
            refusal = unavailable()
        return {
            "error": {
                "code": refusal.code,
                "message": refusal.message,
                "safe_next_actions": ["Refresh the selected task or agent and retry."],
            }
        }, refusal.status_code

    payload, status = await asyncio.to_thread(run)
    return JSONResponse(
        payload,
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
