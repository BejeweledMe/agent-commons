"""Private bounded diagnostics for broker-bound terminal MCP tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.errors import IntegrityError, LifecycleConflictError, ValidationError
from agent_commons.security import SecurityPolicy
from agent_commons.storage.atomic import atomic_write_replace
from agent_commons.storage.opstate import (
    ATTEMPT_STORAGE,
    canonical_state_bytes,
    ensure_private_directory,
    exclusive_lock,
    strict_state_bytes,
)

from .model import _safe_identifier

TOOL_AUDIT_SCHEMA = "agent_commons.terminal_tool_audit.v2"
_LEGACY_TOOL_AUDIT_SCHEMA = "agent_commons.terminal_tool_audit.v1"
_READABLE_TOOL_AUDIT_SCHEMAS = {TOOL_AUDIT_SCHEMA, _LEGACY_TOOL_AUDIT_SCHEMA}
_MAX_REJECTION_DETAILS = 32
_MAX_REJECTION_MESSAGE_BYTES = 512
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:[^\s/:;,()\[\]{}<>\"']+/)+[^\s:;,()\[\]{}<>\"']*"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\(?:[^\s:;,()\[\]{}<>\"']+\\)+"
    r"[^\s:;,()\[\]{}<>\"']*"
)
TERMINAL_TOOL_NAMES = frozenset(
    {
        "commons_delegation_input_needed",
        "commons_succeed_delegation",
        "commons_delegation_needs_operator",
    }
)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class TerminalToolRejection:
    ordinal: int
    tool: str
    error_type: str
    message: str
    recorded_at: str


@dataclass(frozen=True, slots=True)
class TerminalToolAudit:
    schema: str
    delegation_id: str
    terminal_tool_calls: int
    terminal_tool_rejections: int
    terminal_tool_completions: int
    last_tool: str | None
    updated_at: str | None
    rejection_details: tuple[TerminalToolRejection, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["rejection_details"] = [asdict(item) for item in self.rejection_details]
        return value

    @property
    def rejection_details_truncated(self) -> bool:
        return len(self.rejection_details) < self.terminal_tool_rejections


class TerminalToolAuditStore:
    """One private canonical-JSON counter document per delegation."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        security_policy: SecurityPolicy | None = None,
        clock: Callable[[], float] = time.time,
        read_only: bool = False,
    ) -> None:
        self.root = Path(state_root).expanduser().resolve() / "runtime" / "tool-audit"
        self.lock_path = self.root / "audit.lock"
        self.security_policy = security_policy or SecurityPolicy()
        self.clock = clock
        self.read_only = read_only
        if not read_only:
            ensure_private_directory(self.root, policy=ATTEMPT_STORAGE)

    @staticmethod
    def _empty(delegation_id: str) -> TerminalToolAudit:
        _safe_identifier("delegation_id", delegation_id)
        return TerminalToolAudit(
            schema=TOOL_AUDIT_SCHEMA,
            delegation_id=delegation_id,
            terminal_tool_calls=0,
            terminal_tool_rejections=0,
            terminal_tool_completions=0,
            last_tool=None,
            updated_at=None,
            rejection_details=(),
        )

    def _path(self, delegation_id: str) -> Path:
        digest = hashlib.sha256(delegation_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _validate(self, value: Mapping[str, Any]) -> TerminalToolAudit:
        expected = {
            "schema",
            "delegation_id",
            "terminal_tool_calls",
            "terminal_tool_rejections",
            "terminal_tool_completions",
            "last_tool",
            "updated_at",
            "rejection_details",
        }
        schema = value.get("schema")
        legacy = schema == _LEGACY_TOOL_AUDIT_SCHEMA
        if (
            set(value) != (expected - {"rejection_details"} if legacy else expected)
            or schema not in _READABLE_TOOL_AUDIT_SCHEMAS
        ):
            raise IntegrityError("terminal tool audit has an invalid shape")
        try:
            details = tuple(
                TerminalToolRejection(**item)
                for item in (() if legacy else value["rejection_details"])
            )
            audit = TerminalToolAudit(
                **{
                    key: item
                    for key, item in value.items()
                    if key not in {"schema", "rejection_details"}
                },
                schema=TOOL_AUDIT_SCHEMA,
                rejection_details=details,
            )
            _safe_identifier("delegation_id", audit.delegation_id)
        except (KeyError, TypeError, ValidationError) as exc:
            raise IntegrityError("terminal tool audit has invalid identifiers") from exc
        for field in (
            "terminal_tool_calls",
            "terminal_tool_rejections",
            "terminal_tool_completions",
        ):
            counter = getattr(audit, field)
            if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
                raise IntegrityError("terminal tool audit has invalid counters")
        if (
            audit.terminal_tool_completions + audit.terminal_tool_rejections
            > audit.terminal_tool_calls
        ):
            raise IntegrityError("terminal tool audit outcomes exceed calls")
        if audit.last_tool is not None and audit.last_tool not in TERMINAL_TOOL_NAMES:
            raise IntegrityError("terminal tool audit has an unknown tool")
        if len(audit.rejection_details) > _MAX_REJECTION_DETAILS:
            raise IntegrityError("terminal tool audit has too many rejection details")
        previous = max(0, audit.terminal_tool_rejections - len(audit.rejection_details))
        for detail in audit.rejection_details:
            if detail.ordinal <= previous or detail.ordinal > audit.terminal_tool_rejections:
                raise IntegrityError("terminal tool rejection ordinals are invalid")
            previous = detail.ordinal
            if detail.tool not in TERMINAL_TOOL_NAMES:
                raise IntegrityError("terminal tool rejection has an unknown tool")
            try:
                _safe_identifier("terminal tool rejection error type", detail.error_type)
            except ValidationError as exc:
                raise IntegrityError("terminal tool rejection error type is invalid") from exc
            if (
                not detail.message
                or len(detail.message.encode("utf-8")) > _MAX_REJECTION_MESSAGE_BYTES
                or not detail.recorded_at
            ):
                raise IntegrityError("terminal tool rejection detail is invalid")
        self.security_policy.assert_safe(value, context="terminal tool audit")
        return audit

    def _sanitize_rejection_message(self, value: str) -> str:
        rendered = _ANSI_ESCAPE.sub("", value)
        rendered = _CONTROL_CHARACTER.sub("�", rendered)
        rendered = _POSIX_ABSOLUTE_PATH.sub("[agent-commons redacted path]", rendered)
        rendered = _WINDOWS_ABSOLUTE_PATH.sub("[agent-commons redacted path]", rendered)
        if self.security_policy.scan(rendered):
            return "details redacted by security policy"
        encoded = rendered.strip().encode("utf-8")
        if not encoded:
            return "terminal tool call rejected without a message"
        if len(encoded) > _MAX_REJECTION_MESSAGE_BYTES:
            encoded = encoded[: _MAX_REJECTION_MESSAGE_BYTES - len("…".encode())]
            while encoded and (encoded[-1] & 0xC0) == 0x80:
                encoded = encoded[:-1]
            return encoded.decode("utf-8", "ignore").rstrip() + "…"
        return rendered.strip()

    def _read(self, delegation_id: str) -> TerminalToolAudit:
        path = self._path(delegation_id)
        if not path.exists():
            return self._empty(delegation_id)
        if path.is_symlink():
            raise IntegrityError("terminal tool audit must not be a symlink")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("terminal tool audit must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read()
            descriptor = -1
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityError("terminal tool audit is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict) or raw != canonical_state_bytes(value):
            raise IntegrityError("terminal tool audit is not canonical JSON")
        audit = self._validate(value)
        if audit.delegation_id != delegation_id:
            raise IntegrityError("terminal tool audit identity does not match its path")
        return audit

    def get(self, delegation_id: str) -> TerminalToolAudit:
        if self.read_only:
            return self._read(delegation_id)
        with exclusive_lock(self.lock_path, policy=ATTEMPT_STORAGE):
            return self._read(delegation_id)

    def record(
        self,
        delegation_id: str,
        tool: str,
        outcome: str,
        *,
        error_type: str | None = None,
        message: str | None = None,
    ) -> TerminalToolAudit:
        if self.read_only:
            raise LifecycleConflictError("terminal tool audit was opened read-only")
        if tool not in TERMINAL_TOOL_NAMES:
            raise ValidationError("terminal tool audit received an unknown tool")
        if outcome not in {"called", "rejected", "completed"}:
            raise ValidationError("terminal tool audit received an unknown outcome")
        if outcome == "rejected":
            if not error_type or message is None:
                raise ValidationError("terminal tool rejection requires an error type and message")
            _safe_identifier("terminal tool rejection error type", error_type)
        elif error_type is not None or message is not None:
            raise ValidationError("terminal tool audit details are only valid for rejections")
        with exclusive_lock(self.lock_path, policy=ATTEMPT_STORAGE):
            current = self._read(delegation_id)
            changes = {
                "terminal_tool_calls": current.terminal_tool_calls,
                "terminal_tool_rejections": current.terminal_tool_rejections,
                "terminal_tool_completions": current.terminal_tool_completions,
            }
            field = {
                "called": "terminal_tool_calls",
                "rejected": "terminal_tool_rejections",
                "completed": "terminal_tool_completions",
            }[outcome]
            changes[field] += 1
            details = current.rejection_details
            if outcome == "rejected":
                details = (
                    *details,
                    TerminalToolRejection(
                        ordinal=changes["terminal_tool_rejections"],
                        tool=tool,
                        error_type=str(error_type),
                        message=self._sanitize_rejection_message(str(message)),
                        recorded_at=_iso(self.clock()),
                    ),
                )[-_MAX_REJECTION_DETAILS:]
            updated = replace(
                current,
                schema=TOOL_AUDIT_SCHEMA,
                **changes,
                last_tool=tool,
                updated_at=_iso(self.clock()),
                rejection_details=details,
            )
            self._validate(updated.as_dict())
            atomic_write_replace(
                self._path(delegation_id),
                strict_state_bytes(updated.as_dict()),
            )
            return updated
