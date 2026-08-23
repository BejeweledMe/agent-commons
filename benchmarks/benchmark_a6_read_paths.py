"""Profile the A6 canonical, verified-SQLite, and in-memory replay read paths.

Run from a source checkout with::

    uv run --locked python benchmarks/benchmark_a6_read_paths.py --event-count 20000 --repeats 3

The benchmark builds an isolated, valid canonical ledger in a temporary directory.
It deliberately measures no startup, fixture-generation, or index-prime cost.  The
three reported paths are therefore directly comparable after their inputs are warm:

* ``CommonsManager.snapshot()``: canonical files plus replay;
* ``CommonsManager._read_snapshot()``: the interactive verified SQLite path,
  including the unchanged-file sync and replay; and
* ``project_events()`` over the verified tuple already in memory.

Wall-clock and ``tracemalloc`` values are evidence, not pass/fail budgets.  The
two-pass workload assertion is the characterization guard that makes a drift in
the replay shape visible.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypedDict

from agent_commons.core.canonical import canonical_json_file_bytes
from agent_commons.domain.projection import ProjectSnapshot, project_events
from agent_commons.index.sqlite import SQLiteIndex
from agent_commons.services import CommonsManager

try:  # Support both ``python benchmarks/...`` and an imported test module.
    from benchmarks.benchmark_projection import workload
except ModuleNotFoundError:  # pragma: no cover - exercised by the documented command
    from benchmark_projection import workload


class Measurement(TypedDict):
    """One elapsed-time and Python-allocation sample."""

    elapsed_seconds: float
    peak_allocated_bytes: int


class ReadPathReport(TypedDict):
    """Summary for one A6 read path."""

    scope: str
    median_elapsed_seconds: float
    max_peak_allocated_bytes: int
    samples: list[Measurement]


class ReadPathProfile(TypedDict):
    """Machine-readable result of one reproducible A6 profile run."""

    schema: str
    python: str
    platform: str
    event_count: int
    repeats: int
    fixed_point_passes: int
    source: str
    paths: dict[str, ReadPathReport]


_PAYLOAD_SCHEMAS = {
    "artifact": "commons.payload.artifact.v1",
    "objective": "commons.payload.objective.v1",
    "review": "commons.payload.review.v1",
    "task": "commons.payload.task.v1",
}


def _mapping_list(value: object, *, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"benchmark {label} must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"benchmark {label} must contain objects")
        result.append(dict(item))
    return result


def _event_document(event: Mapping[str, object], *, workspace_id: str) -> dict[str, object]:
    """Turn the existing projection workload into a valid canonical event file."""

    event_type = str(event["event_type"])
    family, separator, _ = event_type.partition(".")
    payload_schema = _PAYLOAD_SCHEMAS.get(family) if separator else None
    if payload_schema is None:
        raise ValueError(f"benchmark has no payload schema for {event_type}")
    payload = event.get("payload")
    actor = event.get("actor")
    if not isinstance(payload, Mapping) or not isinstance(actor, Mapping):
        raise ValueError("benchmark event payload and actor must be objects")
    event_id = str(event["event_id"])
    return {
        "schema": "commons.event.v1",
        "payload_schema": payload_schema,
        "event_id": event_id,
        "workspace_id": workspace_id,
        "event_type": event_type,
        "recorded_at": str(event["recorded_at"]),
        "actor": {
            "principal_id": "principal.benchmark",
            "session_id": str(actor["session_id"]),
            "role_id": str(actor["role_id"]),
            "software": "a6-read-path-benchmark",
        },
        "subject_refs": _mapping_list(event.get("subject_refs"), label="subject_refs"),
        "idempotency_namespace": "benchmark:a6-read-path",
        "idempotency_key": event_id,
        "provenance": {
            "writer": "agent-commons-benchmark",
            "writer_version": "1",
            "source_kind": "synthetic",
            "source_refs": [],
        },
        "relations": _mapping_list(event.get("relations"), label="relations"),
        "tags": [],
        "payload": dict(payload),
    }


def _write_workspace(root: Path, *, event_count: int) -> CommonsManager:
    """Build one valid two-pass ledger without timing fixture creation."""

    repo = root / "repo"
    state_root = root / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="a6-read-path-profile")
    manager = CommonsManager(repo, state_root=state_root)
    event_root = manager.paths.events / "2026" / "01" / "01"
    event_root.mkdir(parents=True)
    for event in workload(event_count=event_count, expected_passes=2):
        document = _event_document(event, workspace_id=manager.workspace_id)
        event_path = event_root / f"{document['event_id']}.json"
        event_path.write_bytes(canonical_json_file_bytes(document))
    return manager


def _assert_replay_shape(
    snapshot: ProjectSnapshot, *, expected_event_count: int, expected_passes: int
) -> None:
    if len(snapshot.known_event_ids) != expected_event_count:
        raise AssertionError(
            f"ledger changed during profile: expected {expected_event_count} events, "
            f"got {len(snapshot.known_event_ids)}"
        )
    passes = snapshot.replay_metrics.get("fixed_point_passes")
    if passes != expected_passes:
        raise AssertionError(f"expected {expected_passes} replay passes, got {passes!r}")


def _measure(operation: Callable[[], object]) -> Measurement:
    """Run one measured operation after collecting unrelated temporary objects."""

    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    operation()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"elapsed_seconds": elapsed, "peak_allocated_bytes": peak}


def _summarize(scope: str, samples: list[Measurement]) -> ReadPathReport:
    if not samples:
        raise ValueError("benchmark requires at least one sample")
    return {
        "scope": scope,
        "median_elapsed_seconds": statistics.median(
            sample["elapsed_seconds"] for sample in samples
        ),
        "max_peak_allocated_bytes": max(sample["peak_allocated_bytes"] for sample in samples),
        "samples": samples,
    }


def _profile_manager_read_paths(
    manager: CommonsManager, *, repeats: int, source: str
) -> ReadPathProfile:
    """Measure the three paths after priming one disposable verified projection."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    with SQLiteIndex(manager.paths, manager.events, manager.manifests) as index:
        index.sync()
        projected = index.read_projection(workspace_id=manager.workspace_id)
    in_memory_events = projected.events
    in_memory_manifests = projected.manifest_ids
    expected_event_count = len(in_memory_events)
    baseline = project_events(in_memory_events, known_manifest_ids=in_memory_manifests)
    expected_passes = int(baseline.replay_metrics["fixed_point_passes"])
    _assert_replay_shape(
        baseline,
        expected_event_count=expected_event_count,
        expected_passes=expected_passes,
    )

    def canonical_snapshot() -> None:
        _assert_replay_shape(
            manager.snapshot(),
            expected_event_count=expected_event_count,
            expected_passes=expected_passes,
        )

    def verified_sqlite_read() -> None:
        snapshot, diagnostics = manager._read_snapshot()
        if diagnostics.get("source") != "sqlite" or diagnostics.get("cache_hit") is not True:
            raise AssertionError(f"expected a warm verified SQLite read, got {diagnostics!r}")
        _assert_replay_shape(
            snapshot,
            expected_event_count=expected_event_count,
            expected_passes=expected_passes,
        )

    def in_memory_replay() -> None:
        _assert_replay_shape(
            project_events(in_memory_events, known_manifest_ids=in_memory_manifests),
            expected_event_count=expected_event_count,
            expected_passes=expected_passes,
        )

    return {
        "schema": "agent_commons.a6_read_path_profile.v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "event_count": expected_event_count,
        "repeats": repeats,
        "fixed_point_passes": expected_passes,
        "source": source,
        "paths": {
            "commons_manager_snapshot": _summarize(
                "canonical files plus in-memory replay",
                [_measure(canonical_snapshot) for _ in range(repeats)],
            ),
            "verified_sqlite_read": _summarize(
                "warm sync, verified SQLite read, then in-memory replay",
                [_measure(verified_sqlite_read) for _ in range(repeats)],
            ),
            "in_memory_replay": _summarize(
                "project_events over the verified event tuple already in memory",
                [_measure(in_memory_replay) for _ in range(repeats)],
            ),
        },
    }


def profile_read_paths(*, event_count: int = 20_000, repeats: int = 3) -> ReadPathProfile:
    """Measure A6's three comparable warm read paths over one synthetic ledger."""

    with tempfile.TemporaryDirectory(prefix="agent-commons-a6-profile-") as temporary:
        manager = _write_workspace(Path(temporary), event_count=event_count)
        return _profile_manager_read_paths(manager, repeats=repeats, source="synthetic_two_pass")


def profile_workspace_read_paths(
    repo_root: Path, *, state_root: Path | None = None, repeats: int = 3
) -> ReadPathProfile:
    """Measure the same paths against one quiet, existing workspace ledger."""

    manager = CommonsManager(repo_root, state_root=state_root)
    return _profile_manager_read_paths(manager, repeats=repeats, source="existing_workspace")


def main() -> None:
    """Parse the reproducible benchmark options and emit canonical JSON evidence."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--event-count", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--state-root", type=Path)
    arguments = parser.parse_args()
    if arguments.state_root is not None and arguments.repo is None:
        parser.error("--state-root requires --repo")
    try:
        if arguments.repo is None:
            result = profile_read_paths(
                event_count=arguments.event_count,
                repeats=arguments.repeats,
            )
        else:
            result = profile_workspace_read_paths(
                arguments.repo,
                state_root=arguments.state_root,
                repeats=arguments.repeats,
            )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
