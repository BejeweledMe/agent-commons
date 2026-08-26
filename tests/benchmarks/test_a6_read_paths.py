from __future__ import annotations

import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain import envelopes, lifecycle, projection
from agent_commons.domain.projection import project_events
from agent_commons.services import manager as manager_module
from benchmarks.benchmark_a6_read_paths import (
    _PHASE_LABELS,
    _assert_matching_extended_replay,
    _assert_stable_reuse_counts,
    _EnvelopeReuseAccount,
    _extended_replay_integrity,
    _instrumented_projection_context,
    _instrumented_replay_sample,
    _PhaseCollector,
    _replay_integrity,
    _reuse_task_created,
    _simulated_envelope_reuse,
    _write_workspace,
    profile_projection_components,
    profile_read_paths,
    profile_workspace_projection_components,
    profile_workspace_read_paths,
    reuse_fixture,
)
from benchmarks.benchmark_projection import workload


def test_read_path_profile_keeps_the_three_paths_and_two_pass_workload() -> None:
    profile = profile_read_paths(event_count=16, repeats=1)

    assert profile["schema"] == "agent_commons.a6_read_path_profile.v2"
    assert profile["fixed_point_passes"] == 2
    assert set(profile["paths"]) == {
        "commons_manager_snapshot",
        "verified_sqlite_read",
        "in_memory_replay",
    }
    assert set(profile["components"]) == {
        "sqlite_sync",
        "sqlite_read_projection",
        "project_events",
    }
    for report in profile["paths"].values():
        assert report["median_elapsed_seconds"] >= 0
        assert report["max_peak_allocated_bytes"] > 0
        assert len(report["samples"]) == 1


def test_workspace_profile_uses_the_same_verified_read_boundary() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _write_workspace(root, event_count=16)
        profile = profile_workspace_read_paths(
            root / "repo",
            state_root=root / "state",
            repeats=1,
        )

    assert profile["source"] == "existing_workspace"
    assert profile["event_count"] == 16
    assert profile["fixed_point_passes"] == 2


def test_component_profile_avoids_the_composite_canonical_snapshot() -> None:
    profile = profile_projection_components(event_count=16, repeats=1)

    assert set(profile["paths"]) == {"in_memory_replay"}
    assert profile["paths"]["in_memory_replay"] == profile["components"]["project_events"]
    phase_profile = profile["replay_phase_profile"]
    overhead = profile["instrumentation_overhead"]
    assert set(phase_profile["calls_per_sample"]) == set(_PHASE_LABELS)
    assert len(phase_profile["samples"]) == 1
    assert len(overhead["samples"]) == 1
    assert (
        overhead["samples"][0]["instrumented_elapsed_seconds"]
        == phase_profile["samples"][0]["root_elapsed_seconds"]
    )


def test_workspace_component_profile_uses_the_verified_read_boundary() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        _write_workspace(root, event_count=16)
        profile = profile_workspace_projection_components(
            root / "repo",
            state_root=root / "state",
            repeats=1,
        )

    assert profile["source"] == "existing_workspace"
    assert set(profile["components"]) == {
        "sqlite_sync",
        "sqlite_read_projection",
        "project_events",
    }


def test_instrumented_replay_preserves_two_pass_digest_order_and_phase_accounting() -> None:
    events = tuple(workload(event_count=32, expected_passes=2))
    baseline = project_events(events)
    expected = _replay_integrity(baseline)

    sample = _instrumented_replay_sample(
        events,
        known_manifest_ids=None,
        expected_integrity=expected,
    )

    assert sample["snapshot_sha256"] == expected["snapshot_sha256"]
    assert sample["known_event_ids"] == expected["known_event_ids"]
    assert sample["applied_event_ids"] == expected["applied_event_ids"]
    assert sample["fixed_point_passes"] == 2
    assert sample["phases"]["fixed_point_planning"]["calls"] == 1
    assert sample["phases"]["project_events_once.probe"]["calls"] == 1
    assert sample["phases"]["project_events_once.normal"]["calls"] == 1
    assert sample["phases"]["project_events_once.final"]["calls"] == 0
    accounted = sum(phase["exclusive_elapsed_seconds"] for phase in sample["phases"].values())
    assert accounted + sample["residual_elapsed_seconds"] <= sample["root_elapsed_seconds"] + 1e-9


def test_instrumented_replay_labels_a_final_fixed_point_pass() -> None:
    events = tuple(workload(event_count=32, expected_passes=3))
    sample = _instrumented_replay_sample(events, known_manifest_ids=None)

    assert sample["fixed_point_passes"] == 3
    assert sample["phases"]["project_events_once.probe"]["calls"] == 1
    assert sample["phases"]["project_events_once.normal"]["calls"] == 1
    assert sample["phases"]["project_events_once.final"]["calls"] == 1


def test_instrumentation_restores_projection_globals_after_a_replay_error() -> None:
    events = tuple(workload(event_count=16, expected_passes=2))
    original_invalidation = projection.derive_invalidation_state
    original_once = projection._project_events_once
    original_transition = lifecycle.validate_transition

    def fail_invalidation(*_args: object, **_keywords: object) -> object:
        raise RuntimeError("intentional instrumentation test failure")

    projection.derive_invalidation_state = fail_invalidation
    try:
        with pytest.raises(RuntimeError, match="intentional instrumentation"):
            _instrumented_replay_sample(events, known_manifest_ids=None)
        assert projection.derive_invalidation_state is fail_invalidation
        assert projection._project_events_once is original_once
        assert lifecycle.validate_transition is original_transition
    finally:
        projection.derive_invalidation_state = original_invalidation


def test_instrumentation_bypasses_the_collector_in_an_outside_thread() -> None:
    events = tuple(workload(event_count=16, expected_passes=2))
    baseline = project_events(events)
    snapshots = []
    errors: list[BaseException] = []

    def replay_outside_instrumented_context() -> None:
        try:
            snapshots.append(project_events(events))
        except BaseException as exc:  # pragma: no cover - asserted below if a worker fails
            errors.append(exc)

    collector = _PhaseCollector()
    with _instrumented_projection_context(collector):
        worker = threading.Thread(target=replay_outside_instrumented_context)
        worker.start()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    assert len(snapshots) == 1
    assert _replay_integrity(snapshots[0]) == _replay_integrity(baseline)
    assert all(calls == 0 for calls in collector.phase_call_counts.values())


# --- A6.5 envelope-reuse characterization -------------------------------------
#
# Ground-truth rule: every scenario replays uninstrumented FIRST and the
# instrumented run is compared against that result.  Digests are never
# hardcoded — the input order alone changes them.  What IS hardcoded is the
# instrumentation's own accounting, because those counters are the measurement.


def _evt(number: int) -> str:
    return f"evt.{number:026d}"


def _task(number: int) -> str:
    return f"task.{number:026d}"


def _replay_with_reuse(
    events: tuple[Any, ...], *, key_strategy: str = "occurrence"
) -> tuple[Any, _EnvelopeReuseAccount]:
    """Replay one fixture twice: uninstrumented for truth, then simulated for counters."""

    expected = _extended_replay_integrity(project_events(events))
    account = _EnvelopeReuseAccount(key_strategy=key_strategy)
    with _simulated_envelope_reuse(account):
        snapshot = projection.project_events(events)
    _assert_matching_extended_replay(_extended_replay_integrity(snapshot), expected)
    return snapshot, account


def test_two_pass_reuse_repeats_the_same_key_sequence_and_splits_typed_from_none() -> None:
    events = reuse_fixture("two_pass")

    snapshot, account = _replay_with_reuse(events)

    assert account.report() == {
        "key_strategy": "occurrence",
        "mode": "account",
        "project_events_invocations": 1,
        "pass_labels": ["probe", "normal"],
        "parse_calls_by_pass": {"probe": 16, "normal": 16, "final": 0},
        "total_parse_calls": 32,
        "distinct_keys": 16,
        "repeat_parse_calls": 16,
        "repeat_parse_calls_typed": 8,
        "repeat_parse_calls_none": 8,
        "divergent_repeats": 0,
        "parse_calls_raised": 0,
        "failure_masked_by_cache": 0,
        "unkeyed_parse_calls": 0,
        "identity_mismatches": 0,
        "potential_hit_ratio": 0.5,
    }
    assert account.key_sequence_by_pass["normal"] == account.key_sequence_by_pass["probe"]
    assert account.key_sequence_by_pass["probe"][0] == ("task.created", _evt(1), 1, "")
    assert snapshot.replay_metrics["fixed_point_passes"] == 2
    assert snapshot.issues == []


def test_three_pass_reuse_drops_only_the_forced_stale_acceptance_key() -> None:
    events = reuse_fixture("three_pass")

    snapshot, account = _replay_with_reuse(events)

    assert account.report() == {
        "key_strategy": "occurrence",
        "mode": "account",
        "project_events_invocations": 1,
        "pass_labels": ["probe", "normal", "final"],
        "parse_calls_by_pass": {"probe": 20, "normal": 20, "final": 19},
        "total_parse_calls": 59,
        "distinct_keys": 20,
        "repeat_parse_calls": 39,
        "repeat_parse_calls_typed": 33,
        "repeat_parse_calls_none": 6,
        "divergent_repeats": 0,
        "parse_calls_raised": 0,
        "failure_masked_by_cache": 0,
        "unkeyed_parse_calls": 0,
        "identity_mismatches": 0,
        "potential_hit_ratio": 39 / 59,
    }
    normal = account.key_sequence_by_pass["normal"]
    final = account.key_sequence_by_pass["final"]
    assert set(final) < set(normal)
    assert set(normal) - set(final) == {("task.accepted", _evt(16), 1, "")}
    # The final pass drops one event in the middle, so a key derived from the
    # parse-call ordinal instead of the per-root occurrence ordinal would
    # misalign from here on and quietly serve the wrong envelope.
    first_difference = next(
        index
        for index, (left, right) in enumerate(zip(normal, final, strict=False))
        if left != right
    )
    assert first_difference == 15
    assert normal[15] == ("task.accepted", _evt(16), 1, "")
    assert final[15] == ("artifact.revised", _evt(17), 1, "")
    assert snapshot.replay_metrics["fixed_point_passes"] == 3
    assert len(snapshot.known_event_ids) == 20
    assert len(snapshot.effective_event_revisions) == 19


def test_the_correction_head_is_the_only_thing_separating_three_correct_answers() -> None:
    """One root, one occurrence, three different right answers.

    Within a single invocation the head is a function of the root id, so it is not
    strictly discriminating here.  It stays in the key as the fail-closed guard:
    nothing in production code stops such a cache from outliving the correction
    set it was built against.
    """

    answers = {}
    for name in ("uncorrected_root", "valid_correction", "superseded_correction"):
        snapshot, account = _replay_with_reuse(reuse_fixture(name))
        keys = [
            key
            for key in account.key_sequence_by_pass["probe"]
            if key is not None and key[1] == _evt(200)
        ]
        assert len(keys) == 1
        answers[name] = (snapshot.tasks[_task(200)]["title"], keys[0])
        assert snapshot.tasks[_task(200)]["revision"] == _evt(200)
        assert account.report()["distinct_keys"] == 17
        assert account.report()["repeat_parse_calls"] == 17
        assert account.report()["divergent_repeats"] == 0
        assert snapshot.issues == []

    assert answers == {
        "uncorrected_root": ("Original", ("task.created", _evt(200), 1, "")),
        "valid_correction": ("Corrected once", ("task.created", _evt(200), 1, _evt(201))),
        "superseded_correction": ("Corrected twice", ("task.created", _evt(200), 1, _evt(202))),
    }


def test_an_invalidated_correction_leaves_the_root_answer_and_the_root_key() -> None:
    snapshot, account = _replay_with_reuse(reuse_fixture("inactive_correction"))

    assert snapshot.tasks[_task(200)]["title"] == "Original"
    assert snapshot.effective_event_revisions[_evt(200)] == _evt(200)
    assert ("task.created", _evt(200), 1, "") in account.cached_keys()
    assert ("task.created", _evt(200), 1, _evt(201)) not in account.cached_keys()
    assert account.report()["repeat_parse_calls"] == 17
    assert account.report()["distinct_keys"] == 17


def test_a_structurally_blocked_correction_never_reaches_the_parser() -> None:
    snapshot, account = _replay_with_reuse(reuse_fixture("structural_conflict"))

    assert [(issue.code, issue.event_ids) for issue in snapshot.issues] == [
        ("correction_structural_change", (_evt(210), _evt(211)))
    ]
    # Eighteen roots are present but the blocked one is dropped at revision
    # resolution, so it contributes neither a key nor a parse call.
    assert account.report()["distinct_keys"] == 17
    assert account.report()["repeat_parse_calls"] == 17
    assert _evt(210) not in snapshot.effective_event_revisions
    assert _task(210) not in snapshot.tasks
    assert all(key is None or key[1] != _evt(210) for key in account.key_sequence_by_pass["probe"])


def test_a_multi_head_correction_conflict_records_both_issues_in_order() -> None:
    snapshot, account = _replay_with_reuse(reuse_fixture("multi_head_conflict"))

    assert [(issue.code, issue.event_ids) for issue in snapshot.issues] == [
        ("correction_revision_invalid", (_evt(220), _evt(221), _evt(222))),
        ("correction_conflict", (_evt(220), _evt(221), _evt(222))),
    ]
    assert _task(220) not in snapshot.tasks
    assert account.report()["distinct_keys"] == 16
    assert account.report()["repeat_parse_calls"] == 16


def test_resolution_issues_still_precede_apply_loop_issues() -> None:
    """Pin the issue order an eager pre-parse outside the apply loop would invert."""

    snapshot, _ = _replay_with_reuse(reuse_fixture("mixed_issue_order"))

    assert [issue.code for issue in snapshot.issues] == [
        "correction_structural_change",
        "domain_validation_rejected",
    ]


@pytest.mark.parametrize(
    ("fixture", "key_strategy", "expected"),
    [
        ("duplicate_root_verbatim", "occurrence", (18, 18, 0)),
        ("duplicate_root_verbatim", "root_event_id", (17, 19, 0)),
        ("duplicate_root_divergent", "occurrence", (18, 18, 0)),
        ("duplicate_root_divergent", "root_event_id", (17, 19, 2)),
    ],
)
def test_a_repeated_root_event_id_needs_the_occurrence_ordinal(
    fixture: str, key_strategy: str, expected: tuple[int, int, int]
) -> None:
    """The apply loop does not deduplicate a repeated root id: both occurrences parse.

    Keying on the root id alone therefore collapses two different effective
    payloads onto one entry.  The simulation never serves, so the result stays
    correct in every row and the unsoundness shows up only as a positive
    divergence counter.
    """

    events = reuse_fixture(fixture)

    snapshot, account = _replay_with_reuse(events, key_strategy=key_strategy)
    counts = account.report()

    assert (counts["distinct_keys"], counts["repeat_parse_calls"], counts["divergent_repeats"]) == (
        expected
    )
    assert counts["total_parse_calls"] == 36
    assert counts["unkeyed_parse_calls"] == 0
    assert len(snapshot.known_event_ids) == 17
    assert len(snapshot.effective_event_revisions) == 17


def test_a_repeated_root_id_can_mask_a_parse_failure_behind_the_earlier_occurrence() -> None:
    events = reuse_fixture("duplicate_root_failing")

    _, occurrence = _replay_with_reuse(events, key_strategy="occurrence")
    _, root_only = _replay_with_reuse(events, key_strategy="root_event_id")

    assert occurrence.report()["parse_calls_raised"] == 2
    assert root_only.report()["parse_calls_raised"] == 2
    assert occurrence.report()["failure_masked_by_cache"] == 0
    assert root_only.report()["failure_masked_by_cache"] == 2


def test_a_malformed_payload_is_rejected_before_the_parser_and_contributes_nothing() -> None:
    events = reuse_fixture("malformed_payload")

    snapshot, account = _replay_with_reuse(events)

    assert [(issue.code, issue.event_ids) for issue in snapshot.issues] == [
        ("domain_validation_rejected", (_evt(230),))
    ]
    assert "missing required fields" in snapshot.issues[0].message
    assert account.report()["parse_calls_raised"] == 0
    assert account.report()["distinct_keys"] == 17
    assert account.report()["repeat_parse_calls"] == 17
    assert all(key is None or key[1] != _evt(230) for key in account.key_sequence_by_pass["probe"])


def test_a_correction_that_reaches_the_parser_and_raises_is_never_stored() -> None:
    events = reuse_fixture("malformed_correction")
    failing_key = ("task.created", _evt(240), 1, _evt(241))

    snapshot, account = _replay_with_reuse(events)

    assert [(issue.code, issue.event_ids) for issue in snapshot.issues] == [
        ("domain_validation_rejected", (_evt(240),))
    ]
    assert "commons.payload.task.v1: /priority" in snapshot.issues[0].message
    assert _task(240) not in snapshot.tasks
    assert account.key_sequence_by_pass["probe"].count(failing_key) == 1
    assert account.key_sequence_by_pass["normal"].count(failing_key) == 1
    assert failing_key not in account.cached_keys()
    assert account.report()["parse_calls_raised"] == 2
    assert account.report()["failure_masked_by_cache"] == 0
    assert account.report()["distinct_keys"] == 16
    assert account.report()["repeat_parse_calls"] == 16

    # Mirror: without the bad correction the same root applies cleanly, so this
    # test cannot pass by rejecting everything.
    mirror = tuple(event for event in events if event["event_type"] != "event.corrected")
    clean, clean_account = _replay_with_reuse(mirror)
    assert clean.issues == []
    assert clean.tasks[_task(240)]["title"] == "Corrected into a bad priority"
    assert clean_account.report()["parse_calls_raised"] == 0


def test_the_reuse_cache_does_not_survive_one_project_events_invocation() -> None:
    """Two invocations with identical key sequences and different payloads.

    The kill pair is deliberate: ``divergent_repeats == 0`` alone would also be
    satisfied by an instrumentation that never cached anything, so the number of
    first-sight parses is pinned as well.
    """

    base = reuse_fixture("two_pass")
    left = (*base, _reuse_task_created(300, title="Alpha"))
    right = (*base, _reuse_task_created(300, title="Beta"))
    expected_left = _extended_replay_integrity(project_events(left))
    expected_right = _extended_replay_integrity(project_events(right))
    assert expected_left["snapshot_sha256"] != expected_right["snapshot_sha256"]

    account = _EnvelopeReuseAccount()
    with _simulated_envelope_reuse(account):
        scoped_left = projection.project_events(left)
        scoped_right = projection.project_events(right)

    _assert_matching_extended_replay(_extended_replay_integrity(scoped_left), expected_left)
    _assert_matching_extended_replay(_extended_replay_integrity(scoped_right), expected_right)
    assert account.report()["project_events_invocations"] == 2
    assert account.report()["distinct_keys"] == 34
    assert account.report()["repeat_parse_calls"] == 34
    assert account.report()["divergent_repeats"] == 0

    # Control: the directly imported symbol never enters the wrapped
    # ``project_events``, so the cache is not reset between invocations and the
    # second history is served keys built from the first one.
    leaked = _EnvelopeReuseAccount()
    with _simulated_envelope_reuse(leaked):
        project_events(left)
        project_events(right)

    assert leaked.report()["project_events_invocations"] == 0
    assert leaked.report()["distinct_keys"] == 17
    assert leaked.report()["divergent_repeats"] == 2


def test_a_replay_in_an_outside_thread_bypasses_the_reuse_account() -> None:
    """Isolation of one instrumented context from an uninstrumented thread.

    Two simultaneously active reuse contexts stay out of scope, exactly as in the
    A6.4 phase instrumentation.
    """

    base = reuse_fixture("two_pass")
    left = (*base, _reuse_task_created(300, title="Alpha"))
    right = (*base, _reuse_task_created(300, title="Beta"))
    expected_left = _extended_replay_integrity(project_events(left))
    expected_right = _extended_replay_integrity(project_events(right))
    barrier = threading.Barrier(2)
    outside: list[Any] = []
    errors: list[BaseException] = []

    def replay_outside_reuse_context() -> None:
        try:
            barrier.wait(timeout=10)
            outside.append(project_events(right))
        except BaseException as exc:  # pragma: no cover - asserted below if a worker fails
            errors.append(exc)

    account = _EnvelopeReuseAccount()
    worker = threading.Thread(target=replay_outside_reuse_context)
    with _simulated_envelope_reuse(account):
        worker.start()
        barrier.wait(timeout=10)
        inside = projection.project_events(left)
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert errors == []
    assert len(outside) == 1
    _assert_matching_extended_replay(_extended_replay_integrity(inside), expected_left)
    _assert_matching_extended_replay(_extended_replay_integrity(outside[0]), expected_right)
    assert account.report()["project_events_invocations"] == 1
    assert account.report()["parse_calls_by_pass"] == {"probe": 17, "normal": 17, "final": 0}
    assert account.report()["distinct_keys"] == 17
    assert account.report()["repeat_parse_calls"] == 17
    assert account.report()["divergent_repeats"] == 0


@pytest.mark.parametrize("fixture", ["three_pass", "superseded_correction"])
def test_the_reuse_layer_leaves_the_caller_events_untouched(fixture: str) -> None:
    events = reuse_fixture(fixture)
    originals = list(events)
    before_hash = canonical_sha256({"events": [dict(event) for event in events]})
    before_copy = deepcopy([dict(event) for event in events])

    _, account = _replay_with_reuse(events)

    assert account.report()["unkeyed_parse_calls"] == 0
    assert canonical_sha256({"events": [dict(event) for event in events]}) == before_hash
    assert [dict(event) for event in events] == before_copy
    assert all(left is right for left, right in zip(events, originals, strict=True))
    for event in events:
        assert [key for key in event if key.startswith("_")] == []
        payload = event.get("payload")
        assert isinstance(payload, dict)
        assert [key for key in payload if key.startswith("_")] == []


def test_a_deliberately_unsound_served_cache_trips_the_integrity_assertion() -> None:
    """Negative control: the integrity guard must be falsifiable, not decorative.

    A broken cache does not crash the replay — the apply loop turns the failure
    into an ordinary issue — so only this comparison can catch it.
    """

    events = reuse_fixture("duplicate_root_divergent")
    expected = _extended_replay_integrity(project_events(events))

    account = _EnvelopeReuseAccount(mode="shadow", key_strategy="root_event_id")
    with _simulated_envelope_reuse(account):
        snapshot = projection.project_events(events)

    # The served envelope carries the first occurrence's task id, so the second
    # task silently vanishes and the replay still reports no issue at all.
    assert snapshot.issues == []
    assert snapshot.tasks[_task(100)]["title"] == "Alpha"
    assert _task(101) not in snapshot.tasks
    with pytest.raises(AssertionError, match="differs from the uninstrumented baseline"):
        _assert_matching_extended_replay(_extended_replay_integrity(snapshot), expected)


def test_the_reuse_layer_restores_every_patched_global_after_an_error() -> None:
    events = reuse_fixture("two_pass")
    originals = (
        projection.project_events,
        projection._project_events_once,
        projection.resolve_revision,
        projection.parse_event_envelope,
    )
    write_path_parser = manager_module.parse_event_envelope
    original_invalidation = projection.derive_invalidation_state

    def fail_resolution(*_args: object, **_keywords: object) -> object:
        raise RuntimeError("intentional envelope reuse test failure")

    account = _EnvelopeReuseAccount()
    try:
        with pytest.raises(RuntimeError, match="intentional envelope reuse"):
            with _simulated_envelope_reuse(account):
                # The write-path guard imports the parser directly, so wrapping
                # the projection global must leave it alone.
                assert manager_module.parse_event_envelope is envelopes.parse_event_envelope
                assert manager_module.parse_event_envelope is write_path_parser
                assert projection.parse_event_envelope is not envelopes.parse_event_envelope
                projection.derive_invalidation_state = fail_resolution
                projection.project_events(events)
    finally:
        projection.derive_invalidation_state = original_invalidation

    assert (
        projection.project_events,
        projection._project_events_once,
        projection.resolve_revision,
        projection.parse_event_envelope,
    ) == originals
    assert manager_module.parse_event_envelope is write_path_parser


def test_the_reuse_layer_restores_globals_after_nesting_inside_phase_instrumentation() -> None:
    events = reuse_fixture("two_pass")
    expected = _extended_replay_integrity(project_events(events))
    originals = (
        projection.project_events,
        projection._project_events_once,
        projection.resolve_revision,
        projection.parse_event_envelope,
        projection.derive_invalidation_state,
        lifecycle.validate_transition,
    )

    collector = _PhaseCollector()
    account = _EnvelopeReuseAccount()
    with _instrumented_projection_context(collector):
        with _simulated_envelope_reuse(account):
            snapshot = projection.project_events(events)

    _assert_matching_extended_replay(_extended_replay_integrity(snapshot), expected)
    assert (
        projection.project_events,
        projection._project_events_once,
        projection.resolve_revision,
        projection.parse_event_envelope,
        projection.derive_invalidation_state,
        lifecycle.validate_transition,
    ) == originals
    assert account.report()["distinct_keys"] == 16
    assert account.report()["repeat_parse_calls"] == 16


def test_the_profile_reports_envelope_reuse_and_can_turn_it_off() -> None:
    profile = profile_projection_components(event_count=16, repeats=2)
    reuse = profile["envelope_reuse_profile"]

    assert reuse is not None
    assert reuse["counts"]["parse_calls_by_pass"] == {"probe": 16, "normal": 16, "final": 0}
    assert reuse["counts"]["distinct_keys"] == 16
    assert reuse["counts"]["repeat_parse_calls"] == 16
    assert reuse["purity"] == {
        "distinct_keys": 16,
        "payload_digest_conflicts": 0,
        "envelope_equality_checks": 16,
        "divergent_repeats": 0,
        "unkeyed_parse_calls": 0,
        "identity_mismatches": 0,
    }
    assert reuse["isolated_parse_cost"]["parse_calls"] == 32
    assert reuse["isolated_parse_cost"]["parse_calls_raised"] == 0
    assert [sample["arm"] for sample in reuse["samples"]] == [
        "reuse_baseline",
        "account",
        "shadow",
        "reuse_baseline",
        "account",
        "shadow",
    ]
    assert len({sample["integrity_sha256"] for sample in reuse["samples"]}) == 1
    assert len(reuse["paired_samples"]) == 2
    assert reuse["serving_arm_included"] is True
    assert reuse["serving_arm_dropped_reason"] is None
    assert reuse["caveats"]

    assert (
        profile_projection_components(event_count=16, repeats=1, envelope_reuse=False)[
            "envelope_reuse_profile"
        ]
        is None
    )


def test_a_repeat_whose_parse_accounting_drifted_is_refused() -> None:
    """Interleaved arms are only comparable while every repeat parses the same work."""

    _, first = _replay_with_reuse(reuse_fixture("two_pass"))
    _, second = _replay_with_reuse(reuse_fixture("uncorrected_root"))

    _assert_stable_reuse_counts([first.report(), first.report()], arm="account")
    with pytest.raises(AssertionError, match="counters changed between repeats"):
        _assert_stable_reuse_counts([first.report(), second.report()], arm="account")
