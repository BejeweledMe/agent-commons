from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from agent_commons.domain import lifecycle, projection
from agent_commons.domain.projection import project_events
from benchmarks.benchmark_a6_read_paths import (
    _PHASE_LABELS,
    _instrumented_projection_context,
    _instrumented_replay_sample,
    _PhaseCollector,
    _replay_integrity,
    _write_workspace,
    profile_projection_components,
    profile_read_paths,
    profile_workspace_projection_components,
    profile_workspace_read_paths,
)
from benchmarks.benchmark_projection import workload


def test_read_path_profile_keeps_the_three_paths_and_two_pass_workload() -> None:
    profile = profile_read_paths(event_count=16, repeats=1)

    assert profile["schema"] == "agent_commons.a6_read_path_profile.v1"
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
