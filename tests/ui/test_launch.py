"""Launching a role's run from the panel (MUST-4).

The panel records a delegation on behalf of a role and runs it through the same
broker the CLI uses -- one launch path, not a second. No real provider is
spawned here: a fake runner stands in for the subscription CLI, exactly as the
runtime orchestration tests do, and completes the run as the bound child.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from agent_commons.runtime import (
    AttemptStore,
    ProcessResult,
    RunOutcome,
    RunReason,
    default_profile_registry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import LAUNCH_ROUTES, MUTATING_ROUTES, create_app
from tests.ui.conftest import PORT, authorized

#: Every field the run surface publishes, exactly.  Asserted as an equality so a
#: later change cannot quietly add one -- in particular a "spend"/"cost"/"used"
#: field, which nothing in this codebase can honestly fill: consumed provider
#: units are recorded nowhere, and `limits` is the launch-time cap, not a bill.
_RUN_FIELDS = {
    "delegation_id",
    "attempt_id",
    "phase",
    "live",
    "started_at",
    "updated_at",
    "duration_seconds",
    "profile_id",
    "target_kind",
    "target_id",
    "agent_id",
    "delegation_state",
    "purpose",
    "limits",
    "summary",
}


class FakeRunner:
    """Stands in for the provider CLI: starts, lets the child act, then exits."""

    def __init__(self, after_start: Callable[[str], None]) -> None:
        self.after_start = after_start
        self.calls = 0

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        del invocation
        self.calls += 1
        values["on_started"](7100 + self.calls)
        self.after_start(values["child_session_id"])
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=7100 + self.calls,
            duration_seconds=0.1,
            stdout=b"ephemeral provider content",
            stderr=b"",
            stdout_bytes_seen=26,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def _client(context: UIContext):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    app = create_app(context, token="test-token", port=PORT)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def _launch_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="ui-launch-window-000001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    manager.session_id = session["session_id"]
    role = manager.create_agent(
        name="Backend owner",
        profile_id="claude-builder",
        rationale="owns the surface and does the work",
        idempotency_key="launch-role",
    )
    task = manager.create_task(
        title="Wire the endpoint",
        description="the role will implement this",
        acceptance_criteria=("done",),
        idempotency_key="launch-task",
    )

    def complete_as_child(child_session_id: str) -> None:
        child = CommonsManager(
            workspace["repo"],
            session_id=child_session_id,
            state_root=workspace["state_root"],
        )
        # The child is bound to exactly one active delegation; find and finish it.
        bound = [
            record
            for record in child.snapshot().delegations.values()
            if record.get("child_session_id") == child_session_id
            and record.get("state") == "active"
        ]
        delegation = bound[0]
        child.succeed_delegation(
            str(delegation["id"]),
            str(delegation["revision"]),
            summary="endpoint wired",
            result_refs=({"kind": "task", "id": task["entity_ref"]["id"]},),
            idempotency_key="launch-child-succeed",
        )

    runner = FakeRunner(after_start=complete_as_child)

    def runtime_factory(bound_manager: CommonsManager) -> DelegationRuntimeService:
        return DelegationRuntimeService(
            bound_manager,
            runner=runner,  # type: ignore[arg-type]
            profiles=default_profile_registry(
                claude_executable="/bin/echo",
                mcp_executable="/bin/echo",
                trusted_workspace=True,
            ),
        )

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        launch_enabled=True,
        runtime_factory=runtime_factory,
    )
    return {
        "context": context,
        "manager": manager,
        "role_id": role["entity_ref"]["id"],
        "task_id": task["entity_ref"]["id"],
        "runner": runner,
    }


def test_launching_only_appears_behind_its_own_gate(
    workspace: dict[str, Any],
) -> None:
    """A writable panel without the launch gate exposes no launch route."""

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="ui-nolaunch-window-01",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    writable_only = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
    )
    with _client(writable_only) as client:
        found = {
            (method, route.path)
            for route in client.app.routes
            for method in (getattr(route, "methods", set()) or set())
            if method not in {"GET", "HEAD"}
        }
    assert found == set(MUTATING_ROUTES)  # no launch route
    assert ("POST", "/api/delegations") not in found


def test_the_panel_launches_a_role_on_a_task_end_to_end(
    workspace: dict[str, Any],
) -> None:
    fixture = _launch_workspace(workspace)
    context: UIContext = fixture["context"]

    with _client(context) as client:
        options = client.get("/api/launch", headers=authorized()).json()
        assert options["launch_enabled"] is True
        assert fixture["role_id"] in {role["id"] for role in options["roles"]}
        assert fixture["task_id"] in {task["id"] for task in options["tasks"]}

        response = client.post(
            "/api/delegations",
            json={"agent_id": fixture["role_id"], "task_id": fixture["task_id"]},
            headers=authorized(),
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["launched"] is True
    assert payload["target_profile"] == "claude-builder"
    delegation_id = payload["delegation_id"]

    # The run proceeds off-request; wait for the background launch to finish.
    context.await_launches()
    assert fixture["runner"].calls == 1
    delegation = fixture["manager"].get_delegation(delegation_id)
    assert delegation["state"] == "succeeded"
    assert delegation["agent_id"] == fixture["role_id"]


def test_launch_is_in_its_own_declared_surface_not_the_write_allowlist() -> None:
    assert ("POST", "/api/delegations") in LAUNCH_ROUTES
    assert ("POST", "/api/delegations") not in MUTATING_ROUTES


def test_a_run_appears_on_the_live_runs_surface_and_moves_the_fingerprint(
    workspace: dict[str, Any],
) -> None:
    """MUST-5: a launched run shows up in /api/runs with its phase, and the
    fingerprint folds in the runtime attempt so the panel refreshes as it moves.
    Phase only -- no provider output is ever surfaced."""

    from agent_commons.ui.context import ledger_fingerprint

    fixture = _launch_workspace(workspace)
    context: UIContext = fixture["context"]
    before = ledger_fingerprint(context.paths())

    with _client(context) as client:
        assert client.get("/api/runs", headers=authorized()).json() == []
        client.post(
            "/api/delegations",
            json={"agent_id": fixture["role_id"], "task_id": fixture["task_id"]},
            headers=authorized(),
        )
        context.await_launches()
        runs = client.get("/api/runs", headers=authorized()).json()

    assert len(runs) == 1
    run = runs[0]
    assert run["phase"] == "succeeded"
    assert run["profile_id"] == "claude-builder"
    assert run["agent_id"] == fixture["role_id"]
    assert run["target_id"] == fixture["task_id"]
    # No transcript/prompt/output field leaked onto the run surface.
    assert set(run) == _RUN_FIELDS
    # The runtime attempt moved the change detector, so the stream refreshes
    # during a run, not only on canonical events.
    assert ledger_fingerprint(context.paths()) != before


def _finished_run(workspace: dict[str, Any]) -> dict[str, Any]:
    """Launch one run through the panel and return the fixture once it is done."""

    fixture = _launch_workspace(workspace)
    context: UIContext = fixture["context"]
    with _client(context) as client:
        response = client.post(
            "/api/delegations",
            json={"agent_id": fixture["role_id"], "task_id": fixture["task_id"]},
            headers=authorized(),
        )
    assert response.status_code == 200, response.text
    context.await_launches()
    return fixture


def test_a_finished_run_says_when_it_started_and_how_long_it_took(
    workspace: dict[str, Any],
) -> None:
    """Finding 28: a run was unreadable -- no start time, no elapsed time."""

    fixture = _finished_run(workspace)
    run = fixture["context"].runs()[0]

    assert run["live"] is False
    assert isinstance(run["started_at"], str) and run["started_at"]
    assert isinstance(run["updated_at"], str) and run["updated_at"]
    assert isinstance(run["duration_seconds"], (int, float))
    assert not isinstance(run["duration_seconds"], bool)
    assert run["duration_seconds"] >= 0
    # What the run was for, and the bounds it was launched under.
    assert run["purpose"] == "implementation"
    assert run["limits"]["wall_time_seconds"] == 600


def test_a_live_run_reports_a_start_time_and_no_duration(
    workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moving run gets None: a duration computed here is stale on arrival."""

    fixture = _finished_run(workspace)
    monkeypatch.setattr(AttemptStore, "process_is_live", lambda self, pid: True)
    run = fixture["context"].runs()[0]

    assert run["live"] is True
    assert run["started_at"]
    assert run["duration_seconds"] is None


@pytest.mark.parametrize(
    ("created_at", "updated_at"),
    [
        ("not-a-timestamp", "2026-08-13T10:00:00Z"),
        ("2026-08-13T10:00:00Z", ""),
        # Clocks that disagree produce a negative interval, which is not a
        # duration either.
        ("2026-08-13T10:00:05Z", "2026-08-13T10:00:00Z"),
    ],
)
def test_unreadable_run_timestamps_degrade_to_no_duration(
    workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    created_at: str,
    updated_at: str,
) -> None:
    """The attempt store is a file this surface does not own, so a timestamp it
    cannot read must cost the panel a field, not the whole run list."""

    fixture = _finished_run(workspace)
    original = AttemptStore.list_attempts

    def mangled(self: AttemptStore, *args: Any, **values: Any) -> tuple[Any, ...]:
        return tuple(
            replace(attempt, created_at=created_at, updated_at=updated_at)
            for attempt in original(self, *args, **values)
        )

    monkeypatch.setattr(AttemptStore, "list_attempts", mangled)
    run = fixture["context"].runs()[0]

    assert run["started_at"] == created_at
    assert run["duration_seconds"] is None


def test_the_run_surface_claims_no_spend_it_never_recorded(
    workspace: dict[str, Any],
) -> None:
    """Nothing consumes or records provider units actually used, so nothing on
    this surface may look like a bill.  The budget shown is the launch-time cap,
    under the delegation's own `limits` key."""

    fixture = _finished_run(workspace)
    run = fixture["context"].runs()[0]

    assert set(run) == _RUN_FIELDS
    assert not [
        key
        for key in run
        if any(word in key for word in ("spend", "cost", "usd", "used", "consumed", "price"))
    ]
    assert run["limits"]["budget"] == {"unit": "provider_units", "limit": 1}


def _demo_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    """A launchable panel wired through a real demo runtime config.

    Unlike ``_launch_workspace``, this injects no fake runner: the panel builds
    its runtime service from an operator profile config that carries
    ``demo: true``, so the production ``_runtime_service`` path selects the
    DemoRunner.  This is the seam a newcomer's scratch workspace uses to close
    the Hire -> Task -> Run loop without a subscription.
    """

    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="ui-demo-window-000001",
        principal="operator",
        client="claude",
        software="claude-code",
        role="operator",
    )
    manager.session_id = session["session_id"]
    role = manager.create_agent(
        name="Backend owner",
        profile_id="claude-builder",
        rationale="owns the surface and does the work",
        idempotency_key="demo-role",
    )
    task = manager.create_task(
        title="Wire the endpoint",
        description="the role will implement this",
        acceptance_criteria=("done",),
        idempotency_key="demo-task",
    )
    # The profile config must live outside the delegated workspace; the repo is
    # tmp_path/repo, so a sibling file satisfies the guard.  The executable is
    # never invoked -- the DemoRunner stands in for it -- so /bin/echo is fine.
    config = workspace["repo"].parent / "demo-runtime.yaml"
    config.write_text(
        "demo: true\n"
        "profiles:\n"
        "  claude-builder:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )
    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        launch_enabled=True,
        profile_config=config,
    )
    return {
        "context": context,
        "manager": manager,
        "role_id": role["entity_ref"]["id"],
        "task_id": task["entity_ref"]["id"],
    }


def test_demo_profile_closes_the_loop_without_a_provider(
    workspace: dict[str, Any],
) -> None:
    """A demo-mode config lets the panel's Run reach `succeeded` with no
    provider launched, and labels the result honestly as a demo."""

    fixture = _demo_workspace(workspace)
    context: UIContext = fixture["context"]

    with _client(context) as client:
        assert client.get("/api/launch", headers=authorized()).json()["launch_enabled"] is True
        response = client.post(
            "/api/delegations",
            json={"agent_id": fixture["role_id"], "task_id": fixture["task_id"]},
            headers=authorized(),
        )
    assert response.status_code == 200, response.text
    delegation_id = response.json()["delegation_id"]

    context.await_launches()
    delegation = fixture["manager"].get_delegation(delegation_id)
    assert delegation["state"] == "succeeded"
    assert delegation["agent_id"] == fixture["role_id"]
    # The summary tells the truth: no provider ran.
    assert "demo" in str(delegation.get("summary", "")).lower()
    assert "no provider" in str(delegation.get("summary", "")).lower()
