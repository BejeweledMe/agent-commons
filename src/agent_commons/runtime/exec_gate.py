"""Hold a provider PID inert until canonical delegation start is durable."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

_EXEC_GATE_FRAME = b"AGENT_COMMONS_EXEC_GATE_V1\n"
_INVALID_GATE_MESSAGE = b"agent-commons-exec-gate: invalid control frame\n"
_EXEC_FAILED_MESSAGE = b"agent-commons-exec-gate: provider exec failed\n"


def gated_argv(provider_argv: tuple[str, ...]) -> tuple[str, ...]:
    """Return a shell-free gate invocation around one validated provider argv."""

    if not provider_argv:
        raise ValueError("provider argv cannot be empty")
    interpreter = str(Path(sys.executable).resolve(strict=True))
    gate_script = str(Path(__file__).resolve(strict=True))
    return (
        interpreter,
        "-I",
        gate_script,
        "--",
        *provider_argv,
    )


def gated_stdin(provider_stdin: bytes) -> bytes:
    """Prefix ephemeral provider input with the broker-owned release frame."""

    return _EXEC_GATE_FRAME + provider_stdin


def _read_exact(fd: int, size: int) -> bytes:
    """Read without userspace buffering so provider stdin remains in the pipe."""

    value = bytearray()
    while len(value) < size:
        block = os.read(fd, size - len(value))
        if not block:
            break
        value.extend(block)
    return bytes(value)


def main(argv: Sequence[str] | None = None) -> int:
    """Wait for the release frame, then replace this process with the provider."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 2 or arguments[0] != "--":
        os.write(2, _INVALID_GATE_MESSAGE)
        return 125
    provider_argv = arguments[1:]
    if not Path(provider_argv[0]).is_absolute():
        os.write(2, _INVALID_GATE_MESSAGE)
        return 125
    if _read_exact(0, len(_EXEC_GATE_FRAME)) != _EXEC_GATE_FRAME:
        os.write(2, _INVALID_GATE_MESSAGE)
        return 125
    try:
        # execve preserves the PID and process group recorded before the
        # canonical delegation.started transition. The provider sees only the
        # remaining bytes in the stdin pipe, never the gate control frame.
        os.execve(provider_argv[0], provider_argv, dict(os.environ))
    except OSError:
        os.write(2, _EXEC_FAILED_MESSAGE)
        return 126


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
