"""Bounded authenticated Gallery upload route; no client filesystem paths."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from agent_commons.errors import CommonsError
from agent_commons.services.artifact_content import MAX_PREVIEW_BYTES
from agent_commons.services.design_upload import GalleryImportInputError, import_screen

MAX_UPLOAD_REQUEST = (MAX_PREVIEW_BYTES + 2) // 3 * 4 + 4096


def register_gallery_upload(routes: Any, context: Any) -> None:
    """Register only under the server's existing authenticated write group."""

    @routes.post("/api/gallery/import")
    async def upload_screen(request: Request) -> JSONResponse:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

        def refused(code: str, message: str, status: int = 409) -> JSONResponse:
            return JSONResponse(
                {
                    "error": {
                        "code": code,
                        "message": message,
                        "safe_next_actions": [
                            "Retry the same import after resolving the refusal."
                            if status == 409
                            else "Select a PNG or JPEG under 10 MiB and retry."
                        ],
                    }
                },
                status_code=status,
                headers=headers,
            )

        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return refused("gallery_import_invalid", "Image import requires JSON.", 415)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_UPLOAD_REQUEST:
                return refused("gallery_import_oversized", "Image import exceeds its limit.", 413)
            raw.extend(chunk)
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("expected object")
        except (ValueError, UnicodeDecodeError):
            return refused("gallery_import_invalid", "Image import JSON is invalid.", 400)
        try:
            result = await asyncio.to_thread(lambda: import_screen(context.writer(), body))
        except GalleryImportInputError:
            return refused("gallery_import_invalid", "The selected image is invalid.", 422)
        except CommonsError:
            # Low-level errors may quote filenames or submitted image strings.
            return refused("gallery_import_refused", "The image could not be imported safely.")
        context.invalidate()
        return JSONResponse(result, headers=headers)
