"""Private task-editor DTOs and thin routes over canonical task commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import loads_json_strict
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.task_edits import (
    CANCELLABLE_TASK_STATES,
    EDITABLE_TASK_STATES,
    TaskEditRefusal,
    task_has_live_work,
    validate_task_dependencies,
)
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.services import CommonsManager

TASK_EDIT_SCHEMA = "agent-commons.ui.task-edit.v1"
TASK_EDIT_RESULT_SCHEMA = "agent-commons.ui.task-edit-result.v1"
MAX_TASK_EDIT_BYTES = 64 * 1024
_STATES = EDITABLE_TASK_STATES | {"accepted", "cancelled"}
_CHANGE_FIELDS = frozenset({"title", "description", "acceptance_criteria", "dependencies"})


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValidationError("Task editor text is empty, malformed, or exceeds its size limit.")
    return value


def _changes(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value or set(value) - _CHANGE_FIELDS:
        raise ValidationError("Task edits contain missing or unsupported fields.")
    result: dict[str, Any] = {}
    for field, maximum in (("title", 512), ("description", 16000)):
        if field in value:
            result[field] = _text(value[field], maximum)
    if "acceptance_criteria" in value:
        items = value["acceptance_criteria"]
        if type(items) is not list or not 1 <= len(items) <= 128:
            raise ValidationError("Task acceptance criteria must contain 1 to 128 items.")
        result["acceptance_criteria"] = [_text(item, 2048) for item in items]
    if "dependencies" in value:
        validate_task_dependencies(value["dependencies"])
        result["dependencies"] = list(value["dependencies"])
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_TASK_EDIT_BYTES:
        raise ValidationError("The task edit exceeds the 64 KiB limit.")
    return result


def task_edit_detail(manager: CommonsManager, task_id: str) -> dict[str, Any]:
    if not is_typed_id(task_id, "task"):
        raise TaskEditRefusal("task_edit_missing", "Select an existing task.")
    snapshot = manager.snapshot()
    if any(issue.severity == "error" for issue in snapshot.issues):
        raise TaskEditRefusal("task_edit_unavailable", "The canonical task view is unavailable.")
    task = snapshot.tasks.get(task_id)
    if task is None:
        raise TaskEditRefusal("task_edit_missing", "The selected task no longer exists.")
    state = task.get("state")
    revision = task.get("revision")
    if state not in _STATES or not is_typed_id(str(revision), "evt"):
        raise TaskEditRefusal("task_edit_unavailable", "The task cannot be edited from this view.")
    # No truncation: saving a shortened read would destroy unseen task text.
    content = _changes(
        {
            field: task.get(field, [] if field == "dependencies" else None)
            for field in _CHANGE_FIELDS
        }
    )
    live = task_has_live_work(snapshot, task_id)
    extensions = task.get("extensions")
    suggested = extensions.get("suggested_agent_id") if isinstance(extensions, Mapping) else None
    return {
        "schema": TASK_EDIT_SCHEMA,
        "task_id": task_id,
        "revision": revision,
        **content,
        "state": state,
        "editable": state in EDITABLE_TASK_STATES and not live,
        "cancellable": state in CANCELLABLE_TASK_STATES and not live,
        "refusal_code": "task_live_work"
        if live
        else "task_edit_state"
        if state not in EDITABLE_TASK_STATES
        else None,
        **(
            {"suggested_agent_id": suggested}
            if type(suggested) is str and is_typed_id(suggested, "agent")
            else {}
        ),
    }


def _request(task_id: str, body: object, *, cancel: bool) -> dict[str, Any]:
    required = {"expected_revision", "idempotency_key", "reason" if cancel else "changes"}
    if not is_typed_id(task_id, "task") or not isinstance(body, Mapping) or set(body) != required:
        raise ValidationError("The task editor request has missing or unsupported fields.")
    revision = body["expected_revision"]
    if type(revision) is not str or not is_typed_id(revision, "evt"):
        raise ValidationError("An exact task revision is required.")
    return {
        "expected_revision": revision,
        "idempotency_key": _text(body["idempotency_key"], 256),
        **(
            {"reason": _text(body["reason"], 4096)}
            if cancel
            else {"changes": _changes(body["changes"])}
        ),
    }


def task_edit_action(
    writer: Callable[[], CommonsManager], *, task_id: str, body: object, cancel: bool = False
) -> dict[str, Any]:
    request = _request(task_id, body, cancel=cancel)
    manager = writer()
    event = (manager.cancel_idle_task if cancel else manager.edit_task)(task_id, **request)
    return {
        "schema": TASK_EDIT_RESULT_SCHEMA,
        "task_id": str(event["entity_ref"]["id"]),
        "revision": str(event["revision"]),
        "action": "cancelled" if cancel else "revised",
    }


class TaskReadRoutes(Protocol):
    def get(self, path: str, **kwargs: Any) -> Callable[..., Any]: ...


class TaskWriteRoutes(Protocol):
    def post(self, path: str) -> Callable[..., Any]: ...


async def _bounded_body(request: Request) -> dict[str, Any] | Response:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_TASK_EDIT_BYTES:
            return JSONResponse(
                {
                    "error": {
                        "code": "task_edit_too_large",
                        "message": "Task edit exceeds 64 KiB.",
                        "safe_next_actions": [],
                    }
                },
                status_code=413,
            )
        body.extend(chunk)
    try:
        value = loads_json_strict(bytes(body))
        if not isinstance(value, dict):
            raise ValidationError("Task edit must be an object.")
    except ValidationError:
        return JSONResponse(
            {
                "error": {
                    "code": "task_edit_invalid",
                    "message": "Task edit must be a valid JSON object.",
                    "safe_next_actions": [],
                }
            },
            status_code=422,
        )
    return value


def register_task_edit_reads(
    routes: TaskReadRoutes,
    *,
    manager: Callable[[], CommonsManager],
    dependencies: list[Any],
) -> None:
    @routes.get("/api/work/tasks/{task_id}/edit-detail", dependencies=dependencies)
    async def detail(task_id: str) -> Response:
        try:
            result = await asyncio.to_thread(lambda: task_edit_detail(manager(), task_id))
        except CommonsError as exc:
            code = exc.code if isinstance(exc, TaskEditRefusal) else "task_edit_unavailable"
            return JSONResponse(
                {
                    "error": {
                        "code": code,
                        "message": "The complete task editor view is unavailable.",
                        "safe_next_actions": [],
                    }
                },
                status_code=409,
            )
        return JSONResponse(result)


def register_task_edit_writes(
    routes: TaskWriteRoutes,
    *,
    writer: Callable[[], CommonsManager],
    record: Callable[..., Awaitable[Response]],
) -> None:
    @routes.post("/api/work/tasks/{task_id}/edit")
    async def edit(task_id: str, request: Request) -> Response:
        body = await _bounded_body(request)
        if isinstance(body, Response):
            return body
        return await record(task_edit_action, writer=writer, task_id=task_id, body=body)

    @routes.post("/api/work/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request) -> Response:
        body = await _bounded_body(request)
        if isinstance(body, Response):
            return body
        return await record(
            task_edit_action,
            writer=writer,
            task_id=task_id,
            body=body,
            cancel=True,
        )
