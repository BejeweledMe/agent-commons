"""Deterministic joins for W1 operator work-state views."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from itertools import islice
from typing import Any, Protocol

from agent_commons.core.bounded import truncate_utf8
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.domain.work_state import (
    MAX_ACCEPTANCES,
    MAX_BLOCKING_DEPENDENCIES,
    MAX_REVIEW_GAPS,
    MAX_RUNS,
    AcceptanceState,
    AcceptanceView,
    EvidenceState,
    FreshnessState,
    NextAction,
    ReviewGapKind,
    ReviewLoopGap,
    RunPhase,
    RunView,
    WorkHealth,
    WorkHealthState,
    WorkSourceGap,
)

_TERMINAL_TASK_STATES = frozenset({"accepted", "cancelled"})
_TERMINAL_RUN_PHASES = frozenset(
    {
        RunPhase.SUCCEEDED,
        RunPhase.FAILED,
        RunPhase.CANCELLED,
        RunPhase.TIMED_OUT,
        RunPhase.NEEDS_OPERATOR,
    }
)
_FAILURE_PHASES = frozenset({RunPhase.FAILED, RunPhase.TIMED_OUT, RunPhase.NEEDS_OPERATOR})
_ATTENTION_PHASES = frozenset(
    {RunPhase.INPUT_NEEDED, RunPhase.FAILED, RunPhase.TIMED_OUT, RunPhase.NEEDS_OPERATOR}
)
_PHASES = {
    "requested": RunPhase.REQUESTED,
    "reserved": RunPhase.RESERVED,
    "launching": RunPhase.LAUNCHING,
    "active": RunPhase.RUNNING,
    "running": RunPhase.RUNNING,
    "cancel_requested": RunPhase.CANCELLATION_REQUESTED,
    "input_needed": RunPhase.INPUT_NEEDED,
    "succeeded": RunPhase.SUCCEEDED,
    "failed": RunPhase.FAILED,
    "cancelled": RunPhase.CANCELLED,
    "timed_out": RunPhase.TIMED_OUT,
    "needs_operator": RunPhase.NEEDS_OPERATOR,
}
_KNOWN_PROFILES = {
    "codex-builder": "codex",
    "codex-independent-reviewer": "codex",
    "claude-builder": "claude",
    "claude-independent-reviewer": "claude",
}
MAX_ATTEMPT_INPUTS = MAX_RUNS * 4
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class AttemptLike(Protocol):
    def as_dict(self) -> Mapping[str, Any]: ...


class WorkMetricsInputError(ValueError):
    """A source cannot be joined without violating a bounded read contract."""


def build_work_health(
    snapshot: ProjectSnapshot,
    attempts: Iterable[Mapping[str, Any] | AttemptLike] = (),
    *,
    generated_at: str,
    stale_after_seconds: int = 60,
    graph: Mapping[str, Any] | None = None,
) -> WorkHealth:
    """Join canonical and operational read state without persisting a new truth.

    ``generated_at`` is injected so replay and tests stay deterministic.  An
    optional graph contributes only freshness/truncation evidence; canonical
    task dependencies remain the authoritative source of dependency identity.
    """

    now = _timestamp(generated_at, "generated_at")
    if stale_after_seconds < 0:
        raise WorkMetricsInputError("stale_after_seconds cannot be negative")
    if len(snapshot.tasks) > MAX_ACCEPTANCES:
        raise WorkMetricsInputError(f"task input exceeds the {MAX_ACCEPTANCES}-item bound")
    if len(snapshot.delegations) > MAX_RUNS:
        raise WorkMetricsInputError(f"delegation input exceeds the {MAX_RUNS}-item bound")

    bounded_attempts = tuple(islice(iter(attempts), MAX_ATTEMPT_INPUTS + 1))
    if len(bounded_attempts) > MAX_ATTEMPT_INPUTS:
        raise WorkMetricsInputError("attempt input exceeds its bounded join limit")
    attempt_values = tuple(_attempt_mapping(value) for value in bounded_attempts)
    latest_attempts = _latest_attempts(attempt_values)
    dependency_evidence = {
        task_id: _dependencies(task) for task_id, task in sorted(snapshot.tasks.items())
    }
    freshness, source_updated_at = _freshness(
        snapshot,
        attempt_values,
        graph=graph,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    source_gaps = _source_gaps(
        snapshot,
        attempt_values,
        graph,
        dependency_evidence=dependency_evidence,
        now=now,
    )
    if freshness is FreshnessState.FRESH and {
        WorkSourceGap.GRAPH_FRESHNESS_MISSING,
        WorkSourceGap.SOURCE_TIMESTAMP_INVALID,
        WorkSourceGap.SOURCE_TIMESTAMP_FUTURE,
        WorkSourceGap.SOURCE_TIMESTAMP_ORDER_INVALID,
    } & set(source_gaps):
        freshness = FreshnessState.UNKNOWN

    acceptances: list[AcceptanceView] = []
    review_gaps: list[ReviewLoopGap] = []
    for task_id, task in sorted(snapshot.tasks.items()):
        acceptance, gap = _acceptance_view(snapshot, task_id, task)
        acceptances.append(acceptance)
        if gap is not None:
            review_gaps.append(gap)
    if len(review_gaps) > MAX_REVIEW_GAPS:
        raise WorkMetricsInputError(f"review gaps exceed the {MAX_REVIEW_GAPS}-item bound")

    runs = tuple(
        _run_view(
            snapshot,
            delegation_id,
            delegation,
            latest_attempts.get(delegation_id),
            dependency_evidence=dependency_evidence,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
        for delegation_id, delegation in sorted(snapshot.delegations.items())
    )
    acceptances_tuple = tuple(acceptances)
    review_gaps_tuple = tuple(review_gaps)
    stale_count = sum(run.evidence_state is EvidenceState.STALE for run in runs) + sum(
        acceptance.evidence_state is EvidenceState.STALE for acceptance in acceptances_tuple
    )
    partial_count = sum(
        bool(run.missing_fields)
        or run.evidence_state in {EvidenceState.MISSING, EvidenceState.PARTIAL}
        for run in runs
    ) + sum(
        acceptance.evidence_state in {EvidenceState.MISSING, EvidenceState.PARTIAL}
        for acceptance in acceptances_tuple
    )
    attention_count = sum(run.awaits_human for run in runs) + len(review_gaps_tuple)
    blocked_task_count = sum(
        str(task.get("state", "")) == "blocked" for task in snapshot.tasks.values()
    )
    failure_count = sum(run.phase in _FAILURE_PHASES for run in runs)

    if not snapshot.tasks and not snapshot.delegations and not source_gaps:
        state = WorkHealthState.EMPTY
        evidence_state = EvidenceState.MISSING
    elif source_gaps or partial_count or freshness is FreshnessState.UNKNOWN:
        state = WorkHealthState.PARTIAL
        evidence_state = EvidenceState.PARTIAL
    elif freshness is FreshnessState.STALE or stale_count:
        state = WorkHealthState.STALE
        evidence_state = EvidenceState.STALE
    elif attention_count or blocked_task_count or failure_count:
        state = WorkHealthState.ATTENTION_REQUIRED
        evidence_state = EvidenceState.COMPLETE
    else:
        state = WorkHealthState.HEALTHY
        evidence_state = EvidenceState.COMPLETE

    return WorkHealth(
        generated_at=generated_at,
        source_updated_at=source_updated_at,
        state=state,
        freshness=freshness,
        evidence_state=evidence_state,
        task_count=len(snapshot.tasks),
        run_count=len(runs),
        attention_count=attention_count,
        blocked_task_count=blocked_task_count,
        stale_evidence_count=stale_count,
        terminal_failure_count=failure_count,
        runs=runs,
        acceptances=acceptances_tuple,
        review_gaps=review_gaps_tuple,
        source_gaps=source_gaps,
    )


def _attempt_mapping(value: Mapping[str, Any] | AttemptLike) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    mapped = value.as_dict()
    if not isinstance(mapped, Mapping):
        raise WorkMetricsInputError("attempt as_dict() must return a mapping")
    return mapped


def _latest_attempts(attempts: tuple[Mapping[str, Any], ...]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    keys: dict[str, tuple[int, str, str, str, str, str]] = {}
    for attempt in attempts:
        correlation = _mapping(attempt.get("correlation"))
        delegation_id = _safe_identifier(correlation.get("delegation_id"))
        if not delegation_id:
            continue
        number = _positive_int(attempt.get("number")) or 0
        updated_at = _optional_string(attempt.get("updated_at")) or ""
        attempt_id = _safe_identifier(attempt.get("attempt_id")) or ""
        key = (
            number,
            updated_at,
            attempt_id,
            _optional_string(attempt.get("state")) or "",
            _optional_string(attempt.get("profile_id")) or "",
            _optional_string(attempt.get("provider")) or "",
        )
        if key > keys.get(delegation_id, (-1, "", "", "", "", "")):
            latest[delegation_id] = attempt
            keys[delegation_id] = key
    return latest


def _run_view(
    snapshot: ProjectSnapshot,
    delegation_id: str,
    delegation: Mapping[str, Any],
    attempt: Mapping[str, Any] | None,
    *,
    dependency_evidence: Mapping[str, tuple[tuple[str, ...], bool, bool]],
    now: datetime,
    stale_after_seconds: int,
) -> RunView:
    target = _mapping(delegation.get("target_ref"))
    task_id = _optional_string(target.get("id")) if target.get("kind") == "task" else None
    task = snapshot.tasks.get(task_id) if task_id else None
    task_dependencies, dependencies_valid, dependencies_truncated = dependency_evidence.get(
        task_id or "", ((), True, False)
    )
    all_dependencies = tuple(
        sorted(
            str(dependency)
            for dependency in task_dependencies
            if str(dependency) not in snapshot.tasks
            or str(snapshot.tasks[str(dependency)].get("state", "")) not in _TERMINAL_TASK_STATES
        )
    )
    dependencies = all_dependencies
    agent_id = _optional_string(delegation.get("agent_id"))
    agent = snapshot.agents.get(agent_id) if agent_id else None
    role_name = _display_text(agent.get("name"), maximum_bytes=160) if agent is not None else None
    canonical_profile = _optional_string(delegation.get("target_profile"))
    role_profile = _optional_string(agent.get("profile_id")) if agent is not None else None
    attempt_profile = _optional_string(attempt.get("profile_id")) if attempt else None
    raw_profile_id = attempt_profile or canonical_profile or role_profile
    provider = _KNOWN_PROFILES.get(raw_profile_id or "")
    profile_id = raw_profile_id if provider is not None else None
    attempt_provider = _optional_string(attempt.get("provider")) if attempt else None

    missing: list[str] = []
    if task_id and task is None:
        missing.append("task")
    if agent_id and agent is None:
        missing.append("agent")
    if raw_profile_id is None:
        missing.append("profile_id")
    elif provider is None:
        missing.append("known_profile")
    if attempt_provider is not None and provider is not None and attempt_provider != provider:
        missing.append("provider_profile_match")
    if canonical_profile and attempt_profile and canonical_profile != attempt_profile:
        missing.append("attempt_profile_match")
    if canonical_profile and role_profile and canonical_profile != role_profile:
        missing.append("role_profile_match")
    if not dependencies_valid:
        missing.append("task_dependencies")
    if dependencies_truncated:
        missing.append("blocking_dependencies_truncated")

    raw_canonical_state = _optional_string(delegation.get("state")) or "unknown"
    canonical_state = raw_canonical_state if raw_canonical_state in _PHASES else "unknown"
    attempt_state = _optional_string(attempt.get("state")) if attempt else None
    phase = _PHASES.get(attempt_state or canonical_state, RunPhase.UNKNOWN)
    if phase is RunPhase.UNKNOWN:
        missing.append("known_phase")
    if attempt is None and canonical_state in {
        "active",
        "input_needed",
        "succeeded",
        "failed",
        "timed_out",
        "needs_operator",
    }:
        missing.append("attempt")
    if attempt is not None:
        if _safe_identifier(attempt.get("attempt_id")) is None:
            missing.append("attempt_id")
        if _positive_int(attempt.get("number")) is None:
            missing.append("attempt_number")
        if attempt_state is None:
            missing.append("attempt_state")
        if attempt_profile is None:
            missing.append("attempt_profile_id")
        if attempt_provider is None:
            missing.append("attempt_provider")
    canonical_phase = _PHASES.get(canonical_state, RunPhase.UNKNOWN)
    attempt_phase = _PHASES.get(attempt_state, RunPhase.UNKNOWN) if attempt_state else None
    states_coherent = attempt_phase is None or _states_coherent(canonical_phase, attempt_phase)
    if not states_coherent:
        missing.append("canonical_attempt_state_match")
        # Canonical lifecycle is authoritative.  A provider process terminal
        # cannot be rendered as completed work before its MCP terminal result
        # produced the matching canonical transition.
        phase = canonical_phase
    attempt_id = _safe_identifier(attempt.get("attempt_id")) if attempt else None
    attempt_number = _positive_int(attempt.get("number")) if attempt else None
    started_at = _timestamp_string(attempt.get("created_at")) if attempt else None
    updated_at = (
        _timestamp_string(attempt.get("updated_at"))
        if attempt
        else _timestamp_string(delegation.get("recorded_at"))
    )
    if attempt is not None and started_at is None:
        missing.append("attempt_created_at")
    if attempt is not None and updated_at is None:
        missing.append("attempt_updated_at")
    finished_at = updated_at if phase in _TERMINAL_RUN_PHASES else None
    duration, timestamp_order_valid = _duration_seconds(
        started_at, updated_at, finished_at, now=now
    )
    if not timestamp_order_valid:
        missing.append("attempt_timestamp_order")
        parsed_start = _optional_timestamp(started_at)
        parsed_update = _optional_timestamp(updated_at)
        if parsed_start is not None and parsed_start > now:
            started_at = None
        if parsed_update is not None and (
            parsed_update > now or (parsed_start is not None and parsed_update < parsed_start)
        ):
            updated_at = None
            finished_at = None
    freshness = (
        _run_freshness(
            delegation,
            task,
            agent,
            attempt,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
        if timestamp_order_valid
        else FreshnessState.UNKNOWN
    )
    if freshness is FreshnessState.UNKNOWN:
        missing.append("freshness")
    awaits_human = phase in _ATTENTION_PHASES or not states_coherent
    next_action = _run_next_action(phase, dependencies, missing)
    if freshness is FreshnessState.STALE:
        evidence_state = EvidenceState.STALE
    elif missing:
        evidence_state = EvidenceState.PARTIAL
    else:
        evidence_state = EvidenceState.COMPLETE
    return RunView(
        delegation_id=delegation_id,
        task_id=task_id,
        agent_id=agent_id,
        role_name=role_name,
        provider=provider,
        profile_id=profile_id,
        canonical_state=canonical_state,
        phase=phase,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        started_at=started_at,
        updated_at=updated_at,
        finished_at=finished_at,
        duration_seconds=duration,
        awaits_human=awaits_human,
        blocking_dependencies=dependencies,
        next_action=next_action,
        freshness=freshness,
        evidence_state=evidence_state,
        missing_fields=tuple(sorted(set(missing))),
    )


def _run_next_action(
    phase: RunPhase, dependencies: tuple[str, ...], missing: list[str]
) -> NextAction:
    if missing:
        return NextAction.INSPECT_MISSING_EVIDENCE
    if dependencies:
        return NextAction.RESOLVE_DEPENDENCIES
    if phase in {RunPhase.INPUT_NEEDED, RunPhase.NEEDS_OPERATOR}:
        return NextAction.ANSWER_OPERATOR_REQUEST
    if phase in {RunPhase.FAILED, RunPhase.TIMED_OUT}:
        return NextAction.INSPECT_FAILURE
    if phase in {
        RunPhase.REQUESTED,
        RunPhase.RESERVED,
        RunPhase.LAUNCHING,
        RunPhase.RUNNING,
        RunPhase.CANCELLATION_REQUESTED,
    }:
        return NextAction.WAIT_FOR_RUN
    if phase is RunPhase.SUCCEEDED:
        return NextAction.REQUEST_REVIEW
    return NextAction.NONE


def _acceptance_view(
    snapshot: ProjectSnapshot, task_id: str, task: Mapping[str, Any]
) -> tuple[AcceptanceView, ReviewLoopGap | None]:
    task_state = _optional_string(task.get("state")) or "unknown"
    task_revision = _optional_string(task.get("effective_revision") or task.get("revision"))
    if task_revision is None:
        return (
            AcceptanceView(
                task_id=task_id,
                task_state=task_state,
                task_revision="unknown",
                state=AcceptanceState.EVIDENCE_INCOMPLETE,
                review_id=None,
                review_state=None,
                review_revision=None,
                review_target_revision=None,
                independent=None,
                stale=None,
                next_action=NextAction.INSPECT_MISSING_EVIDENCE,
                evidence_state=EvidenceState.MISSING,
                missing_fields=("task_revision",),
            ),
            ReviewLoopGap(
                task_id=task_id,
                task_revision="unknown",
                kind=ReviewGapKind.REVIEW_EVIDENCE_MISSING,
                review_id=None,
                review_revision=None,
                next_action=NextAction.INSPECT_MISSING_EVIDENCE,
            ),
        )
    reviews = sorted(
        (
            review
            for review in snapshot.reviews.values()
            if _mapping(review.get("target_ref")) == {"kind": "task", "id": task_id}
        ),
        key=lambda review: (
            _optional_string(review.get("recorded_at")) or "",
            _optional_string(review.get("id") or review.get("review_id")) or "",
        ),
    )
    acceptance_binding = _mapping(task.get("acceptance_review"))
    acceptance_ref = _mapping(acceptance_binding.get("ref"))
    accepted_review_id = (
        _optional_string(acceptance_ref.get("id"))
        if acceptance_ref.get("kind") == "review"
        else None
    )
    review = (
        snapshot.reviews.get(accepted_review_id)
        if task_state == "accepted" and accepted_review_id
        else (reviews[-1] if reviews else None)
    )
    if task_state == "cancelled":
        return _acceptance_result(
            task_id,
            task_state,
            task_revision,
            review,
            state=AcceptanceState.CANCELLED,
            action=NextAction.NONE,
            evidence=EvidenceState.COMPLETE,
        )
    if task_state not in {"completed", "review", "accepted"}:
        return _acceptance_result(
            task_id,
            task_state,
            task_revision,
            review,
            state=AcceptanceState.NOT_READY,
            action=(
                NextAction.RESOLVE_DEPENDENCIES
                if task_state == "blocked"
                else NextAction.START_READY_WORK
            ),
            evidence=EvidenceState.COMPLETE,
        )
    if review is None:
        missing_accepted_binding = task_state == "accepted"
        gap = ReviewLoopGap(
            task_id=task_id,
            task_revision=task_revision,
            kind=(
                ReviewGapKind.REVIEW_EVIDENCE_MISSING
                if missing_accepted_binding
                else ReviewGapKind.MISSING_REVIEW
            ),
            review_id=None,
            review_revision=None,
            next_action=(
                NextAction.INSPECT_MISSING_EVIDENCE
                if missing_accepted_binding
                else NextAction.REQUEST_REVIEW
            ),
        )
        view, _ = _acceptance_result(
            task_id,
            task_state,
            task_revision,
            None,
            state=(
                AcceptanceState.EVIDENCE_INCOMPLETE
                if missing_accepted_binding
                else AcceptanceState.REVIEW_REQUIRED
            ),
            action=(
                NextAction.INSPECT_MISSING_EVIDENCE
                if missing_accepted_binding
                else NextAction.REQUEST_REVIEW
            ),
            evidence=(
                EvidenceState.MISSING if missing_accepted_binding else EvidenceState.COMPLETE
            ),
        )
        return view, gap

    review_id = _optional_string(review.get("id") or review.get("review_id"))
    review_revision = _optional_string(review.get("effective_revision") or review.get("revision"))
    review_state = _optional_string(review.get("state"))
    target_revision = _optional_string(review.get("target_revision"))
    independent = review.get("independent") if isinstance(review.get("independent"), bool) else None
    stale = review.get("stale") if isinstance(review.get("stale"), bool) else None
    if task_state == "accepted":
        expected_review_revision = _optional_string(acceptance_binding.get("revision"))
        if accepted_review_id is None:
            missing_accepted = ("acceptance_review_ref",)
        elif expected_review_revision is None:
            missing_accepted = ("acceptance_review_revision",)
        elif expected_review_revision not in {
            review_revision,
            _optional_string(review.get("revision")),
        }:
            missing_accepted = ("acceptance_review_revision_match",)
        else:
            missing_accepted = ()
    else:
        missing_accepted = ()
    missing = (
        tuple(
            field
            for field, value in (
                ("review_id", review_id),
                ("review_revision", review_revision),
                ("review_state", review_state),
                ("review_target_revision", target_revision),
                ("review_independence", independent),
                ("review_stale", stale),
            )
            if value is None
        )
        + missing_accepted
    )
    if missing:
        gap = ReviewLoopGap(
            task_id=task_id,
            task_revision=task_revision,
            kind=ReviewGapKind.REVIEW_EVIDENCE_MISSING,
            review_id=review_id,
            review_revision=review_revision,
            next_action=NextAction.INSPECT_MISSING_EVIDENCE,
        )
        return (
            AcceptanceView(
                task_id=task_id,
                task_state=task_state,
                task_revision=task_revision,
                state=AcceptanceState.EVIDENCE_INCOMPLETE,
                review_id=review_id,
                review_state=review_state,
                review_revision=review_revision,
                review_target_revision=target_revision,
                independent=independent,
                stale=stale,
                next_action=NextAction.INSPECT_MISSING_EVIDENCE,
                evidence_state=EvidenceState.MISSING,
                missing_fields=missing,
            ),
            gap,
        )
    if target_revision != task_revision:
        kind = ReviewGapKind.TARGET_REVISION_MISMATCH
        action = NextAction.REQUEST_REVIEW
    elif stale:
        kind = ReviewGapKind.STALE_REVIEW
        action = NextAction.REQUEST_REVIEW
    elif independent is not True:
        kind = ReviewGapKind.NON_INDEPENDENT_REVIEW
        action = NextAction.REQUEST_REVIEW
    elif review_state == "changes_requested":
        kind = ReviewGapKind.CHANGES_REQUESTED
        action = NextAction.REVISE_WORK
    elif review_state == "requested":
        view, _ = _acceptance_result(
            task_id,
            task_state,
            task_revision,
            review,
            state=AcceptanceState.REVIEW_PENDING,
            action=NextAction.WAIT_FOR_REVIEW,
            evidence=EvidenceState.COMPLETE,
        )
        return view, None
    elif review_state == "approved":
        view, _ = _acceptance_result(
            task_id,
            task_state,
            task_revision,
            review,
            state=(
                AcceptanceState.ACCEPTED if task_state == "accepted" else AcceptanceState.APPROVED
            ),
            action=(NextAction.NONE if task_state == "accepted" else NextAction.ACCEPT_TASK),
            evidence=EvidenceState.COMPLETE,
        )
        return view, None
    else:
        kind = ReviewGapKind.REVIEW_EVIDENCE_MISSING
        action = NextAction.INSPECT_MISSING_EVIDENCE
    if task_state == "accepted":
        action = NextAction.INSPECT_MISSING_EVIDENCE
    gap = ReviewLoopGap(
        task_id=task_id,
        task_revision=task_revision,
        kind=kind,
        review_id=review_id,
        review_revision=review_revision,
        next_action=action,
    )
    view, _ = _acceptance_result(
        task_id,
        task_state,
        task_revision,
        review,
        state=(
            AcceptanceState.EVIDENCE_INCOMPLETE
            if task_state == "accepted" or kind is ReviewGapKind.REVIEW_EVIDENCE_MISSING
            else (
                AcceptanceState.CHANGES_REQUESTED
                if kind is ReviewGapKind.CHANGES_REQUESTED
                else AcceptanceState.REVIEW_REQUIRED
            )
        ),
        action=action,
        evidence=(
            EvidenceState.STALE
            if kind is ReviewGapKind.STALE_REVIEW
            else (
                EvidenceState.MISSING
                if kind is ReviewGapKind.REVIEW_EVIDENCE_MISSING
                else EvidenceState.COMPLETE
            )
        ),
    )
    return view, gap


def _acceptance_result(
    task_id: str,
    task_state: str,
    task_revision: str,
    review: Mapping[str, Any] | None,
    *,
    state: AcceptanceState,
    action: NextAction,
    evidence: EvidenceState,
) -> tuple[AcceptanceView, None]:
    return (
        AcceptanceView(
            task_id=task_id,
            task_state=task_state,
            task_revision=task_revision,
            state=state,
            review_id=(
                _optional_string(review.get("id") or review.get("review_id")) if review else None
            ),
            review_state=_optional_string(review.get("state")) if review else None,
            review_revision=(
                _optional_string(review.get("effective_revision") or review.get("revision"))
                if review
                else None
            ),
            review_target_revision=(
                _optional_string(review.get("target_revision")) if review else None
            ),
            independent=(
                review.get("independent")
                if review and isinstance(review.get("independent"), bool)
                else None
            ),
            stale=(
                review.get("stale") if review and isinstance(review.get("stale"), bool) else None
            ),
            next_action=action,
            evidence_state=evidence,
        ),
        None,
    )


def _freshness(
    snapshot: ProjectSnapshot,
    attempts: tuple[Mapping[str, Any], ...],
    *,
    graph: Mapping[str, Any] | None,
    now: datetime,
    stale_after_seconds: int,
) -> tuple[FreshnessState, str | None]:
    values: list[object] = []
    candidates: list[tuple[datetime, str]] = []
    for collection in (
        snapshot.tasks,
        snapshot.reviews,
        snapshot.delegations,
        snapshot.agents,
    ):
        for record in collection.values():
            value = record.get("recorded_at")
            values.append(value)
            parsed = _optional_timestamp(_optional_string(value))
            if isinstance(value, str) and parsed is not None and parsed <= now:
                candidates.append((parsed, value))
    for attempt in attempts:
        value = attempt.get("updated_at")
        values.append(value)
        parsed = _optional_timestamp(_optional_string(value))
        if isinstance(value, str) and parsed is not None and parsed <= now:
            candidates.append((parsed, value))
    if graph is not None:
        value = graph.get("generated_at")
        values.append(value)
        parsed = _optional_timestamp(_optional_string(value))
        if isinstance(value, str) and parsed is not None and parsed <= now:
            candidates.append((parsed, value))
    source_updated_at = (
        max(candidates, key=lambda item: (item[0], item[1]))[1] if candidates else None
    )
    return _freshness_state(
        values, now=now, stale_after_seconds=stale_after_seconds
    ), source_updated_at


def _source_gaps(
    snapshot: ProjectSnapshot,
    attempts: tuple[Mapping[str, Any], ...],
    graph: Mapping[str, Any] | None,
    *,
    dependency_evidence: Mapping[str, tuple[tuple[str, ...], bool, bool]],
    now: datetime,
) -> tuple[WorkSourceGap, ...]:
    gaps: set[WorkSourceGap] = set()
    if graph is not None:
        if _mapping(graph.get("limits")).get("truncated") is True:
            gaps.add(WorkSourceGap.GRAPH_TRUNCATED)
        graph_time = _optional_timestamp(_optional_string(graph.get("generated_at")))
        if graph_time is None:
            gaps.add(WorkSourceGap.GRAPH_FRESHNESS_MISSING)
        elif graph_time > now:
            gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_FUTURE)
    for collection in (
        snapshot.tasks,
        snapshot.reviews,
        snapshot.delegations,
        snapshot.agents,
    ):
        for record in collection.values():
            timestamp = record.get("recorded_at")
            parsed = _optional_timestamp(_optional_string(timestamp))
            if parsed is None:
                gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_INVALID)
            elif parsed > now:
                gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_FUTURE)
    for _, valid, truncated in dependency_evidence.values():
        if not valid:
            gaps.add(WorkSourceGap.TASK_DEPENDENCIES_INVALID)
        if truncated:
            gaps.add(WorkSourceGap.TASK_DEPENDENCIES_TRUNCATED)
    for attempt in attempts:
        correlation = _mapping(attempt.get("correlation"))
        delegation_id = _safe_identifier(correlation.get("delegation_id"))
        if delegation_id is None:
            gaps.add(WorkSourceGap.ATTEMPT_CORRELATION_MISSING)
        elif delegation_id not in snapshot.delegations:
            gaps.add(WorkSourceGap.ORPHAN_OPERATIONAL_ATTEMPT)
        if any(
            (
                _safe_identifier(attempt.get("attempt_id")) is None,
                _positive_int(attempt.get("number")) is None,
                _optional_string(attempt.get("state")) is None,
                _optional_string(attempt.get("profile_id")) is None,
                _optional_string(attempt.get("provider")) is None,
            )
        ):
            gaps.add(WorkSourceGap.ATTEMPT_EVIDENCE_MISSING)
        parsed_times: dict[str, datetime] = {}
        for field in ("created_at", "updated_at"):
            timestamp = attempt.get(field)
            parsed = _optional_timestamp(_optional_string(timestamp))
            if parsed is None:
                gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_INVALID)
            else:
                parsed_times[field] = parsed
                if parsed > now:
                    gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_FUTURE)
        if (
            "created_at" in parsed_times
            and "updated_at" in parsed_times
            and parsed_times["updated_at"] < parsed_times["created_at"]
        ):
            gaps.add(WorkSourceGap.SOURCE_TIMESTAMP_ORDER_INVALID)
    return tuple(sorted(gaps, key=str))


def _duration_seconds(
    start: str | None,
    updated: str | None,
    end: str | None,
    *,
    now: datetime,
) -> tuple[int | None, bool]:
    started = _optional_timestamp(start)
    if started is None:
        return None, True
    observed = _optional_timestamp(updated)
    finished = _optional_timestamp(end) or now
    if (
        started > now
        or (observed is not None and (observed > now or observed < started))
        or finished > now
        or finished < started
    ):
        return None, False
    return int((finished - started).total_seconds()), True


def _run_freshness(
    delegation: Mapping[str, Any],
    task: Mapping[str, Any] | None,
    agent: Mapping[str, Any] | None,
    attempt: Mapping[str, Any] | None,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> FreshnessState:
    values: list[object] = [delegation.get("recorded_at")]
    if task is not None:
        values.append(task.get("recorded_at"))
    if agent is not None:
        values.append(agent.get("recorded_at"))
    if attempt is not None:
        values.append(attempt.get("updated_at"))
    return _freshness_state(values, now=now, stale_after_seconds=stale_after_seconds)


def _freshness_state(
    values: Sequence[object], *, now: datetime, stale_after_seconds: int
) -> FreshnessState:
    if not values:
        return FreshnessState.UNKNOWN
    unknown = False
    stale = False
    for value in values:
        parsed = _optional_timestamp(_optional_string(value))
        if parsed is None or parsed > now:
            unknown = True
        elif int((now - parsed).total_seconds()) > stale_after_seconds:
            stale = True
    if stale:
        return FreshnessState.STALE
    if unknown:
        return FreshnessState.UNKNOWN
    return FreshnessState.FRESH


def _timestamp(value: str, field: str) -> datetime:
    parsed = _optional_timestamp(value)
    if parsed is None:
        raise WorkMetricsInputError(f"{field} must be an RFC 3339 timestamp")
    return parsed


def _optional_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _display_text(value: object, *, maximum_bytes: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    printable = "".join(character for character in value if ord(character) >= 32).strip()
    return truncate_utf8(printable, maximum_bytes) or None


def _timestamp_string(value: object) -> str | None:
    text = _optional_string(value)
    return text if _optional_timestamp(text) is not None else None


def _dependencies(task: Mapping[str, Any] | None) -> tuple[tuple[str, ...], bool, bool]:
    if task is None:
        return (), True, False
    value = task.get("dependencies", ())
    if isinstance(value, (str, bytes, bytearray, Mapping, set, frozenset)):
        return (), False, False
    try:
        bounded = tuple(islice(iter(value), MAX_BLOCKING_DEPENDENCIES + 1))
    except TypeError:
        return (), False, False
    truncated = len(bounded) > MAX_BLOCKING_DEPENDENCIES
    dependencies: list[str] = []
    valid = True
    for item in bounded[:MAX_BLOCKING_DEPENDENCIES]:
        safe = _safe_identifier(item)
        if safe is None:
            valid = False
            continue
        dependencies.append(safe)
    return tuple(dependencies), valid, truncated


def _safe_identifier(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _states_coherent(canonical: RunPhase, attempt: RunPhase) -> bool:
    if canonical is RunPhase.RUNNING:
        return attempt in {
            RunPhase.RESERVED,
            RunPhase.LAUNCHING,
            RunPhase.RUNNING,
            RunPhase.CANCELLATION_REQUESTED,
        }
    if canonical is RunPhase.REQUESTED:
        return attempt in {RunPhase.RESERVED, RunPhase.FAILED}
    if canonical is RunPhase.INPUT_NEEDED:
        return attempt in {RunPhase.RUNNING, RunPhase.NEEDS_OPERATOR}
    if canonical is RunPhase.CANCELLED:
        return attempt in {RunPhase.CANCELLED, RunPhase.FAILED}
    if canonical in _TERMINAL_RUN_PHASES:
        return attempt is canonical
    return canonical is attempt
