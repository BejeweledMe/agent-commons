"""Compose W1/W2 derived records into the browser tracker contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import islice
from typing import Any

from agent_commons.core.bounded import truncate_utf8
from agent_commons.domain.execution_plan import PlanState
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import FreshnessState
from agent_commons.services.execution_plan import MAX_ATTEMPT_INPUTS, build_execution_plan
from agent_commons.services.work_metrics import build_work_health
from agent_commons.ui.tracker_dtos import (
    TrackerAttentionDTO,
    TrackerCapacityDTO,
    TrackerEdgeDTO,
    TrackerFreshnessDTO,
    TrackerRunDTO,
    TrackerSnapshotDTO,
    TrackerSurfaceState,
    TrackerTaskDTO,
)


def build_tracker_snapshot(
    snapshot: ProjectSnapshot | None,
    attempts: Iterable[Mapping[str, Any] | Any] | None,
    *,
    generated_at: str,
    sequence: int,
    focus_task_ids: Iterable[str] = (),
    stale_after_seconds: int = 60,
    graph: Mapping[str, Any] | None = None,
    resume_gap: bool = False,
    capacity: Mapping[str, Any] | None = None,
) -> TrackerSnapshotDTO:
    """Return one bounded snapshot without exposing provider-controlled text."""

    attempt_values = (
        None if attempts is None else tuple(islice(iter(attempts), MAX_ATTEMPT_INPUTS + 1))
    )
    plan = build_execution_plan(
        snapshot,
        attempt_values,
        generated_at=generated_at,
        focus_task_ids=focus_task_ids,
        stale_after_seconds=stale_after_seconds,
        graph=graph,
        resume_gap=resume_gap,
        capacity=capacity,
    )
    if snapshot is None or plan.state is PlanState.ERROR:
        return _error_snapshot(generated_at, sequence, plan, resume_gap=resume_gap)
    try:
        health = build_work_health(
            snapshot,
            () if attempt_values is None else attempt_values[:MAX_ATTEMPT_INPUTS],
            generated_at=generated_at,
            stale_after_seconds=stale_after_seconds,
            graph=graph,
        )
    except (OverflowError, TypeError, ValueError):
        return _error_snapshot(generated_at, sequence, plan, resume_gap=resume_gap)

    tasks = tuple(
        TrackerTaskDTO(
            task_id=node.task_id,
            title=_task_title(snapshot.tasks.get(node.task_id)),
            task_state=node.task_state,
            readiness=node.readiness.value,
            dependency_task_ids=node.dependency_task_ids,
            blocking_dependency_ids=node.blocking_dependency_ids,
            owner_session_id=node.owner_session_id,
            role_name=node.role_name,
            provider=node.provider,
            profile_id=node.profile_id,
            phase=node.phase.value if node.phase is not None else None,
            awaits_human=node.awaits_human,
            next_action=node.next_action.value,
            freshness=node.freshness.value,
            evidence_state=node.evidence_state.value,
            gaps=tuple(gap.value for gap in node.gaps),
        )
        for node in plan.nodes
    )
    edges = tuple(
        TrackerEdgeDTO(
            prerequisite_task_id=edge.prerequisite_task_id,
            dependent_task_id=edge.dependent_task_id,
            prerequisite_missing=edge.prerequisite_missing,
        )
        for edge in plan.edges
    )
    runs = tuple(
        TrackerRunDTO(
            delegation_id=run.delegation_id,
            task_id=run.task_id,
            agent_id=run.agent_id,
            role_name=run.role_name,
            provider=run.provider,
            profile_id=run.profile_id,
            phase=run.phase.value,
            attempt_id=run.attempt_id,
            attempt_number=run.attempt_number,
            started_at=run.started_at,
            updated_at=run.updated_at,
            finished_at=run.finished_at,
            duration_seconds=run.duration_seconds,
            awaits_human=run.awaits_human,
            next_action=run.next_action.value,
            freshness=run.freshness.value,
            evidence_state=run.evidence_state.value,
        )
        for run in health.runs
    )
    attention = tuple(
        [
            TrackerAttentionDTO(
                kind="run",
                item_id=run.delegation_id,
                task_id=run.task_id,
                reason_code=run.phase.value,
                next_action=run.next_action.value,
            )
            for run in health.runs
            if run.awaits_human
        ]
        + [
            TrackerAttentionDTO(
                kind="review",
                item_id=gap.review_id or gap.task_id,
                task_id=gap.task_id,
                reason_code=gap.kind.value,
                next_action=gap.next_action.value,
            )
            for gap in health.review_gaps
        ]
    )
    state = _surface_state(
        empty=not tasks and not runs,
        partial=plan.state is PlanState.PARTIAL,
        stale=plan.freshness is FreshnessState.STALE or resume_gap,
    )
    gaps = tuple(sorted({gap.value for gap in plan.gaps}))
    return TrackerSnapshotDTO(
        sequence=_sequence(sequence),
        state=state,
        tasks=tasks,
        edges=edges,
        runs=runs,
        attention=attention,
        capacity=TrackerCapacityDTO(
            state=plan.capacity.state.value,
            active=plan.capacity.active,
            limit=plan.capacity.limit,
            queued=plan.capacity.queued,
            queue_capacity=plan.capacity.queue_capacity,
        ),
        freshness=TrackerFreshnessDTO(
            generated_at=generated_at,
            source_updated_at=health.source_updated_at,
            state=plan.freshness.value,
            resume_gap=resume_gap,
        ),
        focus_task_ids=plan.focus_task_ids,
        critical_path_task_ids=plan.critical_path_task_ids,
        gaps=gaps,
    )


def loading_tracker_snapshot(*, generated_at: str, sequence: int = 0) -> TrackerSnapshotDTO:
    """Typed initial state used while the first derived read is assembled."""

    return _blank_snapshot(generated_at, sequence, state="loading", freshness="unknown")


def unavailable_tracker_snapshot(
    *, generated_at: str, sequence: int = 0, gap: str = "projection_unavailable"
) -> TrackerSnapshotDTO:
    """Return a fixed-shape error without propagating an internal exception."""

    return _blank_snapshot(
        generated_at,
        sequence,
        state="error",
        freshness="unknown",
        gaps=(gap,),
    )


def _error_snapshot(
    generated_at: str,
    sequence: int,
    plan: Any,
    *,
    resume_gap: bool,
) -> TrackerSnapshotDTO:
    gaps = tuple(sorted({gap.value for gap in plan.gaps} | {"projection_unavailable"}))
    return _blank_snapshot(
        generated_at,
        sequence,
        state="error",
        freshness="unknown",
        resume_gap=resume_gap,
        gaps=gaps,
    )


def _blank_snapshot(
    generated_at: str,
    sequence: int,
    *,
    state: TrackerSurfaceState,
    freshness: str,
    resume_gap: bool = False,
    gaps: tuple[str, ...] = (),
) -> TrackerSnapshotDTO:
    return TrackerSnapshotDTO(
        sequence=_sequence(sequence),
        state=state,
        tasks=(),
        edges=(),
        runs=(),
        attention=(),
        capacity=TrackerCapacityDTO("unknown", None, None, None, None),
        freshness=TrackerFreshnessDTO(generated_at, None, freshness, resume_gap),
        focus_task_ids=(),
        critical_path_task_ids=(),
        gaps=gaps,
    )


def _sequence(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("tracker sequence must be a non-negative integer")
    return value


def _task_title(task: object) -> str:
    if not isinstance(task, Mapping):
        return ""
    title = task.get("title")
    if not isinstance(title, str):
        return ""
    return truncate_utf8("".join(character for character in title if ord(character) >= 32), 300)


def _surface_state(*, empty: bool, partial: bool, stale: bool) -> TrackerSurfaceState:
    if empty:
        return "empty"
    if stale:
        return "stale"
    if partial:
        return "partial"
    return "ready"
