"""The panel owns its session: opens it, renews it, repairs it, closes it.

Everything here runs on an injected clock; no test sleeps.  The identity
assertions are an eternal contract: `open_session` deduplicates only on a
byte-identical identity, so any varying field (version, port, pid) would turn
every panel restart into a `LifecycleConflictError`.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from agent_commons.coordination.sessions import Session, SessionRegistry
from agent_commons.errors import ConfigurationError, LifecycleConflictError
from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CommunicationAuthorizationError,
    CommunicationStore,
    CorrelationIds,
    RuntimePolicy,
    checkout_fingerprint,
)
from agent_commons.services import CommonsManager
from agent_commons.services.communication import CommunicationRuntimeService, _participant_id
from agent_commons.ui.context import UIContext
from agent_commons.ui.session_owner import (
    ProjectSessionOwner,
    panel_session_identity,
)


class Clock:
    """A hand-cranked clock; starts at real time so real-clock readers agree.

    Starts on a whole second: timestamps round-trip through microsecond ISO
    precision, and a fractional start would make TTL arithmetic drift by
    sub-microsecond rounding.
    """

    def __init__(self) -> None:
        self.value = float(int(time.time()))

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _owner(workspace: dict[str, Any], clock: Clock, **kwargs: Any) -> ProjectSessionOwner:
    return ProjectSessionOwner(
        workspace["repo"],
        state_root=workspace["state_root"],
        clock=clock,
        **kwargs,
    )


def _registry_session(workspace: dict[str, Any], clock: Clock, session_id: str) -> Session:
    registry = SessionRegistry(
        workspace["repo"],
        state_root=CommonsManager(
            workspace["repo"], state_root=workspace["state_root"], read_only=True
        ).paths.state_root,
        clock=clock,
    )
    for session in registry.list_sessions():
        if session.session_id == session_id:
            return session
    raise AssertionError(f"session not in the registry: {session_id}")


def test_the_panel_identity_is_frozen_without_any_varying_field() -> None:
    """Eternal contract: nothing volatile may ever join this identity."""

    identity = panel_session_identity("workspace.abc123")
    assert identity == {
        "stable_instance_id": "agent-commons-ui-workspace.abc123",
        "principal": "local-operator",
        "client": "agent-commons",
        "software": "agent-commons-ui",
        "role": "operator",
        "capabilities": (),
        "model_family": None,
        "model": None,
        "source_producer": None,
    }


def test_heartbeat_extends_the_ttl_and_rotates_the_nonce(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    session_id = owner.ensure_active()
    before = _registry_session(workspace, clock, session_id)

    clock.value += 1000
    owner.heartbeat_now()

    after = _registry_session(workspace, clock, session_id)
    assert after.expires_at > before.expires_at
    assert after.nonce != before.nonce
    # The owner tracked the rotation: a further renewal still succeeds.
    clock.value += 1000
    owner.heartbeat_now()
    assert _registry_session(workspace, clock, session_id).expires_at > after.expires_at


def test_a_restart_with_the_same_identity_re_adopts_the_same_session(
    workspace: dict[str, Any], clock: Clock
) -> None:
    first = _owner(workspace, clock)
    session_id = first.ensure_active()

    restarted = _owner(workspace, clock)
    assert restarted.ensure_active() == session_id
    # And the re-adopted nonce is the live one: the restarted owner can renew.
    restarted.heartbeat_now()


def test_a_changed_identity_is_a_lifecycle_conflict(
    workspace: dict[str, Any], clock: Clock
) -> None:
    """Pin of the deduplication rule that makes the identity a frozen contract."""

    owner = _owner(workspace, clock)
    owner.ensure_active()
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"], read_only=True)
    registry = SessionRegistry(workspace["repo"], state_root=manager.paths.state_root, clock=clock)
    varied = panel_session_identity(manager.workspace_id)
    varied["software"] = "agent-commons-ui-2.0"

    with pytest.raises(LifecycleConflictError):
        registry.open_session(**varied)


def test_an_expired_session_is_replaced_under_the_same_identity(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    first = owner.ensure_active()

    clock.value += 9 * 3600  # the laptop slept past the eight-hour TTL

    second = owner.ensure_active()
    assert second != first
    assert owner.session_ids() == (first, second)
    assert _registry_session(workspace, clock, second).active_at(clock.value)


def test_refresh_liveness_repairs_an_expired_session_without_a_write(
    workspace: dict[str, Any], clock: Clock
) -> None:
    """The stream's probe: expiry is noticed by watching, not only by writing."""

    owner = _owner(workspace, clock)
    first = owner.ensure_active()

    clock.value += 9 * 3600  # the laptop slept past the eight-hour TTL

    owner.refresh_liveness()

    second = owner.session_id
    assert second is not None and second != first
    assert owner.session_ids() == (first, second)
    owner.shutdown()


def test_refresh_liveness_never_opens_a_first_session(tmp_path: Any, clock: Clock) -> None:
    """The probe watches; it must not drag a sessionless panel into opening.

    In particular a panel on a directory with no workspace serves its stream
    long before a session can exist, and the probe must neither raise there
    nor create the session as a side effect of being watched.
    """

    import subprocess

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    owner = ProjectSessionOwner(repo, state_root=tmp_path / "state", clock=clock)

    owner.refresh_liveness()  # no workspace: must be silent

    assert owner.session_id is None
    assert owner.session_ids() == ()
    owner.shutdown()


def test_shutdown_closes_the_session_when_no_run_is_live(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    session_id = owner.ensure_active()

    outcome = owner.shutdown()

    assert outcome == {"session_id": session_id, "closed": True, "reason": None}
    assert _registry_session(workspace, clock, session_id).status == "closed"


def test_shutdown_deliberately_keeps_the_session_open_under_a_live_delegation(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    session_id = owner.ensure_active()
    bound = CommonsManager(
        workspace["repo"], state_root=workspace["state_root"], session_id=session_id
    )
    task = bound.create_task(
        title="Keep the session alive",
        description="A live run must survive the panel closing.",
        acceptance_criteria=("The session outlives the panel",),
    )
    delegation = bound.create_delegation(
        target_ref={"kind": "task", "id": task["entity_ref"]["id"]},
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 900,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )

    outcome = owner.shutdown()

    assert outcome["closed"] is False
    assert str(delegation["entity_ref"]["id"]) in str(outcome["reason"])
    assert _registry_session(workspace, clock, session_id).status == "active"


def test_a_second_panel_refuses_to_start_and_names_the_first_ones_port(
    workspace: dict[str, Any], clock: Clock
) -> None:
    first = _owner(workspace, clock)
    first.acquire_panel_lock(4321)

    second = _owner(workspace, clock)
    with pytest.raises(ConfigurationError) as refusal:
        second.acquire_panel_lock(9999)

    assert getattr(refusal.value, "code", None) == "panel_already_open"
    assert getattr(refusal.value, "details", None) == {"port": 4321}
    assert "4321" in str(refusal.value)
    # The refused panel must not touch the shared session on its way out.
    session_id = first.ensure_active()
    second.ensure_active()
    second.shutdown()
    assert _registry_session(workspace, clock, session_id).status == "active"
    # Once the first panel is gone the project is claimable again.
    first.shutdown()
    second.acquire_panel_lock(9999)
    second.shutdown()


def test_a_wall_time_near_the_ttl_forces_a_heartbeat_before_the_launch(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    session_id = owner.ensure_active()
    before = _registry_session(workspace, clock, session_id)

    # The default TTL is eight hours; a run of the same length cannot fit the
    # finalization margin, so the guarantee must renew first.
    assert owner.ensure_run_ttl(8 * 3600) == session_id

    after = _registry_session(workspace, clock, session_id)
    assert after.nonce != before.nonce
    from agent_commons.storage.opstate import parse_timestamp

    assert parse_timestamp(after.expires_at) - clock.value >= 8 * 3600 + 120


def test_a_run_the_session_cannot_cover_is_refused_in_words(
    workspace: dict[str, Any], clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renewal failure surfaces as a sentence, not a raw broker internal."""

    owner = _owner(workspace, clock)
    owner.ensure_active()

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise LifecycleConflictError("session ownership nonce does not match")

    monkeypatch.setattr(owner._registry, "heartbeat", refuse)
    with pytest.raises(ConfigurationError) as refusal:
        owner.ensure_run_ttl(8 * 3600)
    assert "could not extend its session" in str(refusal.value)


def test_the_context_writer_repairs_a_stale_session_before_the_first_write(
    workspace: dict[str, Any], clock: Clock
) -> None:
    """The panel used to start with an expired session and die on its first POST."""

    owner = _owner(workspace, clock)
    first = owner.ensure_active()
    context = UIContext(workspace["repo"], state_root=workspace["state_root"], session_owner=owner)

    clock.value += 9 * 3600

    writer = context.writer()
    assert writer.session_id != first
    created = writer.create_task(
        title="Written after recovery",
        description="The first POST after waking must succeed.",
        acceptance_criteria=("The write lands under the recovered session",),
    )
    assert created["entity_ref"]["id"]


def test_pending_operations_stay_answerable_across_a_session_recovery(
    workspace: dict[str, Any], clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successor answers its predecessor's blocker, but a stranger cannot."""

    owner = _owner(workspace, clock)
    first = owner.ensure_active()
    context = UIContext(workspace["repo"], state_root=workspace["state_root"], session_owner=owner)

    parent = CommonsManager(workspace["repo"], state_root=workspace["state_root"], session_id=first)
    child = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    child_session = child.start_session(
        stable_instance_id="panel-lineage-child-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    child.session_id = child_session["session_id"]
    task = parent.create_task(
        title="Answer after panel recovery",
        description="Keep one private question answerable across the panel TTL.",
        acceptance_criteria=("The recovered panel answers without impersonating its predecessor",),
        idempotency_key="panel-lineage-task",
    )
    requested = parent.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 900,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 10_000},
        },
        idempotency_key="panel-lineage-delegation",
    )
    delegation_id = requested["entity_ref"]["id"]
    parent.start_delegation(
        delegation_id,
        requested["revision"],
        child_session_id=child_session["session_id"],
        idempotency_key="panel-lineage-delegation-start",
    )

    parent_policy = RuntimePolicy(
        remaining_depth=1,
        max_fanout=1,
        max_attempts=1,
        max_concurrency=1,
        timeout_seconds=900,
    )
    attempts = AttemptStore(workspace["state_root"])
    attempt = attempts.reserve(
        AttemptSpec(
            idempotency_key="panel-lineage-attempt",
            profile_id=BuiltinProfileId.CODEX_BUILDER,
            provider=BuiltinProfileId.CODEX_BUILDER.provider,
            correlation=CorrelationIds(
                delegation_id=delegation_id,
                target_kind="task",
                target_id=task["entity_ref"]["id"],
                target_revision=task["revision"],
                parent_session_id=first,
                child_session_id=child_session["session_id"],
            ),
            parent_policy=parent_policy,
            child_policy=parent_policy.derive_child(),
            checkout_fingerprint=checkout_fingerprint(workspace["repo"]),
        ),
        parent_policy=parent_policy,
    ).attempt
    attempts.transition(attempt.attempt_id, AttemptState.LAUNCHING, reason="process_starting")
    attempts.transition(
        attempt.attempt_id,
        AttemptState.RUNNING,
        reason="process_started",
        pid=4242,
    )
    opened = CommunicationRuntimeService(child, attempts=attempts).request_input(
        delegation_id,
        idempotency_key="panel-lineage-question",
        question="Which bounded branch should the worker take?",
        why_needed="Both branches satisfy the task.",
        safe_context={"choices": ["small", "large"]},
        desired_outcome="Choose one listed branch.",
        deadline_seconds=300,
    )
    operation_id = opened["operation"]["operation_id"]

    clock.value += 9 * 3600
    second = owner.ensure_active()
    assert second != first
    operations = context.pending_operations()
    assert [item["operation_id"] for item in operations] == [operation_id]
    assert operations[0]["answerable_here"] is True

    stranger = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    stranger_session = stranger.start_session(
        stable_instance_id="panel-lineage-stranger-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="observer",
    )
    stranger.session_id = stranger_session["session_id"]
    with pytest.raises(LifecycleConflictError, match="only replacements"):
        CommunicationRuntimeService(
            stranger,
            session_lineage=(first, stranger_session["session_id"]),
        ).inbox()
    foreign_context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=stranger_session["session_id"],
    )
    with pytest.raises(CommunicationAuthorizationError):
        foreign_context.answer_operation(
            operation_id=operation_id,
            answer={"selection": "large"},
            idempotency_key="foreign-lineage-answer",
        )

    observed: dict[str, Any] = {}
    original_reply = CommunicationStore.reply

    def observe_reply(self: CommunicationStore, *args: Any, **kwargs: Any) -> Any:
        observed["responder_session_id"] = kwargs.get("responder_session_id")
        return original_reply(self, *args, **kwargs)

    monkeypatch.setattr(CommunicationStore, "reply", observe_reply)
    replied = context.answer_operation(
        operation_id=operation_id,
        answer={"selection": "small"},
        idempotency_key="successor-lineage-answer",
    )
    assert replied["operation"]["state"] == "replied"
    assert replied["delegation"]["state"] == "active"
    assert observed["responder_session_id"] == _participant_id(second)


def test_the_owner_exposes_its_whole_session_lineage(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    first = owner.ensure_active()
    context = UIContext(workspace["repo"], state_root=workspace["state_root"], session_owner=owner)
    assert context.writer_session_ids == (first,)
    clock.value += 9 * 3600
    second = owner.ensure_active()
    assert context.writer_session_ids == (first, second)
    assert context.writer_session_id == second


def test_start_launches_a_renewal_thread_and_shutdown_stops_it(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock, heartbeat_interval_seconds=3600.0)
    session_id = owner.start()
    assert session_id.startswith("session.")
    thread = owner._thread
    assert thread is not None and thread.is_alive()

    outcome = owner.shutdown()

    assert outcome["closed"] is True
    assert not thread.is_alive()


def test_an_owner_built_before_the_workspace_exists_defers_instead_of_refusing(
    tmp_path: Any, clock: Clock
) -> None:
    """Constructing the owner must not settle the workspace.

    It used to, and that is what made `agent-commons ui` refuse in a directory
    with no workspace -- the one directory the panel's first-run screen exists
    for.  Everything the owner needs is resolved at first use instead, and the
    panel lock asked for at startup is taken as part of that resolution, still
    before any session is opened.
    """

    import subprocess

    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True)
    owner = ProjectSessionOwner(repo, state_root=tmp_path / "state", clock=clock)

    assert owner.workspace_ready() is False
    # Both deferred rather than raised: the panel is up and serving first run.
    owner.acquire_panel_lock(4321)
    assert owner.start() is None

    CommonsManager.initialize(repo, integrations=())

    session_id = owner.ensure_active()
    assert session_id.startswith("session.")
    assert owner.workspace_ready() is True
    # The lock asked for before there was anywhere to put it is held now, at
    # the port the panel actually bound.
    assert '"port":4321' in owner.panel_lock_path.read_text(encoding="utf-8")
    # And the renewal that could not start at the terminal is running.
    assert any(
        thread.name == f"agent-commons-ui-heartbeat-{owner.workspace_id}"
        for thread in threading.enumerate()
    )
    owner.shutdown()


def test_the_panel_lock_records_the_port_with_private_permissions(
    workspace: dict[str, Any], clock: Clock
) -> None:
    owner = _owner(workspace, clock)
    owner.acquire_panel_lock(51234)
    path = owner.panel_lock_path
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert '"port":51234' in path.read_text(encoding="utf-8")
    owner.shutdown()
