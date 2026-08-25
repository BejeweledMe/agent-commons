from __future__ import annotations

from .fixture_loader import FixtureCase, load_work_metrics_fixture
from .work_metrics_cases import assess_case, materialize_fixture


def _case(case_id: str) -> FixtureCase:
    fixture = load_work_metrics_fixture()
    return next(case for case in fixture.cases if case.case_id == case_id)


def test_corpus_has_all_required_w0_boundary_cases_and_terminal_states() -> None:
    fixture = load_work_metrics_fixture()
    names = {case.case_id for case in fixture.cases}
    events = {event.event_type for case in fixture.cases for event in case.events}
    reason_codes = {
        event.reason_code
        for case in fixture.cases
        for event in case.events
        if event.reason_code is not None
    }

    assert {
        "review_current_pair",
        "review_missing_pair",
        "review_stale_pair",
        "review_nonindependent_pair",
        "review_changes_requested",
        "handoff_acknowledged",
        "handoff_open_fresh",
        "handoff_open_aged",
        "delegation_terminals",
        "empty_review_queue",
        "strict_acceptance_valid",
        "strict_acceptance_invalid",
        "correction_and_retry",
        "reordered_retry",
    } <= names
    assert {
        "delegation.succeeded",
        "delegation.failed",
        "delegation.cancelled",
        "delegation.timed_out",
        "delegation.needs_operator",
    } <= events
    assert {"provider_unavailable", "provider_auth", "unknown"} <= reason_codes


def test_l1_replay_keeps_staleness_strict_acceptance_and_retry_visible() -> None:
    fixture = load_work_metrics_fixture()

    current = assess_case(_case("review_current_pair"), fixture.fixed_now)
    stale = assess_case(_case("review_stale_pair"), fixture.fixed_now)
    valid = assess_case(_case("strict_acceptance_valid"), fixture.fixed_now)
    invalid = assess_case(_case("strict_acceptance_invalid"), fixture.fixed_now)
    retry = assess_case(_case("correction_and_retry"), fixture.fixed_now)
    reordered = assess_case(_case("reordered_retry"), fixture.fixed_now)

    assert current.current_independent_review is True
    assert stale.review_is_stale is True
    assert valid.false_strict_acceptance is False
    assert invalid.false_strict_acceptance is True
    assert retry.duplicate_retry is True
    assert reordered.reordered_input is True


def test_l1_never_counts_missing_or_nonindependent_review_as_a_current_pair() -> None:
    fixture = load_work_metrics_fixture()

    missing = assess_case(_case("review_missing_pair"), fixture.fixed_now)
    nonindependent = assess_case(_case("review_nonindependent_pair"), fixture.fixed_now)
    changes_requested = assess_case(_case("review_changes_requested"), fixture.fixed_now)

    assert missing.current_independent_review is False
    assert nonindependent.current_independent_review is False
    assert changes_requested.current_independent_review is False


def test_materialisation_is_repeatable_and_uses_only_the_injected_clock() -> None:
    fixture = load_work_metrics_fixture()
    first = materialize_fixture(fixture)
    second = materialize_fixture(fixture)

    assert first == second
    assert tuple(case_id for case_id, _ in first) == tuple(sorted(case_id for case_id, _ in first))
    assert all(event.recorded_at.tzinfo is not None for _, events in first for event in events)
