from __future__ import annotations

from agent_commons.evals.catalog import (
    CATALOG_VERSION,
    EVAL_CATALOG,
    aggregate_metrics,
    run_catalog,
)
from agent_commons.evals.fake_provider import DeterministicFakeProvider
from agent_commons.evals.model import EvalResult, EvalStatus


def test_catalog_is_a_stable_25_case_cross_section_of_requested_workflows() -> None:
    assert CATALOG_VERSION == "2026-08-wave4-v1"
    assert len(EVAL_CATALOG) == 25
    assert len({case.case_id for case in EVAL_CATALOG}) == 25
    assert {case.scenario for case in EVAL_CATALOG} >= {
        "state_isolation",
        "typed_references",
        "claims",
        "dag",
        "input",
        "resume",
        "cancel",
        "crash_recovery",
        "path_enforcement",
        "compact_read",
        "council",
        "routing",
        "budget",
        "stale_state",
        "secret_rejection",
    }


def test_catalog_execution_is_offline_and_never_turns_deferred_cases_green() -> None:
    results = run_catalog()
    by_id = {result.case_id: result for result in results}

    assert len(results) == 25
    assert all(result.status is EvalStatus.PASSED for result in results[:8])
    assert by_id["eval.claims-overlap"].status is EvalStatus.PLANNED
    assert by_id["eval.council-dissent"].status is EvalStatus.UNSUPPORTED
    assert by_id["eval.claims-overlap"].outcome_code != "passed"
    assert by_id["eval.council-dissent"].outcome_code != "passed"
    assert all(len(result.evidence_digest) == 64 for result in results[:8])


def test_fake_provider_is_deterministic_and_accepts_only_catalog_cases() -> None:
    provider = DeterministicFakeProvider()
    first = provider.run(EVAL_CATALOG[0])
    second = provider.run(EVAL_CATALOG[0])

    assert first == second
    assert first.digest == second.digest
    assert first.action_codes == ("isolated_fixture", "deterministic_grader")


def test_metrics_are_low_cardinality_and_exclude_unexecuted_cases_from_pass_rate() -> None:
    results = (
        EvalResult("eval.one", EvalStatus.PASSED, "passed", 3, provider_units=1),
        EvalResult("eval.two", EvalStatus.FAILED, "assertion_failed", 5, needs_operator=True),
        EvalResult("eval.three", EvalStatus.PLANNED, "planned_capability", 0),
        EvalResult("eval.four", EvalStatus.UNSUPPORTED, "unsupported_offline_harness", 0),
    )

    metrics = aggregate_metrics(results)

    assert metrics.executed_cases == 2
    assert metrics.pass_at_1 == 0.5
    assert metrics.pass_power_k == 0.0
    assert metrics.needs_operator_rate == 0.5
    assert metrics.provider_units_total == 1
    assert metrics.latency_ms_total == 8


def test_result_serialization_has_only_the_documented_privacy_safe_fields() -> None:
    result = EvalResult(
        "eval.safe",
        EvalStatus.PASSED,
        "passed",
        1,
        evidence_digest="a" * 64,
    )

    assert set(result.as_dict()) == {
        "case_id",
        "status",
        "outcome_code",
        "latency_ms",
        "provider_units",
        "needs_operator",
        "handoff_loops",
        "evidence_digest",
    }
