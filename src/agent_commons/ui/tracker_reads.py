"""Compose W1/W2 derived records into the browser tracker contract."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from itertools import islice
from typing import TYPE_CHECKING, Any

from agent_commons.core.bounded import truncate_utf8
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.execution_plan import PlanGap, PlanState
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import FreshnessState
from agent_commons.runtime import AttemptStore
from agent_commons.runtime.model import BuiltinProfileId
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

if TYPE_CHECKING:
    from agent_commons.ui.context import UIContext


def _observation_clock() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ObservedTrackerSource:
    """Sample canonical and operational state against a stable shared fingerprint.

    Sequence belongs to meaningful tracker changes, including clock-driven
    freshness transitions and visible active-run durations. Re-observing
    unchanged data neither reuses a cursor
    for changed content nor floods the stream with observation timestamps.
    """

    def __init__(self, context: UIContext, *, clock: Callable[[], str] | None = None):
        self._context = context
        self._clock = clock or _observation_clock
        self._lock = threading.Lock()
        self._sequence = 0
        self._signature: bytes | None = None

    def __call__(self, *, resume_after: int | None = None) -> TrackerSnapshotDTO:
        del resume_after  # The SSE transport owns resume-gap handling.
        with self._lock:
            snapshot = self._sample()
            wire = snapshot.to_wire()
            wire["sequence"] = 0
            wire["freshness"]["generated_at"] = ""
            wire["freshness"]["source_updated_at"] = None
            signature = json.dumps(wire, sort_keys=True, separators=(",", ":")).encode()
            if signature != self._signature:
                self._sequence += 1
                self._signature = signature
            return replace(snapshot, sequence=self._sequence)

    def _sample(self) -> TrackerSnapshotDTO:
        context = self._context
        context.refresh_if_changed()
        _, graph = context.snapshot_frame()
        before = context.fingerprint()
        if _source_revision(graph) != before:
            return unavailable_tracker_snapshot(
                generated_at=self._clock(), sequence=0, gap="source_revision_unavailable"
            )
        manager = context.manager()
        canonical = manager.snapshot()
        attempts = AttemptStore(manager.paths.state_root, read_only=True).list_attempts()
        after = context.fingerprint()
        observed_at = self._clock()
        if before != after:
            return unavailable_tracker_snapshot(
                generated_at=observed_at, sequence=0, gap="source_revision_unavailable"
            )
        try:
            graph_time = datetime.fromisoformat(
                str(graph.get("generated_at", "")).replace("Z", "+00:00")
            )
            observation_time = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            valid_clock = graph_time.tzinfo is not None and graph_time <= observation_time
        except (ValueError, TypeError):
            valid_clock = False
        if not valid_clock:
            return unavailable_tracker_snapshot(
                generated_at=observed_at, sequence=0, gap="graph_malformed"
            )
        # The unchanged fingerprint revalidates the cached graph against these
        # exact sources. Refresh its observation clock only in this local read;
        # never rewrite canonical event clocks or mutate the shared graph cache.
        observed_graph = {**graph, "generated_at": observed_at}
        return build_tracker_snapshot(
            canonical,
            attempts,
            generated_at=observed_at,
            sequence=0,
            graph=observed_graph,
            canonical_observed_at=observed_at,
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
    canonical_observed_at: str | None = None,
) -> TrackerSnapshotDTO:
    """Return one bounded snapshot without exposing provider-controlled text."""

    source_revision = _source_revision(graph)
    if source_revision is None:
        return unavailable_tracker_snapshot(
            generated_at=generated_at,
            sequence=sequence,
            gap="source_revision_unavailable",
        )
    attempt_values = (
        None if attempts is None else tuple(islice(iter(attempts), MAX_ATTEMPT_INPUTS + 1))
    )
    attempts_truncated = attempt_values is not None and len(attempt_values) > MAX_ATTEMPT_INPUTS
    plan = build_execution_plan(
        snapshot,
        attempt_values,
        generated_at=generated_at,
        focus_task_ids=focus_task_ids,
        stale_after_seconds=stale_after_seconds,
        graph=graph,
        resume_gap=resume_gap,
        capacity=capacity,
        canonical_observed_at=canonical_observed_at,
    )
    if snapshot is None or plan.state is PlanState.ERROR:
        return _error_snapshot(
            generated_at,
            sequence,
            plan,
            source_revision=source_revision,
            attempts_truncated=attempts_truncated,
            resume_gap=resume_gap,
        )
    try:
        health = build_work_health(
            snapshot,
            () if attempt_values is None else attempt_values[:MAX_ATTEMPT_INPUTS],
            generated_at=generated_at,
            stale_after_seconds=stale_after_seconds,
            graph=graph,
            canonical_observed_at=canonical_observed_at,
        )
    except (OverflowError, TypeError, ValueError):
        return _error_snapshot(
            generated_at,
            sequence,
            plan,
            source_revision=source_revision,
            attempts_truncated=attempts_truncated,
            resume_gap=resume_gap,
        )

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
            **_suggested_role(snapshot, node.task_id),
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
            wall_time_seconds=run.wall_time_seconds,
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
    truncated = attempts_truncated or bool(
        set(plan.gaps)
        & {
            PlanGap.DEPENDENCIES_TRUNCATED,
            PlanGap.PLAN_TRUNCATED,
            PlanGap.EDGE_LIMIT_EXCEEDED,
            PlanGap.GRAPH_TRUNCATED,
        }
    )
    return TrackerSnapshotDTO(
        sequence=_sequence(sequence),
        source_revision=source_revision,
        truncated=truncated,
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


def loading_tracker_snapshot(
    *, generated_at: str, sequence: int = 0, source_revision: str | None = None
) -> TrackerSnapshotDTO:
    """Typed initial state used while the first derived read is assembled."""

    return _blank_snapshot(
        generated_at,
        sequence,
        source_revision=source_revision,
        state="loading",
        freshness="unknown",
    )


def unavailable_tracker_snapshot(
    *,
    generated_at: str,
    sequence: int = 0,
    source_revision: str | None = None,
    truncated: bool = False,
    resume_gap: bool = False,
    gap: str = "projection_unavailable",
) -> TrackerSnapshotDTO:
    """Return a fixed-shape error without propagating an internal exception."""

    return _blank_snapshot(
        generated_at,
        sequence,
        source_revision=source_revision,
        truncated=truncated,
        state="error",
        freshness="unknown",
        resume_gap=resume_gap,
        gaps=(gap,),
    )


def _error_snapshot(
    generated_at: str,
    sequence: int,
    plan: Any,
    *,
    source_revision: str,
    attempts_truncated: bool,
    resume_gap: bool,
) -> TrackerSnapshotDTO:
    gaps = tuple(sorted({gap.value for gap in plan.gaps} | {"projection_unavailable"}))
    return _blank_snapshot(
        generated_at,
        sequence,
        source_revision=source_revision,
        truncated=attempts_truncated
        or bool(
            set(plan.gaps)
            & {
                PlanGap.DEPENDENCIES_TRUNCATED,
                PlanGap.PLAN_TRUNCATED,
                PlanGap.EDGE_LIMIT_EXCEEDED,
                PlanGap.GRAPH_TRUNCATED,
            }
        ),
        state="error",
        freshness="unknown",
        resume_gap=resume_gap,
        gaps=gaps,
    )


def _blank_snapshot(
    generated_at: str,
    sequence: int,
    *,
    source_revision: str | None,
    truncated: bool = False,
    state: TrackerSurfaceState,
    freshness: str,
    resume_gap: bool = False,
    gaps: tuple[str, ...] = (),
) -> TrackerSnapshotDTO:
    return TrackerSnapshotDTO(
        sequence=_sequence(sequence),
        source_revision=source_revision,
        truncated=truncated,
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


_SOURCE_REVISION = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _source_revision(graph: Mapping[str, Any] | None) -> str | None:
    """Return the exact stable read fingerprint or fail closed on a moving source."""

    if not isinstance(graph, Mapping):
        return None
    try:
        value = graph.get("ledger_fingerprint")
    except Exception:
        return None
    return value if isinstance(value, str) and _SOURCE_REVISION.fullmatch(value) else None


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


def _suggested_role(snapshot: ProjectSnapshot, task_id: str) -> dict[str, str]:
    """Project a planned role hint without assigning work or changing readiness."""

    task = snapshot.tasks.get(task_id)
    extensions = task.get("extensions") if task is not None else None
    identifier = extensions.get("suggested_agent_id") if isinstance(extensions, Mapping) else None
    if type(identifier) is not str or not is_typed_id(identifier, "agent"):
        return {}
    role = snapshot.agents.get(identifier)
    if role is None or role.get("state") != "active" or role.get("template"):
        return {}
    name = role.get("name")
    if type(name) is not str:
        return {}
    name = truncate_utf8("".join(character for character in name if character.isprintable()), 160)
    if not name.strip():
        return {}
    try:
        profile = BuiltinProfileId(role.get("profile_id"))
    except (ValueError, TypeError):
        return {}
    return {
        "suggested_agent_id": identifier,
        "suggested_role_name": name,
        "suggested_provider": profile.value.split("-", 1)[0],
    }


def _surface_state(*, empty: bool, partial: bool, stale: bool) -> TrackerSurfaceState:
    if empty:
        return "empty"
    if stale:
        return "stale"
    if partial:
        return "partial"
    return "ready"
