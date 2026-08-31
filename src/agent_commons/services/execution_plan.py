"""Build bounded W2 readiness and execution-plan projections."""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from itertools import islice
from typing import Any

from agent_commons.domain.execution_plan import (
    MAX_FOCUS_TASKS,
    MAX_PLAN_EDGES,
    MAX_PLAN_TASKS,
    CapacitySignal,
    CapacityState,
    CriticalPathBasis,
    ExecutionPlanEdge,
    ExecutionPlanView,
    PlanGap,
    PlanState,
    ReadinessState,
    TaskReadiness,
)
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import (
    AcceptanceView,
    EvidenceState,
    FreshnessState,
    NextAction,
    RunView,
    WorkSourceGap,
)
from agent_commons.services.work_metrics import build_work_health

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_SATISFIED_DEPENDENCY_STATES = frozenset({"accepted"})
_KNOWN_TASK_STATES = frozenset(
    {"ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"}
)
_HUMAN_ACTIONS = frozenset(
    {
        NextAction.ANSWER_OPERATOR_REQUEST,
        NextAction.INSPECT_FAILURE,
        NextAction.REQUEST_REVIEW,
        NextAction.REVISE_WORK,
        NextAction.ACCEPT_TASK,
        NextAction.INSPECT_MISSING_EVIDENCE,
    }
)
MAX_ATTEMPT_INPUTS = MAX_PLAN_TASKS * 4


def build_execution_plan(
    snapshot: ProjectSnapshot | None,
    attempts: Iterable[Mapping[str, Any] | Any] | None,
    *,
    generated_at: str,
    focus_task_ids: Iterable[str] = (),
    stale_after_seconds: int = 60,
    graph: Mapping[str, Any] | None = None,
    resume_gap: bool = False,
    capacity: Mapping[str, Any] | None = None,
) -> ExecutionPlanView:
    """Build a deterministic advisory plan from existing read sources.

    The longest path is dependency depth only.  It does not estimate elapsed
    time, completion percentage, cost, tokens, or future provider capacity.
    """

    gaps: set[PlanGap] = set()
    parsed_generated_at = _timestamp(generated_at)
    if parsed_generated_at is None or stale_after_seconds < 0 or type(resume_gap) is not bool:
        return _error_view(generated_at, PlanGap.PROJECTION_MISSING, capacity=capacity)
    if snapshot is None:
        return _error_view(generated_at, PlanGap.PROJECTION_MISSING, capacity=capacity)
    if len(snapshot.tasks) > MAX_PLAN_TASKS:
        return _error_view(generated_at, PlanGap.PLAN_TRUNCATED, capacity=capacity)

    focus_values, focus_overflow, focus_failed = _bounded_materialize(
        focus_task_ids, MAX_FOCUS_TASKS
    )
    if focus_failed:
        return _error_view(generated_at, PlanGap.FOCUS_INPUT_MALFORMED, capacity=capacity)
    if focus_overflow:
        return _error_view(generated_at, PlanGap.PLAN_TRUNCATED, capacity=capacity)
    if any(not _safe_identifier(value) for value in focus_values):
        return _error_view(generated_at, PlanGap.TASK_MALFORMED, capacity=capacity)

    attempt_values: tuple[Mapping[str, Any] | Any, ...]
    if attempts is None:
        attempt_values = ()
        gaps.add(PlanGap.ATTEMPTS_MISSING)
    else:
        bounded_attempts, attempts_overflow, attempts_failed = _bounded_materialize(
            attempts, MAX_ATTEMPT_INPUTS
        )
        attempt_values = bounded_attempts
        if attempts_overflow or attempts_failed:
            attempt_values = ()
            gaps.add(PlanGap.ATTEMPTS_PARTIAL)

    health = None
    try:
        health = build_work_health(
            snapshot,
            attempt_values,
            generated_at=generated_at,
            stale_after_seconds=stale_after_seconds,
            graph=graph,
        )
    except Exception:
        gaps.add(PlanGap.ATTEMPTS_PARTIAL)
        try:
            health = build_work_health(
                snapshot,
                (),
                generated_at=generated_at,
                stale_after_seconds=stale_after_seconds,
                graph=None,
            )
        except Exception:
            health = None
    if health is not None and set(health.source_gaps) & {
        WorkSourceGap.ATTEMPT_CORRELATION_MISSING,
        WorkSourceGap.ORPHAN_OPERATIONAL_ATTEMPT,
        WorkSourceGap.ATTEMPT_EVIDENCE_MISSING,
        WorkSourceGap.SOURCE_TIMESTAMP_INVALID,
        WorkSourceGap.SOURCE_TIMESTAMP_FUTURE,
        WorkSourceGap.SOURCE_TIMESTAMP_ORDER_INVALID,
    }:
        gaps.add(PlanGap.ATTEMPTS_PARTIAL)

    dependencies, task_gaps, fatal_task_input = _task_dependencies(snapshot)
    gaps.update(gap for values in task_gaps.values() for gap in values)
    if fatal_task_input:
        gaps.add(PlanGap.TASK_MALFORMED)

    selected, selection_fatal = _focused_tasks(snapshot, dependencies, focus_values, gaps)
    if selection_fatal:
        gaps.add(PlanGap.PLAN_TRUNCATED)

    graph_gap, graph_fatal = _graph_state(
        graph,
        generated_at=parsed_generated_at,
        stale_after_seconds=stale_after_seconds,
    )
    gaps.update(graph_gap)
    if resume_gap:
        gaps.add(PlanGap.RESUME_GAP)

    capacity_signal, capacity_gap = _capacity_signal(capacity)
    if capacity_gap is not None:
        gaps.add(capacity_gap)
    if sum(len(dependencies.get(task_id, ())) for task_id in selected) > MAX_PLAN_EDGES:
        return _error_view(generated_at, PlanGap.EDGE_LIMIT_EXCEEDED, capacity=capacity)

    run_by_task = _runs_by_task(health.runs if health is not None else ())
    acceptance_by_task = (
        {value.task_id: value for value in health.acceptances} if health is not None else {}
    )
    nodes: list[TaskReadiness] = []
    edges: list[ExecutionPlanEdge] = []
    for task_id in sorted(selected):
        task = snapshot.tasks.get(task_id)
        if not isinstance(task, Mapping) or task_id not in dependencies:
            continue
        task_dependencies = dependencies.get(task_id, ())
        dependency_states = {
            dependency: _dependency_state(snapshot.tasks.get(dependency))
            for dependency in task_dependencies
        }
        blockers = tuple(
            dependency
            for dependency in task_dependencies
            if dependency_states[dependency] not in _SATISFIED_DEPENDENCY_STATES
        )
        terminal_failures = tuple(
            dependency
            for dependency in task_dependencies
            if dependency_states[dependency] == "cancelled"
        )
        policy_unknown = tuple(
            dependency for dependency in task_dependencies if dependency_states[dependency] is None
        )
        if terminal_failures:
            gaps.add(PlanGap.TERMINAL_DEPENDENCY_FAILURE)
        if policy_unknown:
            gaps.add(PlanGap.DEPENDENCY_POLICY_UNKNOWN)
        for dependency in task_dependencies:
            if len(edges) >= MAX_PLAN_EDGES:
                gaps.add(PlanGap.EDGE_LIMIT_EXCEEDED)
                break
            edges.append(
                ExecutionPlanEdge(
                    prerequisite_task_id=dependency,
                    dependent_task_id=task_id,
                    prerequisite_missing=not isinstance(snapshot.tasks.get(dependency), Mapping),
                )
            )
        run = run_by_task.get(task_id)
        acceptance = acceptance_by_task.get(task_id)
        node_gaps = set(task_gaps.get(task_id, ()))
        if terminal_failures:
            node_gaps.add(PlanGap.TERMINAL_DEPENDENCY_FAILURE)
        if policy_unknown:
            node_gaps.add(PlanGap.DEPENDENCY_POLICY_UNKNOWN)
        if health is None:
            node_gaps.add(PlanGap.PROJECTION_MISSING)
        nodes.append(
            _task_readiness(
                task_id,
                task,
                dependencies=task_dependencies,
                blockers=blockers,
                terminal_failures=terminal_failures,
                policy_unknown=policy_unknown,
                run=run,
                acceptance=acceptance,
                gaps=node_gaps,
                aggregate_freshness=(
                    health.freshness if health is not None else FreshnessState.UNKNOWN
                ),
            )
        )

    if any(node.evidence_state in {EvidenceState.MISSING, EvidenceState.PARTIAL} for node in nodes):
        gaps.add(PlanGap.PROJECTION_PARTIAL)

    planned_task_ids = tuple(node.task_id for node in nodes)
    planned_task_id_set = set(planned_task_ids)
    known_edges = tuple(
        sorted(
            (
                edge
                for edge in edges
                if not edge.prerequisite_missing
                and edge.prerequisite_task_id in planned_task_id_set
                and edge.dependent_task_id in planned_task_id_set
            ),
            key=lambda value: (value.prerequisite_task_id, value.dependent_task_id),
        )
    )
    critical_path, cycle = _critical_path(tuple(sorted(planned_task_ids)), known_edges)
    if cycle:
        gaps.add(PlanGap.CYCLE_DETECTED)
        critical_path = ()

    freshness = health.freshness if health is not None else FreshnessState.UNKNOWN
    if freshness is FreshnessState.STALE:
        gaps.add(PlanGap.PROJECTION_STALE)
    if PlanGap.GRAPH_STALE in gaps or PlanGap.RESUME_GAP in gaps:
        freshness = FreshnessState.STALE
    elif graph_fatal or health is None:
        freshness = FreshnessState.UNKNOWN

    fatal = bool(
        fatal_task_input
        or selection_fatal
        or graph_fatal
        or cycle
        or PlanGap.EDGE_LIMIT_EXCEEDED in gaps
    )
    state = PlanState.ERROR if fatal else (PlanState.PARTIAL if gaps else PlanState.COMPLETE)
    return ExecutionPlanView(
        generated_at=generated_at,
        state=state,
        freshness=freshness,
        nodes=tuple(nodes),
        edges=tuple(
            sorted(edges, key=lambda value: (value.prerequisite_task_id, value.dependent_task_id))
        ),
        focus_task_ids=tuple(sorted(set(focus_values) & planned_task_id_set)),
        critical_path_task_ids=critical_path,
        critical_path_basis=CriticalPathBasis.DEPENDENCY_DEPTH_ONLY,
        critical_path_predictive=False,
        capacity=capacity_signal,
        gaps=tuple(sorted(gaps, key=str)),
    )


def _error_view(
    generated_at: str,
    gap: PlanGap,
    *,
    capacity: Mapping[str, Any] | None,
) -> ExecutionPlanView:
    capacity_signal, capacity_gap = _capacity_signal(capacity)
    gaps = {gap}
    if capacity_gap is not None:
        gaps.add(capacity_gap)
    return ExecutionPlanView(
        generated_at=generated_at,
        state=PlanState.ERROR,
        freshness=FreshnessState.UNKNOWN,
        nodes=(),
        edges=(),
        focus_task_ids=(),
        critical_path_task_ids=(),
        critical_path_basis=CriticalPathBasis.DEPENDENCY_DEPTH_ONLY,
        critical_path_predictive=False,
        capacity=capacity_signal,
        gaps=tuple(sorted(gaps, key=str)),
    )


def _task_dependencies(
    snapshot: ProjectSnapshot,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[PlanGap, ...]], bool]:
    values: dict[str, tuple[str, ...]] = {}
    gaps: dict[str, tuple[PlanGap, ...]] = {}
    fatal = False
    for task_id, task in sorted(snapshot.tasks.items()):
        task_gap: set[PlanGap] = set()
        if not _safe_identifier(task_id) or not isinstance(task, Mapping):
            fatal = True
            continue
        try:
            state = task.get("state")
            raw = task.get("dependencies", ())
        except Exception:
            task_gap.add(PlanGap.TASK_MALFORMED)
            gaps[task_id] = tuple(sorted(task_gap, key=str))
            fatal = True
            continue
        if not isinstance(state, str) or state not in _KNOWN_TASK_STATES:
            task_gap.add(PlanGap.TASK_MALFORMED)
            fatal = True
        if isinstance(raw, (str, bytes, bytearray)):
            raw_values: tuple[object, ...] = ()
            task_gap.add(PlanGap.TASK_MALFORMED)
            fatal = True
        else:
            raw_values, raw_overflow, raw_failed = _bounded_materialize(raw, MAX_PLAN_TASKS)
            if raw_failed:
                raw_values = ()
                task_gap.add(PlanGap.TASK_MALFORMED)
                fatal = True
            if raw_overflow:
                raw_values = ()
                task_gap.add(PlanGap.DEPENDENCIES_TRUNCATED)
                fatal = True
        if len(raw_values) > MAX_PLAN_TASKS:
            raw_values = ()
            task_gap.add(PlanGap.DEPENDENCIES_TRUNCATED)
            fatal = True
        if any(not _safe_identifier(value) for value in raw_values):
            raw_values = ()
            task_gap.add(PlanGap.TASK_MALFORMED)
            fatal = True
        if len(set(raw_values)) != len(raw_values):
            task_gap.add(PlanGap.TASK_MALFORMED)
            fatal = True
        dependencies = tuple(sorted(set(raw_values)))  # type: ignore[arg-type]
        if task_id in dependencies:
            task_gap.add(PlanGap.CYCLE_DETECTED)
        if any(value not in snapshot.tasks for value in dependencies):
            task_gap.add(PlanGap.DEPENDENCY_MISSING)
        values[task_id] = dependencies
        if task_gap:
            gaps[task_id] = tuple(sorted(task_gap, key=str))
    return values, gaps, fatal


def _focused_tasks(
    snapshot: ProjectSnapshot,
    dependencies: Mapping[str, tuple[str, ...]],
    focus: tuple[str, ...],
    gaps: set[PlanGap],
) -> tuple[set[str], bool]:
    if not focus:
        return set(snapshot.tasks), False
    selected: set[str] = set()
    pending = list(sorted(set(focus), reverse=True))
    fatal = False
    while pending:
        task_id = pending.pop()
        if task_id in selected:
            continue
        if task_id not in snapshot.tasks:
            gaps.add(PlanGap.FOCUS_TASK_MISSING)
            continue
        selected.add(task_id)
        if len(selected) > MAX_PLAN_TASKS:
            fatal = True
            break
        pending.extend(
            sorted(
                (
                    value
                    for value in dependencies.get(task_id, ())
                    if value in snapshot.tasks and value not in selected
                ),
                reverse=True,
            )
        )
    return selected, fatal


def _runs_by_task(runs: tuple[RunView, ...]) -> dict[str, RunView]:
    selected: dict[str, RunView] = {}
    keys: dict[str, tuple[datetime, str]] = {}
    minimum = datetime.min.replace(tzinfo=UTC)
    for run in runs:
        if run.task_id is None:
            continue
        key = (_timestamp(run.updated_at) or minimum, run.delegation_id)
        if key > keys.get(run.task_id, (minimum, "")):
            selected[run.task_id] = run
            keys[run.task_id] = key
    return selected


def _task_readiness(
    task_id: str,
    task: Mapping[str, Any],
    *,
    dependencies: tuple[str, ...],
    blockers: tuple[str, ...],
    terminal_failures: tuple[str, ...],
    policy_unknown: tuple[str, ...],
    run: RunView | None,
    acceptance: AcceptanceView | None,
    gaps: set[PlanGap],
    aggregate_freshness: FreshnessState,
) -> TaskReadiness:
    state = str(task.get("state", "unknown"))
    action = (
        acceptance.next_action if acceptance is not None else NextAction.INSPECT_MISSING_EVIDENCE
    )
    awaits_human = action in _HUMAN_ACTIONS
    if run is not None:
        action = run.next_action
        awaits_human = run.awaits_human or action in _HUMAN_ACTIONS
    dependency_input_invalid = bool(gaps & {PlanGap.TASK_MALFORMED, PlanGap.DEPENDENCIES_TRUNCATED})
    if dependency_input_invalid:
        readiness = ReadinessState.UNKNOWN
        awaits_human = True
        action = NextAction.INSPECT_MISSING_EVIDENCE
    elif state == "accepted":
        readiness = ReadinessState.COMPLETE
        awaits_human = False
        action = NextAction.NONE
    elif state == "cancelled":
        readiness = ReadinessState.CANCELLED
        awaits_human = False
        action = NextAction.NONE
    elif terminal_failures:
        readiness = ReadinessState.TERMINAL_DEPENDENCY_FAILURE
        awaits_human = True
        action = NextAction.INSPECT_MISSING_EVIDENCE
    elif policy_unknown:
        readiness = ReadinessState.POLICY_UNKNOWN
        awaits_human = True
        action = NextAction.INSPECT_MISSING_EVIDENCE
    elif awaits_human:
        readiness = ReadinessState.HUMAN_ATTENTION
    elif blockers:
        readiness = ReadinessState.BLOCKED
        action = NextAction.RESOLVE_DEPENDENCIES
    elif state == "ready":
        readiness = ReadinessState.READY
        action = NextAction.START_READY_WORK
    elif state in {"assigned", "active", "completed", "review"}:
        readiness = ReadinessState.IN_PROGRESS
    elif state == "blocked":
        readiness = ReadinessState.BLOCKED
    else:
        readiness = ReadinessState.UNKNOWN
        gaps.add(PlanGap.TASK_MALFORMED)
        action = NextAction.INSPECT_MISSING_EVIDENCE
    freshness = run.freshness if run is not None else aggregate_freshness
    evidence = (
        run.evidence_state
        if run is not None
        else (acceptance.evidence_state if acceptance is not None else EvidenceState.MISSING)
    )
    if gaps and evidence is EvidenceState.COMPLETE:
        evidence = EvidenceState.PARTIAL
    return TaskReadiness(
        task_id=task_id,
        task_state=state,
        readiness=readiness,
        dependency_task_ids=dependencies,
        blocking_dependency_ids=blockers,
        terminal_dependency_failure_ids=terminal_failures,
        policy_unknown_dependency_ids=policy_unknown,
        owner_session_id=_optional_identifier(task.get("owner_session_id")),
        role_name=run.role_name if run is not None else None,
        provider=run.provider if run is not None else None,
        profile_id=run.profile_id if run is not None else None,
        phase=run.phase if run is not None else None,
        awaits_human=awaits_human,
        next_action=action,
        freshness=freshness,
        evidence_state=evidence,
        gaps=tuple(sorted(gaps, key=str)),
    )


def _critical_path(
    task_ids: tuple[str, ...], edges: tuple[ExecutionPlanEdge, ...]
) -> tuple[tuple[str, ...], bool]:
    successors = {task_id: [] for task_id in task_ids}
    predecessors = {task_id: [] for task_id in task_ids}
    for edge in edges:
        successors[edge.prerequisite_task_id].append(edge.dependent_task_id)
        predecessors[edge.dependent_task_id].append(edge.prerequisite_task_id)
    indegree = {task_id: len(predecessors[task_id]) for task_id in task_ids}
    ready = [task_id for task_id, value in indegree.items() if value == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        task_id = heapq.heappop(ready)
        ordered.append(task_id)
        for successor in sorted(successors[task_id]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(ready, successor)
    if len(ordered) != len(task_ids):
        return (), True
    paths: dict[str, tuple[str, ...]] = {}
    for task_id in ordered:
        candidates = [paths[value] + (task_id,) for value in predecessors[task_id]]
        paths[task_id] = (
            min(candidates, key=lambda value: (-len(value), value)) if candidates else (task_id,)
        )
    return (
        min(paths.values(), key=lambda value: (-len(value), value)) if paths else (),
        False,
    )


def _capacity_signal(
    value: Mapping[str, Any] | None,
) -> tuple[CapacitySignal, PlanGap | None]:
    if value is None:
        return CapacitySignal(
            CapacityState.UNKNOWN, None, None, None, None
        ), PlanGap.CAPACITY_MISSING
    if not isinstance(value, Mapping):
        return CapacitySignal(
            CapacityState.UNKNOWN, None, None, None, None
        ), PlanGap.CAPACITY_MALFORMED
    try:
        fields = tuple(
            value.get(field) for field in ("active", "limit", "queued", "queue_capacity")
        )
    except Exception:
        return CapacitySignal(
            CapacityState.UNKNOWN, None, None, None, None
        ), PlanGap.CAPACITY_MALFORMED
    if any(type(item) is not int or item < 0 for item in fields):
        return CapacitySignal(
            CapacityState.UNKNOWN, None, None, None, None
        ), PlanGap.CAPACITY_MALFORMED
    active, limit, queued, queue_capacity = fields
    if active > limit or queued > queue_capacity:
        return CapacitySignal(
            CapacityState.UNKNOWN, None, None, None, None
        ), PlanGap.CAPACITY_MALFORMED
    if queued:
        state = CapacityState.BACKPRESSURE
    elif active >= limit:
        state = CapacityState.SATURATED
    else:
        state = CapacityState.AVAILABLE
    return CapacitySignal(state, active, limit, queued, queue_capacity), None


def _graph_state(
    graph: Mapping[str, Any] | None,
    *,
    generated_at: datetime,
    stale_after_seconds: int,
) -> tuple[set[PlanGap], bool]:
    if graph is None:
        return set(), False
    if not isinstance(graph, Mapping):
        return {PlanGap.GRAPH_MALFORMED}, True
    gaps: set[PlanGap] = set()
    try:
        limits = graph.get("limits", {})
    except Exception:
        return {PlanGap.GRAPH_MALFORMED}, True
    if not isinstance(limits, Mapping):
        return {PlanGap.GRAPH_MALFORMED}, True
    try:
        if "truncated" in limits and type(limits.get("truncated")) is not bool:
            return {PlanGap.GRAPH_MALFORMED}, True
        if limits.get("truncated") is True:
            gaps.add(PlanGap.GRAPH_TRUNCATED)
        graph_time = _timestamp(graph.get("generated_at"))
    except Exception:
        return {PlanGap.GRAPH_MALFORMED}, True
    if graph_time is None or graph_time > generated_at:
        return gaps | {PlanGap.GRAPH_MALFORMED}, True
    if int((generated_at - graph_time).total_seconds()) > stale_after_seconds:
        gaps.add(PlanGap.GRAPH_STALE)
    return gaps, False


def _safe_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value.encode("utf-8")) <= 256
        and _SAFE_IDENTIFIER.fullmatch(value) is not None
    )


def _bounded_materialize(value: object, maximum: int) -> tuple[tuple[Any, ...], bool, bool]:
    """Read at most max+1 items; iterator exceptions become no-echo typed gaps."""

    if isinstance(value, (str, bytes, bytearray)):
        return (), False, True
    try:
        result = tuple(islice(iter(value), maximum + 1))  # type: ignore[arg-type]
    except Exception:
        return (), False, True
    if len(result) > maximum:
        return result[:maximum], True, False
    return result, False, False


def _dependency_state(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    try:
        state = value.get("state")
    except Exception:
        return None
    return state if isinstance(state, str) and state in _KNOWN_TASK_STATES else None


def _optional_identifier(value: object) -> str | None:
    return value if _safe_identifier(value) else None  # type: ignore[return-value]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
