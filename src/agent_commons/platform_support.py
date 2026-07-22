"""Explicit platform boundary for process-safe Agent Commons locks."""

from __future__ import annotations

import os
import sys

from agent_commons.errors import ConfigurationError

try:  # pragma: no branch - the unsupported branch is exercised with monkeypatching.
    import fcntl as _fcntl
except ModuleNotFoundError:  # pragma: no cover - exercised on non-POSIX hosts
    _fcntl = None


SUPPORTED_PLATFORM_MESSAGE = (
    "Agent Commons supports macOS and Linux only because its durable coordination "
    "locks require POSIX fcntl.flock; use a supported host or container."
)


def require_supported_platform() -> None:
    """Fail before state mutation when POSIX locking is unavailable."""

    if os.name != "posix" or _fcntl is None or sys.platform not in {"darwin", "linux"}:
        raise ConfigurationError(SUPPORTED_PLATFORM_MESSAGE)


def lock_exclusive(descriptor: int) -> None:
    """Acquire the project-wide supported exclusive file lock."""

    require_supported_platform()
    assert _fcntl is not None
    _fcntl.flock(descriptor, _fcntl.LOCK_EX)


def unlock(descriptor: int) -> None:
    """Release a lock acquired with :func:`lock_exclusive`."""

    require_supported_platform()
    assert _fcntl is not None
    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
