"""Closed, secret-free reasons for refusing a provider launch or explaining a stop."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_commons.errors import ConfigurationError

_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+/)+[^\s:;,()\[\]{}<>\"']*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LAUNCH_CODES = frozenset(
    {
        "workspace_snapshot_unreadable",
        "executor_access_missing",
        "provider_mcp_handshake_failed",
        "unknown",
    }
)
_STOP_CODES = _LAUNCH_CODES | frozenset(
    {"executor_stopped_by_environment", "provider_reported_error"}
)


STOP_REASON_MARKER = "stop_reason:"
# A valid marker encodes three strings: a code from the closed set plus a reason
# and a next action of at most 256 characters each.  ``json.dumps`` escapes
# non-ASCII to ``\uXXXX``, so the widest legitimate encoding stays well below
# this ceiling; anything longer is not a marker this runtime wrote.
_MAX_STOP_REASON_JSON_CHARS = 4096
_STOP_REASON_KEYS = frozenset({"code", "reason", "next_action"})


def _safe_reason(value: str) -> str:
    value = _CONTROL.sub(" ", _PATH.sub("[redacted path]", value)).strip()
    return (value or "Launch could not be admitted safely.")[:256]


@dataclass(frozen=True, slots=True)
class LaunchRefusal:
    code: str
    reason: str
    next_action: str

    def __post_init__(self) -> None:
        if self.code not in _LAUNCH_CODES:
            raise ConfigurationError("launch refusal code is not allowlisted")
        object.__setattr__(self, "reason", _safe_reason(self.reason))
        object.__setattr__(self, "next_action", _safe_reason(self.next_action))

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def as_error(self) -> ConfigurationError:
        """Expose an admission refusal on the runtime's typed error surface."""

        error = ConfigurationError(
            "Provider launch was refused before any delegation attempt or child session existed. "
            + self.reason
        )
        error.code = self.code  # type: ignore[attr-defined]
        error.safe_next_actions = (self.next_action,)  # type: ignore[attr-defined]
        return error


@dataclass(frozen=True, slots=True)
class StopReason:
    code: str
    reason: str
    next_action: str

    def __post_init__(self) -> None:
        if self.code not in _STOP_CODES:
            raise ConfigurationError("stop reason code is not allowlisted")
        object.__setattr__(self, "reason", _safe_reason(self.reason))
        object.__setattr__(self, "next_action", _safe_reason(self.next_action))

    def summary(self) -> str:
        return STOP_REASON_MARKER + json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def decode_stop_reason(summary: str | None) -> StopReason | None:
    """Read back the marker :meth:`StopReason.summary` writes, or return ``None``.

    The marker is accepted either as the whole summary or as the trailing
    segment after one space, which is exactly how the delegation runtime writes
    it.  Every other summary, and every marker whose payload is not the closed
    ``{code, reason, next_action}`` object with an allowlisted code, decodes to
    ``None``: a surface then keeps its existing copy instead of rendering half a
    reason.  This function is total — a malformed or hostile summary never
    raises.
    """

    if not isinstance(summary, str):
        return None
    if summary.startswith(STOP_REASON_MARKER):
        encoded = summary[len(STOP_REASON_MARKER) :]
    else:
        appended = summary.rfind(" " + STOP_REASON_MARKER)
        if appended == -1:
            return None
        encoded = summary[appended + 1 + len(STOP_REASON_MARKER) :]
    if len(encoded) > _MAX_STOP_REASON_JSON_CHARS:
        return None
    try:
        decoded = json.loads(encoded)
    except ValueError:
        return None
    if not isinstance(decoded, dict) or set(decoded) != _STOP_REASON_KEYS:
        return None
    if not all(isinstance(value, str) and value != "" for value in decoded.values()):
        return None
    try:
        # The dataclass is the validator: it owns the closed code set and the
        # sanitization and bounds applied to the two text fields.
        return StopReason(
            code=decoded["code"], reason=decoded["reason"], next_action=decoded["next_action"]
        )
    except ConfigurationError:
        return None


def verify_executor_access(
    workspace_root: Path,
    *,
    profile: Any | None = None,
    profiles: Any | None = None,
) -> LaunchRefusal | None:
    """Check the local inputs a worker receives before an attempt is reserved."""

    root = workspace_root.expanduser()
    if root.is_symlink():
        return LaunchRefusal(
            "executor_access_missing",
            "The selected checkout must not be a symlink.",
            "select_git_worktree",
        )
    root = root.resolve()
    if not root.is_dir():
        return LaunchRefusal(
            "executor_access_missing", "The selected checkout is unavailable.", "select_checkout"
        )
    if not (root / ".git").exists():
        return LaunchRefusal(
            "executor_access_missing",
            "The selected checkout is not a Git worktree root.",
            "select_git_worktree",
        )
    # ONBOARDING.md is written by workspace initialization and is the one control
    # file every worker reads; AGENTS.md exists only when a client integration was
    # installed, so its absence is not an access failure.
    for relative in (".agent-commons/ONBOARDING.md",):
        candidate = root / relative
        try:
            if not candidate.is_file() or not candidate.stat().st_mode:
                raise OSError
            candidate.open("rb").close()
        except OSError:
            return LaunchRefusal(
                "executor_access_missing",
                f"Required control file {relative} is unreadable.",
                "repair_control_files",
            )
    if profile is not None and profiles is not None:
        try:
            recorded = profiles.get(profile.profile_id)
        except (AttributeError, ConfigurationError):
            return LaunchRefusal(
                "executor_access_missing",
                "The selected executor profile is unavailable.",
                "repair_runtime_profile",
            )
        # A role may only substitute its model.  Permission mode and workspace
        # trust are operator-owned registry fields and must remain identical.
        if getattr(profile, "permission_mode", None) != getattr(
            recorded, "permission_mode", None
        ) or getattr(profile, "trusted_workspace", None) != getattr(
            recorded, "trusted_workspace", None
        ):
            return LaunchRefusal(
                "executor_access_missing",
                "The selected executor profile no longer matches its recorded permissions.",
                "repair_runtime_profile",
            )
    return None
