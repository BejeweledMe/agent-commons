"""In-memory provider authentication recovery for the local Work surface.

The runtime owns executable resolution, fixed argv, process groups, output
classification, and credential context.  This coordinator owns only browser
workflow state: a short-lived secret-free snapshot and one asynchronous login
thread per provider.  It never persists provider output or creates a
canonical record.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, cast

from agent_commons.errors import ConfigurationError
from agent_commons.runtime.model import BuiltinProfileId
from agent_commons.runtime.subprocess_runner import CancellationToken
from agent_commons.ui.read_dtos import (
    ProviderAuthActionKey,
    ProviderAuthDTO,
    ProviderAuthOperationKey,
    ProviderAuthPayload,
    ProviderAuthStateKey,
)

PROVIDER_AUTH_FRESH_SECONDS: Final = 15

_KNOWN_STATES: Final = frozenset(
    {
        "ready",
        "authentication_required",
        "authenticating",
        "timed_out",
        "cancelled",
        "failed",
        "unsupported",
        # Reserved for a future runtime classifier that can positively prove
        # the host credential store is unavailable.  Generic failures are not
        # promoted to this state and the UI never diagnoses host corruption.
        "credential_store_unavailable",
    }
)
_KNOWN_OPERATIONS: Final = frozenset({"status", "login"})


def _now() -> datetime:
    return datetime.now(UTC)


def _action_ids(state: ProviderAuthStateKey) -> tuple[ProviderAuthActionKey, ...]:
    if state == "ready":
        return ("continue_launch",)
    if state == "authentication_required":
        return ("authenticate", "check_again")
    if state == "authenticating":
        return ("cancel_authentication", "check_again")
    if state == "credential_store_unavailable":
        # Host repair is explanatory remediation, not an executable browser
        # action.  The only safe operation this surface can perform is a new
        # fixed status probe after the operator repairs the host externally.
        return ("check_again",)
    if state in {"timed_out", "cancelled", "failed"}:
        return ("check_again",)
    return ()


def _closed_status(
    profile_id: BuiltinProfileId,
    value: Mapping[str, Any],
    *,
    checked_at: datetime | None = None,
) -> ProviderAuthDTO:
    """Drop every runtime field except the closed availability vocabulary."""

    raw_state = value.get("state")
    state = cast(
        ProviderAuthStateKey,
        raw_state if isinstance(raw_state, str) and raw_state in _KNOWN_STATES else "failed",
    )
    raw_operation = value.get("operation")
    operation = cast(
        ProviderAuthOperationKey,
        raw_operation
        if isinstance(raw_operation, str) and raw_operation in _KNOWN_OPERATIONS
        else "status",
    )
    provider = profile_id.provider.value
    supported = state != "unsupported"
    blocks_launch = supported and state != "ready"
    moment = checked_at or _now()
    return ProviderAuthDTO(
        profile_id=profile_id.value,
        provider=provider,
        operation=operation,
        state=state,
        supported=supported,
        blocks_launch=blocks_launch,
        checked_at=moment.isoformat().replace("+00:00", "Z"),
        freshness="fresh",
        fresh_for_seconds=PROVIDER_AUTH_FRESH_SECONDS,
        action_ids=_action_ids(state),
    )


def _failed_status(profile_id: BuiltinProfileId) -> ProviderAuthDTO:
    return _closed_status(
        profile_id,
        {"state": "failed", "operation": "status"},
    )


def provider_auth_launch_refusal(status: ProviderAuthPayload) -> ConfigurationError:
    """Build a typed pre-start refusal without echoing a provider result."""

    state = status["state"]
    if not status["blocks_launch"]:
        raise ValueError("only a blocking provider auth status refuses launch")
    if state == "authentication_required":
        code = "provider_auth_required"
        message = "This provider requires authentication before a run can start."
    elif state == "credential_store_unavailable":
        code = "credential_store_unavailable"
        message = "This host cannot currently use the provider credential store."
    else:
        code = "provider_auth_unknown"
        message = "Provider authentication could not be confirmed before launch."
    error = ConfigurationError(message)
    error.code = code  # type: ignore[attr-defined]
    error.safe_next_actions = tuple(status["action_ids"])  # type: ignore[attr-defined]
    error.provider_auth_state = state  # type: ignore[attr-defined]
    return error


class UIProviderAuthCoordinator:
    """Own one provider login flow without owning credentials or provider argv."""

    def __init__(self, runtime_factory: Callable[[], Any]) -> None:
        self._runtime_factory = runtime_factory
        self._guard = threading.RLock()
        self._probe_barrier = threading.Condition(self._guard)
        self._snapshots: dict[BuiltinProfileId, ProviderAuthDTO] = {}
        self._checked_monotonic: dict[BuiltinProfileId, float] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, CancellationToken] = {}
        self._active_probes = 0
        self._closing = False

    @staticmethod
    def _profile(profile_id: str) -> BuiltinProfileId:
        try:
            return BuiltinProfileId(profile_id)
        except ValueError as exc:
            raise ConfigurationError("provider auth profile is not configured") from exc

    def _is_login_active(self, profile_id: BuiltinProfileId) -> bool:
        thread = self._threads.get(profile_id.provider.value)
        return thread is not None and thread.is_alive()

    @staticmethod
    def _closing_refusal() -> ConfigurationError:
        error = ConfigurationError("Provider authentication recovery is shutting down.")
        error.code = "provider_auth_unknown"  # type: ignore[attr-defined]
        error.safe_next_actions = ()  # type: ignore[attr-defined]
        return error

    def _authenticating(self, profile_id: BuiltinProfileId) -> ProviderAuthDTO:
        return _closed_status(
            profile_id,
            {"state": "authenticating", "operation": "login"},
        )

    def _remember(self, profile_id: BuiltinProfileId, status: ProviderAuthDTO) -> None:
        with self._guard:
            self._snapshots[profile_id] = status
            self._checked_monotonic[profile_id] = time.monotonic()

    def _probe(self, profile_id: BuiltinProfileId) -> ProviderAuthDTO:
        """Run one probe already admitted under ``self._guard``."""

        try:
            try:
                runtime = self._runtime_factory()
                value = runtime.provider_auth_status(profile_id)
                status = (
                    _closed_status(profile_id, value)
                    if isinstance(value, Mapping)
                    else _failed_status(profile_id)
                )
            except Exception:  # noqa: BLE001 - provider secrecy boundary
                # Exception text is untrusted provider/operator detail.  Collapse
                # every failure to the same maintainer-authored state and never log
                # or return the exception itself.
                status = _failed_status(profile_id)
            self._remember(profile_id, status)
            return status
        finally:
            with self._probe_barrier:
                self._active_probes -= 1
                self._probe_barrier.notify_all()

    def status(self, profile_id: str) -> ProviderAuthPayload:
        """Return a fresh snapshot, probing only when the cache has expired."""

        profile = self._profile(profile_id)
        with self._guard:
            if self._closing:
                return _failed_status(profile).to_wire()
            if self._is_login_active(profile):
                status = self._authenticating(profile)
                self._snapshots[profile] = status
                self._checked_monotonic[profile] = time.monotonic()
                return status.to_wire()
            cached = self._snapshots.get(profile)
            checked = self._checked_monotonic.get(profile)
            if (
                cached is not None
                and checked is not None
                and time.monotonic() - checked <= PROVIDER_AUTH_FRESH_SECONDS
            ):
                return cached.to_wire()
            self._active_probes += 1
        return self._probe(profile).to_wire()

    def check_again(self, profile_id: str) -> ProviderAuthPayload:
        """Explicitly refresh one fixed profile unless its login owns the slot."""

        profile = self._profile(profile_id)
        with self._guard:
            if self._closing:
                raise self._closing_refusal()
            if self._is_login_active(profile):
                return self._authenticating(profile).to_wire()
            self._active_probes += 1
        return self._probe(profile).to_wire()

    def start_login(self, profile_id: str) -> ProviderAuthPayload:
        """Start the provider-owned login process asynchronously and single-flight."""

        profile = self._profile(profile_id)
        provider_key = profile.provider.value
        with self._guard:
            if self._closing:
                raise self._closing_refusal()
            if self._is_login_active(profile):
                return self._authenticating(profile).to_wire()
            cancellation = CancellationToken()
            self._cancellations[provider_key] = cancellation
            status = self._authenticating(profile)
            self._snapshots[profile] = status

            def login() -> None:
                try:
                    value = self._runtime_factory().provider_auth_login(
                        profile,
                        cancellation=cancellation,
                    )
                    outcome = (
                        _closed_status(profile, value)
                        if isinstance(value, Mapping)
                        else _failed_status(profile)
                    )
                except Exception:  # noqa: BLE001 - this is the provider secrecy boundary
                    outcome = _failed_status(profile)
                finally:
                    with self._guard:
                        self._cancellations.pop(provider_key, None)
                self._remember(profile, outcome)

            thread = threading.Thread(
                target=login,
                name=f"provider-auth-{profile.provider.value}",
                daemon=True,
            )
            self._threads[provider_key] = thread
            thread.start()
            return self._snapshots[profile].to_wire()

    def cancel_login(self, profile_id: str) -> ProviderAuthPayload:
        """Request cancellation; process ownership remains with the runtime runner."""

        profile = self._profile(profile_id)
        with self._guard:
            cancellation = self._cancellations.get(profile.provider.value)
            if cancellation is not None:
                cancellation.cancel()
            if self._is_login_active(profile):
                return self._authenticating(profile).to_wire()
        return self.check_again(profile.value)

    def await_logins(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        for thread in list(self._threads.values()):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel owned login flows and wait briefly for process cleanup."""

        deadline = time.monotonic() + timeout
        with self._probe_barrier:
            self._closing = True
            tokens = tuple(self._cancellations.values())
        for token in tokens:
            token.cancel()

        with self._probe_barrier:
            while self._active_probes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConfigurationError(
                        "Provider authentication shutdown did not reach its bounded barrier."
                    )
                self._probe_barrier.wait(timeout=remaining)

        self.await_logins(timeout=max(0.0, deadline - time.monotonic()))
        with self._probe_barrier:
            live = tuple(thread for thread in self._threads.values() if thread.is_alive())
            probes_live = self._active_probes != 0
        if live or probes_live:
            raise ConfigurationError(
                "Provider authentication shutdown did not reach its bounded barrier."
            )
