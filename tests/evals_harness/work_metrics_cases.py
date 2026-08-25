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
    current_independent_review = (
        review_requested is not None
        and review_requested.independent is True
        and review_completed is not None
        and review_completed.verdict == "approved"
        and submitted is not None
        and submitted.recorded_at <= review_requested.recorded_at <= review_completed.recorded_at
        and not corrected_after_review
    )
    false_strict_acceptance = accepted is not None and (
        review_requested is None
        or review_requested.independent is not True
        or review_completed is None
        or review_completed.verdict != "approved"
        or submitted is None
        or submitted.recorded_at > review_requested.recorded_at
        or review_requested.recorded_at > review_completed.recorded_at
        or accepted.recorded_at < review_completed.recorded_at
    )
    return ReplayAssessment(
        current_independent_review=current_independent_review,
        review_is_stale=corrected_after_review,
        false_strict_acceptance=false_strict_acceptance,
        reordered_input=raw_offsets != tuple(sorted(raw_offsets)),
        duplicate_retry=len(retries) > 0,
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
