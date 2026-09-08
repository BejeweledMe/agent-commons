"""Authenticated project conversations and bounded private attachment transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.runtime.message_attachments import MAX_ATTACHMENT_BYTES
from agent_commons.services.conversations import (
    ConversationDraftExpired,
    ConversationIntentConflict,
    ConversationMessageInvalid,
    ConversationRevisionConflict,
    Conversations,
)


def register_conversation_routes(
    routes: Any,
    *,
    dependencies: list[object],
    manager_factory: Callable[[], Any],
    writer_factory: Callable[[], Any],
    authorize_write: Callable[[], None],
    register_writes: bool,
) -> None:
    async def respond(operation: Callable[[], Any]) -> Response:
        try:
            value = await asyncio.to_thread(operation)
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except (
            ConversationDraftExpired,
            ConversationRevisionConflict,
            ConversationMessageInvalid,
            ConversationIntentConflict,
        ) as exc:
            code = (
                "conversation_revision_conflict"
                if isinstance(exc, ConversationRevisionConflict)
                else (
                    "conversation_intent_conflict"
                    if isinstance(exc, ConversationIntentConflict)
                    else (
                        "conversation_draft_expired"
                        if isinstance(exc, ConversationDraftExpired)
                        else "conversation_invalid_message"
                    )
                )
            )
            return JSONResponse(
                {"error": {"code": code}},
                status_code=422 if isinstance(exc, ConversationMessageInvalid) else 409,
                headers={"Cache-Control": "no-store"},
            )
        except (CommonsError, OSError):
            return JSONResponse(
                {
                    "error": {
                        "code": "conversation_unavailable",
                        "message": (
                            "The conversation changed or is unavailable. "
                            "Refresh and retry the same operation."
                        ),
                    }
                },
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )

    async def body(request: Request) -> dict[str, Any]:
        data = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 100_000:
                    raise ValidationError("Conversation request is too large.")
        result = loads_json_strict(bytes(data))
        if not isinstance(result, dict):
            raise ValidationError("Conversation request must be an object.")
        return result

    def scope(kind: str, identifier: str) -> dict[str, str]:
        return {"kind": kind, **({"id": identifier} if identifier else {})}

    @routes.get("/api/conversations", dependencies=dependencies)
    async def list_conversations(scope_kind: str = "project", scope_id: str = "") -> Response:
        return await respond(
            lambda: Conversations(manager_factory()).list(scope(scope_kind, scope_id))
        )

    @routes.get("/api/conversations/{thread_id}/messages", dependencies=dependencies)
    async def messages(
        thread_id: str,
        after: str | None = None,
        before: str | None = None,
        tail: bool = False,
        limit: int = 50,
    ) -> Response:
        return await respond(
            lambda: Conversations(manager_factory()).messages(
                thread_id, after=after, before=before, tail=tail, limit=limit
            )
        )

    @routes.get(
        "/api/conversations/{thread_id}/messages/{message_id}/attachments/{attachment_id}",
        dependencies=dependencies,
    )
    async def attachment(thread_id: str, message_id: str, attachment_id: str) -> Response:
        try:
            descriptor, data = await asyncio.to_thread(
                lambda: Conversations(manager_factory()).read_attachment(
                    thread_id, message_id, attachment_id
                )
            )
        except (CommonsError, OSError):
            return JSONResponse({"error": {"code": "attachment_unavailable"}}, status_code=409)
        return Response(
            data,
            media_type=descriptor.media_type
            if descriptor.kind == "image"
            else "application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(descriptor.filename, safe=""),
            },
        )

    if not register_writes:
        return

    async def write(
        request: Request, operation: Callable[[Conversations, dict[str, Any]], Any]
    ) -> Response:
        try:
            value = await body(request)
        except (CommonsError, TimeoutError):
            return JSONResponse(
                {"error": {"code": "conversation_invalid_request"}}, status_code=422
            )

        def run() -> Any:
            authorize_write()
            return operation(Conversations(writer_factory()), value)

        return await respond(run)

    @routes.post("/api/conversations", dependencies=dependencies)
    async def ensure(request: Request) -> Response:
        def run(service: Conversations, value: dict[str, Any]) -> Any:
            if set(value) != {"scope", "idempotency_key"}:
                raise ValidationError("Invalid conversation fields.")
            return service.ensure(**value)

        return await write(request, run)

    @routes.post("/api/conversations/{thread_id}/messages", dependencies=dependencies)
    async def send(thread_id: str, request: Request) -> Response:
        def run(service: Conversations, value: dict[str, Any]) -> Any:
            required = {"body", "expected_revision", "idempotency_key"}
            if not required <= set(value) or set(value) - required - {
                "draft_id",
                "reply_to_message_id",
            }:
                raise ValidationError("Invalid message fields.")
            return service.send(thread_id, **value)

        return await write(request, run)

    @routes.post("/api/conversations/{thread_id}/drafts", dependencies=dependencies)
    async def reserve(thread_id: str, request: Request) -> Response:
        def run(service: Conversations, value: dict[str, Any]) -> Any:
            if value:
                raise ValidationError("Draft creation accepts no fields.")
            return service.reserve_draft(thread_id)

        return await write(request, run)

    @routes.get("/api/conversations/{thread_id}/drafts/{draft_id}", dependencies=dependencies)
    async def draft(thread_id: str, draft_id: str) -> Response:
        return await respond(
            lambda: Conversations(writer_factory()).show_draft(thread_id, draft_id)
        )

    @routes.post(
        "/api/conversations/{thread_id}/drafts/{draft_id}/attachments", dependencies=dependencies
    )
    async def upload(thread_id: str, draft_id: str, request: Request) -> Response:
        # Fully receive with deadlines before entering the private store's lock.
        # Never parse multipart filenames as host paths or stream while holding that lock.
        data = bytearray()
        try:
            authorize_write()
            async with asyncio.timeout(60):
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > MAX_ATTACHMENT_BYTES:
                        return JSONResponse(
                            {"error": {"code": "attachment_too_large"}}, status_code=413
                        )
        except TimeoutError:
            return JSONResponse({"error": {"code": "attachment_upload_timeout"}}, status_code=408)
        except (CommonsError, OSError):
            return JSONResponse(
                {"error": {"code": "attachment_upload_unavailable"}}, status_code=409
            )
        try:
            value = await asyncio.to_thread(
                lambda: Conversations(writer_factory()).upload(
                    thread_id,
                    draft_id,
                    filename=request.query_params.get("filename", ""),
                    media_type=request.headers.get("content-type", "application/octet-stream"),
                    data=bytes(data),
                    operation_id=request.headers.get("idempotency-key"),
                )
            )
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except ValidationError:
            return JSONResponse({"error": {"code": "attachment_invalid"}}, status_code=422)
        except (CommonsError, OSError):
            return JSONResponse({"error": {"code": "attachment_upload_uncertain"}}, status_code=409)

    @routes.post(
        "/api/conversations/{thread_id}/drafts/{draft_id}/attachments/{attachment_id}/remove",
        dependencies=dependencies,
    )
    async def remove(
        thread_id: str, draft_id: str, attachment_id: str, request: Request
    ) -> Response:
        def run(service: Conversations, value: dict[str, Any]) -> Any:
            if value:
                raise ValidationError("Attachment removal accepts no fields.")
            return service.remove_attachment(thread_id, draft_id, attachment_id)

        return await write(request, run)
