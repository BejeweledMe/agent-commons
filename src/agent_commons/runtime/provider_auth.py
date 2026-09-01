"""Provider-neutral authentication state for allowlisted provider CLIs.

A delegation launch spends a real provider unit.  When the host is signed out
that unit buys nothing: the provider starts, fails its own credential check, and
the delegation ends as an ambiguous runtime failure after a child session and a
durable attempt already exist.  This module answers the one question that makes
that avoidable *before* anything durable is written -- can the provider CLI, in
the same host credential context the broker itself runs in, act at all.

Three boundaries hold this narrow:

* **Fixed operations only.**  A caller selects a :class:`ProviderAuthOperation`,
  never argv.  Each adapter owns a literal argument tuple per operation, so no
  delegation, role, profile, UI field, or provider output can reach the command
  line.  The executable is the profile's own, resolved through the same
  ``resolve_trusted_executable`` gate a launch uses.
* **Closed results only.**  A probe returns a :class:`ProviderAuthState` and a
  maintainer-owned :class:`SafeDiagnostic`.  Provider bytes are decoded once,
  matched against a fixed marker table, and dropped.  No output, path, account,
  OAuth URL, verification code, or token is returned, cached, or persisted.
* **Single-flight process ownership.**  At most one auth process per
  workspace/provider exists across UI and broker processes.  Concurrent status
  readers coalesce in-process or serialize behind the operational file lock;
  a concurrent login reports ``AUTHENTICATING`` without starting another flow.

For an advertised adapter the gate fails closed: only ``READY`` permits launch.
A malformed, oversized, timed-out, cancelled, or unrecognized result is a typed
refusal before child-session or attempt creation.  ``UNSUPPORTED`` is distinct:
it says that this provider has no advertised auth-remediation capability, rather
than guessing that authentication is healthy or unhealthy.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from agent_commons.errors import ConfigurationError
from agent_commons.platform_support import try_lock_exclusive, unlock
from agent_commons.storage.opstate import ATTEMPT_STORAGE, ensure_private_directory

from .diagnostics import DiagnosticCode, SafeDiagnostic
from .model import (
    BuiltinProfileId,
    ExecutableRole,
    Provider,
    RunnerInvocation,
    RunnerProfile,
    fixed_profile_environment,
    resolve_trusted_executable,
)
from .subprocess_runner import (
    CancellationToken,
    ProcessResult,
    RunOutcome,
    SubprocessRunner,
)

#: Bounded read budget for one auth probe.  Anything larger is refused rather
#: than parsed: a provider that prints a megabyte at a credential check is not
#: one this closed marker table can honestly classify.
PROVIDER_AUTH_MAX_OUTPUT_BYTES = 16 * 1024
#: A status probe is a local credential lookup, not model work.
PROVIDER_AUTH_STATUS_TIMEOUT_SECONDS = 10
#: An operator-initiated sign-in may wait for a browser round trip.
PROVIDER_AUTH_LOGIN_TIMEOUT_SECONDS = 300

#: Session identity handed to the sanitized environment for an auth probe.  It
#: is deliberately not a canonical session: an auth process performs no
#: coordination and must never look like a bound child.
_AUTH_PROBE_SESSION_ID = "session.provider-auth-probe"


class ProviderAuthState(StrEnum):
    """The closed set of answers a provider auth probe may give."""

    #: The provider reported usable credentials in this host context.
    READY = "ready"
    #: The provider positively reported that this host is not signed in.
    AUTHENTICATION_REQUIRED = "authentication_required"
    #: A provider-owned interactive login currently owns the single-flight slot.
    AUTHENTICATING = "authenticating"
    #: The probe exceeded its bounded wall time.
    TIMED_OUT = "timed_out"
    #: The caller cancelled the probe before it finished.
    CANCELLED = "cancelled"
    #: The result was malformed, oversized, or otherwise unclassifiable.
    FAILED = "failed"
    #: This provider has no advertised auth-remediation adapter.
    UNSUPPORTED = "unsupported"

    @property
    def blocks_launch(self) -> bool:
        """Every advertised state except READY refuses a launch."""

        return self not in {ProviderAuthState.READY, ProviderAuthState.UNSUPPORTED}

    @property
    def determinate(self) -> bool:
        """Whether the probe actually decided something about the account."""

        return self in {
            ProviderAuthState.READY,
            ProviderAuthState.AUTHENTICATION_REQUIRED,
        }


class ProviderAuthOperation(StrEnum):
    """The fixed operations an adapter may expose."""

    STATUS = "status"
    LOGIN = "login"


_STATE_DIAGNOSTICS = {
    ProviderAuthState.READY: DiagnosticCode.NONE,
    ProviderAuthState.AUTHENTICATION_REQUIRED: DiagnosticCode.PROVIDER_AUTH_REQUIRED,
    ProviderAuthState.AUTHENTICATING: DiagnosticCode.PROVIDER_AUTH_UNKNOWN,
    ProviderAuthState.TIMED_OUT: DiagnosticCode.PROVIDER_AUTH_UNKNOWN,
    ProviderAuthState.CANCELLED: DiagnosticCode.PROVIDER_AUTH_UNKNOWN,
    ProviderAuthState.FAILED: DiagnosticCode.PROVIDER_AUTH_UNKNOWN,
    ProviderAuthState.UNSUPPORTED: DiagnosticCode.PROVIDER_AUTH_UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class ProviderAuthStatus:
    """One bounded, secret-free provider authentication result."""

    provider: Provider
    operation: ProviderAuthOperation
    state: ProviderAuthState
    diagnostic: SafeDiagnostic

    @classmethod
    def create(
        cls,
        *,
        provider: Provider,
        operation: ProviderAuthOperation,
        state: ProviderAuthState,
    ) -> ProviderAuthStatus:
        return cls(
            provider=provider,
            operation=operation,
            state=state,
            diagnostic=SafeDiagnostic.create(_STATE_DIAGNOSTICS[state]),
        )

    @property
    def blocks_launch(self) -> bool:
        return self.state.blocks_launch

    def as_dict(self) -> dict[str, Any]:
        """Return the read projection.  Every value here is maintainer-owned."""

        return {
            "provider": self.provider.value,
            "operation": self.operation.value,
            "state": self.state.value,
            "supported": self.state is not ProviderAuthState.UNSUPPORTED,
            "blocks_launch": self.blocks_launch,
            "diagnostic_code": self.diagnostic.code.value,
            "diagnostic_hint": self.diagnostic.hint,
            "safe_next_actions": list(self.diagnostic.safe_next_actions),
        }


class ProviderAuthAdapter(Protocol):
    """A provider's fixed auth operations and its bounded output reading."""

    provider: Provider

    def arguments(self, operation: ProviderAuthOperation) -> tuple[str, ...]:
        """Return the literal arguments appended to the resolved executable."""
        ...

    def classify(
        self,
        result: ProcessResult,
        operation: ProviderAuthOperation,
    ) -> ProviderAuthState:
        """Map one bounded process result onto the closed state set."""
        ...


def _status_document(result: ProcessResult) -> Mapping[str, Any] | None:
    """Parse only Claude's bounded ``auth status --json`` document.

    Truncated output is refused outright rather than matched on a prefix: a
    marker table applied to half a message is a guess wearing a closed enum.
    """

    if result.output_truncated:
        return None
    if len(result.stdout) + len(result.stderr) > PROVIDER_AUTH_MAX_OUTPUT_BYTES:
        return None
    try:
        value = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


@dataclass(frozen=True, slots=True)
class ClaudeAuthAdapter:
    """Fixed Claude Code auth operations run in the broker's own host context.

    The argument tuples are literals on purpose.  ``claude auth`` is the CLI's
    documented authentication group; if a future CLI renames or removes a
    subcommand the probe returns ``FAILED`` and launch refuses before spending
    a provider attempt.

    Agent Commons never supplies, stores, rotates, or switches a credential.
    ``login`` exists so an operator can be handed back to the provider's own
    sign-in flow; its result is a state, never an OAuth URL, code, or account.
    """

    provider: Provider = Provider.CLAUDE

    def arguments(self, operation: ProviderAuthOperation) -> tuple[str, ...]:
        return {
            ProviderAuthOperation.STATUS: ("auth", "status", "--json"),
            ProviderAuthOperation.LOGIN: ("auth", "login", "--claudeai"),
        }[ProviderAuthOperation(operation)]

    def classify(
        self,
        result: ProcessResult,
        operation: ProviderAuthOperation,
    ) -> ProviderAuthState:
        if result.outcome is RunOutcome.TIMED_OUT:
            return ProviderAuthState.TIMED_OUT
        if result.outcome is RunOutcome.CANCELLED:
            return ProviderAuthState.CANCELLED
        if operation is ProviderAuthOperation.LOGIN:
            return (
                ProviderAuthState.READY
                if result.outcome is RunOutcome.SUCCEEDED
                else ProviderAuthState.FAILED
            )
        value = _status_document(result)
        if value is None or not isinstance(value.get("loggedIn"), bool):
            return ProviderAuthState.FAILED
        if value["loggedIn"] is True and result.outcome is not RunOutcome.SUCCEEDED:
            return ProviderAuthState.FAILED
        return (
            ProviderAuthState.READY
            if value["loggedIn"] is True
            else ProviderAuthState.AUTHENTICATION_REQUIRED
        )


@dataclass(frozen=True, slots=True)
class CodexAuthAdapter:
    """Fixed Codex CLI status and browser-login operations.

    Codex does not expose a JSON status document.  Its current fixed contract
    is therefore deliberately narrower: a zero exit plus the maintainer-pinned
    ``Logged in using`` prefix is ready, while the exact signed-out marker is
    actionable.  Every other combination fails closed.
    """

    provider: Provider = Provider.CODEX

    def arguments(self, operation: ProviderAuthOperation) -> tuple[str, ...]:
        return {
            ProviderAuthOperation.STATUS: ("login", "status"),
            ProviderAuthOperation.LOGIN: ("login",),
        }[ProviderAuthOperation(operation)]

    def classify(
        self,
        result: ProcessResult,
        operation: ProviderAuthOperation,
    ) -> ProviderAuthState:
        if result.outcome is RunOutcome.TIMED_OUT:
            return ProviderAuthState.TIMED_OUT
        if result.outcome is RunOutcome.CANCELLED:
            return ProviderAuthState.CANCELLED
        if operation is ProviderAuthOperation.LOGIN:
            return (
                ProviderAuthState.READY
                if result.outcome is RunOutcome.SUCCEEDED
                else ProviderAuthState.FAILED
            )
        if result.output_truncated:
            return ProviderAuthState.FAILED
        if len(result.stdout) + len(result.stderr) > PROVIDER_AUTH_MAX_OUTPUT_BYTES:
            return ProviderAuthState.FAILED
        try:
            stdout = result.stdout.decode("utf-8", "strict").strip()
            stderr = result.stderr.decode("utf-8", "strict").strip()
        except UnicodeDecodeError:
            return ProviderAuthState.FAILED
        lines = (*stdout.splitlines(), *stderr.splitlines())
        ready_marker = any(line.startswith("Logged in using ") for line in lines)
        combined = f"{stdout}\n{stderr}".casefold()
        signed_out_marker = "not logged in" in combined
        if ready_marker and signed_out_marker:
            return ProviderAuthState.FAILED
        if result.outcome is RunOutcome.SUCCEEDED and ready_marker:
            return ProviderAuthState.READY
        if result.outcome is RunOutcome.FAILED and signed_out_marker:
            return ProviderAuthState.AUTHENTICATION_REQUIRED
        return ProviderAuthState.FAILED


@dataclass(frozen=True, slots=True)
class GrokAuthAdapter:
    """Fixed Grok Build model-catalog status and device-code login.

    Grok 1.0.13 exposes no ``login status`` command.  ``models`` is its
    documented non-interactive authenticated catalog operation; signed-out
    responses carry one of the closed login markers below.  API-key readiness
    records presence only, never the key value.
    """

    provider: Provider = Provider.GROK
    api_key_present: bool = field(default_factory=lambda: bool(os.environ.get("XAI_API_KEY")))

    def arguments(self, operation: ProviderAuthOperation) -> tuple[str, ...]:
        return {
            ProviderAuthOperation.STATUS: ("models",),
            ProviderAuthOperation.LOGIN: ("login", "--device-auth"),
        }[ProviderAuthOperation(operation)]

    def classify(
        self,
        result: ProcessResult,
        operation: ProviderAuthOperation,
    ) -> ProviderAuthState:
        if result.outcome is RunOutcome.TIMED_OUT:
            return ProviderAuthState.TIMED_OUT
        if result.outcome is RunOutcome.CANCELLED:
            return ProviderAuthState.CANCELLED
        if operation is ProviderAuthOperation.LOGIN:
            return (
                ProviderAuthState.READY
                if result.outcome is RunOutcome.SUCCEEDED
                else ProviderAuthState.FAILED
            )
        if result.output_truncated:
            return ProviderAuthState.FAILED
        if len(result.stdout) + len(result.stderr) > PROVIDER_AUTH_MAX_OUTPUT_BYTES:
            return ProviderAuthState.FAILED
        try:
            stdout = result.stdout.decode("utf-8", "strict").strip()
            stderr = result.stderr.decode("utf-8", "strict").strip()
        except UnicodeDecodeError:
            return ProviderAuthState.FAILED
        combined = f"{stdout}\n{stderr}".casefold()
        needs_login = any(
            marker in combined
            for marker in (
                "authenticate",
                "log in",
                "login",
                "not authenticated",
                "unauthorized",
                "xai_api_key",
            )
        )
        ready_marker = any(
            line.startswith("You are logged in with ") for line in stdout.splitlines()
        )
        api_key_ready = self.api_key_present and "available models:" in combined
        if (ready_marker or api_key_ready) and needs_login:
            return ProviderAuthState.FAILED
        if result.outcome is RunOutcome.SUCCEEDED and (ready_marker or api_key_ready):
            return ProviderAuthState.READY
        if result.outcome is RunOutcome.FAILED and needs_login:
            return ProviderAuthState.AUTHENTICATION_REQUIRED
        return ProviderAuthState.FAILED


def default_auth_adapters() -> dict[Provider, ProviderAuthAdapter]:
    """Return the operator-allowlisted adapters.

    All adapters expose only fixed, locally qualified CLI operations.  They do
    not accept workspace-supplied argv, credentials, or provider output.
    """

    return {
        Provider.CLAUDE: ClaudeAuthAdapter(),
        Provider.CODEX: CodexAuthAdapter(),
        Provider.GROK: GrokAuthAdapter(),
    }


@dataclass(slots=True)
class _InProcessFlight:
    operation: ProviderAuthOperation
    completed: threading.Event = field(default_factory=threading.Event)
    state: ProviderAuthState | None = None


_SINGLE_FLIGHT_GUARD = threading.Lock()
_SINGLE_FLIGHTS: dict[str, _InProcessFlight] = {}


class ProviderAuthController:
    """Run fixed provider auth operations under single-flight ownership."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        adapters: Mapping[Provider, ProviderAuthAdapter] | None = None,
        status_timeout_seconds: int = PROVIDER_AUTH_STATUS_TIMEOUT_SECONDS,
        login_timeout_seconds: int = PROVIDER_AUTH_LOGIN_TIMEOUT_SECONDS,
        max_output_bytes: int = PROVIDER_AUTH_MAX_OUTPUT_BYTES,
        lock_root: Path | None = None,
    ) -> None:
        if status_timeout_seconds < 1 or login_timeout_seconds < 1:
            raise ConfigurationError("provider auth timeouts must be positive")
        if not 1 <= max_output_bytes <= PROVIDER_AUTH_MAX_OUTPUT_BYTES:
            raise ConfigurationError("provider auth output budget is out of range")
        self.runner = runner or SubprocessRunner()
        self.adapters = dict(adapters if adapters is not None else default_auth_adapters())
        self.status_timeout_seconds = status_timeout_seconds
        self.login_timeout_seconds = login_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.lock_root = Path(lock_root).expanduser() if lock_root is not None else None

    def supports(self, provider: Provider) -> bool:
        return Provider(provider) in self.adapters

    def status(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: Path,
        cancellation: CancellationToken | None = None,
    ) -> ProviderAuthStatus:
        """Probe whether this host can act as the provider right now."""

        return self._operate(
            profile,
            ProviderAuthOperation.STATUS,
            workspace_root=workspace_root,
            cancellation=cancellation,
        )

    def login(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: Path,
        cancellation: CancellationToken | None = None,
    ) -> ProviderAuthStatus:
        """Hand an operator back to the provider's own sign-in flow.

        Never called by a launch.  A delegation that finds itself signed out is
        refused and reported; it does not start an interactive credential flow
        on the operator's behalf.
        """

        return self._operate(
            profile,
            ProviderAuthOperation.LOGIN,
            workspace_root=workspace_root,
            cancellation=cancellation,
        )

    def _operate(
        self,
        profile: RunnerProfile,
        operation: ProviderAuthOperation,
        *,
        workspace_root: Path,
        cancellation: CancellationToken | None,
    ) -> ProviderAuthStatus:
        operation = ProviderAuthOperation(operation)
        provider = Provider(profile.provider)
        adapter = self.adapters.get(provider)
        if adapter is None:
            return ProviderAuthStatus.create(
                provider=provider,
                operation=operation,
                state=ProviderAuthState.UNSUPPORTED,
            )
        root = self.lock_root or (Path(workspace_root).expanduser() / ".agent-commons")
        key = f"{root.resolve()}\0{provider.value}"
        with _SINGLE_FLIGHT_GUARD:
            active_flight = _SINGLE_FLIGHTS.get(key)
            if active_flight is None:
                flight = _InProcessFlight(operation=operation)
                _SINGLE_FLIGHTS[key] = flight
                owns_flight = True
            else:
                flight = active_flight
                owns_flight = False
        if not owns_flight:
            if flight.operation is ProviderAuthOperation.LOGIN:
                return ProviderAuthStatus.create(
                    provider=provider,
                    operation=operation,
                    state=ProviderAuthState.AUTHENTICATING,
                )
            timeout = (
                self.login_timeout_seconds
                if operation is ProviderAuthOperation.LOGIN
                else self.status_timeout_seconds
            )
            state = self._wait_for_flight(
                flight,
                timeout_seconds=timeout,
                cancellation=cancellation,
            )
            if operation is ProviderAuthOperation.STATUS or state in {
                ProviderAuthState.CANCELLED,
                ProviderAuthState.TIMED_OUT,
            }:
                return ProviderAuthStatus.create(
                    provider=provider,
                    operation=operation,
                    state=state,
                )
            # A login request that collided with a short status read must not
            # pretend a browser flow exists.  Retry ownership after that read;
            # a real concurrent login will then be reported as AUTHENTICATING.
            return self._operate(
                profile,
                operation,
                workspace_root=workspace_root,
                cancellation=cancellation,
            )
        try:
            try:
                state = self._operate_cross_process(
                    root=root,
                    adapter=adapter,
                    profile=profile,
                    operation=operation,
                    workspace_root=workspace_root,
                    cancellation=cancellation,
                )
            except OSError:
                state = ProviderAuthState.FAILED
        finally:
            with _SINGLE_FLIGHT_GUARD:
                flight.state = locals().get("state", ProviderAuthState.FAILED)
                flight.completed.set()
                if _SINGLE_FLIGHTS.get(key) is flight:
                    _SINGLE_FLIGHTS.pop(key, None)
        return ProviderAuthStatus.create(
            provider=provider,
            operation=operation,
            state=state,
        )

    def _wait_for_flight(
        self,
        flight: _InProcessFlight,
        *,
        timeout_seconds: int,
        cancellation: CancellationToken | None,
    ) -> ProviderAuthState:
        deadline = time.monotonic() + timeout_seconds
        while not flight.completed.wait(timeout=0.05):
            if cancellation is not None and cancellation.cancelled:
                return ProviderAuthState.CANCELLED
            if time.monotonic() >= deadline:
                return ProviderAuthState.TIMED_OUT
        return flight.state or ProviderAuthState.FAILED

    def _operate_cross_process(
        self,
        adapter: ProviderAuthAdapter,
        profile: RunnerProfile,
        operation: ProviderAuthOperation,
        *,
        root: Path,
        workspace_root: Path,
        cancellation: CancellationToken | None,
    ) -> ProviderAuthState:
        lock_dir = root / "runtime" / "provider-auth-locks"
        ensure_private_directory(lock_dir, policy=ATTEMPT_STORAGE)
        lock_path = lock_dir / f"{adapter.provider.value}.lock"
        login_lock_path = lock_dir / f"{adapter.provider.value}.login.lock"
        deadline = time.monotonic() + (
            self.login_timeout_seconds
            if operation is ProviderAuthOperation.LOGIN
            else self.status_timeout_seconds
        )
        login_descriptor = self._try_operation_lock(login_lock_path)
        if operation is ProviderAuthOperation.LOGIN:
            if login_descriptor is None:
                return ProviderAuthState.AUTHENTICATING
        else:
            # Contention here is proof of a *live* login owner; no stale file
            # contents participate in the state decision.
            if login_descriptor is None:
                return ProviderAuthState.AUTHENTICATING
            self._release_operation_lock(login_descriptor)
            login_descriptor = None
        descriptor: int | None = None
        try:
            while descriptor is None:
                descriptor = self._try_operation_lock(lock_path)
                if descriptor is not None:
                    break
                if cancellation is not None and cancellation.cancelled:
                    return ProviderAuthState.CANCELLED
                if time.monotonic() >= deadline:
                    return ProviderAuthState.TIMED_OUT
                time.sleep(0.05)
            body = json.dumps({"operation": operation.value}, sort_keys=True).encode()
            os.ftruncate(descriptor, 0)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, body)
            state = self._probe(
                adapter,
                profile,
                operation,
                workspace_root=workspace_root,
                cancellation=cancellation,
            )
            if operation is ProviderAuthOperation.LOGIN and state is ProviderAuthState.READY:
                state = self._probe(
                    adapter,
                    profile,
                    ProviderAuthOperation.STATUS,
                    workspace_root=workspace_root,
                    cancellation=cancellation,
                )
            return state
        finally:
            try:
                if descriptor is not None:
                    self._release_operation_lock(descriptor, clear=True)
            finally:
                if login_descriptor is not None:
                    self._release_operation_lock(login_descriptor)

    @staticmethod
    def _try_operation_lock(path: Path) -> int | None:
        if path.is_symlink():
            raise OSError("provider auth lock must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        keep_open = False
        try:
            os.fchmod(descriptor, 0o600)
            if not try_lock_exclusive(descriptor):
                return None
            keep_open = True
            return descriptor
        finally:
            if not keep_open:
                os.close(descriptor)

    @staticmethod
    def _release_operation_lock(descriptor: int, *, clear: bool = False) -> None:
        try:
            if clear:
                os.ftruncate(descriptor, 0)
        finally:
            try:
                unlock(descriptor)
            finally:
                os.close(descriptor)

    def _probe(
        self,
        adapter: ProviderAuthAdapter,
        profile: RunnerProfile,
        operation: ProviderAuthOperation,
        *,
        workspace_root: Path,
        cancellation: CancellationToken | None,
    ) -> ProviderAuthState:
        root = Path(workspace_root).expanduser()
        try:
            executable = resolve_trusted_executable(
                str(getattr(profile, "executable", "")),
                workspace_root=root,
                role=ExecutableRole.PROVIDER,
            )
        except ConfigurationError:
            # An unresolvable provider is already a launch refusal with its own
            # diagnostic; this probe must not shadow it with an auth verdict.
            return ProviderAuthState.FAILED
        invocation = RunnerInvocation(
            provider=Provider(profile.provider),
            profile_id=BuiltinProfileId(profile.profile_id),
            argv=(executable, *adapter.arguments(operation)),
            stdin=b"",
            extra_env=fixed_profile_environment(profile),
        )
        timeout = (
            self.login_timeout_seconds
            if operation is ProviderAuthOperation.LOGIN
            else self.status_timeout_seconds
        )
        try:
            result = self.runner.run(
                invocation,
                cwd=root,
                child_session_id=_AUTH_PROBE_SESSION_ID,
                timeout_seconds=timeout,
                max_output_bytes=self.max_output_bytes,
                cancellation=cancellation,
            )
        except OSError:
            return ProviderAuthState.FAILED
        return ProviderAuthState(adapter.classify(result, operation))


def provider_auth_refusal(status: ProviderAuthStatus) -> ConfigurationError:
    """Return the pre-start refusal for a blocking provider-auth state.

    Shaped like ``sanitized_configuration_failure`` so every caller that already
    renders ``code``/``safe_next_actions`` keeps working unchanged.
    """

    if not status.blocks_launch:
        raise ValueError("only a blocking provider auth status refuses a launch")
    error = ConfigurationError(
        "Provider launch was refused before any child session or delegation attempt "
        "existed. " + status.diagnostic.hint
    )
    error.code = status.diagnostic.code.value  # type: ignore[attr-defined]
    error.safe_next_actions = status.diagnostic.safe_next_actions  # type: ignore[attr-defined]
    error.provider_auth_state = status.state.value  # type: ignore[attr-defined]
    return error
