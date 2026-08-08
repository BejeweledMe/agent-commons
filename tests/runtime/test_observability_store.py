"""The run-observability store is disposable, single-writer, and never authoritative."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.runtime.observability import (
    RunEventEnvelope,
    RunEventStore,
    iter_export_records,
)
from agent_commons.runtime.policy import PolicyViolationError, RunRetentionLimits
from agent_commons.runtime.run_state import RunEventKind

WORKSPACE = "workspace.01KXG0E9R9C1JAEZYMH12FQN4Z"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def paths_for(tmp_path: Path) -> CommonsPaths:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return CommonsPaths.for_workspace(repo, state_root=tmp_path / "state", workspace_id=WORKSPACE)


def open_store(tmp_path: Path, **kwargs: object) -> RunEventStore:
    return RunEventStore(paths_for(tmp_path), **kwargs)  # type: ignore[arg-type]


def make_run(store: RunEventStore, run_id: str = "run.1") -> str:
    store.create_run(
        run_id=run_id,
        workspace_id=WORKSPACE,
        org_ref="art.org",
        org_revision="evt.01KXG0E9R9C1JAEZYMH12FQN4Z",
        root_target="team.eng",
    )
    return run_id


def node_event(run_id: str, state: str = "working") -> RunEventEnvelope:
    return RunEventEnvelope(
        run_id=run_id,
        node_id="agent.backend",
        kind=RunEventKind.NODE_STATE,
        body={"state": state},
    )


def test_store_uses_wal_and_incremental_auto_vacuum(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        journal = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        auto_vacuum = int(store.connection.execute("PRAGMA auto_vacuum").fetchone()[0])
    assert str(journal).lower() == "wal"
    assert auto_vacuum == 2  # INCREMENTAL


def test_store_rejects_a_foreign_workspace(tmp_path: Path) -> None:
    with open_store(tmp_path):
        pass
    foreign = CommonsPaths.for_workspace(
        tmp_path / "repo",
        state_root=tmp_path / "state",
        workspace_id="workspace.01KXG0E9R9C1JAEZYMH12FQN4A",
    )
    with pytest.raises(IntegrityError, match="different workspace"):
        RunEventStore(foreign)


def test_store_rejects_an_unknown_schema_version(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        database = store.database_path
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.commit()
    connection.close()
    with pytest.raises(IntegrityError, match="disposable"):
        open_store(tmp_path)


def test_a_second_writer_is_refused_while_the_first_holds_the_lock(tmp_path: Path) -> None:
    with open_store(tmp_path):
        with pytest.raises(IntegrityError, match="writer lock"):
            open_store(tmp_path)


def test_a_reader_may_open_alongside_a_writer(tmp_path: Path) -> None:
    with open_store(tmp_path) as writer:
        run_id = make_run(writer)
        writer.append(node_event(run_id))
        with open_store(tmp_path, writer=False) as reader:
            assert reader.head_seq(run_id) == 1


def test_a_reader_cannot_write(tmp_path: Path) -> None:
    with open_store(tmp_path) as writer:
        make_run(writer)
    with open_store(tmp_path, writer=False) as reader:
        with pytest.raises(IntegrityError, match="read-only"):
            reader.append(node_event("run.1"))


def test_sequence_numbers_are_dense_and_monotonic_across_threads(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        assigned: list[int] = []
        guard = threading.Lock()

        def produce() -> None:
            for _ in range(25):
                seq = store.append(node_event(run_id))
                with guard:
                    assigned.append(seq)

        workers = [threading.Thread(target=produce) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert sorted(assigned) == list(range(1, 201))
        assert store.head_seq(run_id) == 200


def test_append_to_an_unknown_run_is_rejected(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        with pytest.raises(ValidationError, match="unknown run"):
            store.append(node_event("run.missing"))


def test_span_projection_follows_span_events(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(
            RunEventEnvelope(
                run_id=run_id,
                node_id="agent.backend",
                kind=RunEventKind.SPAN_START,
                span_id="sp1",
                body={"kind": "tool", "attrs": {"server": "github"}},
            )
        )
        assert len(store.read_spans(run_id, open_only=True)) == 1
        store.append(
            RunEventEnvelope(
                run_id=run_id,
                node_id="agent.backend",
                kind=RunEventKind.SPAN_END,
                span_id="sp1",
            )
        )
        spans = store.read_spans(run_id)
        assert spans[0].ended_seq == 2
        assert store.read_spans(run_id, open_only=True) == []


def test_snapshots_are_written_on_the_interval(tmp_path: Path) -> None:
    with open_store(tmp_path, snapshot_interval=10) as store:
        run_id = make_run(store)
        for _ in range(25):
            store.append(node_event(run_id))
        snapshot = store.latest_snapshot(run_id)
        assert snapshot is not None
        assert snapshot.upto_seq == 20


def test_replay_state_matches_a_full_fold(tmp_path: Path) -> None:
    with open_store(tmp_path, snapshot_interval=7) as store:
        run_id = make_run(store)
        for index in range(40):
            store.append(
                RunEventEnvelope(
                    run_id=run_id,
                    node_id="agent.backend",
                    kind=RunEventKind.LLM_TURN,
                    body={"input_tokens": index, "output_tokens": 1},
                )
            )
        replayed = store.replay_state(run_id)
    assert replayed.upto_seq == 40
    assert replayed.nodes["agent.backend"].usage.input_tokens == sum(range(40))
    assert replayed.nodes["agent.backend"].counters.llm_turns == 40


def test_can_replay_from_reports_full_and_digest_runs_differently(tmp_path: Path) -> None:
    with open_store(
        tmp_path, retention=RunRetentionLimits(full_run_limit=1), snapshot_interval=5
    ) as store:
        first = make_run(store, "run.1")
        for _ in range(10):
            store.append(node_event(first))
        store.set_run_state(first, "completed")
        assert store.can_replay_from(first, 3) is True

        second = make_run(store, "run.2")
        store.append(node_event(second))
        store.set_run_state(second, "completed")

        # run.1 is now beyond full_run_limit and has been demoted.
        assert store.get_run(first).retention_tier == "digest"
        assert store.can_replay_from(first, 3) is False
        assert store.can_replay_from(first, store.head_seq(first)) is False


def test_read_events_filters_by_kind_and_node(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(node_event(run_id))
        store.append(
            RunEventEnvelope(
                run_id=run_id,
                node_id="agent.reviewer",
                kind=RunEventKind.TOOL_STARTED,
                body={"tool": "get_pull_request", "args_sha256": "0" * 64},
            )
        )
        assert len(store.read_events(run_id, kind="tool.started")) == 1
        assert len(store.read_events(run_id, node_id="agent.reviewer")) == 1
        assert len(store.read_events(run_id, after_seq=1)) == 1


def test_layout_round_trips_without_touching_runs(tmp_path: Path) -> None:
    from agent_commons.runtime.observability import LayoutEntry

    with open_store(tmp_path) as store:
        store.put_layout("art.org", {"agent.backend": LayoutEntry(x=10.0, y=20.0)})
        layout = store.get_layout("art.org")
    assert layout["agent.backend"].x == 10.0


def test_export_round_trips_and_detects_truncation(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        for _ in range(5):
            store.append(node_event(run_id))
        target = tmp_path / "out" / "run.jsonl"
        result = store.export_run(run_id, target)
    assert result.events == 5
    records = list(iter_export_records(target))
    assert records[0]["schema"] == "agent_commons.run_export.v1"
    assert sum(1 for entry in records if entry.get("record") == "event") == 5

    lines = target.read_text("utf-8").splitlines()
    truncated = tmp_path / "truncated.jsonl"
    truncated.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="truncated"):
        list(iter_export_records(truncated))


def test_export_detects_an_altered_record(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(node_event(run_id))
        target = tmp_path / "run.jsonl"
        store.export_run(run_id, target)
    lines = target.read_text("utf-8").splitlines()
    lines[1] = lines[1].replace("working", "faked!")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="digest mismatch"):
        list(iter_export_records(target))


def test_export_of_an_unknown_run_is_rejected(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        with pytest.raises(ValidationError, match="unknown run"):
            store.export_run("run.missing", tmp_path / "out.jsonl")


def test_store_is_disposable(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(node_event(run_id))
        database = store.database_path
    database.unlink()
    with open_store(tmp_path) as rebuilt:
        assert rebuilt.list_runs() == []


def test_set_run_state_rejects_an_unsupported_state(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        with pytest.raises(ValidationError, match="unsupported run state"):
            store.set_run_state(run_id, "vibing")


def test_run_state_transition_is_recorded_as_an_event(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.set_run_state(run_id, "running", reason="operator_started")
        events = store.read_events(run_id, kind="run.state")
        assert events[0].body["state"] == "running"
        assert store.replay_state(run_id).state == "running"


def test_retention_limits_reject_unknown_keys() -> None:
    with pytest.raises(PolicyViolationError, match="unsupported fields"):
        RunRetentionLimits.from_mapping({"full_run_limit": 5, "nope": 1})


def test_retention_limits_round_trip() -> None:
    limits = RunRetentionLimits(full_run_limit=3, digest_age_days=7, max_total_bytes=1024)
    assert RunRetentionLimits.from_mapping(limits.as_dict()) == limits


def test_event_payload_is_canonical_json(tmp_path: Path) -> None:
    with open_store(tmp_path) as store:
        run_id = make_run(store)
        store.append(
            RunEventEnvelope(
                run_id=run_id,
                node_id="agent.backend",
                kind=RunEventKind.NODE_STATE,
                body={"state": "working"},
                trace_id="a" * 32,
            )
        )
        stored = store.read_events(run_id)[0]
    assert stored.payload["trace_id"] == "a" * 32
    assert canonical_json_bytes(stored.payload)
