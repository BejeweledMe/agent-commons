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
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from agent_commons.core.canonical import canonical_json_file_bytes, canonical_sha256
from agent_commons.domain import lifecycle, projection
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
    components: dict[str, ReadPathReport]
    paths: dict[str, ReadPathReport]
    replay_phase_profile: InstrumentedReplayReport
    instrumentation_overhead: InstrumentationOverheadReport


class PhaseTiming(TypedDict):
    """Exclusive elapsed time and invocation count for one replay phase."""

    calls: int
    exclusive_elapsed_seconds: float


class ReplayIntegrity(TypedDict):
    """Stable observable result used to compare instrumented and baseline replay."""

    snapshot_sha256: str
    known_event_ids: list[str]
    applied_event_ids: list[str]
    fixed_point_passes: int


class InstrumentedReplaySample(ReplayIntegrity):
    """One timed replay with benchmark-local phase accounting."""

    root_elapsed_seconds: float
    peak_allocated_bytes: int
    phases: dict[str, PhaseTiming]
    residual_elapsed_seconds: float


class InstrumentedReplayReport(TypedDict):
    """Repeated phase-accounted replay measurements over one verified event tuple."""

    scope: str
    median_elapsed_seconds: float
    max_peak_allocated_bytes: int
    samples: list[InstrumentedReplaySample]
    median_phase_exclusive_seconds: dict[str, float]
    calls_per_sample: dict[str, int]


class InstrumentationOverheadSample(TypedDict):
    """Matched baseline and instrumented observations from one repeat."""

    baseline_elapsed_seconds: float
    instrumented_elapsed_seconds: float
    observed_delta_seconds: float


class InstrumentationOverheadReport(TypedDict):
    """Observed overhead of benchmark-only timing wrappers, not a production budget."""

    scope: str
    median_observed_delta_seconds: float
    samples: list[InstrumentationOverheadSample]


_PHASE_LABELS = (
    "fixed_point_planning",
    "project_events_once.probe",
    "project_events_once.normal",
    "project_events_once.final",
    "invalidation",
    "revision_resolution",
    "acceptance_staleness",
    "cas_conflict_detection",
    "payload_validation",
    "event_envelope_parsing",
    "transition_validation",
    "event_application",
    "bound_evidence_staleness",
    "decision_conflict_detection",
)


@dataclass
class _PhaseFrame:
    """One active timed region, with child time excluded before reporting."""

    label: str
    started_at: float
    child_elapsed_seconds: float = 0.0


@dataclass
class _PhaseCollector:
    """Collect an exclusive timing tree without changing projection semantics."""

    phase_elapsed_seconds: dict[str, float] = field(
        default_factory=lambda: {label: 0.0 for label in _PHASE_LABELS}
    )
    phase_call_counts: dict[str, int] = field(
        default_factory=lambda: {label: 0 for label in _PHASE_LABELS}
    )
    _stack: list[_PhaseFrame] = field(default_factory=list)
    _project_events_once_calls: int = 0

    @contextmanager
    def phase(self, label: str) -> Iterator[None]:
        """Measure one nested phase and attribute only time not spent in its children."""

        if label not in self.phase_elapsed_seconds:
            raise ValueError(f"unknown replay phase {label}")
        frame = _PhaseFrame(label=label, started_at=time.perf_counter())
        self._stack.append(frame)
        self.phase_call_counts[label] += 1
        try:
            yield
        finally:
            elapsed = time.perf_counter() - frame.started_at
            popped = self._stack.pop()
            if popped is not frame:  # pragma: no cover - defensive against broken wrappers
                raise RuntimeError("replay phase timing stack became unbalanced")
            exclusive = max(0.0, elapsed - frame.child_elapsed_seconds)
            self.phase_elapsed_seconds[label] += exclusive
            if self._stack:
                self._stack[-1].child_elapsed_seconds += elapsed

    def project_events_once_label(self, keywords: Mapping[str, object]) -> str:
        """Name the fixed-point pass from its stable call sequence and inputs."""

        self._project_events_once_calls += 1
        if keywords.get("forced_stale_acceptance_ids"):
            return "project_events_once.final"
        if self._project_events_once_calls == 1 and keywords.get("exempt_acceptance_ids"):
            return "project_events_once.probe"
        return "project_events_once.normal"

    def report(self) -> dict[str, PhaseTiming]:
        """Return every declared phase so a zero-call branch remains explicit."""

        return {
            label: {
                "calls": self.phase_call_counts[label],
                "exclusive_elapsed_seconds": self.phase_elapsed_seconds[label],
            }
            for label in _PHASE_LABELS
        }


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


def _timed_wrapper(
    collector: _PhaseCollector, label: str, original: Callable[..., object]
) -> Callable[..., object]:
    """Wrap one projection helper while preserving its arguments and return value."""

    def wrapped(*args: object, **keywords: object) -> object:
        with collector.phase(label):
            return original(*args, **keywords)

    return wrapped


@contextmanager
def _instrument_projection(collector: _PhaseCollector) -> Iterator[None]:
    """Temporarily attach timing wrappers and restore every module global on exit.

    The benchmark installs wrappers only around the individual in-memory profile
    call.  It does not alter the checked-in production module or leave a global
    patch behind for a later baseline sample.
    """

    originals: list[tuple[object, str, object]] = []

    def replace(module: object, name: str, label: str) -> None:
        original = getattr(module, name)
        if not callable(original):  # pragma: no cover - protects benchmark assumptions
            raise TypeError(f"{module!r}.{name} is not callable")
        originals.append((module, name, original))
        setattr(module, name, _timed_wrapper(collector, label, original))

    def project_events_once(*args: object, **keywords: object) -> object:
        label = collector.project_events_once_label(keywords)
        with collector.phase(label):
            return original_project_events_once(*args, **keywords)

    try:
        original_project_events_once = projection._project_events_once
        originals.append((projection, "_project_events_once", original_project_events_once))
        projection._project_events_once = project_events_once
        replace(projection, "derive_invalidation_state", "invalidation")
        replace(projection, "resolve_revision", "revision_resolution")
        replace(projection, "_stale_task_acceptance_ids", "acceptance_staleness")
        replace(projection, "_cas_conflicts", "cas_conflict_detection")
        replace(projection, "validate_payload", "payload_validation")
        replace(projection, "parse_event_envelope", "event_envelope_parsing")
        replace(lifecycle, "validate_transition", "transition_validation")
        replace(projection, "_apply_effective_event", "event_application")
        replace(projection, "_mark_bound_evidence_stale", "bound_evidence_staleness")
        replace(projection, "_fail_closed_decision_conflicts", "decision_conflict_detection")
        yield
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)


def _replay_integrity(snapshot: ProjectSnapshot) -> ReplayIntegrity:
    """Capture the observable replay result that instrumentation must preserve."""

    return {
        "snapshot_sha256": canonical_sha256(snapshot.to_dict()),
        "known_event_ids": sorted(snapshot.known_event_ids),
        "applied_event_ids": list(snapshot.effective_event_revisions),
        "fixed_point_passes": int(snapshot.replay_metrics["fixed_point_passes"]),
    }


def _assert_matching_replay(actual: ReplayIntegrity, expected: ReplayIntegrity) -> None:
    """Reject a timing run that changed the fixed-point result rather than timing it."""

    if actual != expected:
        raise AssertionError(
            "instrumented replay differs from the uninstrumented baseline: "
            f"expected {expected!r}, got {actual!r}"
        )


def _instrumented_replay_sample(
    events: tuple[Mapping[str, Any], ...],
    *,
    known_manifest_ids: tuple[str, ...] | None,
    expected_integrity: ReplayIntegrity | None = None,
) -> InstrumentedReplaySample:
    """Measure one replay with temporary call instrumentation and whole-run peak memory.

    ``tracemalloc`` deliberately remains whole-run only: attributing allocation
    samples to nested helpers would perturb the replay and is not needed to
    choose the next A6 investigation.
    """

    collector = _PhaseCollector()
    with _instrument_projection(collector):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        try:
            with collector.phase("fixed_point_planning"):
                snapshot = projection.project_events(events, known_manifest_ids=known_manifest_ids)
        finally:
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

    integrity = _replay_integrity(snapshot)
    if expected_integrity is not None:
        _assert_matching_replay(integrity, expected_integrity)
    phases = collector.report()
    accounted = sum(timing["exclusive_elapsed_seconds"] for timing in phases.values())
    if accounted > elapsed + 1e-9:
        raise AssertionError(
            f"exclusive replay phases exceed their root timing: {accounted:.9f} > {elapsed:.9f}"
        )
    return {
        **integrity,
        "root_elapsed_seconds": elapsed,
        "peak_allocated_bytes": peak,
        "phases": phases,
        "residual_elapsed_seconds": max(0.0, elapsed - accounted),
    }


def _summarize_instrumented_replay(
    samples: list[InstrumentedReplaySample],
) -> InstrumentedReplayReport:
    """Summarize repeated instrumentation runs without hiding individual samples."""

    if not samples:
        raise ValueError("benchmark requires at least one instrumented replay sample")
    calls_per_sample = {label: samples[0]["phases"][label]["calls"] for label in _PHASE_LABELS}
    for sample in samples[1:]:
        for label, expected_calls in calls_per_sample.items():
            calls = sample["phases"][label]["calls"]
            if calls != expected_calls:
                raise AssertionError(
                    f"replay phase {label} changed call count between samples: "
                    f"expected {expected_calls}, got {calls}"
                )
    return {
        "scope": "instrumented project_events over the verified event tuple already in memory",
        "median_elapsed_seconds": statistics.median(
            sample["root_elapsed_seconds"] for sample in samples
        ),
        "max_peak_allocated_bytes": max(sample["peak_allocated_bytes"] for sample in samples),
        "samples": samples,
        "median_phase_exclusive_seconds": {
            label: statistics.median(
                sample["phases"][label]["exclusive_elapsed_seconds"] for sample in samples
            )
            for label in _PHASE_LABELS
        },
        "calls_per_sample": calls_per_sample,
    }


def _summarize_instrumentation_overhead(
    baseline_samples: list[Measurement],
    instrumented_samples: list[InstrumentedReplaySample],
) -> InstrumentationOverheadReport:
    """Report observed wrapper cost against matched normal benchmark samples."""

    if len(baseline_samples) != len(instrumented_samples):
        raise AssertionError("baseline and instrumented replay repeat counts differ")
    samples = [
        {
            "baseline_elapsed_seconds": baseline["elapsed_seconds"],
            "instrumented_elapsed_seconds": instrumented["root_elapsed_seconds"],
            "observed_delta_seconds": (
                instrumented["root_elapsed_seconds"] - baseline["elapsed_seconds"]
            ),
        }
        for baseline, instrumented in zip(baseline_samples, instrumented_samples, strict=True)
    ]
    return {
        "scope": "instrumented replay minus the matching normal replay sample",
        "median_observed_delta_seconds": statistics.median(
            sample["observed_delta_seconds"] for sample in samples
        ),
        "samples": samples,
    }


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
    manager: CommonsManager, *, repeats: int, source: str, include_full_paths: bool = True
) -> ReadPathProfile:
    """Measure verified-reader components and, optionally, the composite read paths."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    with SQLiteIndex(manager.paths, manager.events, manager.manifests) as index:
        index.sync()
        projected = index.read_projection(workspace_id=manager.workspace_id)
        in_memory_events = projected.events
        in_memory_manifests = projected.manifest_ids
        expected_event_count = len(in_memory_events)
        baseline = project_events(in_memory_events, known_manifest_ids=in_memory_manifests)
        baseline_integrity = _replay_integrity(baseline)
        expected_passes = int(baseline.replay_metrics["fixed_point_passes"])
        _assert_replay_shape(
            baseline,
            expected_event_count=expected_event_count,
            expected_passes=expected_passes,
        )

        def warm_sync() -> None:
            sync = index.sync()
            if sync.indexed != 0 or sync.removed != 0:
                raise AssertionError(f"expected an unchanged ledger during profile, got {sync!r}")

        def verified_projection_read() -> None:
            candidate = index.read_projection(workspace_id=manager.workspace_id)
            if (
                len(candidate.events) != expected_event_count
                or len(candidate.manifest_ids) != len(in_memory_manifests)
                or candidate.source_count != expected_event_count + len(in_memory_manifests)
            ):
                raise AssertionError("verified projection changed during profile")

        def in_memory_replay() -> None:
            _assert_replay_shape(
                project_events(in_memory_events, known_manifest_ids=in_memory_manifests),
                expected_event_count=expected_event_count,
                expected_passes=expected_passes,
            )

        replay_baseline_samples = [_measure(in_memory_replay) for _ in range(repeats)]
        instrumented_replay_samples = [
            _instrumented_replay_sample(
                in_memory_events,
                known_manifest_ids=in_memory_manifests,
                expected_integrity=baseline_integrity,
            )
            for _ in range(repeats)
        ]
        components = {
            "sqlite_sync": _summarize(
                "warm SQLiteIndex.sync() only",
                [_measure(warm_sync) for _ in range(repeats)],
            ),
            "sqlite_read_projection": _summarize(
                "verified SQLiteIndex.read_projection() only",
                [_measure(verified_projection_read) for _ in range(repeats)],
            ),
            "project_events": _summarize(
                "project_events over the verified event tuple already in memory",
                replay_baseline_samples,
            ),
        }

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

    paths = {"in_memory_replay": components["project_events"]}
    if include_full_paths:
        paths.update(
            {
                "commons_manager_snapshot": _summarize(
                    "canonical files plus in-memory replay",
                    [_measure(canonical_snapshot) for _ in range(repeats)],
                ),
                "verified_sqlite_read": _summarize(
                    "warm sync, verified SQLite read, then in-memory replay",
                    [_measure(verified_sqlite_read) for _ in range(repeats)],
                ),
            }
        )

    return {
        "schema": "agent_commons.a6_read_path_profile.v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "event_count": expected_event_count,
        "repeats": repeats,
        "fixed_point_passes": expected_passes,
        "source": source,
        "components": components,
        "paths": paths,
        "replay_phase_profile": _summarize_instrumented_replay(instrumented_replay_samples),
        "instrumentation_overhead": _summarize_instrumentation_overhead(
            replay_baseline_samples, instrumented_replay_samples
        ),
    }


def profile_read_paths(*, event_count: int = 20_000, repeats: int = 3) -> ReadPathProfile:
    """Measure A6's three comparable warm read paths over one synthetic ledger."""

    with tempfile.TemporaryDirectory(prefix="agent-commons-a6-profile-") as temporary:
        manager = _write_workspace(Path(temporary), event_count=event_count)
        return _profile_manager_read_paths(manager, repeats=repeats, source="synthetic_two_pass")


def profile_projection_components(
    *, event_count: int = 20_000, repeats: int = 3
) -> ReadPathProfile:
    """Measure only the three separable verified-projection components synthetically."""

    with tempfile.TemporaryDirectory(prefix="agent-commons-a6-profile-") as temporary:
        manager = _write_workspace(Path(temporary), event_count=event_count)
        return _profile_manager_read_paths(
            manager,
            repeats=repeats,
            source="synthetic_two_pass",
            include_full_paths=False,
        )


def profile_workspace_read_paths(
    repo_root: Path, *, state_root: Path | None = None, repeats: int = 3
) -> ReadPathProfile:
    """Measure the same paths against one quiet, existing workspace ledger."""

    manager = CommonsManager(repo_root, state_root=state_root)
    return _profile_manager_read_paths(manager, repeats=repeats, source="existing_workspace")


def profile_workspace_projection_components(
    repo_root: Path, *, state_root: Path | None = None, repeats: int = 3
) -> ReadPathProfile:
    """Measure only the three separable components on one quiet existing workspace."""

    manager = CommonsManager(repo_root, state_root=state_root)
    return _profile_manager_read_paths(
        manager,
        repeats=repeats,
        source="existing_workspace",
        include_full_paths=False,
    )


def main() -> None:
    """Parse the reproducible benchmark options and emit canonical JSON evidence."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--event-count", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--components-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.state_root is not None and arguments.repo is None:
        parser.error("--state-root requires --repo")
    try:
        if arguments.repo is None:
            if arguments.components_only:
                result = profile_projection_components(
                    event_count=arguments.event_count,
                    repeats=arguments.repeats,
                )
            else:
                result = profile_read_paths(
                    event_count=arguments.event_count,
                    repeats=arguments.repeats,
                )
        else:
            if arguments.components_only:
                result = profile_workspace_projection_components(
                    arguments.repo,
                    state_root=arguments.state_root,
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
