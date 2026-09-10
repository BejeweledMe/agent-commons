from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent_commons.services.manager import CommonsManager
from agent_commons.storage.events import EventStore
from benchmarks.benchmark_work_mutation import (
    LARGE_BENCHMARK_ENV,
    MUTATION_LABELS,
    PHASE_LABELS,
    PROFILES,
    SCHEMA,
    build_mutation_fixture,
    profile_work_mutations,
)

_PHASE_SUM_TOLERANCE_SECONDS = 1e-3
_SMALL_TEST_MUTATIONS = ("task_create", "panel_http_task_create")


def test_small_profile_is_a_300_task_control() -> None:
    spec = PROFILES["small"]
    assert (
        spec.leftover_ready + spec.leftover_active + spec.leftover_completed + spec.accepted_cycles
        == 300
    )
    assert set(MUTATION_LABELS) >= set(_SMALL_TEST_MUTATIONS)


def test_small_profile_report_shape_and_phase_sum() -> None:
    original_lock = CommonsManager._canonical_write_lock
    original_read = EventStore.read_path
    with tempfile.TemporaryDirectory(prefix="ac-mutation-test-") as temporary:
        root = Path(temporary)
        fixture = build_mutation_fixture(root, profile="small")
        assert fixture.repo.resolve().is_relative_to(root.resolve())
        assert fixture.state_root.resolve().is_relative_to(root.resolve())
        assert fixture.task_count == 300
        assert fixture.event_count >= fixture.task_count
        assert fixture.manifest_count == 8
        report = profile_work_mutations(
            fixture,
            repeats=1,
            mutations=_SMALL_TEST_MUTATIONS,
            profile="small",
        )

    assert CommonsManager._canonical_write_lock is original_lock
    assert EventStore.read_path is original_read
    assert report["schema"] == SCHEMA
    assert report["profile"] == "small"
    assert report["repeats"] == 1
    assert report["fixture"]["task_count"] == 300
    assert set(report["mutations"]) == set(_SMALL_TEST_MUTATIONS)
    for mutation in report["mutations"].values():
        for temperature in ("cold", "warm"):
            aggregate = mutation[temperature]
            assert aggregate["samples"] == 1
            assert aggregate["median_seconds"] >= 0
            assert aggregate["p95_seconds"] >= aggregate["min_seconds"]
            assert set(aggregate["median_phase_exclusive_seconds"]) == set(PHASE_LABELS)
            samples = mutation["samples"][temperature]
            assert len(samples) == 1
            sample = samples[0]
            assert set(sample["phases"]) == set(PHASE_LABELS)
            accounted = sum(
                sample["phases"][label]["exclusive_elapsed_seconds"] for label in PHASE_LABELS
            )
            assert accounted + sample["residual_elapsed_seconds"] <= (
                sample["elapsed_seconds"] + _PHASE_SUM_TOLERANCE_SECONDS
            )
            assert sample["read_calls"] >= 0
            assert sample["read_bytes"] >= 0


@pytest.mark.skipif(
    os.environ.get(LARGE_BENCHMARK_ENV) != "1",
    reason="large fixture is opt-in via AGENT_COMMONS_LARGE_BENCHMARK=1",
)
def test_large_fixture_report_shape() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-mutation-large-") as temporary:
        fixture = build_mutation_fixture(Path(temporary), profile="large")
        report = profile_work_mutations(fixture, repeats=1, profile="large")

    assert fixture.event_count >= 3000
    assert fixture.manifest_count >= 300
    assert report["schema"] == SCHEMA
    assert set(report["mutations"]) == set(MUTATION_LABELS)
