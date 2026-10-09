"""Private, revision-guarded workspace layout and explicitly saved text drafts.

These bytes are operator convenience state, never messages or canonical facts.
The attachment/send reservation API remains independent. A draft save cannot send
anything, and losing a layout costs only the operator's arrangement.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.conversations import conversation_scope, validate_scope_exists
from agent_commons.errors import CommonsError
from agent_commons.runtime.collaboration_storage import collaboration_state_root
from agent_commons.storage.opstate import (
    COMMUNICATION_STORAGE,
    ensure_private_directory,
    exclusive_lock,
)

PREFERENCES_SCHEMA = "agent_commons.workspace-preferences.v1"
TEXT_DRAFT_SCHEMA = "agent_commons.conversation-text-draft.v1"
DRAFT_CLOCK_SCHEMA = "agent_commons.conversation-draft-clock.v1"
MAX_DOCUMENT_BYTES = 100_000
MAX_DRAFTS = 256
DEFAULT_PREFERENCES: dict[str, Any] = {
    "sidebar_width": 208,
    "chat_width": 320,
    "sidebar_collapsed": False,
    "chat_collapsed": False,
    "panels_swapped": False,
}


class WorkspacePreferenceError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise WorkspacePreferenceError("invalid_workspace_preferences")
    return value


def _layout(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sidebar_width": _integer(value.get("sidebar_width"), 160, 320),
        "chat_width": _integer(value.get("chat_width"), 280, 560),
    }
    for key in ("sidebar_collapsed", "chat_collapsed", "panels_swapped"):
        if type(value.get(key)) is not bool:
            raise WorkspacePreferenceError("invalid_workspace_preferences")
        result[key] = value[key]
    return result


def _draft(value: Mapping[str, Any]) -> dict[str, Any]:
    scope = conversation_scope(value.get("scope"))
    text = value.get("text")
    reply = value.get("reply_to_message_id")
    if (
        not isinstance(text, str)
        or len(text.encode("utf-8")) > 64_000
        or "\x00" in text
        or (reply is not None and (not isinstance(reply, str) or not is_typed_id(reply, "message")))
    ):
        raise WorkspacePreferenceError("invalid_conversation_text_draft")
    return {"scope": scope, "text": text, "reply_to_message_id": reply}


class WorkspacePreferenceStore:
    """Bounded per-principal/project files; lock spans the complete CAS operation."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        project_root: str | Path,
        workspace_id: str,
        principal: str,
    ) -> None:
        self.state = Path(state_root).expanduser().absolute()
        project = Path(project_root).expanduser().resolve()
        if not workspace_id or not principal or self.state.resolve().is_relative_to(project):
            raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
        identity = canonical_json_bytes([str(project), workspace_id, principal])
        self.root = (
            self.state / "runtime" / "workspace-preferences" / hashlib.sha256(identity).hexdigest()
        )

    def _prepare(self, *, create: bool) -> None:
        # Check every existing component rather than resolving away a redirect.
        for path in reversed((self.root, *self.root.parents)):
            if path.is_symlink():
                raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
        if create:
            for path in (self.state, self.state / "runtime", self.root.parent, self.root):
                ensure_private_directory(path, policy=COMMUNICATION_STORAGE)

    def _read(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        self._prepare(create=False)
        try:
            fd = os.open(self.root / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return default
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_DOCUMENT_BYTES
            ):
                raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                value = loads_json_strict(stream.read(MAX_DOCUMENT_BYTES + 1))
        finally:
            os.close(fd)
        if not isinstance(value, dict) or value.get("schema") != default["schema"]:
            raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
        _integer(value.get("revision"), 0, 2**53 - 1)
        if default["schema"] == DRAFT_CLOCK_SCHEMA:
            return {"schema": DRAFT_CLOCK_SCHEMA, "revision": value["revision"]}
        if default["schema"] == PREFERENCES_SCHEMA:
            return {"schema": PREFERENCES_SCHEMA, "revision": value["revision"], **_layout(value)}
        body = _draft(value)
        if body["scope"] != default["scope"]:
            raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
        updated = value.get("updated_at")
        if not isinstance(updated, str):
            raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
        datetime.fromisoformat(updated.replace("Z", "+00:00"))
        return {
            "schema": TEXT_DRAFT_SCHEMA,
            "revision": value["revision"],
            **body,
            "updated_at": updated,
            "previous_revision": _integer(value.get("previous_revision"), 0, 2**53 - 2),
        }

    def _clock(self) -> int:
        return self._read(".draft-clock.json", {"schema": DRAFT_CLOCK_SCHEMA, "revision": 0})[
            "revision"
        ]

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if key != "previous_revision"}

    def _cleanup_pending(self) -> None:
        # Every writer holds the same lock. Thus no pending file can belong to a
        # live writer here, including files left by an uncatchable process exit.
        for path in self.root.glob(".pending-*"):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise WorkspacePreferenceError("workspace_preferences_unavailable", 409)
            path.unlink()

    def _publish(self, path: Path, encoded: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _sync_directory(self) -> None:
        directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _replace(
        self, name: str, value: object, default: dict[str, Any], *, draft: bool = False
    ) -> dict[str, Any]:
        allowed = (
            {"expected_revision", "scope", "text", "reply_to_message_id"}
            if draft
            else {"expected_revision", *DEFAULT_PREFERENCES}
        )
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise WorkspacePreferenceError(
                "invalid_conversation_text_draft" if draft else "invalid_workspace_preferences"
            )
        expected = _integer(value["expected_revision"], 0, 2**53 - 2)
        body = _draft(value) if draft else _layout(value)
        self._prepare(create=True)
        with exclusive_lock(self.root / ".lock", policy=COMMUNICATION_STORAGE):
            self._cleanup_pending()
            clock = self._clock() if draft else 0
            if draft:
                default = {**default, "revision": clock}
            current = self._read(name, default)
            path = self.root / name
            clearing = draft and not body["text"] and body["reply_to_message_id"] is None
            if current["revision"] != expected:
                # A lost reply may retry the exact replacement without adding a
                # new revision. A newer/different document must never be erased.
                previous = current.get("previous_revision") if draft else current["revision"] - 1
                if previous == expected and all(current[k] == v for k, v in body.items()):
                    return self._public(current)
                if clearing and not path.exists() and expected <= clock:
                    return self._public(current)
                raise WorkspacePreferenceError(
                    "conversation_text_draft_conflict"
                    if draft
                    else "workspace_preferences_conflict",
                    409,
                )
            if (
                draft
                and not clearing
                and not path.exists()
                and sum(1 for _ in self.root.glob("draft-*.json")) >= MAX_DRAFTS
            ):
                raise WorkspacePreferenceError("conversation_text_draft_limit", 409)
            revision = _integer(clock + 1, 1, 2**53 - 1) if draft else expected + 1
            document = {"schema": default["schema"], "revision": revision, **body}
            if draft:
                document["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                document["previous_revision"] = expected
            encoded = canonical_json_bytes(document)
            if len(encoded) > MAX_DOCUMENT_BYTES:
                raise WorkspacePreferenceError("workspace_preferences_too_large", 413)
            if draft:
                # Advance a bounded global clock before publication/removal. A
                # crash may leave a harmless gap, never a reused revision. Missing
                # scopes read this clock, so deletion needs no permanent per-task
                # tombstone and stale saves cannot resurrect a cleared draft.
                self._publish(
                    self.root / ".draft-clock.json",
                    canonical_json_bytes({"schema": DRAFT_CLOCK_SCHEMA, "revision": revision}),
                )
            if clearing:
                path.unlink(missing_ok=True)
                self._sync_directory()
                return {**default, "revision": revision}
            self._publish(path, encoded)
            return self._public(document)

    def preferences(self) -> dict[str, Any]:
        return self._read(
            "layout.json", {"schema": PREFERENCES_SCHEMA, "revision": 0, **DEFAULT_PREFERENCES}
        )

    def save_preferences(self, value: object) -> dict[str, Any]:
        return self._replace(
            "layout.json",
            value,
            {"schema": PREFERENCES_SCHEMA, "revision": 0, **DEFAULT_PREFERENCES},
        )

    @staticmethod
    def _scope_document(scope: object) -> tuple[str, dict[str, Any]]:
        parsed = conversation_scope(scope)
        name = "draft-" + hashlib.sha256(canonical_json_bytes(parsed)).hexdigest() + ".json"
        return name, {
            "schema": TEXT_DRAFT_SCHEMA,
            "revision": 0,
            "scope": parsed,
            "text": "",
            "reply_to_message_id": None,
            "updated_at": None,
        }

    def text_draft(self, scope: object) -> dict[str, Any]:
        name, default = self._scope_document(scope)
        if not self.root.exists():
            self._prepare(create=False)
            return default
        # Read the clock and document under one lock: a simultaneous deletion
        # must not combine an old clock with an already removed file.
        self._prepare(create=False)
        with exclusive_lock(self.root / ".lock", policy=COMMUNICATION_STORAGE):
            default["revision"] = self._clock()
            return self._public(self._read(name, default))

    def save_text_draft(self, value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise WorkspacePreferenceError("invalid_conversation_text_draft")
        name, default = self._scope_document(value.get("scope"))
        return self._replace(name, value, default, draft=True)


def register_workspace_preference_routes(
    routes: Any,
    *,
    dependencies: list[object],
    owner_factory: Callable[[], Any],
    register_writes: bool,
) -> None:
    def operation(callback: Callable[[WorkspacePreferenceStore, Any], Any]) -> Any:
        manager = owner_factory()
        session = manager.sessions.require_active(manager.session_id)
        snapshot = manager.snapshot()
        if any(
            item.get("child_session_id") == session.session_id
            for item in snapshot.delegations.values()
        ):
            raise WorkspacePreferenceError("workspace_preferences_unavailable", 403)
        store = WorkspacePreferenceStore(
            collaboration_state_root(manager),
            project_root=manager.repo_root,
            workspace_id=manager.workspace_id,
            principal=session.principal,
        )
        return callback(store, snapshot)

    async def respond(callback: Callable[[WorkspacePreferenceStore, Any], Any]) -> Response:
        try:
            result = await asyncio.to_thread(operation, callback)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except WorkspacePreferenceError as exc:
            code, status = exc.code, exc.status
        except (CommonsError, OSError, ValueError):
            code, status = "workspace_preferences_unavailable", 409
        return JSONResponse(
            {"error": {"code": code}}, status_code=status, headers={"Cache-Control": "no-store"}
        )

    @routes.get("/api/workspace/preferences", dependencies=dependencies)
    async def preferences() -> Response:
        return await respond(lambda store, snapshot: store.preferences())

    def validated_scope(scope: object, snapshot: Any) -> dict[str, str]:
        parsed = conversation_scope(scope)
        validate_scope_exists(parsed, snapshot)
        return parsed

    @routes.get("/api/conversations/text-draft", dependencies=dependencies)
    async def text_draft(scope_kind: str = "project", scope_id: str = "") -> Response:
        scope = {"kind": scope_kind, **({"id": scope_id} if scope_id else {})}
        return await respond(
            lambda store, snapshot: store.text_draft(validated_scope(scope, snapshot))
        )

    if not register_writes:
        return

    async def body(request: Request) -> object:
        data = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_DOCUMENT_BYTES:
                    raise WorkspacePreferenceError("workspace_preferences_too_large", 413)
        return loads_json_strict(bytes(data))

    async def write(request: Request, *, draft: bool) -> Response:
        try:
            value = await body(request)
        except WorkspacePreferenceError as exc:
            return JSONResponse(
                {"error": {"code": exc.code}},
                status_code=exc.status,
                headers={"Cache-Control": "no-store"},
            )
        except (CommonsError, ValueError, TimeoutError):
            return JSONResponse(
                {"error": {"code": "invalid_workspace_preferences"}},
                status_code=422,
                headers={"Cache-Control": "no-store"},
            )

        def save(store: WorkspacePreferenceStore, snapshot: Any) -> Any:
            if not draft:
                return store.save_preferences(value)
            if not isinstance(value, Mapping):
                raise WorkspacePreferenceError("invalid_conversation_text_draft")
            validated_scope(value.get("scope"), snapshot)
            return store.save_text_draft(value)

        return await respond(save)

    @routes.post("/api/workspace/preferences", dependencies=dependencies)
    async def save_preferences(request: Request) -> Response:
        return await write(request, draft=False)

    @routes.post("/api/conversations/text-draft", dependencies=dependencies)
    async def save_text_draft(request: Request) -> Response:
        return await write(request, draft=True)
