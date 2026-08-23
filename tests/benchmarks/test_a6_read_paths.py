from __future__ import annotations

import tempfile
from pathlib import Path

from benchmarks.benchmark_a6_read_paths import (
    _write_workspace,
    profile_projection_components,
    profile_read_paths,
    profile_workspace_projection_components,
    profile_workspace_read_paths,
)


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
