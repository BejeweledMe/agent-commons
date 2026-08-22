"""Shared Click state and parsing helpers for the command-line interface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from agent_commons.core.refs import parse_ref
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.runtime import error_safe_next_actions
from agent_commons.services import CommonsManager


class CommonsGroup(click.Group):
    """Render domain failures as concise, non-traceback CLI errors."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except CommonsError as exc:
            return self._render_error(ctx, exc)
        except FileNotFoundError as exc:
            return self._render_error(ctx, exc)

    @staticmethod
    def _render_error(ctx: click.Context, exc: Exception) -> Any:
        state = ctx.obj
        safe_actions = list(getattr(exc, "safe_next_actions", error_safe_next_actions(exc)))
        if isinstance(state, CLIState) and state.json_output:
            error: dict[str, Any] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "safe_next_actions": safe_actions,
            }
            if code := getattr(exc, "code", None):
                error["code"] = code
            if details := getattr(exc, "details", None):
                error["details"] = details
            state.emit(
                {
                    "ok": False,
                    "error": error,
                }
            )
            ctx.exit(1)
        actions = "\n".join(f"  - {action}" for action in safe_actions)
        raise click.ClickException(f"{exc}\nSafe next actions:\n{actions}") from exc


@dataclass
class CLIState:
    """Resolved CLI invocation state shared by every command group."""

    repo: Path
    session_id: str | None
    json_output: bool
    state_root: Path | None
    state_base: Path | None
    state_source: str
    read_only: bool

    def manager(self) -> CommonsManager:
        return CommonsManager(
            self.repo,
            session_id=self.session_id,
            state_root=self.state_root,
            state_base=self.state_base,
            state_source=self.state_source,
            read_only=self.read_only,
        )

    def emit(self, value: Any) -> None:
        if self.json_output:
            click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return
        click.echo(yaml.safe_dump(value, allow_unicode=True, sort_keys=False).rstrip())


def _json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return parsed


_REFERENCE_KINDS = (
    "artifact",
    "decision",
    "delegation",
    "event",
    "finding",
    "handoff",
    "manifest",
    "objective",
    "review",
    "task",
    "thread",
    "verification",
)


def _input_error(
    message: str, *, code: str, field: str, allowed_kinds: tuple[str, ...]
) -> ValidationError:
    error = ValidationError(message)
    error.code = code  # type: ignore[attr-defined]
    error.details = {  # type: ignore[attr-defined]
        "field": field,
        "allowed_kinds": list(allowed_kinds),
        "example": f"{allowed_kinds[0]}:<id>",
    }
    return error


def _ref(
    value: str,
    *,
    field: str,
    allowed_kinds: tuple[str, ...] = _REFERENCE_KINDS,
) -> dict[str, str]:
    try:
        parsed = parse_ref(value)
    except ValidationError as exc:
        raise _input_error(
            f"{field} must use '<kind>:<id>' syntax; example: {allowed_kinds[0]}:<id>",
            code="invalid_typed_ref",
            field=field,
            allowed_kinds=allowed_kinds,
        ) from exc
    if parsed.kind not in allowed_kinds:
        raise _input_error(
            f"{field} kind must be one of: {', '.join(allowed_kinds)}",
            code="unsupported_ref_kind",
            field=field,
            allowed_kinds=allowed_kinds,
        )
    return parsed.as_dict()


def _refs(
    values: tuple[str, ...],
    *,
    field: str = "reference",
    allowed_kinds: tuple[str, ...] = _REFERENCE_KINDS,
) -> list[dict[str, str]]:
    return [_ref(value, field=field, allowed_kinds=allowed_kinds) for value in values]


def _idem(function: Any) -> Any:
    return click.option(
        "--idempotency-key",
        help="Stable retry identity for this canonical write.",
    )(function)


def _expected(function: Any) -> Any:
    function = click.argument("expected_revision")(function)
    return click.argument("entity_id")(function)
