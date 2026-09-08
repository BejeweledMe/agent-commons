"""Bounded, storage-independent conversation and attachment metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.errors import ValidationError

MAX_ATTACHMENT_BYTES = 15_000_000
MAX_IMAGES = 10
MAX_FILES = 10
MAX_MESSAGE_BYTES = 64_000
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ATTACHMENT = re.compile(r"attachment\.[a-zA-Z0-9_-]{16,64}\Z")
_MEDIA = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,95}\Z")


def conversation_scope(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationError("Conversation scope must be an object.")
    kind = value.get("kind")
    if kind == "project" and set(value) == {"kind"}:
        return {"kind": "project"}
    if (
        isinstance(kind, str)
        and kind in {"task", "agent"}
        and set(value) == {"kind", "id"}
        and isinstance(value.get("id"), str)
        and is_typed_id(value["id"], kind)
    ):
        return {"kind": kind, "id": value["id"]}
    raise ValidationError("Conversation scope is invalid.")


def attachment_refs(values: object) -> list[dict[str, Any]]:
    if not isinstance(values, (list, tuple)) or len(values) > MAX_IMAGES + MAX_FILES:
        raise ValidationError("A message accepts at most 20 attachments.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {"image": 0, "file": 0}
    fields = {"attachment_id", "digest", "media_type", "size_bytes", "display_name", "category"}
    for item in values:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise ValidationError("Attachment metadata has an invalid shape.")
        identifier = item["attachment_id"]
        name = item["display_name"]
        category = item["category"]
        media = item["media_type"]
        if (
            not isinstance(identifier, str)
            or not _ATTACHMENT.fullmatch(identifier)
            or identifier in seen
            or not isinstance(item["digest"], str)
            or not _DIGEST.fullmatch(item["digest"])
            or type(item["size_bytes"]) is not int
            or not 1 <= item["size_bytes"] <= MAX_ATTACHMENT_BYTES
            or not isinstance(name, str)
            or not 1 <= len(name) <= 200
            or len(name.encode("utf-8")) > 800
            or name in {".", ".."}
            or any(character in name for character in "/\\")
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or not isinstance(media, str)
            or not _MEDIA.fullmatch(media)
            or not isinstance(category, str)
            or category not in {"image", "file"}
            or (media.startswith("image/") != (category == "image"))
            or (category == "image" and media not in {"image/png", "image/jpeg"})
        ):
            raise ValidationError("Attachment metadata is invalid.")
        counts[category] += 1
        if counts[category] > 10:
            raise ValidationError("A message accepts at most 10 images and 10 other files.")
        seen.add(identifier)
        result.append(dict(item))
    return result


def validate_conversation_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    if "conversation_scope" in payload:
        if event_type != "thread.opened":
            raise ValidationError("Conversation scope is immutable.")
        conversation_scope(payload["conversation_scope"])
    if "attachments" in payload or "reply_to_message_id" in payload:
        if event_type != "thread.replied":
            raise ValidationError("Attachments belong to thread replies.")
        attachments = attachment_refs(payload.get("attachments", []))
        body = payload.get("body")
        if not isinstance(body, str) or (not body.strip() and not attachments):
            raise ValidationError("A message needs text or an attachment.")
        if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValidationError("Message text exceeds its limit.")
        reply = payload.get("reply_to_message_id")
        if reply is not None and (not isinstance(reply, str) or not is_typed_id(reply, "message")):
            raise ValidationError("Reply must identify a message in the conversation.")


def validate_scope_exists(scope: Mapping[str, str], snapshot: Any) -> None:
    scope = conversation_scope(scope)
    if scope["kind"] == "project":
        return
    collection = snapshot.tasks if scope["kind"] == "task" else snapshot.agents
    if scope["id"] not in collection:
        raise ValidationError("Conversation subject does not exist in this project.")


def recipient_ids(values: Sequence[str], snapshot: Any) -> list[str]:
    if isinstance(values, (str, bytes)) or len(values) > 128:
        raise ValidationError("Conversation recipients exceed their limit.")
    result = sorted(set(values))
    for identifier in result:
        record = snapshot.agents.get(identifier)
        if record is None or record.get("state") != "active" or record.get("template"):
            raise ValidationError("Conversation recipient is not an active project agent.")
    return result
