"""Bounded, cancellable subprocess execution without shell interpolation."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol

from agent_commons.errors import ValidationError

from .exec_gate import gated_argv, gated_stdin
from .model import RunnerInvocation, _safe_identifier

_SAFE_HOST_ENVIRONMENT = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SystemRoot",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "WINDIR",
    }
)


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RunReason(StrEnum):
    COMPLETED = "completed"
    NONZERO_EXIT = "nonzero_exit"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    START_FAILED = "start_failed"
    INVALID_WORKING_DIRECTORY = "invalid_working_directory"
    CONTROL_ERROR = "control_error"


@dataclass(frozen=True, slots=True)
class SafeEnvironment:
    """A sanitized host environment with one broker-owned session override."""

    _values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        values: dict[str, str] = {}
        for item in self._values:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValidationError("runtime environment entries must be key/value pairs")
            key, value = item
            if key in values:
                raise ValidationError(f"runtime environment key is duplicated: {key}")
            if key not in _SAFE_HOST_ENVIRONMENT:
                raise ValidationError(f"runtime environment contains unsupported key: {key}")
            if not isinstance(value, str) or "\x00" in value:
                raise ValidationError(f"runtime environment value is invalid: {key}")
            values[key] = value
        object.__setattr__(self, "_values", tuple(sorted(values.items())))

    @classmethod
    def from_host(cls, source: Mapping[str, str] | None = None) -> SafeEnvironment:
        values = os.environ if source is None else source
        return cls.from_mapping(
            {key: values[key] for key in _SAFE_HOST_ENVIRONMENT if key in values}
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> SafeEnvironment:
        unknown = sorted(set(values) - _SAFE_HOST_ENVIRONMENT)
        if unknown:
            raise ValidationError(
                "runtime environment contains unsupported keys: " + ", ".join(unknown)
            )
        return cls(tuple(values.items()))

    def for_child_session(
        self, child_session_id: str, *, delegation_id: str | None = None
    ) -> dict[str, str]:
        _safe_identifier("child_session_id", child_session_id)
        if delegation_id is not None:
            _safe_identifier("delegation_id", delegation_id)
        result = dict(self._values)
        # Parent identity is never inherited; these are broker-derived bindings,
        # never caller-controlled environment overrides.
        result["AGENT_COMMONS_SESSION_ID"] = child_session_id
        if delegation_id is not None:
            result["AGENT_COMMONS_DELEGATION_ID"] = delegation_id
        return result


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class ProcessHandle(Protocol):
    pid: int
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class ProcessFactory(Protocol):
    def __call__(
        self, argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
    ) -> ProcessHandle: ...


class ProcessGroupTerminator(Protocol):
    def __call__(self, process: ProcessHandle, *, force: bool) -> None: ...


def _spawn_process(
    argv: tuple[str, ...], cwd: Path, environment: Mapping[str, str]
) -> ProcessHandle:
    return subprocess.Popen(  # type: ignore[return-value]
        argv,
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )


def _terminate_process_group(process: ProcessHandle, *, force: bool) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    if force:
        process.kill()
    else:
        process.terminate()


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._remaining = limit
        self._lock = threading.Lock()
        self._values = {"stdout": bytearray(), "stderr": bytearray()}
        self._seen = {"stdout": 0, "stderr": 0}

    def consume(self, channel: str, data: bytes) -> None:
        with self._lock:
            self._seen[channel] += len(data)
            retained = data[: self._remaining]
            self._values[channel].extend(retained)
            self._remaining -= len(retained)

    def value(self, channel: str) -> bytes:
        with self._lock:
            return bytes(self._values[channel])

    def seen(self, channel: str) -> int:
        with self._lock:
            return self._seen[channel]

    @property
    def truncated(self) -> bool:
        with self._lock:
            return sum(self._seen.values()) > self.limit


@dataclass(frozen=True, slots=True)
class ProcessResult:
    outcome: RunOutcome
    reason: RunReason
    exit_code: int | None
    pid: int | None
    duration_seconds: float
    stdout: bytes
    stderr: bytes
    stdout_bytes_seen: int
    stderr_bytes_seen: int
    output_truncated: bool


class SubprocessRunner:
    """Execute a fixed profile invocation with bounded observable behavior."""

    def __init__(
        self,
        *,
        environment: SafeEnvironment | None = None,
        process_factory: ProcessFactory = _spawn_process,
        terminator: ProcessGroupTerminator = _terminate_process_group,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 0.05,
        termination_grace_seconds: float = 2.0,
    ) -> None:
        if poll_interval_seconds <= 0 or termination_grace_seconds < 0:
            raise ValueError("runner polling and termination intervals are invalid")
        self.environment = environment or SafeEnvironment.from_host()
        self.process_factory = process_factory
        self.terminator = terminator
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval_seconds = poll_interval_seconds
        self.termination_grace_seconds = termination_grace_seconds

    @staticmethod
    def _drain(stream: BinaryIO | None, output: _BoundedOutput, channel: str) -> None:
        if stream is None:
            return
        try:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    return
                output.consume(channel, block)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _write_stdin(stream: BinaryIO | None, value: bytes) -> None:
        if stream is None:
            return
        try:
            stream.write(value)
            stream.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _stop(self, process: ProcessHandle) -> None:
        self.terminator(process, force=False)
        deadline = self.monotonic() + self.termination_grace_seconds
        while process.poll() is None and self.monotonic() < deadline:
            self.sleeper(self.poll_interval_seconds)
        if process.poll() is None:
            self.terminator(process, force=True)
        try:
            process.wait(timeout=max(self.termination_grace_seconds, 0.1))
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _empty_result(
        *, outcome: RunOutcome, reason: RunReason, duration_seconds: float = 0.0
    ) -> ProcessResult:
        return ProcessResult(
            outcome=outcome,
            reason=reason,
            exit_code=None,
            pid=None,
            duration_seconds=duration_seconds,
            stdout=b"",
            stderr=b"",
            stdout_bytes_seen=0,
            stderr_bytes_seen=0,
            output_truncated=False,
        )

    def run(
        self,
        invocation: RunnerInvocation,
        *,
        cwd: str | Path,
        child_session_id: str,
        delegation_id: str | None = None,
        timeout_seconds: int,
        max_output_bytes: int,
        cancellation: CancellationToken | None = None,
        on_started: Callable[[int], None] | None = None,
    ) -> ProcessResult:
        if timeout_seconds < 1 or max_output_bytes < 1:
            raise ValueError("timeout and output limits must be positive")
        started_at = self.monotonic()
        token = cancellation or CancellationToken()
        if token.cancelled:
            return self._empty_result(outcome=RunOutcome.CANCELLED, reason=RunReason.CANCELLED)
        workdir = Path(cwd).expanduser().resolve()
        if not workdir.is_dir():
            return self._empty_result(
                outcome=RunOutcome.FAILED,
                reason=RunReason.INVALID_WORKING_DIRECTORY,
            )
        environment = self.environment.for_child_session(
            child_session_id, delegation_id=delegation_id
        )
        try:
            # Spawn only the inert gate before canonical delegation.started.
            # The gate is replaced in-place by the provider after the lifecycle
            # hook succeeds, preserving the recorded PID and process group while
            # preventing provider startup timeouts on a slow canonical write.
            process = self.process_factory(gated_argv(invocation.argv), workdir, environment)
        except OSError:
            return self._empty_result(
                outcome=RunOutcome.FAILED,
                reason=RunReason.START_FAILED,
                duration_seconds=self.monotonic() - started_at,
            )

        output = _BoundedOutput(max_output_bytes)
        readers = [
            threading.Thread(
                target=self._drain,
                args=(process.stdout, output, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=self._drain,
                args=(process.stderr, output, "stderr"),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        writer: threading.Thread | None = None

        try:
            if on_started is not None:
                on_started(process.pid)
        except Exception:
            # The instruction remains undisclosed until the operational process
            # identity and canonical delegation.started hook both succeed.
            self._stop(process)
            self._write_stdin(process.stdin, b"")
            outcome = RunOutcome.FAILED
            reason = RunReason.CONTROL_ERROR
        else:
            writer = threading.Thread(
                target=self._write_stdin,
                args=(process.stdin, gated_stdin(invocation.stdin)),
                daemon=True,
            )
            writer.start()
            outcome = RunOutcome.FAILED
            reason = RunReason.NONZERO_EXIT
            deadline = started_at + timeout_seconds
            while True:
                return_code = process.poll()
                if return_code is not None:
                    if return_code == 0:
                        outcome = RunOutcome.SUCCEEDED
                        reason = RunReason.COMPLETED
                    break
                if token.cancelled:
                    self._stop(process)
                    outcome = RunOutcome.CANCELLED
                    reason = RunReason.CANCELLED
                    break
                if self.monotonic() >= deadline:
                    self._stop(process)
                    outcome = RunOutcome.TIMED_OUT
                    reason = RunReason.TIMEOUT
                    break
                self.sleeper(self.poll_interval_seconds)

        if writer is not None:
            writer.join(timeout=1.0)
        for reader in readers:
            reader.join(timeout=1.0)
        exit_code = process.poll()
        return ProcessResult(
            outcome=outcome,
            reason=reason,
            exit_code=exit_code,
            pid=process.pid,
            duration_seconds=max(0.0, self.monotonic() - started_at),
            stdout=output.value("stdout"),
            stderr=output.value("stderr"),
            stdout_bytes_seen=output.seen("stdout"),
            stderr_bytes_seen=output.seen("stderr"),
            output_truncated=output.truncated,
        )
