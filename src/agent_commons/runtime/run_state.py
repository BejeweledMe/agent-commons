"""Pure reducer for disposable run-observability state.

The reducer is deliberately free of clocks, environment, and storage so the same
fold runs in the writer, in a replay, and in a property test.  It reconstructs
run state from an ordered event stream; it is never a source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from agent_commons.errors import ValidationError

RUN_STATE_SCHEMA = "agent_commons.run_state.v1"


class RunEventKind(StrEnum):
    """Closed set of observability event kinds."""

    RUN_STARTED = "run.started"
    RUN_STATE = "run.state"
    RUN_FINISHED = "run.finished"
    WAVE_STARTED = "wave.started"
    WAVE_FINISHED = "wave.finished"
    NODE_STATE = "node.state"
    DELEGATION_PLANNED = "delegation.planned"
    DELEGATION_STATE = "delegation.state"
    LLM_TURN = "llm.turn"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    SPAN_START = "span.start"
    SPAN_END = "span.end"
    INPUT_NEEDED = "input.needed"
    INPUT_PROVIDED = "input.provided"
    GUARDRAIL_TRIPPED = "guardrail.tripped"
    MILESTONE = "milestone"
    ERROR = "error"


class NodeState(StrEnum):
    """Node lifecycle as rendered on the canvas."""

    IDLE = "idle"
    QUEUED = "queued"
    WORKING = "working"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    NEEDS_OPERATOR = "needs_operator"
    CANCELLED = "cancelled"


TERMINAL_NODE_STATES = frozenset(
    {NodeState.DONE, NodeState.FAILED, NodeState.NEEDS_OPERATOR, NodeState.CANCELLED}
)

#: Kinds whose high-frequency payload is dropped when a run is demoted to digest.
DIGEST_DROP_KINDS = frozenset(
    {
        RunEventKind.LLM_TURN,
        RunEventKind.TOOL_STARTED,
        RunEventKind.TOOL_FINISHED,
        RunEventKind.WAVE_STARTED,
        RunEventKind.WAVE_FINISHED,
        RunEventKind.SPAN_START,
        RunEventKind.SPAN_END,
    }
)

RUN_LEVEL_NODE_ID = "run"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(frozen=True, slots=True)
class NodeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_microusd: int = 0
    cost_is_estimated: bool = False
    provider_units_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_microusd": self.cost_microusd,
            "cost_is_estimated": self.cost_is_estimated,
            "provider_units_used": self.provider_units_used,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> NodeUsage:
        return cls(
            input_tokens=_int(value.get("input_tokens")),
            output_tokens=_int(value.get("output_tokens")),
            cache_read_tokens=_int(value.get("cache_read_tokens")),
            cache_write_tokens=_int(value.get("cache_write_tokens")),
            cost_microusd=_int(value.get("cost_microusd")),
            cost_is_estimated=bool(value.get("cost_is_estimated", False)),
            provider_units_used=_int(value.get("provider_units_used")),
        )


@dataclass(frozen=True, slots=True)
class NodeCounters:
    llm_turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "llm_turns": self.llm_turns,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> NodeCounters:
        return cls(
            llm_turns=_int(value.get("llm_turns")),
            tool_calls=_int(value.get("tool_calls")),
            tool_errors=_int(value.get("tool_errors")),
        )


@dataclass(frozen=True, slots=True)
class NodeState_:
    """Per-node projection.  Named with a trailing underscore to avoid clashing
    with the :class:`NodeState` enum while keeping the enum's plain name."""

    state: NodeState = NodeState.IDLE
    state_reason: str | None = None
    last_seq: int = 0
    usage: NodeUsage = field(default_factory=NodeUsage)
    counters: NodeCounters = field(default_factory=NodeCounters)
    last_error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "state_reason": self.state_reason,
            "last_seq": self.last_seq,
            "usage": self.usage.as_dict(),
            "counters": self.counters.as_dict(),
            "last_error": self.last_error,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> NodeState_:
        raw_state = value.get("state", NodeState.IDLE)
        try:
            node_state = NodeState(str(raw_state))
        except ValueError as exc:
            raise ValidationError(f"unknown node state {raw_state!r}") from exc
        last_error = value.get("last_error")
        return cls(
            state=node_state,
            state_reason=value.get("state_reason"),
            last_seq=_int(value.get("last_seq")),
            usage=NodeUsage.from_mapping(value.get("usage") or {}),
            counters=NodeCounters.from_mapping(value.get("counters") or {}),
            last_error=dict(last_error) if isinstance(last_error, Mapping) else None,
        )


@dataclass(frozen=True, slots=True)
class OpenSpan:
    node_id: str
    kind: str
    started_seq: int
    parent_span_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "started_seq": self.started_seq,
            "parent_span_id": self.parent_span_id,
        }


@dataclass(frozen=True, slots=True)
class RunState:
    """Folded run projection.  Immutable; every reduction returns a new value."""

    run_id: str
    upto_seq: int = 0
    state: str = "created"
    state_reason: str | None = None
    current_wave: int = 0
    planned_steps: int = 0
    started_ts: str | None = None
    finished_ts: str | None = None
    nodes: Mapping[str, NodeState_] = field(default_factory=dict)
    open_spans: Mapping[str, OpenSpan] = field(default_factory=dict)
    pending_inputs: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    guardrails: tuple[dict[str, Any], ...] = ()
    milestones: tuple[dict[str, Any], ...] = ()
    ignored_events: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": RUN_STATE_SCHEMA,
            "run_id": self.run_id,
            "upto_seq": self.upto_seq,
            "run": {
                "state": self.state,
                "state_reason": self.state_reason,
                "current_wave": self.current_wave,
                "planned_steps": self.planned_steps,
                "started_ts": self.started_ts,
                "finished_ts": self.finished_ts,
            },
            "nodes": {key: node.as_dict() for key, node in sorted(self.nodes.items())},
            "open_spans": {key: span.as_dict() for key, span in sorted(self.open_spans.items())},
            "pending_inputs": {
                key: dict(value) for key, value in sorted(self.pending_inputs.items())
            },
            "guardrails": [dict(entry) for entry in self.guardrails],
            "milestones": [dict(entry) for entry in self.milestones],
            "ignored_events": self.ignored_events,
        }


def initial_state(run_id: str) -> RunState:
    return RunState(run_id=run_id)


def state_from_dict(value: Mapping[str, Any]) -> RunState:
    """Rebuild a folded state from its serialized form."""

    if value.get("schema") != RUN_STATE_SCHEMA:
        raise ValidationError(f"unsupported run state schema {value.get('schema')!r}")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValidationError("run state requires a run_id")
    run = value.get("run") or {}
    spans = value.get("open_spans") or {}
    return RunState(
        run_id=run_id,
        upto_seq=_int(value.get("upto_seq")),
        state=str(run.get("state", "created")),
        state_reason=run.get("state_reason"),
        current_wave=_int(run.get("current_wave")),
        planned_steps=_int(run.get("planned_steps")),
        started_ts=run.get("started_ts"),
        finished_ts=run.get("finished_ts"),
        nodes={
            key: NodeState_.from_mapping(node) for key, node in (value.get("nodes") or {}).items()
        },
        open_spans={
            key: OpenSpan(
                node_id=str(span.get("node_id", "")),
                kind=str(span.get("kind", "")),
                started_seq=_int(span.get("started_seq")),
                parent_span_id=span.get("parent_span_id"),
            )
            for key, span in spans.items()
        },
        pending_inputs={
            key: dict(entry) for key, entry in (value.get("pending_inputs") or {}).items()
        },
        guardrails=tuple(dict(entry) for entry in (value.get("guardrails") or ())),
        milestones=tuple(dict(entry) for entry in (value.get("milestones") or ())),
        ignored_events=_int(value.get("ignored_events")),
    )


def _with_node(current: RunState, node_id: str, /, **changes: Any) -> Mapping[str, NodeState_]:
    nodes = dict(current.nodes)
    nodes[node_id] = replace(nodes.get(node_id, NodeState_()), **changes)
    return nodes


def _advance(current: RunState, seq: int, /, **changes: Any) -> RunState:
    return replace(current, upto_seq=seq, **changes)


def fold_event(state: RunState, event: Any) -> RunState:
    """Apply one stored event.  Total: an unknown kind is counted, never fatal.

    Replaying an event at or below ``upto_seq`` is a no-op, so redelivery across
    a reconnect cannot corrupt the projection.
    """

    seq = _int(getattr(event, "seq", 0))
    if seq <= state.upto_seq:
        return state
    raw_kind = str(getattr(event, "kind", ""))
    node_id = str(getattr(event, "node_id", RUN_LEVEL_NODE_ID))
    body = getattr(event, "body", None) or {}
    ts = getattr(event, "ts", None)
    try:
        kind = RunEventKind(raw_kind)
    except ValueError:
        return _advance(state, seq, ignored_events=state.ignored_events + 1)

    if kind is RunEventKind.RUN_STARTED:
        return _advance(state, seq, state="running", started_ts=ts)
    if kind is RunEventKind.RUN_STATE:
        return _advance(
            state, seq, state=str(body.get("state", state.state)), state_reason=body.get("reason")
        )
    if kind is RunEventKind.RUN_FINISHED:
        return _advance(
            state,
            seq,
            state=str(body.get("outcome", "completed")),
            finished_ts=ts,
        )
    if kind is RunEventKind.WAVE_STARTED:
        return _advance(
            state,
            seq,
            current_wave=_int(body.get("wave")),
            planned_steps=_int(body.get("planned_steps")),
        )
    if kind is RunEventKind.WAVE_FINISHED:
        return _advance(state, seq, planned_steps=0)
    if kind is RunEventKind.NODE_STATE:
        current = state.nodes.get(node_id, NodeState_())
        try:
            requested = NodeState(str(body.get("state", current.state)))
        except ValueError:
            return _advance(state, seq, ignored_events=state.ignored_events + 1)
        # A terminal node never rolls back to an in-flight state: a late or
        # duplicated event must not resurrect finished work on the canvas.
        if current.state in TERMINAL_NODE_STATES and requested not in TERMINAL_NODE_STATES:
            return _advance(state, seq)
        return _advance(
            state,
            seq,
            nodes=_with_node(
                state,
                node_id,
                state=requested,
                state_reason=body.get("reason"),
                last_seq=seq,
            ),
        )
    if kind is RunEventKind.DELEGATION_PLANNED:
        current = state.nodes.get(node_id, NodeState_())
        if current.state in TERMINAL_NODE_STATES:
            return _advance(state, seq)
        return _advance(
            state, seq, nodes=_with_node(state, node_id, state=NodeState.QUEUED, last_seq=seq)
        )
    if kind is RunEventKind.DELEGATION_STATE:
        return _advance(state, seq, nodes=_with_node(state, node_id, last_seq=seq))
    if kind is RunEventKind.LLM_TURN:
        current = state.nodes.get(node_id, NodeState_())
        usage = current.usage
        merged = NodeUsage(
            input_tokens=usage.input_tokens + _int(body.get("input_tokens")),
            output_tokens=usage.output_tokens + _int(body.get("output_tokens")),
            cache_read_tokens=usage.cache_read_tokens + _int(body.get("cache_read_tokens")),
            cache_write_tokens=usage.cache_write_tokens + _int(body.get("cache_write_tokens")),
            cost_microusd=usage.cost_microusd + _int(body.get("cost_microusd")),
            cost_is_estimated=usage.cost_is_estimated or bool(body.get("cost_is_estimated")),
            provider_units_used=usage.provider_units_used + _int(body.get("provider_units")),
        )
        counters = replace(current.counters, llm_turns=current.counters.llm_turns + 1)
        return _advance(
            state,
            seq,
            nodes=_with_node(state, node_id, usage=merged, counters=counters, last_seq=seq),
        )
    if kind is RunEventKind.TOOL_STARTED:
        current = state.nodes.get(node_id, NodeState_())
        counters = replace(current.counters, tool_calls=current.counters.tool_calls + 1)
        return _advance(
            state, seq, nodes=_with_node(state, node_id, counters=counters, last_seq=seq)
        )
    if kind is RunEventKind.TOOL_FINISHED:
        current = state.nodes.get(node_id, NodeState_())
        if body.get("ok") is False:
            counters = replace(current.counters, tool_errors=current.counters.tool_errors + 1)
            return _advance(
                state, seq, nodes=_with_node(state, node_id, counters=counters, last_seq=seq)
            )
        return _advance(state, seq, nodes=_with_node(state, node_id, last_seq=seq))
    if kind is RunEventKind.SPAN_START:
        span_id = getattr(event, "span_id", None)
        if not span_id:
            return _advance(state, seq, ignored_events=state.ignored_events + 1)
        spans = dict(state.open_spans)
        spans[str(span_id)] = OpenSpan(
            node_id=node_id,
            kind=str(body.get("kind", "")),
            started_seq=seq,
            parent_span_id=getattr(event, "parent_span_id", None),
        )
        return _advance(state, seq, open_spans=spans)
    if kind is RunEventKind.SPAN_END:
        span_id = getattr(event, "span_id", None)
        spans = dict(state.open_spans)
        spans.pop(str(span_id), None)
        return _advance(state, seq, open_spans=spans)
    if kind is RunEventKind.INPUT_NEEDED:
        operation_id = str(body.get("operation_id", f"op:{seq}"))
        pending = dict(state.pending_inputs)
        pending[operation_id] = {"node_id": node_id, "since_seq": seq}
        return _advance(
            state,
            seq,
            pending_inputs=pending,
            nodes=_with_node(state, node_id, state=NodeState.WAITING, last_seq=seq),
        )
    if kind is RunEventKind.INPUT_PROVIDED:
        pending = dict(state.pending_inputs)
        pending.pop(str(body.get("operation_id", "")), None)
        return _advance(state, seq, pending_inputs=pending)
    if kind is RunEventKind.GUARDRAIL_TRIPPED:
        entry = {"seq": seq, "guard": str(body.get("guard", "")), "action": body.get("action")}
        return _advance(state, seq, guardrails=state.guardrails + (entry,))
    if kind is RunEventKind.MILESTONE:
        entry = {"seq": seq, "event_id": body.get("event_id")}
        return _advance(state, seq, milestones=state.milestones + (entry,))
    if kind is RunEventKind.ERROR:
        error = {"seq": seq, "code": body.get("diagnostic_code") or body.get("code")}
        return _advance(
            state, seq, nodes=_with_node(state, node_id, last_error=error, last_seq=seq)
        )
    return _advance(state, seq, ignored_events=state.ignored_events + 1)


def fold_events(state: RunState, events: Iterable[Any]) -> RunState:
    for event in events:
        state = fold_event(state, event)
    return state
