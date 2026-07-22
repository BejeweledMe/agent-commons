"""Content-free durable counters for broker-bound terminal MCP tools."""

from __future__ import annotations

import hashlib
import json
import os
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

from .attempts import _canonical_bytes, _ensure_private_directory, _exclusive_lock
from .model import _safe_identifier

TOOL_AUDIT_SCHEMA = "agent_commons.terminal_tool_audit.v1"
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
class TerminalToolAudit:
    schema: str
    delegation_id: str
    terminal_tool_calls: int
    terminal_tool_rejections: int
    terminal_tool_completions: int
    last_tool: str | None
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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
            _ensure_private_directory(self.root)

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
        }
        if set(value) != expected or value.get("schema") != TOOL_AUDIT_SCHEMA:
            raise IntegrityError("terminal tool audit has an invalid shape")
        try:
            audit = TerminalToolAudit(**value)
            _safe_identifier("delegation_id", audit.delegation_id)
        except (TypeError, ValidationError) as exc:
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
        self.security_policy.assert_safe(value, context="terminal tool audit")
        return audit

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
        if not isinstance(value, dict) or raw != _canonical_bytes(value):
            raise IntegrityError("terminal tool audit is not canonical JSON")
        audit = self._validate(value)
        if audit.delegation_id != delegation_id:
            raise IntegrityError("terminal tool audit identity does not match its path")
        return audit

    def get(self, delegation_id: str) -> TerminalToolAudit:
        if self.read_only:
            return self._read(delegation_id)
        with _exclusive_lock(self.lock_path):
            return self._read(delegation_id)

    def record(self, delegation_id: str, tool: str, outcome: str) -> TerminalToolAudit:
        if self.read_only:
            raise LifecycleConflictError("terminal tool audit was opened read-only")
        if tool not in TERMINAL_TOOL_NAMES:
            raise ValidationError("terminal tool audit received an unknown tool")
        if outcome not in {"called", "rejected", "completed"}:
            raise ValidationError("terminal tool audit received an unknown outcome")
        with _exclusive_lock(self.lock_path):
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
            updated = replace(
                current,
                **changes,
                last_tool=tool,
                updated_at=_iso(self.clock()),
            )
            self._validate(updated.as_dict())
            atomic_write_replace(self._path(delegation_id), _canonical_bytes(updated.as_dict()))
            return updated
