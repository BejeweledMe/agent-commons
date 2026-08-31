"""Bounded, non-predictive read models for task readiness and execution order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice

from agent_commons.domain.work_state import EvidenceState, FreshnessState, NextAction, RunPhase

MAX_PLAN_TASKS = 512
MAX_PLAN_EDGES = 4_096
MAX_FOCUS_TASKS = 64
MAX_PLAN_GAPS = 24
MAX_TASK_GAPS = 16
MAX_IDENTIFIER_BYTES = 256

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class PlanState(StrEnum):
    """Whether the derived plan is safe to use as an operator aid."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


class ReadinessState(StrEnum):
    """Task posture derived from canonical lifecycle and current run evidence."""

    READY = "ready"
    BLOCKED = "blocked"
    TERMINAL_DEPENDENCY_FAILURE = "terminal_dependency_failure"
    POLICY_UNKNOWN = "policy_unknown"
    IN_PROGRESS = "in_progress"
    HUMAN_ATTENTION = "human_attention"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CapacityState(StrEnum):
    """Observed admission posture; never a forecast of future capacity."""

    AVAILABLE = "available"
    SATURATED = "saturated"
    BACKPRESSURE = "backpressure"
    UNKNOWN = "unknown"


class CriticalPathBasis(StrEnum):
    """The only supported critical-path interpretation."""

    DEPENDENCY_DEPTH_ONLY = "dependency_depth_only"


class PlanGap(StrEnum):
    """Closed diagnostics for incomplete or unsafe source evidence."""

    PROJECTION_MISSING = "projection_missing"
    PROJECTION_PARTIAL = "projection_partial"
    PROJECTION_STALE = "projection_stale"
    ATTEMPTS_MISSING = "attempts_missing"
    ATTEMPTS_PARTIAL = "attempts_partial"
    TASK_MALFORMED = "task_malformed"
    DEPENDENCY_MISSING = "dependency_missing"
    TERMINAL_DEPENDENCY_FAILURE = "terminal_dependency_failure"
    DEPENDENCY_POLICY_UNKNOWN = "dependency_policy_unknown"
    DEPENDENCIES_TRUNCATED = "dependencies_truncated"
    FOCUS_INPUT_MALFORMED = "focus_input_malformed"
    FOCUS_TASK_MISSING = "focus_task_missing"
    PLAN_TRUNCATED = "plan_truncated"
    EDGE_LIMIT_EXCEEDED = "edge_limit_exceeded"
    CYCLE_DETECTED = "cycle_detected"
    GRAPH_MALFORMED = "graph_malformed"
    GRAPH_TRUNCATED = "graph_truncated"
    GRAPH_STALE = "graph_stale"
    RESUME_GAP = "resume_gap"
    CAPACITY_MISSING = "capacity_missing"
    CAPACITY_MALFORMED = "capacity_malformed"


def _identifier(value: str | None, field: str, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or None")
    if (
        len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES
        or _SAFE_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(f"{field} is not a safe identifier")


def _bounded_tuple(value: object, field: str, maximum: int) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be a collection")
    try:
        result = tuple(islice(iter(value), maximum + 1))  # type: ignore[arg-type]
    except Exception as exc:
        raise TypeError(f"{field} must be iterable") from exc
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds its {maximum}-item bound")
    return result


def _non_negative(value: int | None, field: str) -> None:
    if value is None:
        return
    if type(value) is not int:
        raise TypeError(f"{field} must be an int or None")
    if value < 0:
        raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True, slots=True)
class ExecutionPlanEdge:
    """One canonical dependency edge, directed prerequisite to dependent."""

    prerequisite_task_id: str
    dependent_task_id: str
    prerequisite_missing: bool = False

    def __post_init__(self) -> None:
        _identifier(self.prerequisite_task_id, "prerequisite_task_id", required=True)
        _identifier(self.dependent_task_id, "dependent_task_id", required=True)
        if type(self.prerequisite_missing) is not bool:
            raise TypeError("prerequisite_missing must be a bool")


@dataclass(frozen=True, slots=True)
class TaskReadiness:
    """One task's exact blockers and provider-safe current execution posture."""

    task_id: str
    task_state: str
    readiness: ReadinessState
    dependency_task_ids: tuple[str, ...]
    blocking_dependency_ids: tuple[str, ...]
    terminal_dependency_failure_ids: tuple[str, ...]
    policy_unknown_dependency_ids: tuple[str, ...]
    owner_session_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    phase: RunPhase | None
    awaits_human: bool
    next_action: NextAction
    freshness: FreshnessState
    evidence_state: EvidenceState
    gaps: tuple[PlanGap, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.task_id, "task_id", required=True)
        _identifier(self.task_state, "task_state", required=True)
        _identifier(self.owner_session_id, "owner_session_id")
        _identifier(self.provider, "provider")
        _identifier(self.profile_id, "profile_id")
        if self.role_name is not None:
            if not isinstance(self.role_name, str) or len(self.role_name.encode("utf-8")) > 160:
                raise ValueError("role_name exceeds its safe display bound")
        if type(self.awaits_human) is not bool:
            raise TypeError("awaits_human must be a bool")
        dependencies = _bounded_tuple(
            self.dependency_task_ids, "dependency_task_ids", MAX_PLAN_TASKS
        )
        blockers = _bounded_tuple(
            self.blocking_dependency_ids, "blocking_dependency_ids", MAX_PLAN_TASKS
        )
        terminal_failures = _bounded_tuple(
            self.terminal_dependency_failure_ids,
            "terminal_dependency_failure_ids",
            MAX_PLAN_TASKS,
        )
        policy_unknown = _bounded_tuple(
            self.policy_unknown_dependency_ids,
            "policy_unknown_dependency_ids",
            MAX_PLAN_TASKS,
        )
        for field, values in (
            ("dependency_task_ids", dependencies),
            ("blocking_dependency_ids", blockers),
            ("terminal_dependency_failure_ids", terminal_failures),
            ("policy_unknown_dependency_ids", policy_unknown),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field} contains duplicates")
            for value in values:
                _identifier(value, field, required=True)  # type: ignore[arg-type]
        if not set(blockers) <= set(dependencies):
            raise ValueError("blocking dependencies must be canonical dependencies")
        if not set(terminal_failures) <= set(blockers):
            raise ValueError("terminal dependency failures must remain blockers")
        if not set(policy_unknown) <= set(blockers):
            raise ValueError("policy-unknown dependencies must remain blockers")
        if set(terminal_failures) & set(policy_unknown):
            raise ValueError("dependency failure and unknown-policy sets must be disjoint")
        gaps = tuple(PlanGap(value) for value in _bounded_tuple(self.gaps, "gaps", MAX_TASK_GAPS))
        readiness = ReadinessState(self.readiness)
        next_action = NextAction(self.next_action)
        if readiness is ReadinessState.TERMINAL_DEPENDENCY_FAILURE and not terminal_failures:
            raise ValueError("terminal dependency failures require their exact readiness state")
        if terminal_failures and readiness not in {
            ReadinessState.TERMINAL_DEPENDENCY_FAILURE,
            ReadinessState.COMPLETE,
            ReadinessState.CANCELLED,
        }:
            raise ValueError("terminal dependency failures require their exact readiness state")
        if readiness is ReadinessState.POLICY_UNKNOWN and not policy_unknown:
            raise ValueError("policy-unknown dependencies require their exact readiness state")
        if policy_unknown and readiness not in {
            ReadinessState.POLICY_UNKNOWN,
            ReadinessState.COMPLETE,
            ReadinessState.CANCELLED,
        }:
            raise ValueError("policy-unknown dependencies require their exact readiness state")
        if readiness is ReadinessState.READY and blockers:
            raise ValueError("a ready task cannot retain blocking dependencies")
        if readiness in {
            ReadinessState.TERMINAL_DEPENDENCY_FAILURE,
            ReadinessState.POLICY_UNKNOWN,
        } and (not self.awaits_human or next_action is not NextAction.INSPECT_MISSING_EVIDENCE):
            raise ValueError(
                "dependency failure and policy-unknown states require human inspection"
            )
        if set(gaps) & {PlanGap.TASK_MALFORMED, PlanGap.DEPENDENCIES_TRUNCATED}:
            if (
                readiness not in {ReadinessState.UNKNOWN, ReadinessState.POLICY_UNKNOWN}
                or not self.awaits_human
                or next_action is not NextAction.INSPECT_MISSING_EVIDENCE
            ):
                raise ValueError("malformed dependency evidence must fail closed")
        object.__setattr__(self, "dependency_task_ids", dependencies)
        object.__setattr__(self, "blocking_dependency_ids", blockers)
        object.__setattr__(self, "terminal_dependency_failure_ids", terminal_failures)
        object.__setattr__(self, "policy_unknown_dependency_ids", policy_unknown)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "next_action", next_action)
        object.__setattr__(self, "freshness", FreshnessState(self.freshness))
        object.__setattr__(self, "evidence_state", EvidenceState(self.evidence_state))
        if self.phase is not None:
            object.__setattr__(self, "phase", RunPhase(self.phase))


@dataclass(frozen=True, slots=True)
class CapacitySignal:
    """Bounded current admission signal copied from operational state."""

    state: CapacityState
    active: int | None
    limit: int | None
    queued: int | None
    queue_capacity: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", CapacityState(self.state))
        for field in ("active", "limit", "queued", "queue_capacity"):
            _non_negative(getattr(self, field), field)
        if self.active is not None and self.limit is not None and self.active > self.limit:
            raise ValueError("active cannot exceed limit")
        if (
            self.queued is not None
            and self.queue_capacity is not None
            and self.queued > self.queue_capacity
        ):
            raise ValueError("queued cannot exceed queue_capacity")
        values = (self.active, self.limit, self.queued, self.queue_capacity)
        if self.state is CapacityState.UNKNOWN:
            if any(value is not None for value in values):
                raise ValueError("unknown capacity cannot retain numeric claims")
            return
        if any(value is None for value in values):
            raise ValueError("known capacity requires every bounded source value")
        if self.queued:  # type: ignore[truthy-bool]
            expected = CapacityState.BACKPRESSURE
        elif self.active == self.limit:
            expected = CapacityState.SATURATED
        else:
            expected = CapacityState.AVAILABLE
        if self.state is not expected:
            raise ValueError("capacity state disagrees with its source values")


@dataclass(frozen=True, slots=True)
class ExecutionPlanView:
    """A deterministic dependency plan; it is explicitly not a schedule or forecast."""

    generated_at: str
    state: PlanState
    freshness: FreshnessState
    nodes: tuple[TaskReadiness, ...]
    edges: tuple[ExecutionPlanEdge, ...]
    focus_task_ids: tuple[str, ...]
    critical_path_task_ids: tuple[str, ...]
    critical_path_basis: CriticalPathBasis
    critical_path_predictive: bool
    capacity: CapacitySignal
    gaps: tuple[PlanGap, ...] = ()

    def __post_init__(self) -> None:
        nodes = _bounded_tuple(self.nodes, "nodes", MAX_PLAN_TASKS)
        edges = _bounded_tuple(self.edges, "edges", MAX_PLAN_EDGES)
        focus = _bounded_tuple(self.focus_task_ids, "focus_task_ids", MAX_FOCUS_TASKS)
        path = _bounded_tuple(self.critical_path_task_ids, "critical_path_task_ids", MAX_PLAN_TASKS)
        gaps = tuple(PlanGap(value) for value in _bounded_tuple(self.gaps, "gaps", MAX_PLAN_GAPS))
        if any(type(value) is not TaskReadiness for value in nodes):
            raise TypeError("nodes must contain exact TaskReadiness records")
        if any(type(value) is not ExecutionPlanEdge for value in edges):
            raise TypeError("edges must contain exact ExecutionPlanEdge records")
        if type(self.capacity) is not CapacitySignal:
            raise TypeError("capacity must be an exact immutable CapacitySignal")
        node_ids = tuple(value.task_id for value in nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("nodes contain duplicate task ids")
        for field, values in (("focus_task_ids", focus), ("critical_path_task_ids", path)):
            if len(values) != len(set(values)):
                raise ValueError(f"{field} contains duplicates")
            for value in values:
                _identifier(value, field, required=True)  # type: ignore[arg-type]
            if not set(values) <= set(node_ids):
                raise ValueError(f"{field} must reference plan nodes")
        edge_keys = tuple((edge.prerequisite_task_id, edge.dependent_task_id) for edge in edges)
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("edges contain duplicates")
        for edge in edges:
            if edge.dependent_task_id not in node_ids:
                raise ValueError("edge dependent must reference a plan node")
            if edge.prerequisite_missing:
                if edge.prerequisite_task_id in node_ids:
                    raise ValueError("missing edge prerequisite unexpectedly has a plan node")
            elif edge.prerequisite_task_id not in node_ids:
                raise ValueError("edge prerequisite must reference a plan node")
        expected_edges = {
            (dependency, node.task_id) for node in nodes for dependency in node.dependency_task_ids
        }
        if set(edge_keys) != expected_edges:
            raise ValueError("edges must exactly represent node dependencies")
        edge_set = set(edge_keys)
        if any(pair not in edge_set for pair in zip(path, path[1:], strict=False)):
            raise ValueError("critical path must follow dependency edges")
        if type(self.critical_path_predictive) is not bool:
            raise TypeError("critical_path_predictive must be a bool")
        if self.critical_path_predictive:
            raise ValueError("execution plans cannot claim predictive critical paths")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "focus_task_ids", focus)
        object.__setattr__(self, "critical_path_task_ids", path)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "state", PlanState(self.state))
        object.__setattr__(self, "freshness", FreshnessState(self.freshness))
        object.__setattr__(self, "critical_path_basis", CriticalPathBasis(self.critical_path_basis))
