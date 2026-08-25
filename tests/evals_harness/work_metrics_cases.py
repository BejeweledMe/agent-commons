"""Pure L1 replay helpers for the closed W0 fixture DSL.

They intentionally derive only properties which the fixture can prove.  This
is not a production metric calculator and does not read a workspace or ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from .fixture_loader import (
    ActorKind,
    EventType,
    FixtureCase,
    FixtureEvent,
    FixtureExpectation,
    MetricId,
    MetricState,
    ReasonCode,
    ReviewVerdict,
    WorkMetricsFixture,
)


@dataclass(frozen=True, slots=True)
class SyntheticEvent:
    """A materialised content-free event whose time comes from the injected clock."""

    event_id: str
    event_type: EventType
    recorded_at: datetime
    actor_kind: ActorKind
    independent: bool | None
    verdict: ReviewVerdict | None
    reason_code: ReasonCode | None


@dataclass(frozen=True, slots=True)
class ReplayAssessment:
    """L1 properties needed to prevent metric claims from hiding bad input."""

    current_independent_review: bool
    review_is_stale: bool
    false_strict_acceptance: bool
    reordered_input: bool
    duplicate_retry: bool
    orphan_review: bool
    revision_mismatch: bool
    cas_conflict: bool


_REVIEW_TERMINALS: Final[frozenset[str]] = frozenset({"review.completed"})


def materialize_case(case: FixtureCase, fixed_now: datetime) -> tuple[SyntheticEvent, ...]:
    """Make a stable event trace without introducing prose or reading a ledger."""

    ordered = sorted(enumerate(case.events), key=lambda item: (item[1].offset_seconds, item[0]))
    return tuple(
        _materialize_event(case.case_id, source_index, event, fixed_now)
        for source_index, event in ordered
    )


def assess_case(case: FixtureCase, fixed_now: datetime) -> ReplayAssessment:
    """Replay the narrow W0 semantics needed for fixture-integrity regression tests."""

    trace = materialize_case(case, fixed_now)
    raw_offsets = tuple(event.offset_seconds for event in case.events)
    source_event_types = tuple(event.event_type for event in case.events)
    submitted = next((event for event in trace if event.event_type == "task.submitted"), None)
    review_requested = next(
        (event for event in trace if event.event_type == "review.requested"), None
    )
    review_completed = next(
        (event for event in trace if event.event_type in _REVIEW_TERMINALS), None
    )
    accepted = next((event for event in trace if event.event_type == "task.accepted"), None)
    corrected = any(event.event_type == "event.corrected" for event in trace)
    corrected_after_review = (
        corrected
        and review_completed is not None
        and any(
            event.event_type == "event.corrected"
            and event.recorded_at >= review_completed.recorded_at
            for event in trace
        )
    )
    retries = tuple(event for event in trace if event.event_type == "event.retry")
    binding = case.binding
    orphan_review = review_requested is not None and binding.task_ref != binding.target_task_ref
    revision_mismatch = review_requested is not None and (
        binding.task_revision != binding.request_target_revision
        or binding.request_target_revision != binding.completion_target_revision
        or binding.request_revision != binding.completion_expected_revision
    )
    review_join_valid = (
        submitted is not None
        and review_requested is not None
        and review_completed is not None
        and binding.task_ref is not None
        and binding.review_ref is not None
        and not orphan_review
        and not revision_mismatch
    )
    effective_task_revision = binding.effective_task_revision or binding.task_revision
    review_is_stale = review_requested is not None and (
        binding.request_target_revision != effective_task_revision or corrected_after_review
    )
    current_independent_review = (
        review_join_valid
        and review_requested.independent is True
        and submitted.recorded_at <= review_requested.recorded_at
        and not review_is_stale
    )
    acceptance_is_bound = (
        binding.acceptance_task_revision == effective_task_revision
        and binding.acceptance_review_ref == binding.review_ref
        and binding.acceptance_review_revision == binding.completion_revision
    )
    false_strict_acceptance = accepted is not None and (
        not current_independent_review
        or not acceptance_is_bound
        or review_completed is None
        or review_completed.verdict != "approved"
        or review_requested is None
        or review_requested.recorded_at > review_completed.recorded_at
        or accepted.recorded_at < review_completed.recorded_at
    )
    retry_positions = tuple(
        index for index, event_type in enumerate(source_event_types) if event_type == "event.retry"
    )
    submitted_position = next(
        (
            index
            for index, event_type in enumerate(source_event_types)
            if event_type == "task.submitted"
        ),
        None,
    )
    cas_conflict = bool(retry_positions) and (
        submitted_position is None
        or retry_positions[0] < submitted_position
        or binding.retry_expected_revision != effective_task_revision
    )
    return ReplayAssessment(
        current_independent_review=current_independent_review,
        review_is_stale=review_is_stale,
        false_strict_acceptance=false_strict_acceptance,
        reordered_input=raw_offsets != tuple(sorted(raw_offsets)),
        duplicate_retry=len(retries) > 1 and binding.retry_key is not None,
        orphan_review=orphan_review,
        revision_mismatch=revision_mismatch,
        cas_conflict=cas_conflict,
    )


def grade_case(case: FixtureCase, fixed_now: datetime) -> FixtureExpectation:
    """Evaluate one fixture expectation with pure, deterministic W0 rules."""

    trace = materialize_case(case, fixed_now)
    assessment = assess_case(case, fixed_now)
    metric_id = case.expectation.metric_id
    if metric_id == "current_review_coverage":
        submitted = any(event.event_type == "task.submitted" for event in trace)
        if not submitted:
            return _expectation(metric_id, "empty", None, None)
        return _expectation(metric_id, "complete", int(assessment.current_independent_review), 1)
    if metric_id == "review_disposition_latency":
        requested = next((event for event in trace if event.event_type == "review.requested"), None)
        completed = next((event for event in trace if event.event_type == "review.completed"), None)
        if requested is None or completed is None:
            return _expectation(metric_id, "not_measurable", None, None)
        return _expectation(
            metric_id,
            "complete",
            int((completed.recorded_at - requested.recorded_at).total_seconds()),
            1,
        )
    if metric_id == "review_queue_age":
        if not any(event.event_type == "task.submitted" for event in trace):
            return _expectation(metric_id, "not_measurable", None, None)
        return _expectation(metric_id, "unsupported", None, None)
    if metric_id == "handoff_acknowledgement_latency":
        created = next((event for event in trace if event.event_type == "handoff.created"), None)
        acknowledged = next(
            (event for event in trace if event.event_type == "handoff.acknowledged"), None
        )
        if created is None or acknowledged is None:
            return _expectation(metric_id, "unsupported", None, None)
        return _expectation(
            metric_id,
            "complete",
            int((acknowledged.recorded_at - created.recorded_at).total_seconds()),
            1,
        )
    if metric_id == "needs_operator_rate":
        terminal = tuple(
            event
            for event in trace
            if event.event_type
            in {
                "delegation.succeeded",
                "delegation.failed",
                "delegation.cancelled",
                "delegation.timed_out",
                "delegation.needs_operator",
            }
        )
        numerator = sum(event.event_type == "delegation.needs_operator" for event in terminal)
        return _expectation(metric_id, "complete", numerator, len(terminal))
    if metric_id == "false_strict_acceptance":
        accepted = any(event.event_type == "task.accepted" for event in trace)
        if not accepted:
            return _expectation(metric_id, "empty", None, None)
        return _expectation(metric_id, "complete", int(assessment.false_strict_acceptance), 1)
    raise AssertionError(f"W0 fixture uses an ungraded metric: {metric_id}")


def _expectation(
    metric_id: MetricId,
    state: MetricState,
    numerator: int | None,
    denominator: int | None,
) -> FixtureExpectation:
    return FixtureExpectation(
        metric_id=metric_id,
        state=state,
        numerator=numerator,
        denominator=denominator,
    )


def materialize_fixture(
    fixture: WorkMetricsFixture,
) -> tuple[tuple[str, tuple[SyntheticEvent, ...]], ...]:
    """Materialise every case in a deterministic identifier order."""

    return tuple(
        (case.case_id, materialize_case(case, fixture.fixed_now))
        for case in sorted(fixture.cases, key=lambda item: item.case_id)
    )


def _materialize_event(
    case_id: str,
    source_index: int,
    event: FixtureEvent,
    fixed_now: datetime,
) -> SyntheticEvent:
    return SyntheticEvent(
        event_id=f"evt.synthetic.{case_id}.{source_index:03d}",
        event_type=event.event_type,
        recorded_at=fixed_now + timedelta(seconds=event.offset_seconds),
        actor_kind=event.actor_kind,
        independent=event.independent,
        verdict=event.verdict,
        reason_code=event.reason_code,
    )
