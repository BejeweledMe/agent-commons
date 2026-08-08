"""The run-state reducer must be total, idempotent, and replay-deterministic."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

import pytest

from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.errors import ValidationError
from agent_commons.runtime.run_state import (
    NodeState,
    RunEventKind,
    fold_event,
    fold_events,
    initial_state,
    state_from_dict,
)

_KINDS = [
    RunEventKind.NODE_STATE,
    RunEventKind.LLM_TURN,
    RunEventKind.TOOL_STARTED,
    RunEventKind.TOOL_FINISHED,
    RunEventKind.SPAN_START,
    RunEventKind.SPAN_END,
    RunEventKind.WAVE_STARTED,
    RunEventKind.MILESTONE,
    RunEventKind.GUARDRAIL_TRIPPED,
    RunEventKind.INPUT_NEEDED,
    RunEventKind.ERROR,
]


@dataclass(frozen=True)
class FakeEvent:
    seq: int
    kind: str
    node_id: str = "agent.backend"
    ts: str = "2026-08-09T00:00:00Z"
    body: dict[str, Any] | None = None
    span_id: str | None = None
    parent_span_id: str | None = None


def _stream(count: int, *, seed: int) -> list[FakeEvent]:
    rng = random.Random(seed)
    events: list[FakeEvent] = []
    for index in range(1, count + 1):
        kind = rng.choice(_KINDS)
        body: dict[str, Any] = {}
        span_id = None
        if kind is RunEventKind.NODE_STATE:
            body = {"state": rng.choice(["queued", "working", "waiting", "done", "failed"])}
        elif kind is RunEventKind.LLM_TURN:
            body = {"input_tokens": rng.randint(1, 100), "output_tokens": rng.randint(1, 50)}
        elif kind is RunEventKind.TOOL_FINISHED:
            body = {"ok": rng.choice([True, False])}
        elif kind in {RunEventKind.SPAN_START, RunEventKind.SPAN_END}:
            span_id = f"sp{rng.randint(1, 5)}"
            body = {"kind": "tool"}
        elif kind is RunEventKind.WAVE_STARTED:
            body = {"wave": rng.randint(1, 4), "planned_steps": rng.randint(1, 3)}
        elif kind is RunEventKind.MILESTONE:
            body = {"event_id": f"evt.{index:026d}"}
        elif kind is RunEventKind.GUARDRAIL_TRIPPED:
            body = {"guard": "cost_ceiling", "action": "halt_run"}
        elif kind is RunEventKind.INPUT_NEEDED:
            body = {"operation_id": f"op{rng.randint(1, 3)}"}
        elif kind is RunEventKind.ERROR:
            body = {"diagnostic_code": "mcp_handshake_failed"}
        events.append(
            FakeEvent(
                seq=index,
                kind=str(kind),
                node_id=rng.choice(["agent.backend", "agent.reviewer"]),
                body=body,
                span_id=span_id,
            )
        )
    return events


@pytest.mark.parametrize("seed", [1, 7, 20260809])
def test_snapshot_plus_tail_equals_full_fold_at_every_cut(seed: int) -> None:
    """fold(events) == fold(snapshot(k), tail) for every k, byte for byte."""

    events = _stream(120, seed=seed)
    complete = fold_events(initial_state("run.1"), events)
    for cut in range(0, len(events) + 1):
        snapshot = fold_events(initial_state("run.1"), events[:cut])
        # Round-trip the snapshot through its serialized form, exactly as the
        # store does, so serialization loss would surface here.
        restored = state_from_dict(json.loads(canonical_json_bytes(snapshot.as_dict())))
        resumed = fold_events(restored, events[cut:])
        assert canonical_json_bytes(resumed.as_dict()) == canonical_json_bytes(
            complete.as_dict()
        ), f"replay diverged at cut {cut}"


def test_replaying_an_already_folded_event_is_a_no_op() -> None:
    events = _stream(20, seed=3)
    state = fold_events(initial_state("run.1"), events)
    redelivered = fold_events(state, events)
    assert canonical_json_bytes(redelivered.as_dict()) == canonical_json_bytes(state.as_dict())


def test_terminal_node_state_never_rolls_back() -> None:
    state = initial_state("run.1")
    state = fold_event(state, FakeEvent(seq=1, kind="node.state", body={"state": "failed"}))
    state = fold_event(state, FakeEvent(seq=2, kind="node.state", body={"state": "working"}))
    assert state.nodes["agent.backend"].state is NodeState.FAILED
    assert state.upto_seq == 2


def test_terminal_node_state_may_move_to_another_terminal_state() -> None:
    state = initial_state("run.1")
    state = fold_event(state, FakeEvent(seq=1, kind="node.state", body={"state": "failed"}))
    state = fold_event(state, FakeEvent(seq=2, kind="node.state", body={"state": "needs_operator"}))
    assert state.nodes["agent.backend"].state is NodeState.NEEDS_OPERATOR


def test_unknown_kind_is_counted_and_never_fatal() -> None:
    state = fold_event(initial_state("run.1"), FakeEvent(seq=1, kind="future.kind.v9"))
    assert state.ignored_events == 1
    assert state.upto_seq == 1


def test_unknown_node_state_value_is_counted_not_applied() -> None:
    state = fold_event(
        initial_state("run.1"), FakeEvent(seq=1, kind="node.state", body={"state": "teleporting"})
    )
    assert state.ignored_events == 1
    assert "agent.backend" not in state.nodes


def test_usage_accumulates_per_node() -> None:
    state = initial_state("run.1")
    for index, tokens in enumerate((10, 20, 30), start=1):
        state = fold_event(
            state,
            FakeEvent(
                seq=index,
                kind="llm.turn",
                body={"input_tokens": tokens, "output_tokens": 1, "cost_microusd": 5},
            ),
        )
    node = state.nodes["agent.backend"]
    assert node.usage.input_tokens == 60
    assert node.usage.cost_microusd == 15
    assert node.counters.llm_turns == 3


def test_spans_open_and_close() -> None:
    state = initial_state("run.1")
    state = fold_event(
        state, FakeEvent(seq=1, kind="span.start", span_id="sp1", body={"kind": "tool"})
    )
    assert "sp1" in state.open_spans
    state = fold_event(state, FakeEvent(seq=2, kind="span.end", span_id="sp1"))
    assert state.open_spans == {}


def test_pending_input_clears_on_reply() -> None:
    state = initial_state("run.1")
    state = fold_event(state, FakeEvent(seq=1, kind="input.needed", body={"operation_id": "op1"}))
    assert state.nodes["agent.backend"].state is NodeState.WAITING
    assert "op1" in state.pending_inputs
    state = fold_event(state, FakeEvent(seq=2, kind="input.provided", body={"operation_id": "op1"}))
    assert state.pending_inputs == {}


def test_state_from_dict_rejects_a_foreign_schema() -> None:
    with pytest.raises(ValidationError, match="schema"):
        state_from_dict({"schema": "something.else.v1", "run_id": "run.1"})


def test_run_level_events_update_run_fields() -> None:
    """``run.state`` carries a ``state`` key; the reducer must not confuse it
    with its own accumulator argument."""

    state = initial_state("run.1")
    state = fold_event(state, FakeEvent(seq=1, kind="run.started", node_id="run"))
    assert state.state == "running"
    state = fold_event(
        state,
        FakeEvent(seq=2, kind="run.state", node_id="run", body={"state": "stopping"}),
    )
    assert state.state == "stopping"
    state = fold_event(
        state,
        FakeEvent(seq=3, kind="run.finished", node_id="run", body={"outcome": "completed"}),
    )
    assert state.state == "completed"
    assert state.finished_ts is not None
