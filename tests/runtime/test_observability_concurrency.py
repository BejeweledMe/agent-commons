"""Regressions found by review: concurrent reads and size-cap enforcement."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent_commons.errors import ValidationError
from agent_commons.runtime.observability import RunEventEnvelope, RunEventStore
from agent_commons.runtime.policy import RunRetentionLimits
from agent_commons.runtime.run_state import MAX_STATE_ENTRIES, RunEventKind
from tests.runtime.test_observability_store import (
    WORKSPACE,
    Clock,
    make_run,
    node_event,
    open_store,
    paths_for,
)


def test_reads_and_writes_may_run_concurrently(tmp_path: Path) -> None:
    """Regression: readers shared the write connection, so a concurrent reader
    saw a reset cursor and crashed with InterfaceError or a None row."""

    with open_store(tmp_path) as store:
        run_id = make_run(store)
        failures: list[BaseException] = []
        stop = threading.Event()

        def write() -> None:
            try:
                for _ in range(120):
                    store.append(node_event(run_id))
            except BaseException as exc:  # noqa: BLE001 - recorded for the assert
                failures.append(exc)
            finally:
                stop.set()

        def read() -> None:
            try:
                while not stop.is_set():
                    store.head_seq(run_id)
                    store.get_run(run_id)
                    store.list_runs()
                    store.read_events(run_id, limit=20)
                    store.retained_bytes()
            except BaseException as exc:  # noqa: BLE001 - recorded for the assert
                failures.append(exc)

        threads = [threading.Thread(target=write)] + [
            threading.Thread(target=read) for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert failures == [], f"concurrent access failed: {failures[:3]}"
        assert store.head_seq(run_id) == 120


def test_a_reader_never_sees_a_rolled_back_batch(tmp_path: Path) -> None:
    """Regression: a shared connection exposed uncommitted rows, so the UI could
    render events that a rollback then removed."""

    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(node_event(run_id))
        with pytest.raises(ValidationError):
            store.append_many([node_event(run_id), node_event("run.missing"), node_event(run_id)])
        assert store.head_seq(run_id) == 1
        assert len(store.read_events(run_id, limit=100)) == 1


def test_folded_state_stops_growing_without_bound(tmp_path: Path) -> None:
    """Regression: guardrail and milestone history grew forever and was copied
    into every snapshot, so a digest was larger than the stream it replaced."""

    with open_store(tmp_path, snapshot_interval=10_000) as store:
        run_id = make_run(store)
        for index in range(MAX_STATE_ENTRIES * 3):
            store.append(
                RunEventEnvelope(
                    run_id=run_id,
                    node_id="run",
                    kind=RunEventKind.GUARDRAIL_TRIPPED,
                    body={"guard": "cost_ceiling", "action": "halt_run", "index": index},
                )
            )
        state = store.replay_state(run_id)
    assert len(state.guardrails) == MAX_STATE_ENTRIES
    # The newest trip must survive; the oldest is the one that goes.
    assert state.guardrails[-1]["seq"] == MAX_STATE_ENTRIES * 3


def test_digest_shrinks_a_run_rather_than_inflating_it(tmp_path: Path) -> None:
    clock = Clock()
    store = RunEventStore(
        paths_for(tmp_path),
        retention=RunRetentionLimits(full_run_limit=1),
        clock=clock,
        snapshot_interval=10_000,
    )
    with store:
        for name in ("run.old", "run.new"):
            store.create_run(
                run_id=name,
                workspace_id=WORKSPACE,
                org_ref="art.org",
                org_revision="evt.01KXG0E9R9C1JAEZYMH12FQN4Z",
                root_target="team.eng",
            )
            for index in range(300):
                store.append(
                    RunEventEnvelope(
                        run_id=name,
                        node_id="agent.backend",
                        kind=RunEventKind.TOOL_STARTED,
                        body={"tool": "get_pull_request", "call_id": f"tc{index}"},
                    )
                )
            before = store.retained_bytes()
            store.set_run_state(name, "completed")
            clock.advance(60)
        after = store.retained_bytes()
        assert store.get_run("run.old").retention_tier == "digest"
    # Two runs, one digested: the total must not exceed what one full run cost.
    assert after < before * 2


def test_size_cap_keeps_working_past_a_victim_that_did_not_shrink(tmp_path: Path) -> None:
    """Regression: the loop stopped at the first victim that failed to reduce
    the store, leaving the ceiling unenforced for every run behind it."""

    clock = Clock()
    cap = 40_000
    store = RunEventStore(
        paths_for(tmp_path),
        retention=RunRetentionLimits(full_run_limit=100, max_total_bytes=cap),
        clock=clock,
        snapshot_interval=10_000,
    )
    with store:
        for index in range(6):
            run_id = f"run.{index}"
            store.create_run(
                run_id=run_id,
                workspace_id=WORKSPACE,
                org_ref="art.org",
                org_revision="evt.01KXG0E9R9C1JAEZYMH12FQN4Z",
                root_target="team.eng",
            )
            for step in range(150):
                store.append(
                    RunEventEnvelope(
                        run_id=run_id,
                        node_id="agent.backend",
                        kind=RunEventKind.TOOL_STARTED,
                        body={"tool": "t", "call_id": f"c{step}"},
                    )
                )
            store.set_run_state(run_id, "completed")
            clock.advance(60)
        runs = store.list_runs()
        assert runs, "the size cap deleted every run"
        assert store.retained_bytes() <= cap


def test_the_newest_finished_run_is_never_a_size_cap_victim(tmp_path: Path) -> None:
    """A store that answers "what just happened" with nothing is worse than one
    slightly over its ceiling."""

    clock = Clock()
    store = RunEventStore(
        paths_for(tmp_path),
        retention=RunRetentionLimits(full_run_limit=1, max_total_bytes=1),
        clock=clock,
        snapshot_interval=10_000,
    )
    with store:
        for index in range(4):
            run_id = f"run.{index}"
            store.create_run(
                run_id=run_id,
                workspace_id=WORKSPACE,
                org_ref="art.org",
                org_revision="evt.01KXG0E9R9C1JAEZYMH12FQN4Z",
                root_target="team.eng",
            )
            for _ in range(40):
                store.append(node_event(run_id))
            store.set_run_state(run_id, "completed")
            clock.advance(60)
        surviving = {run.run_id for run in store.list_runs()}
    assert "run.3" in surviving, "the most recent finished run was deleted"


def test_active_run_snapshots_do_not_accumulate(tmp_path: Path) -> None:
    """Regression: an active run is excluded from retention, so its periodic
    snapshots were never pruned and grew quadratically."""

    with open_store(tmp_path, snapshot_interval=50) as store:
        run_id = make_run(store)
        for _ in range(500):
            store.append(node_event(run_id))
        rows = store.connection.execute(
            "SELECT COUNT(*) AS total FROM run_snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert int(rows["total"]) == 1


def test_using_a_closed_store_reports_a_typed_error(tmp_path: Path) -> None:
    from agent_commons.errors import IntegrityError

    store = open_store(tmp_path)
    make_run(store)
    store.close()
    with pytest.raises(IntegrityError, match="closed"):
        store.head_seq("run.1")
