"""Hermetic P0 provider-auth recovery through the Work HTTP boundary."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_commons.errors import ConfigurationError
from agent_commons.services import CommonsManager
from agent_commons.ui.context import UIContext
from agent_commons.ui.security import SESSION_COOKIE_NAME
from agent_commons.ui.server import LAUNCH_ROUTES, create_app

_SECRET = "oauth-secret-that-must-never-cross-the-ui"
PORT = 51234


def _authorized() -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE_NAME}=test-token"}


class FakeAuthRuntime:
    """A fixed-profile runtime seam with no provider process or credential."""

    def __init__(self) -> None:
        self.state = "authentication_required"
        self.login_calls = 0
        self.run_calls = 0
        self.login_entered = threading.Event()
        self.release_login = threading.Event()
        self.status_entered = threading.Event()
        self.release_status = threading.Event()
        self.block_status = False
        self.late_refusal = False
        self.active_logins = 0
        self.status_calls = 0
        self._guard = threading.Lock()

    def _status(self, profile_id: str, operation: str = "status") -> dict[str, Any]:
        return {
            "provider": "claude",
            "profile_id": profile_id,
            "operation": operation,
            "state": self.state,
            "supported": True,
            "blocks_launch": self.state != "ready",
            # The UI coordinator must drop unknown runtime fields.
            "raw_output": _SECRET,
            "account": _SECRET,
        }

    def provider_auth_status(self, profile_id: str) -> dict[str, Any]:
        with self._guard:
            self.status_calls += 1
        if self.block_status:
            self.status_entered.set()
            self.release_status.wait(timeout=10)
        return self._status(str(profile_id))

    def provider_auth_login(self, profile_id: str, *, cancellation: Any) -> dict[str, Any]:
        with self._guard:
            self.login_calls += 1
            self.active_logins += 1
        try:
            self.login_entered.set()
            self.release_login.wait(timeout=10)
            self.state = "cancelled" if cancellation.cancelled else "ready"
            return self._status(str(profile_id), "login")
        finally:
            with self._guard:
                self.active_logins -= 1

    def run(
        self,
        delegation_id: str,
        revision: str,
        *,
        idempotency_key: str,
        context: Any = None,
    ) -> None:
        del delegation_id, revision, idempotency_key, context
        self.run_calls += 1
        if self.late_refusal:
            error = ConfigurationError("provider authentication changed before start")
            error.code = "provider_auth_required"  # type: ignore[attr-defined]
            raise error


def _fixture(workspace: dict[str, Any]) -> tuple[UIContext, FakeAuthRuntime, str, str]:
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="ui-provider-auth-window",
        principal="operator",
        client="codex",
        software="codex-desktop",
        role="operator",
    )
    manager.session_id = session["session_id"]
    role = manager.create_agent(
        name="Claude builder",
        profile_id="claude-builder",
        rationale="exercise the auth recovery flow",
        idempotency_key="provider-auth-role",
    )
    task = manager.create_task(
        title="Authenticate before launch",
        description="prove the UI gate is pure",
        acceptance_criteria=("no delegation before readiness",),
        idempotency_key="provider-auth-task",
    )
    runtime = FakeAuthRuntime()
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        runtime_factory=lambda _: runtime,
    )
    return (
        context,
        runtime,
        str(role["entity_ref"]["id"]),
        str(task["entity_ref"]["id"]),
    )


def _client(context: UIContext) -> TestClient:
    return TestClient(
        create_app(context, token="test-token", port=PORT),
        base_url=f"http://127.0.0.1:{PORT}",
    )


def test_signed_out_launch_is_pure_then_async_login_allows_explicit_retry(
    workspace: dict[str, Any],
) -> None:
    context, runtime, role_id, task_id = _fixture(workspace)

    with _client(context) as client:
        status = client.get(
            "/api/provider-auth/claude-builder",
            headers=_authorized(),
        )
        assert status.status_code == 200
        assert status.json()["state"] == "authentication_required"
        assert status.json()["action_ids"] == ["authenticate", "check_again"]
        assert set(status.json()) == {
            "profile_id",
            "provider",
            "operation",
            "state",
            "supported",
            "blocks_launch",
            "checked_at",
            "freshness",
            "fresh_for_seconds",
            "action_ids",
            "post_start_recovery",
        }
        assert status.json()["freshness"] == "fresh"
        assert status.json()["post_start_recovery"] == "new_run_only"

        refused = client.post(
            "/api/delegations",
            headers=_authorized(),
            json={"agent_id": role_id, "task_id": task_id},
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "provider_auth_required"
        assert context.manager().snapshot().delegations == {}

        first = client.post(
            "/api/provider-auth/claude-builder/login",
            headers=_authorized(),
        )
        second = client.post(
            "/api/provider-auth/claude-independent-reviewer/login",
            headers=_authorized(),
        )
        assert first.json()["state"] == second.json()["state"] == "authenticating"
        assert runtime.login_entered.wait(timeout=10)
        assert runtime.login_calls == 1

        runtime.release_login.set()
        context._launch_coordinator._provider_auth.await_logins()  # exact owned process
        checked = client.post(
            "/api/provider-auth/claude-builder/check",
            headers=_authorized(),
        )
        assert checked.json()["state"] == "ready"
        assert checked.json()["action_ids"] == ["continue_launch"]

        launched = client.post(
            "/api/delegations",
            headers=_authorized(),
            json={
                "agent_id": role_id,
                "task_id": task_id,
                "idempotency_key": "continue-after-auth",
            },
        )
        assert launched.status_code == 200
        context.await_launches()

    assert runtime.run_calls == 1
    assert len(context.manager().snapshot().delegations) == 1
    wire = status.text + first.text + second.text + checked.text + launched.text
    assert _SECRET not in wire
    assert "oauth" not in wire.casefold()
    assert "token" not in wire.casefold()


def test_late_pre_start_auth_refusal_keeps_requested_delegation_retryable(
    workspace: dict[str, Any],
) -> None:
    context, runtime, role_id, task_id = _fixture(workspace)
    runtime.state = "ready"
    runtime.late_refusal = True

    result = context.run_role_on_task(
        agent_id=role_id,
        task_id=task_id,
        idempotency_key="late-auth-refusal",
        background=False,
    )

    delegation = context.manager().get_delegation(result["delegation_id"])
    assert delegation["state"] == "requested"
    assert delegation.get("reason_code") is None
    assert runtime.run_calls == 1


def test_auth_routes_are_fixed_operator_actions_and_unknown_profiles_refuse(
    workspace: dict[str, Any],
) -> None:
    context, _, _, _ = _fixture(workspace)
    assert {
        ("POST", "/api/provider-auth/{profile_id}/login"),
        ("POST", "/api/provider-auth/{profile_id}/cancel"),
        ("POST", "/api/provider-auth/{profile_id}/check"),
    } <= set(LAUNCH_ROUTES)

    with _client(context) as client:
        assert client.get("/api/provider-auth/claude-builder").status_code == 401
        assert client.post("/api/provider-auth/claude-builder/login").status_code == 401
        refusal = client.post(
            "/api/provider-auth/arbitrary-provider/login",
            headers=_authorized(),
        )

    assert refusal.status_code == 409
    assert refusal.json()["error"]["code"] == "ConfigurationError"
    assert _SECRET not in refusal.text


def test_cancel_and_shutdown_signal_the_owned_login_without_persisting_state(
    workspace: dict[str, Any],
) -> None:
    context, runtime, _, _ = _fixture(workspace)

    started = context.start_provider_login(profile_id="claude-builder")
    assert started["state"] == "authenticating"
    assert runtime.login_entered.wait(timeout=10)
    cancelled = context.cancel_provider_login(profile_id="claude-builder")
    assert cancelled["state"] == "authenticating"
    runtime.release_login.set()
    context._launch_coordinator._provider_auth.await_logins()
    assert context.provider_auth_status(profile_id="claude-builder")["state"] == "cancelled"
    context._launch_coordinator.shutdown()

    assert context.provider_auth_status(profile_id="claude-builder")["state"] == "failed"
    assert context.manager().snapshot().delegations == {}


def test_host_credential_remediation_is_never_inferred_from_a_generic_failure(
    workspace: dict[str, Any],
) -> None:
    context, runtime, _, _ = _fixture(workspace)

    runtime.state = "failed"
    generic = context.check_provider_auth(profile_id="claude-builder")
    assert generic["state"] == "failed"
    assert "repair_host_credentials" not in generic["action_ids"]

    # Only an explicit future runtime classifier may activate this closed
    # remediation.  The UI itself never guesses at Keychain/store corruption.
    runtime.state = "credential_store_unavailable"
    explicit = context.check_provider_auth(profile_id="claude-builder")
    assert explicit["state"] == "credential_store_unavailable"
    assert explicit["action_ids"] == ["check_again"]


def test_runtime_exceptions_are_sanitized_before_any_durable_launch_state(
    workspace: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    original, _, role_id, task_id = _fixture(workspace)
    secret = "sk-ant-review-secret-marker"
    private_path = "/private/var/provider/credentials.json"

    def failing_runtime_factory(_manager: CommonsManager) -> None:
        raise ConfigurationError(f"{secret} at {private_path}")

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(original.writer_session_id),
        runtime_factory=failing_runtime_factory,
    )
    before_sessions = len(context.manager().sessions.list_sessions())

    with caplog.at_level("WARNING"), _client(context) as client:
        status = client.get(
            "/api/provider-auth/claude-builder",
            headers=_authorized(),
        )
        login = client.post(
            "/api/provider-auth/claude-builder/login",
            headers=_authorized(),
        )
        context._launch_coordinator._provider_auth.await_logins()
        checked = client.post(
            "/api/provider-auth/claude-builder/check",
            headers=_authorized(),
        )
        refused = client.post(
            "/api/delegations",
            headers=_authorized(),
            json={"agent_id": role_id, "task_id": task_id},
        )

    assert status.json()["state"] == "failed"
    assert login.json()["state"] in {"authenticating", "failed"}
    assert checked.json()["state"] == "failed"
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "provider_auth_unknown"
    observed = status.text + login.text + checked.text + refused.text + caplog.text
    assert secret not in observed
    assert private_path not in observed
    assert context.manager().snapshot().delegations == {}
    assert context.runs() == []
    assert len(context.manager().sessions.list_sessions()) == before_sessions
    ledger = b"".join(
        path.read_bytes()
        for root in (workspace["repo"] / ".agent-commons" / "events",)
        for path in root.rglob("*")
        if path.is_file()
    )
    assert secret.encode() not in ledger
    assert private_path.encode() not in ledger


def test_shutdown_is_a_barrier_against_new_or_surviving_login_flights(
    workspace: dict[str, Any],
) -> None:
    context, runtime, _, _ = _fixture(workspace)
    coordinator = context._launch_coordinator._provider_auth
    assert context.start_provider_login(profile_id="claude-builder")["state"] == ("authenticating")
    assert runtime.login_entered.wait(timeout=10)
    shutdown = threading.Thread(target=coordinator.shutdown, kwargs={"timeout": 5.0})
    shutdown.start()
    deadline = time.monotonic() + 5.0
    while not coordinator._closing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert coordinator._closing is True

    with pytest.raises(ConfigurationError, match="shutting down"):
        context.start_provider_login(profile_id="claude-independent-reviewer")

    runtime.release_login.set()
    shutdown.join(timeout=10)
    assert shutdown.is_alive() is False
    assert runtime.active_logins == 0
    assert not any(thread.is_alive() for thread in coordinator._threads.values())


def test_shutdown_waits_for_admitted_status_probe_and_rejects_new_runtime_calls(
    workspace: dict[str, Any],
) -> None:
    context, runtime, _, _ = _fixture(workspace)
    coordinator = context._launch_coordinator._provider_auth
    runtime.block_status = True
    probe_results: list[dict[str, Any]] = []
    probe = threading.Thread(
        target=lambda: probe_results.append(
            context.provider_auth_status(profile_id="claude-builder")
        )
    )
    probe.start()
    assert runtime.status_entered.wait(timeout=10)

    shutdown_returned = threading.Event()
    shutdown_errors: list[Exception] = []

    def shut_down() -> None:
        try:
            coordinator.shutdown(timeout=5.0)
        except Exception as exc:  # pragma: no cover - assertion captures failure
            shutdown_errors.append(exc)
        finally:
            shutdown_returned.set()

    shutdown = threading.Thread(target=shut_down)
    shutdown.start()
    deadline = time.monotonic() + 5.0
    while not coordinator._closing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert coordinator._closing is True
    assert shutdown_returned.is_set() is False

    admitted_calls = runtime.status_calls
    assert context.provider_auth_status(profile_id="claude-builder")["state"] == "failed"
    with pytest.raises(ConfigurationError, match="shutting down"):
        context.check_provider_auth(profile_id="claude-builder")
    with pytest.raises(ConfigurationError, match="shutting down"):
        context.start_provider_login(profile_id="claude-builder")
    assert runtime.status_calls == admitted_calls
    assert runtime.login_calls == 0
    assert shutdown_returned.is_set() is False

    runtime.release_status.set()
    probe.join(timeout=10)
    shutdown.join(timeout=10)
    assert probe.is_alive() is False
    assert shutdown.is_alive() is False
    assert shutdown_errors == []
    assert shutdown_returned.is_set() is True
    assert probe_results[0]["state"] == "authentication_required"
    assert runtime.status_calls == admitted_calls
    assert coordinator._active_probes == 0


def test_status_and_login_method_exceptions_are_also_closed_before_launch(
    workspace: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    original, _, role_id, task_id = _fixture(workspace)
    secret = "method-secret-marker"
    private_path = "/private/var/provider/method.json"

    class ExplodingRuntime:
        def provider_auth_status(self, _profile_id: str) -> None:
            raise RuntimeError(f"{secret} at {private_path}")

        def provider_auth_login(self, _profile_id: str, *, cancellation: Any) -> None:
            del cancellation
            raise ConfigurationError(f"{secret} at {private_path}")

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(original.writer_session_id),
        runtime_factory=lambda _manager: ExplodingRuntime(),
    )
    with caplog.at_level("WARNING"), _client(context) as client:
        status = client.get(
            "/api/provider-auth/claude-builder",
            headers=_authorized(),
        )
        login = client.post(
            "/api/provider-auth/claude-builder/login",
            headers=_authorized(),
        )
        context._launch_coordinator._provider_auth.await_logins()
        checked = client.post(
            "/api/provider-auth/claude-builder/check",
            headers=_authorized(),
        )
        refused = client.post(
            "/api/delegations",
            headers=_authorized(),
            json={"agent_id": role_id, "task_id": task_id},
        )

    assert status.json()["state"] == checked.json()["state"] == "failed"
    assert login.json()["state"] in {"authenticating", "failed"}
    assert refused.status_code == 409
    observed = status.text + login.text + checked.text + refused.text + caplog.text
    assert secret not in observed
    assert private_path not in observed
    assert context.manager().snapshot().delegations == {}
    assert context.runs() == []


def test_late_launch_exception_uses_only_fixed_log_and_canonical_diagnostics(
    workspace: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    context, runtime, role_id, task_id = _fixture(workspace)
    runtime.state = "ready"
    secret = "late-provider-secret-marker"
    private_path = "/private/var/provider/session.json"

    def fail_late(_manager: CommonsManager) -> None:
        raise OSError(f"{secret} at {private_path}")

    # The auth coordinator captured the original fixed test runtime.  This
    # models a failure after the pure auth precheck but before provider start.
    context._runtime_factory = fail_late
    with caplog.at_level("WARNING"), _client(context) as client:
        response = client.post(
            "/api/delegations",
            headers=_authorized(),
            json={"agent_id": role_id, "task_id": task_id},
        )
        assert response.status_code == 200
        context.await_launches()

    delegation = context.manager().get_delegation(response.json()["delegation_id"])
    assert delegation["state"] == "needs_operator"
    assert delegation["summary"] == (
        "the panel could not start this run because runtime configuration or process "
        "startup failed; provider details were suppressed"
    )
    observed = caplog.text + str(delegation)
    assert secret not in observed
    assert private_path not in observed
