"""Regressions from the safety review: honest stop, and budget by tree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_commons.runtime import (
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CorrelationIds,
    PolicyViolationError,
    Provider,
    RuntimePolicy,
)
from agent_commons.runtime.attempts import AttemptSpec, checkout_fingerprint


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def spec(
    tmp_path: Path,
    *,
    key: str = "req-1",
    delegation: str = "delegation.01KXAAAAAAAAAAAAAAAAAAAAAA",
    root: str | None = None,
    parent_session: str = "session.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    child_session: str = "session.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    provider_units: bool = True,
) -> AttemptSpec:
    parent = RuntimePolicy(remaining_depth=1, max_attempts=2)
    child = parent.derive_child(
        max_budget_microusd=None if provider_units else 1_000,
    )
    return AttemptSpec(
        idempotency_key=key,
        # A reviewer profile: writable builders are additionally capped at one
        # per checkout, which would mask the budget behaviour under test.
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        provider=Provider.CODEX,
        correlation=CorrelationIds(
            delegation_id=delegation,
            target_kind="task",
            target_id="task.01KXAAAAAAAAAAAAAAAAAAAAAA",
            target_revision="evt.01KXAAAAAAAAAAAAAAAAAAAAAA",
            parent_session_id=parent_session,
            child_session_id=child_session,
            root_delegation_id=root,
        ),
        parent_policy=parent,
        child_policy=child,
        checkout_fingerprint=checkout_fingerprint(tmp_path),
    )


def test_provider_units_are_charged_against_the_delegation_tree(tmp_path: Path) -> None:
    """Regression: accounting keyed on parent_session_id handed every generation
    a fresh allowance, because a child session is new by construction."""

    store = AttemptStore(tmp_path / "state", clock=Clock())
    root = "delegation.01KXROOTROOTROOTROOTROOTRO"
    cap = store.operator_limits.provider_units_cap(Provider.CODEX.value)

    for index in range(cap):
        reserved = store.reserve(
            spec(
                tmp_path,
                key=f"req-{index}",
                delegation=f"delegation.01KXCHILD{index:017d}",
                root=root,
                # Each generation runs under its own freshly opened child session.
                parent_session=f"session.{index:032d}",
                child_session=f"session.{index + 100:032d}",
            ),
            parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2),
        )
        # Finish each one so concurrency never masks the budget under test; a
        # spent unit stays spent.
        store.transition(reserved.attempt.attempt_id, AttemptState.FAILED, reason="done")

    with pytest.raises(PolicyViolationError, match="provider_units budget"):
        store.reserve(
            spec(
                tmp_path,
                key="req-overflow",
                delegation="delegation.01KXCHILDOVERFLOWOVERFLOW",
                root=root,
                parent_session="session." + "f" * 32,
                child_session="session." + "e" * 32,
            ),
            parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2),
        )


def test_one_session_still_cannot_open_unlimited_separate_trees(tmp_path: Path) -> None:
    """The tree scope must not replace the session scope: they bound different
    amplifications, and dropping either one opens the other."""

    store = AttemptStore(tmp_path / "state", clock=Clock())
    cap = store.operator_limits.provider_units_cap(Provider.CODEX.value)
    session = "session." + "a" * 32

    for index in range(cap):
        root = f"delegation.01KXROOT{index:018d}"
        reserved = store.reserve(
            spec(
                tmp_path,
                key=f"flat-{index}",
                delegation=root,
                root=root,
                parent_session=session,
                child_session=f"session.{index + 200:032d}",
            ),
            parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2),
        )
        store.transition(reserved.attempt.attempt_id, AttemptState.FAILED, reason="done")

    with pytest.raises(PolicyViolationError, match="provider_units budget"):
        root = "delegation.01KXROOTOVERFLOWOVERFLOWO"
        store.reserve(
            spec(
                tmp_path,
                key="flat-overflow",
                delegation=root,
                root=root,
                parent_session=session,
                child_session="session." + "d" * 32,
            ),
            parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2),
        )


def test_reconcile_refuses_to_terminalize_a_live_process(tmp_path: Path) -> None:
    """Regression: reconcile wrote needs_operator without checking liveness, so
    the ledger claimed the work had stopped while the provider kept writing."""

    store = AttemptStore(tmp_path / "state", clock=Clock())
    reserved = store.reserve(
        spec(tmp_path), parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2)
    )
    store.transition(reserved.attempt.attempt_id, AttemptState.LAUNCHING, reason="launching")
    # This interpreter is certainly alive, which is the point of the probe.
    store.transition(
        reserved.attempt.attempt_id,
        AttemptState.RUNNING,
        reason="running",
        pid=os.getpid(),
    )

    assert store.reconcile() == ()
    live = store.live_attempts()
    assert [attempt.attempt_id for attempt in live] == [reserved.attempt.attempt_id]
    assert store.list_attempts()[0].state is AttemptState.RUNNING


def test_reconcile_still_terminalizes_a_dead_process(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "state", clock=Clock())
    reserved = store.reserve(
        spec(tmp_path), parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2)
    )
    store.transition(reserved.attempt.attempt_id, AttemptState.LAUNCHING, reason="launching")
    # A pid that has certainly exited and been reaped.
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    store.transition(
        reserved.attempt.attempt_id,
        AttemptState.RUNNING,
        reason="running",
        pid=finished.pid,
    )

    reconciled = store.reconcile()
    assert [attempt.state for attempt in reconciled] == [AttemptState.NEEDS_OPERATOR]
    assert store.live_attempts() == ()


def test_service_reconcile_reports_a_live_provider_instead_of_terminalizing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the liveness probe lived in AttemptStore.reconcile, but the
    CLI calls DelegationRuntimeService.reconcile, which had its own path and
    terminalized any non-terminal attempt regardless."""

    from agent_commons.runtime import default_profile_registry
    from agent_commons.services.delegation_runtime import DelegationRuntimeService, _request_key
    from tests.runtime.test_orchestration import _delegation, _workspace

    manager, task = _workspace(tmp_path)
    _, delegation = _delegation(manager, task)
    delegation_id = delegation["entity_ref"]["id"]
    service = DelegationRuntimeService(manager, profiles=default_profile_registry())

    reserved = service.attempts.reserve(
        spec(
            tmp_path,
            # The service binds exactly one operational request per delegation.
            key=_request_key(delegation_id),
            delegation=delegation_id,
            parent_session=manager.session_id,
        ),
        parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2),
    )
    service.attempts.transition(
        reserved.attempt.attempt_id, AttemptState.LAUNCHING, reason="launching"
    )
    service.attempts.transition(
        reserved.attempt.attempt_id,
        AttemptState.RUNNING,
        reason="running",
        pid=os.getpid(),
    )

    reported = service.reconcile()
    entry = next(item for item in reported if item["attempt"]["correlation"]["delegation_id"])
    assert entry["reconciled"] is False
    assert entry["provider_still_running"] is True
    assert "broker stop" in " ".join(entry["safe_next_actions"])
    # The attempt is untouched: no outcome was invented for a running process.
    assert service.attempts.list_attempts()[0].state is AttemptState.RUNNING


def test_the_subtree_ceiling_refuses_a_real_reservation(tmp_path: Path) -> None:
    """Driven through AttemptStore.reserve, not through a hand-built usage
    object.  The counter this guard reads was never produced, so a test that
    constructed RuntimeUsage itself confirmed a guard that could not fire."""

    from agent_commons.runtime import OperatorLimits

    store = AttemptStore(
        tmp_path / "state",
        clock=Clock(),
        operator_limits=OperatorLimits(max_delegations_total=2),
    )
    root = "delegation.01KXTREEROOTTREEROOTTREERO"
    parent = RuntimePolicy(remaining_depth=1, max_attempts=2)

    for index in range(2):
        reserved = store.reserve(
            spec(
                tmp_path,
                key=f"tree-{index}",
                delegation=f"delegation.01KXNODE{index:018d}",
                root=root,
                parent_session=f"session.{index:032d}",
                child_session=f"session.{index + 400:032d}",
            ),
            parent_policy=parent,
        )
        store.transition(reserved.attempt.attempt_id, AttemptState.FAILED, reason="done")

    with pytest.raises(PolicyViolationError, match="subtree"):
        store.reserve(
            spec(
                tmp_path,
                key="tree-overflow",
                delegation="delegation.01KXNODEOVERFLOWOVERFLOWX",
                root=root,
                parent_session="session." + "c" * 32,
                child_session="session." + "9" * 32,
            ),
            parent_policy=parent,
        )


def test_a_retry_does_not_consume_a_slot_in_the_tree_total(tmp_path: Path) -> None:
    """The ceiling counts delegations, not attempts: retrying one delegation
    must not exhaust the tree it belongs to."""

    from agent_commons.runtime import OperatorLimits

    store = AttemptStore(
        tmp_path / "state",
        clock=Clock(),
        operator_limits=OperatorLimits(max_delegations_total=1),
    )
    only = spec(tmp_path, key="solo", root="delegation.01KXAAAAAAAAAAAAAAAAAAAAAA")
    parent = only.parent_policy
    reserved = store.reserve(only, parent_policy=parent)
    store.transition(reserved.attempt.attempt_id, AttemptState.FAILED, reason="failed")
    again = store.reserve(only, parent_policy=parent, retry=True)
    assert again.attempt.number == 2


def test_a_deliberate_stop_is_not_recorded_as_a_broker_restart(tmp_path: Path) -> None:
    """Regression: the documented stop path told the operator to reconcile, and
    reconcile filed the result under broker_restart_ambiguous -- a reason that
    never happened, in the record an incident review reads first."""

    store = AttemptStore(tmp_path / "state", clock=Clock())
    reserved = store.reserve(
        spec(tmp_path), parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2)
    )
    store.transition(reserved.attempt.attempt_id, AttemptState.LAUNCHING, reason="launching")
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    store.transition(
        reserved.attempt.attempt_id, AttemptState.RUNNING, reason="running", pid=finished.pid
    )
    # The operator asked for the stop; the intent is recorded before the outcome.
    store.transition(
        reserved.attempt.attempt_id,
        AttemptState.CANCEL_REQUESTED,
        reason="operator_stop_requested",
    )

    reconciled = store.reconcile()
    assert [attempt.reason for attempt in reconciled] == ["operator_stop_requested"]


def test_an_ambiguous_restart_is_still_reported_as_one(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "state", clock=Clock())
    reserved = store.reserve(
        spec(tmp_path), parent_policy=RuntimePolicy(remaining_depth=1, max_attempts=2)
    )
    store.transition(reserved.attempt.attempt_id, AttemptState.LAUNCHING, reason="launching")
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    store.transition(
        reserved.attempt.attempt_id, AttemptState.RUNNING, reason="running", pid=finished.pid
    )
    reconciled = store.reconcile()
    assert [attempt.reason for attempt in reconciled] == ["broker_restart_ambiguous"]
