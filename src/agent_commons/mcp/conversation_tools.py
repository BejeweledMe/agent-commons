"""Addressed conversations with real MCP image content, no filesystem paths."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from mcp.types import CallToolResult, ImageContent

from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.runtime.collaboration_storage import collaboration_state_root
from agent_commons.runtime.message_delivery import MessageDeliveryStore
from agent_commons.services.conversations import Conversations


def register_conversation_tools(
    register: Callable[..., Any], commons: Any, require_worker: Callable[[], Any]
) -> None:
    def addressed(thread_id: str, message_id: str | None = None) -> tuple[Any, Any, Any]:
        worker = require_worker()
        if not worker or worker.get("purpose") not in {"implementation", "verification"}:
            raise LifecycleConflictError("Project conversation is outside this worker scope.")
        snapshot = commons.snapshot()
        thread = snapshot.threads.get(thread_id)
        recipients = {"*", worker.get("agent_id"), worker.get("child_session_id")}
        target = worker.get("target_ref") or {}
        if target.get("kind") == "task":
            recipients.add("task:" + target["id"])
        if thread is None or not set(thread.get("to", [])).intersection(recipients):
            raise LifecycleConflictError("Project conversation is outside this worker scope.")
        message = next(
            (item for item in thread.get("messages", []) if item.get("message_id") == message_id),
            None,
        )
        if message_id is not None and message is None:
            raise LifecycleConflictError("Message is unavailable in this conversation.")
        return worker, snapshot, message

    def delivery() -> MessageDeliveryStore:
        return MessageDeliveryStore(collaboration_state_root(commons), commons.workspace_id)

    annotation = {
        "readOnlyHint": False,
        "idempotentHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    }

    @register(annotation, worker_only=True, worker_purposes=("implementation", "verification"))
    def commons_read_message(thread_id: str, message_id: str) -> dict[str, Any]:
        """Fetch one addressed message and its attachment metadata."""
        worker, snapshot, message = addressed(thread_id, message_id)
        result = Conversations.message_view(message, snapshot)
        delivery().record(thread_id, message_id, worker["id"])
        return result

    @register(annotation, worker_only=True, worker_purposes=("implementation", "verification"))
    def commons_read_message_image(
        thread_id: str, message_id: str, attachment_id: str
    ) -> CallToolResult:
        """Receive an addressed PNG/JPEG attachment as actual MCP image content."""
        worker, _, _ = addressed(thread_id, message_id)
        item, data = Conversations(commons).read_attachment(thread_id, message_id, attachment_id)
        if item.kind != "image":
            raise ValidationError("This attachment is not an image.")
        delivery().record(thread_id, message_id, worker["id"], attachment_id=attachment_id)
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(data).decode("ascii"),
                    mimeType=item.media_type,
                )
            ]
        )

    @register(annotation, worker_only=True, worker_purposes=("implementation", "verification"))
    def commons_read_message_file(
        thread_id: str, message_id: str, attachment_id: str, offset: int = 0, limit: int = 65536
    ) -> dict[str, Any]:
        """Read bounded file bytes as base64 without executing or rendering them."""
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 65536
        ):
            raise ValidationError("Invalid file page.")
        worker, _, _ = addressed(thread_id, message_id)
        item, data = Conversations(commons).read_attachment(thread_id, message_id, attachment_id)
        if item.kind != "file":
            raise ValidationError("Use the image tool for image attachments.")
        if offset > len(data):
            raise ValidationError("File offset exceeds its size.")
        chunk = data[offset : offset + limit]
        delivery().record(thread_id, message_id, worker["id"], attachment_id=attachment_id)
        return {
            "attachment_id": attachment_id,
            "offset": offset,
            "total_bytes": len(data),
            "encoding": "base64",
            "data": base64.b64encode(chunk).decode("ascii"),
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(data) else None,
        }

    @register(annotation, worker_only=True, worker_purposes=("implementation", "verification"))
    def commons_acknowledge_message(thread_id: str, message_id: str) -> dict[str, Any]:
        """Explicitly acknowledge having read a message. This never claims work is completed."""
        worker, _, _ = addressed(thread_id, message_id)
        receipt = delivery().record(thread_id, message_id, worker["id"], acknowledged=True)
        return {
            "message_id": message_id,
            "state": "acknowledged",
            "acknowledged_at": receipt["acknowledged_at"],
        }
