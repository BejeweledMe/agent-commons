"""Retention prunes finished runs only, and never the ones needed for forensics."""

from __future__ import annotations

from pathlib import Path

from agent_commons.runtime.observability import RunEventEnvelope, RunEventStore
from agent_commons.runtime.policy import RunRetentionLimits
from agent_commons.runtime.run_state import RunEventKind
from tests.runtime.test_observability_store import (
    WORKSPACE,
    Clock,
    node_event,
    paths_for,
)


def open_store(
    tmp_path: Path, *, retention: RunRetentionLimits, clock: Clock, snapshot_interval: int = 1000
) -> RunEventStore:
    return RunEventStore(
        paths_for(tmp_path),
        retention=retention,
        clock=clock,
        snapshot_interval=snapshot_interval,
    )


def seed_run(
    store: RunEventStore,
    run_id: str,
    *,
    events: int = 5,
    final_state: str | None = "completed",
) -> None:
    store.create_run(
        run_id=run_id,
        workspace_id=WORKSPACE,
        org_ref="art.org",
        org_revision="evt.01KXG0E9R9C1JAEZYMH12FQN4Z",
        root_target="team.eng",
    )
    for index in range(events):
        store.append(
            RunEventEnvelope(
                run_id=run_id,
                node_id="agent.backend",
                kind=RunEventKind.LLM_TURN,
                body={"input_tokens": index, "output_tokens": 1},
            )
        )
    store.append(node_event(run_id, "done"))
    if final_state is not None:
        store.set_run_state(run_id, final_state)


def test_needs_operator_runs_survive_every_retention_trigger(tmp_path: Path) -> None:
    """The single most important retention property: a run that stopped in an
    ambiguous state is exactly the one an operator will need, so no threshold
    may reach it."""

    clock = Clock()
    retention = RunRetentionLimits(full_run_limit=1, digest_age_days=1, max_total_bytes=4096)
    with open_store(tmp_path, retention=retention, clock=clock) as store:
        seed_run(store, "run.stuck", events=20, final_state=None)
        store.set_run_state("run.stuck", "needs_operator")
        before_events = len(store.read_events("run.stuck", limit=10_000))
        before_head = store.head_seq("run.stuck")

        # Blow past every threshold: many newer runs, and a lot of elapsed time.
        for index in range(5):
            seed_run(store, f"run.other{index}", events=20)
        clock.advance(90 * 86_400)
        result = store.sweep(reason="test")

        assert "run.stuck" not in result.purged
        assert "run.stuck" not in result.digested
        survivor = store.get_run("run.stuck")
        assert survivor is not None
        assert survivor.retention_tier == "full"
        assert survivor.state == "needs_operator"
        assert len(store.read_events("run.stuck", limit=10_000)) == before_events
        assert store.head_seq("run.stuck") == before_head


def test_active_runs_survive_every_retention_trigger(tmp_path: Path) -> None:
    clock = Clock()
    retention = RunRetentionLimits(full_run_limit=1, digest_age_days=1, max_total_bytes=4096)
    with open_store(tmp_path, retention=retention, clock=clock) as store:
        for state in ("created", "running", "stopping"):
            run_id = f"run.{state}"
            seed_run(store, run_id, events=10, final_state=None)
            if state != "created":
                store.set_run_state(run_id, state)
        for index in range(4):
            seed_run(store, f"run.done{index}", events=10)
        clock.advance(90 * 86_400)
        store.sweep(reason="test")
        for state in ("created", "running", "stopping"):
            run = store.get_run(f"run.{state}")
            assert run is not None, f"{state} run was pruned"
            assert run.retention_tier == "full"


def test_full_run_limit_demotes_the_oldest_finished_runs(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(tmp_path, retention=RunRetentionLimits(full_run_limit=3), clock=clock) as store:
        for index in range(6):
            seed_run(store, f"run.{index}", events=4)
            clock.advance(60)
        tiers = {run.run_id: run.retention_tier for run in store.list_runs()}
    assert [tiers[f"run.{index}"] for index in range(6)] == [
        "digest",
        "digest",
        "digest",
        "full",
        "full",
        "full",
    ]


def test_digest_keeps_milestones_and_drops_the_high_frequency_stream(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(tmp_path, retention=RunRetentionLimits(full_run_limit=1), clock=clock) as store:
        seed_run(store, "run.old", events=30)
        clock.advance(60)
        seed_run(store, "run.new", events=4)

        assert store.get_run("run.old").retention_tier == "digest"
        kinds = {event.kind for event in store.read_events("run.old", limit=10_000)}
        assert "llm.turn" not in kinds
        assert "node.state" in kinds
        assert "run.state" in kinds
        assert store.read_spans("run.old") == []


def test_digest_materializes_a_terminal_snapshot_before_dropping_events(
    tmp_path: Path,
) -> None:
    """Without materialising the snapshot first, everything after the last
    periodic snapshot would be lost rather than summarised."""

    clock = Clock()
    with open_store(
        tmp_path,
        retention=RunRetentionLimits(full_run_limit=1),
        clock=clock,
        snapshot_interval=10_000,  # no periodic snapshot will ever fire
    ) as store:
        seed_run(store, "run.old", events=30)
        before = store.replay_state("run.old")
        # No periodic snapshot has fired, so the digest step is the only thing
        # that can preserve this state.
        assert store.latest_snapshot("run.old") is None

        clock.advance(60)
        seed_run(store, "run.new", events=2)

        assert store.get_run("run.old").retention_tier == "digest"
        assert store.latest_snapshot("run.old") is not None
        after = store.replay_state("run.old")
    assert after.upto_seq == before.upto_seq
    assert (
        after.nodes["agent.backend"].usage.input_tokens
        == before.nodes["agent.backend"].usage.input_tokens
    )


def test_age_purges_a_run_outright_even_if_it_never_became_digest(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(
        tmp_path,
        retention=RunRetentionLimits(full_run_limit=100, digest_age_days=30),
        clock=clock,
    ) as store:
        seed_run(store, "run.ancient", events=4)
        assert store.get_run("run.ancient").retention_tier == "full"
        clock.advance(31 * 86_400)
        result = store.sweep(reason="test")
        assert "run.ancient" in result.purged
        assert store.get_run("run.ancient") is None


def test_purge_removes_every_trace_of_the_run(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(
        tmp_path, retention=RunRetentionLimits(digest_age_days=1), clock=clock
    ) as store:
        seed_run(store, "run.gone", events=6)
        clock.advance(2 * 86_400)
        store.sweep(reason="test")
        assert store.get_run("run.gone") is None
        assert store.read_events("run.gone", limit=100) == []
        assert store.read_spans("run.gone") == []
        assert store.latest_snapshot("run.gone") is None


def test_purge_leaves_canvas_layout_alone(tmp_path: Path) -> None:
    from agent_commons.runtime.observability import LayoutEntry

    clock = Clock()
    with open_store(
        tmp_path, retention=RunRetentionLimits(digest_age_days=1), clock=clock
    ) as store:
        store.put_layout("art.org", {"agent.backend": LayoutEntry(x=1.0, y=2.0)})
        seed_run(store, "run.gone", events=4)
        clock.advance(2 * 86_400)
        store.sweep(reason="test")
        assert store.get_run("run.gone") is None
        assert store.get_layout("art.org")["agent.backend"].x == 1.0


def test_sweep_is_idempotent(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(tmp_path, retention=RunRetentionLimits(full_run_limit=2), clock=clock) as store:
        for index in range(5):
            seed_run(store, f"run.{index}", events=4)
            clock.advance(60)
        first = store.sweep(reason="test")
        second = store.sweep(reason="test")
    assert second.digested == ()
    assert second.purged == ()
    assert first.reason == "test"


def test_sweep_runs_when_the_store_is_reopened(tmp_path: Path) -> None:
    clock = Clock()
    retention = RunRetentionLimits(digest_age_days=1)
    with open_store(tmp_path, retention=retention, clock=clock) as store:
        seed_run(store, "run.old", events=4)
    clock.advance(5 * 86_400)
    with open_store(tmp_path, retention=retention, clock=clock) as reopened:
        assert reopened.get_run("run.old") is None


def test_size_cap_bounds_the_store_without_deleting_everything(tmp_path: Path) -> None:
    """Regression: driving the size loop off the physical file size never made
    progress after a delete, so the loop consumed every run in the store."""

    clock = Clock()
    cap = 32_768
    with open_store(
        tmp_path,
        retention=RunRetentionLimits(full_run_limit=100, max_total_bytes=cap),
        clock=clock,
    ) as store:
        for index in range(8):
            seed_run(store, f"run.{index}", events=200)
            clock.advance(60)
        runs = store.list_runs()
        tiers = [run.retention_tier for run in runs]

        assert store.retained_bytes() <= cap
        assert runs, "the size cap deleted every run"
        # Demotion is preferred over deletion: the newest run keeps its stream.
        assert "full" in tiers
        assert "digest" in tiers
        newest = max(runs, key=lambda run: run.created_at)
        assert newest.retention_tier == "full"
        # A digested run still answers "what happened" from its snapshot.
        digested = next(run for run in runs if run.retention_tier == "digest")
        assert store.replay_state(digested.run_id).upto_seq == digested.head_seq


def test_size_cap_sweep_is_stable_once_under_the_cap(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(
        tmp_path,
        retention=RunRetentionLimits(full_run_limit=100, max_total_bytes=32_768),
        clock=clock,
    ) as store:
        for index in range(6):
            seed_run(store, f"run.{index}", events=150)
            clock.advance(60)
        surviving = {run.run_id for run in store.list_runs()}
        result = store.sweep(reason="test")
    assert result.digested == ()
    assert result.purged == ()
    assert surviving


def test_finishing_a_run_triggers_a_sweep(tmp_path: Path) -> None:
    clock = Clock()
    with open_store(tmp_path, retention=RunRetentionLimits(full_run_limit=1), clock=clock) as store:
        seed_run(store, "run.first", events=4)
        clock.advance(60)
        assert store.get_run("run.first").retention_tier == "full"
        seed_run(store, "run.second", events=4)
        # The second run's terminal transition is what demoted the first.
        assert store.get_run("run.first").retention_tier == "digest"
