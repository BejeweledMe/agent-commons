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
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from agent_commons.core.canonical import canonical_json_file_bytes, canonical_sha256
from agent_commons.domain import lifecycle, projection
from agent_commons.domain.envelopes import serialize_event_envelope
from agent_commons.domain.projection import ProjectSnapshot, project_events
from agent_commons.index.sqlite import SQLiteIndex
from agent_commons.services import CommonsManager

try:  # Support both ``python benchmarks/...`` and an imported test module.
    from benchmarks.benchmark_projection import _event, _identifier, workload
except ModuleNotFoundError:  # pragma: no cover - exercised by the documented command
    from benchmark_projection import _event, _identifier, workload


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
    envelope_reuse_profile: EnvelopeReuseReport | None


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


class ExtendedReplayIntegrity(TypedDict):
    """Every observable replay fact an envelope-reuse experiment must leave untouched.

    ``ReplayIntegrity`` above is the A6.4 record.  Envelope reuse can change what
    an event parses to, so a digest-and-id comparison is not enough: a broken
    cache is swallowed by the ``except`` at ``projection.py`` and re-surfaces as a
    plausible-looking ``domain_validation_rejected`` issue.  This record therefore
    also pins the issue list with its messages and order, the raw warnings
    sequence (``to_dict()`` sorts and dedups warnings), the full revision map and
    the whole ``replay_metrics`` mapping.
    """

    snapshot_sha256: str
    known_event_ids: list[str]
    known_manifest_ids: list[str]
    applied_event_ids: list[str]
    effective_event_revisions: dict[str, str]
    fixed_point_passes: int
    replay_metrics: dict[str, int]
    issues: list[list[object]]
    warnings_sequence: list[str]


class EnvelopeReuseCounts(TypedDict):
    """Benchmark-local accounting of one simulated envelope-reuse experiment.

    ``distinct_keys`` and ``repeat_parse_calls`` describe how often a parse of the
    same effective event recurred inside one ``project_events()`` invocation.
    They are an upper bound on what any future cache could avoid, not a latency
    claim and not a statement that such a cache exists or is approved.
    """

    key_strategy: str
    mode: str
    project_events_invocations: int
    pass_labels: list[str]
    parse_calls_by_pass: dict[str, int]
    total_parse_calls: int
    distinct_keys: int
    repeat_parse_calls: int
    repeat_parse_calls_typed: int
    repeat_parse_calls_none: int
    divergent_repeats: int
    parse_calls_raised: int
    failure_masked_by_cache: int
    unkeyed_parse_calls: int
    identity_mismatches: int
    potential_hit_ratio: float


class EnvelopeReuseSample(TypedDict):
    """One timed arm of one repeat, with its integrity digest and its counters."""

    arm: str
    elapsed_seconds: float
    peak_allocated_bytes: int
    integrity_sha256: str
    counts: EnvelopeReuseCounts | None


class EnvelopeReusePairedSample(TypedDict):
    """Matched arms from one repeat; only differences within a repeat are quoted."""

    repeat_index: int
    reuse_baseline_elapsed_seconds: float
    account_elapsed_seconds: float
    shadow_elapsed_seconds: float | None
    paired_saving_seconds: float | None
    wrapper_tax_seconds: float


class EnvelopeReusePurity(TypedDict):
    """Untimed verification that one key never covered two different parse inputs."""

    distinct_keys: int
    payload_digest_conflicts: int
    envelope_equality_checks: int
    divergent_repeats: int
    unkeyed_parse_calls: int
    identity_mismatches: int


class IsolatedParseCost(TypedDict):
    """Cost of replaying the recorded parse sequence outside ``project_events()``."""

    parse_calls: int
    elapsed_seconds: float
    parse_calls_raised: int


class EnvelopeReuseReport(TypedDict):
    """Characterization of envelope reuse over one already-verified event tuple."""

    scope: str
    key_strategy: str
    repeats: int
    counts: EnvelopeReuseCounts
    samples: list[EnvelopeReuseSample]
    paired_samples: list[EnvelopeReusePairedSample]
    median_reuse_baseline_elapsed_seconds: float
    median_account_elapsed_seconds: float
    median_shadow_elapsed_seconds: float | None
    median_paired_saving_seconds: float | None
    median_wrapper_tax_seconds: float
    max_peak_allocated_bytes: dict[str, int]
    purity: EnvelopeReusePurity
    isolated_parse_cost: IsolatedParseCost
    baseline_integrity: ExtendedReplayIntegrity
    serving_arm_included: bool
    serving_arm_dropped_reason: str | None
    caveats: list[str]


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


_PASS_LABELS = ("probe", "normal", "final")


def _project_events_once_label(call_ordinal: int, keywords: Mapping[str, object]) -> str:
    """Name one fixed-point pass from its stable call sequence and inputs.

    Both benchmark instrumentation layers read the pass this way, so a change in
    how ``project_events()`` orders its passes moves the phase profile and the
    envelope-reuse accounting together instead of silently disagreeing.
    """

    if keywords.get("forced_stale_acceptance_ids"):
        return "final"
    if call_ordinal == 1 and keywords.get("exempt_acceptance_ids"):
        return "probe"
    return "normal"


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
        """Name the fixed-point pass phase from its stable call sequence and inputs."""

        self._project_events_once_calls += 1
        pass_label = _project_events_once_label(self._project_events_once_calls, keywords)
        return f"project_events_once.{pass_label}"

    def report(self) -> dict[str, PhaseTiming]:
        """Return every declared phase so a zero-call branch remains explicit."""

        return {
            label: {
                "calls": self.phase_call_counts[label],
                "exclusive_elapsed_seconds": self.phase_elapsed_seconds[label],
            }
            for label in _PHASE_LABELS
        }


_ACTIVE_PHASE_COLLECTOR: ContextVar[_PhaseCollector | None] = ContextVar(
    "agent_commons_a6_active_phase_collector", default=None
)


_ACTIVE_ENVELOPE_REUSE_ACCOUNT: ContextVar[_EnvelopeReuseAccount | None] = ContextVar(
    "agent_commons_a6_active_envelope_reuse_account", default=None
)


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


# Envelope-reuse characterization fixtures.  They live here rather than in the
# test package because a benchmark must never import from ``tests/``; the payload
# shapes follow the correction cases already pinned in tests/domain/test_projection.py.


def _reuse_task_created(
    number: int, *, title: str, priority: str = "normal", task_number: int | None = None
) -> dict[str, Any]:
    """Build one standalone ``task.created`` root event for a reuse fixture."""

    task_id = _identifier("task", number if task_number is None else task_number)
    return _event(
        number,
        "task.created",
        {
            "task_id": task_id,
            "title": title,
            "description": "envelope reuse characterization fixture",
            "acceptance_criteria": ["replayed"],
            "priority": priority,
        },
        subject_kind="task",
        subject_id=task_id,
    )


def _reuse_correction(
    number: int,
    root: Mapping[str, Any],
    replacement_payload: Mapping[str, Any],
    *,
    superseded: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Build one ``event.corrected`` whose root hash matches the immutable root."""

    payload: dict[str, Any] = {
        "target_event_id": str(root["event_id"]),
        "expected_target_sha256": canonical_sha256(root),
        "replacement_payload": dict(replacement_payload),
    }
    if superseded:
        payload["superseded_correction_event_ids"] = [
            str(event["event_id"]) for event in superseded
        ]
    return _event(
        number,
        "event.corrected",
        payload,
        subject_kind="event",
        subject_id=str(root["event_id"]),
    )


def _reuse_invalidation(number: int, target: Mapping[str, Any]) -> dict[str, Any]:
    """Build one ``event.invalidated`` that removes a correction from the active set."""

    return _event(
        number,
        "event.invalidated",
        {
            "target_ref": {"kind": "event", "id": str(target["event_id"])},
            "reason": "superseded by the reuse characterization fixture",
        },
        subject_kind="event",
        subject_id=str(target["event_id"]),
    )


def reuse_fixture(name: str) -> tuple[Mapping[str, Any], ...]:
    """Return one named envelope-reuse scenario as an immutable event tuple.

    Every scenario is a direct ``project_events()`` input.  Two of them cannot be
    expressed on the canonical read path at all: ``{event_id}.json`` collapses a
    duplicated root event id, and the verified reader rejects a malformed payload
    before replay ever sees it.
    """

    if name == "two_pass":
        return tuple(workload(event_count=16, expected_passes=2))
    if name == "three_pass":
        return tuple(workload(event_count=20, expected_passes=3))
    base = tuple(workload(event_count=16, expected_passes=2))
    root = _reuse_task_created(200, title="Original")
    if name == "uncorrected_root":
        return (*base, root)
    if name == "valid_correction":
        corrected = _reuse_correction(201, root, {**root["payload"], "title": "Corrected once"})
        return (*base, root, corrected)
    if name == "superseded_correction":
        first = _reuse_correction(201, root, {**root["payload"], "title": "Corrected once"})
        second = _reuse_correction(
            202, root, {**root["payload"], "title": "Corrected twice"}, superseded=(first,)
        )
        return (*base, root, first, second)
    if name == "inactive_correction":
        first = _reuse_correction(201, root, {**root["payload"], "title": "Corrected once"})
        return (*base, root, first, _reuse_invalidation(203, first))
    if name == "structural_conflict":
        blocked = _reuse_task_created(210, title="Structurally corrected")
        correction = _reuse_correction(
            211,
            blocked,
            {**blocked["payload"], "expected_revision": str(root["event_id"])},
        )
        return (*base, root, blocked, correction)
    if name == "multi_head_conflict":
        contested = _reuse_task_created(220, title="Contested")
        branch_a = _reuse_correction(221, contested, {**contested["payload"], "title": "Branch A"})
        branch_b = _reuse_correction(222, contested, {**contested["payload"], "title": "Branch B"})
        return (*base, contested, branch_a, branch_b)
    if name in {"duplicate_root_verbatim", "duplicate_root_divergent", "duplicate_root_failing"}:
        first = _reuse_task_created(100, title="Alpha", task_number=100)
        if name == "duplicate_root_verbatim":
            second = _reuse_task_created(100, title="Alpha", task_number=100)
        elif name == "duplicate_root_divergent":
            second = _reuse_task_created(100, title="Beta", task_number=101)
        else:
            second = _reuse_task_created(
                100, title="Beta", priority="weird-priority", task_number=101
            )
        return (*base, first, second)
    if name == "malformed_payload":
        broken = _reuse_task_created(230, title="Malformed")
        del broken["payload"]["task_id"]
        return (*base, root, broken)
    if name == "malformed_correction":
        target = _reuse_task_created(240, title="Corrected into a bad priority")
        correction = _reuse_correction(
            241, target, {**target["payload"], "priority": "weird-priority"}
        )
        return (*base, target, correction)
    if name == "mixed_issue_order":
        blocked = _reuse_task_created(210, title="Structurally corrected")
        structural = _reuse_correction(
            211,
            blocked,
            {**blocked["payload"], "expected_revision": str(root["event_id"])},
        )
        target = _reuse_task_created(240, title="Corrected into a bad priority")
        malformed = _reuse_correction(
            241, target, {**target["payload"], "priority": "weird-priority"}
        )
        return (*base, blocked, structural, target, malformed)
    raise ValueError(f"unknown envelope reuse fixture {name!r}")


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
        if _ACTIVE_PHASE_COLLECTOR.get() is not collector:
            return original(*args, **keywords)
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
        if _ACTIVE_PHASE_COLLECTOR.get() is not collector:
            return original_project_events_once(*args, **keywords)
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


@contextmanager
def _instrumented_projection_context(collector: _PhaseCollector) -> Iterator[None]:
    """Activate one collector only in this context while globals are temporarily wrapped."""

    with _instrument_projection(collector):
        token = _ACTIVE_PHASE_COLLECTOR.set(collector)
        try:
            yield
        finally:
            _ACTIVE_PHASE_COLLECTOR.reset(token)


# A distinct sentinel is mandatory: ``parse_event_envelope`` legitimately returns
# ``None`` for whole event families (``objective.created`` among them), so ``None``
# as the "not cached" marker would silently never memoize half of a real ledger.
_UNCACHED = object()

_KEY_STRATEGIES = ("occurrence", "root_event_id")


@dataclass
class _EnvelopeReuseAccount:
    """Account what an envelope cache scoped to one ``project_events()`` would do.

    This is characterization instrumentation, not a cache: in ``account`` mode the
    original parser runs on every call and the memoized value is only compared
    against it, so the layer can never change a replay result and is strictly
    slower than the code it measures.  ``shadow`` mode does serve the memoized
    envelope, and is admissible only while every one of its samples still matches
    the uninstrumented baseline field by field.

    The key is ``(event_type, root_event_id, occurrence_ordinal, correction_id)``.
    The occurrence ordinal is load-bearing because the apply loop does not
    deduplicate a repeated root event id, and the effective correction id is kept
    as a fail-closed guard: nothing in production code enforces that such a cache
    stops at the end of one invocation.
    """

    key_strategy: str = "occurrence"
    mode: str = "account"
    record_payload_digests: bool = False
    record_parse_sequence: bool = False
    project_events_invocations: int = 0
    pass_labels: list[str] = field(default_factory=list)
    parse_calls_by_pass: dict[str, int] = field(
        default_factory=lambda: {label: 0 for label in _PASS_LABELS}
    )
    key_sequence_by_pass: dict[str, list[tuple[object, ...] | None]] = field(
        default_factory=lambda: {label: [] for label in _PASS_LABELS}
    )
    first_sight_parse_calls: int = 0
    repeat_parse_calls: int = 0
    repeat_parse_calls_typed: int = 0
    repeat_parse_calls_none: int = 0
    divergent_repeats: int = 0
    envelope_equality_checks: int = 0
    parse_calls_raised: int = 0
    failure_masked_by_cache: int = 0
    unkeyed_parse_calls: int = 0
    identity_mismatches: int = 0
    payload_digest_conflicts: int = 0
    parse_sequence: list[tuple[str, Mapping[str, object]]] = field(default_factory=list, repr=False)
    _cache: dict[tuple[object, ...], object] = field(default_factory=dict, repr=False)
    _payload_digests: dict[tuple[object, ...], str] = field(default_factory=dict, repr=False)
    _registry: dict[int, tuple[tuple[object, ...], Mapping[str, object]]] = field(
        default_factory=dict, repr=False
    )
    # Registered effective payloads are keyed by ``id()``, and CPython reuses the
    # address of a freed object.  Without a strong reference a later deepcopy
    # could land on a recycled address and fabricate a hit against an unrelated
    # event, so every registered payload is pinned for the whole experiment.
    _pinned_payloads: list[object] = field(default_factory=list, repr=False)
    _pass_calls: int = 0
    _current_pass: str = "normal"
    _occurrences: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.key_strategy not in _KEY_STRATEGIES:
            raise ValueError(f"unknown envelope reuse key strategy {self.key_strategy!r}")
        if self.mode not in {"account", "shadow"}:
            raise ValueError(f"unknown envelope reuse mode {self.mode!r}")

    def begin_invocation(self) -> None:
        """Start the one scope a reused envelope may live in."""

        self.project_events_invocations += 1
        self._cache = {}
        self._registry = {}
        self._pass_calls = 0
        self._occurrences = {}

    def begin_pass(self, keywords: Mapping[str, object]) -> None:
        """Label the pass and reset the per-pass occurrence ordinals."""

        self._pass_calls += 1
        self._current_pass = _project_events_once_label(self._pass_calls, keywords)
        self.pass_labels.append(self._current_pass)
        self._occurrences = {}

    def register_revision(self, revision: object) -> None:
        """Key the effective payload the apply loop is about to validate and parse.

        Registration happens at the revision-resolution site because that is where
        the root event id, the effective correction head and the per-pass
        occurrence ordinal are all known.  A registered event still contributes
        nothing when a later structural, identity, staleness or CAS guard drops it
        before the parser is reached.
        """

        effective = getattr(revision, "effective_event", None)
        if not isinstance(effective, Mapping):
            return
        root_event_id = str(getattr(revision, "root_event_id", effective.get("event_id", "")))
        ordinal = self._occurrences.get(root_event_id, 0) + 1
        self._occurrences[root_event_id] = ordinal
        correction_id = str(effective.get("_effective_correction_id") or "")
        if self.key_strategy == "root_event_id":
            key: tuple[object, ...] = (root_event_id,)
        else:
            key = (root_event_id, ordinal, correction_id)
        payload = effective.get("payload")
        if not isinstance(payload, Mapping):
            return
        self._registry[id(payload)] = (key, effective)
        self._pinned_payloads.append(payload)

    def observe_parse(
        self,
        original: Callable[[str, Mapping[str, object]], object],
        event_type: str,
        payload: Mapping[str, object],
    ) -> object:
        """Account one parse call and, in shadow mode only, serve the memoized value."""

        self.parse_calls_by_pass[self._current_pass] += 1
        entry = self._registry.get(id(payload))
        if entry is None:
            self.unkeyed_parse_calls += 1
            self.key_sequence_by_pass[self._current_pass].append(None)
            return original(event_type, payload)
        key, effective = entry
        if effective.get("payload") is not payload:
            # Defensive only: an id() collision after a freed payload would land
            # here.  Pinning makes it unreachable, so this counter is evidence of
            # a broken registry, never proof that the registry is sound.
            self.identity_mismatches += 1
        full_key = (event_type, *key)
        self.key_sequence_by_pass[self._current_pass].append(full_key)
        if self.record_parse_sequence:
            self.parse_sequence.append((event_type, payload))
        if self.record_payload_digests:
            digest = canonical_sha256({"event_type": event_type, "payload": payload})
            known = self._payload_digests.setdefault(full_key, digest)
            if known != digest:
                self.payload_digest_conflicts += 1
        cached = self._cache.get(full_key, _UNCACHED)
        if self.mode == "shadow" and cached is not _UNCACHED:
            self._count_repeat(cached)
            return cached
        try:
            parsed = original(event_type, payload)
        except Exception:
            self.parse_calls_raised += 1
            if cached is not _UNCACHED:
                self.failure_masked_by_cache += 1
            # A failure is never stored: ``str(exc)`` reaches the issue text and
            # therefore the snapshot digest, so a cached failure would have to
            # reproduce the exact exception identity to stay honest.
            raise
        if cached is _UNCACHED:
            self.first_sight_parse_calls += 1
            self._cache[full_key] = parsed
            return parsed
        self._count_repeat(cached)
        self.envelope_equality_checks += 1
        if not _equivalent_envelopes(cached, parsed):
            self.divergent_repeats += 1
        return parsed

    def cached_keys(self) -> frozenset[tuple[object, ...]]:
        """Return the keys the per-invocation cache still held when it was discarded.

        A parse that raised is absent here even though its key was looked up, which
        is how a test tells "never stored" apart from "stored and never reused".
        """

        return frozenset(self._cache)

    def _count_repeat(self, cached: object) -> None:
        self.repeat_parse_calls += 1
        if cached is None:
            self.repeat_parse_calls_none += 1
        else:
            self.repeat_parse_calls_typed += 1

    def report(self) -> EnvelopeReuseCounts:
        """Return the counters as published evidence, never as a latency claim."""

        total = sum(self.parse_calls_by_pass.values())
        return {
            "key_strategy": self.key_strategy,
            "mode": self.mode,
            "project_events_invocations": self.project_events_invocations,
            "pass_labels": list(self.pass_labels),
            "parse_calls_by_pass": dict(self.parse_calls_by_pass),
            "total_parse_calls": total,
            "distinct_keys": self.first_sight_parse_calls,
            "repeat_parse_calls": self.repeat_parse_calls,
            "repeat_parse_calls_typed": self.repeat_parse_calls_typed,
            "repeat_parse_calls_none": self.repeat_parse_calls_none,
            "divergent_repeats": self.divergent_repeats,
            "parse_calls_raised": self.parse_calls_raised,
            "failure_masked_by_cache": self.failure_masked_by_cache,
            "unkeyed_parse_calls": self.unkeyed_parse_calls,
            "identity_mismatches": self.identity_mismatches,
            "potential_hit_ratio": (self.repeat_parse_calls / total) if total else 0.0,
        }


def _equivalent_envelopes(cached: object, parsed: object) -> bool:
    """Compare a memoized envelope with a freshly parsed one, ``None`` results included."""

    if cached is None or parsed is None:
        return cached is None and parsed is None
    if type(cached) is not type(parsed):
        return False
    if cached != parsed:
        return False
    return serialize_event_envelope(cached) == serialize_event_envelope(parsed)


@contextmanager
def _instrument_envelope_reuse(account: _EnvelopeReuseAccount) -> Iterator[None]:
    """Temporarily wrap the four projection globals the reuse question depends on.

    ``project_events`` is wrapped as well so the per-invocation cache is created
    and discarded inside it: that is the whole scope claim under test, and no
    module-level dictionary or ``functools`` cache is involved.
    """

    originals: list[tuple[object, str, object]] = []

    def project_events_wrapper(*args: object, **keywords: object) -> object:
        if _ACTIVE_ENVELOPE_REUSE_ACCOUNT.get() is not account:
            return original_project_events(*args, **keywords)
        account.begin_invocation()
        return original_project_events(*args, **keywords)

    def project_events_once(*args: object, **keywords: object) -> object:
        if _ACTIVE_ENVELOPE_REUSE_ACCOUNT.get() is not account:
            return original_project_events_once(*args, **keywords)
        account.begin_pass(keywords)
        return original_project_events_once(*args, **keywords)

    def resolve_revision(*args: object, **keywords: object) -> object:
        if _ACTIVE_ENVELOPE_REUSE_ACCOUNT.get() is not account:
            return original_resolve_revision(*args, **keywords)
        revision = original_resolve_revision(*args, **keywords)
        account.register_revision(revision)
        return revision

    def parse_event_envelope(event_type: str, payload: Mapping[str, object]) -> object:
        if _ACTIVE_ENVELOPE_REUSE_ACCOUNT.get() is not account:
            return original_parse_event_envelope(event_type, payload)
        return account.observe_parse(original_parse_event_envelope, event_type, payload)

    try:
        original_project_events = projection.project_events
        original_project_events_once = projection._project_events_once
        original_resolve_revision = projection.resolve_revision
        original_parse_event_envelope = projection.parse_event_envelope
        originals.append((projection, "project_events", original_project_events))
        originals.append((projection, "_project_events_once", original_project_events_once))
        originals.append((projection, "resolve_revision", original_resolve_revision))
        originals.append((projection, "parse_event_envelope", original_parse_event_envelope))
        projection.project_events = project_events_wrapper
        projection._project_events_once = project_events_once
        projection.resolve_revision = resolve_revision
        projection.parse_event_envelope = parse_event_envelope
        yield
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)


@contextmanager
def _simulated_envelope_reuse(account: _EnvelopeReuseAccount) -> Iterator[None]:
    """Activate one reuse account only in this context while globals are wrapped."""

    with _instrument_envelope_reuse(account):
        token = _ACTIVE_ENVELOPE_REUSE_ACCOUNT.set(account)
        try:
            yield
        finally:
            _ACTIVE_ENVELOPE_REUSE_ACCOUNT.reset(token)


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


def _extended_replay_integrity(snapshot: ProjectSnapshot) -> ExtendedReplayIntegrity:
    """Capture every replay fact an envelope-reuse experiment is forbidden to move."""

    return {
        "snapshot_sha256": canonical_sha256(snapshot.to_dict()),
        "known_event_ids": sorted(snapshot.known_event_ids),
        "known_manifest_ids": sorted(snapshot.known_manifest_ids),
        "applied_event_ids": list(snapshot.effective_event_revisions),
        "effective_event_revisions": dict(snapshot.effective_event_revisions),
        "fixed_point_passes": int(snapshot.replay_metrics["fixed_point_passes"]),
        "replay_metrics": dict(snapshot.replay_metrics),
        "issues": [
            [issue.code, issue.severity, issue.message, list(issue.event_ids)]
            for issue in snapshot.issues
        ],
        "warnings_sequence": list(snapshot.warnings),
    }


def _assert_matching_extended_replay(
    actual: ExtendedReplayIntegrity, expected: ExtendedReplayIntegrity
) -> None:
    """Reject an envelope-reuse experiment that changed the replay result.

    The differing field is named because a broken cache does not crash: the apply
    loop turns the resulting ``KeyError`` into an ordinary
    ``domain_validation_rejected`` issue and the run still "succeeds".
    """

    if actual == expected:
        return
    differing = sorted(field for field in expected if actual.get(field) != expected[field])
    raise AssertionError(
        "envelope-reuse replay differs from the uninstrumented baseline in "
        f"{', '.join(differing)}: expected {expected!r}, got {actual!r}"
    )


def _envelope_reuse_sample(
    events: tuple[Mapping[str, Any], ...],
    *,
    known_manifest_ids: tuple[str, ...] | None,
    expected_integrity: ExtendedReplayIntegrity,
    arm: str,
    account: _EnvelopeReuseAccount | None,
) -> EnvelopeReuseSample:
    """Time one arm and prove it left the fixed-point result byte-for-byte alone.

    ``tracemalloc`` stays whole-run only.  The reuse arms additionally pin every
    registered payload, so their peaks are inflated by the instrumentation and
    are not comparable with the A6.3/A6.4 peaks.
    """

    def replay() -> ProjectSnapshot:
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        try:
            snapshot = projection.project_events(events, known_manifest_ids=known_manifest_ids)
        finally:
            timing["elapsed"] = time.perf_counter() - started
            _, timing["peak"] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        return snapshot

    timing: dict[str, float] = {}
    if account is None:
        snapshot = replay()
    else:
        with _simulated_envelope_reuse(account):
            snapshot = replay()
    integrity = _extended_replay_integrity(snapshot)
    _assert_matching_extended_replay(integrity, expected_integrity)
    return {
        "arm": arm,
        "elapsed_seconds": float(timing["elapsed"]),
        "peak_allocated_bytes": int(timing["peak"]),
        "integrity_sha256": canonical_sha256(integrity),
        "counts": account.report() if account is not None else None,
    }


def _verify_envelope_reuse_purity(
    events: tuple[Mapping[str, Any], ...],
    *,
    known_manifest_ids: tuple[str, ...] | None,
    expected_integrity: ExtendedReplayIntegrity,
) -> tuple[EnvelopeReusePurity, IsolatedParseCost]:
    """Check key purity and measure parse cost outside the replay, both untimed here.

    Digesting every parse input is far too expensive to leave inside a timed arm,
    so this runs on its own and its wall clock is deliberately not published.
    """

    account = _EnvelopeReuseAccount(record_payload_digests=True, record_parse_sequence=True)
    with _simulated_envelope_reuse(account):
        snapshot = projection.project_events(events, known_manifest_ids=known_manifest_ids)
    _assert_matching_extended_replay(_extended_replay_integrity(snapshot), expected_integrity)
    purity: EnvelopeReusePurity = {
        "distinct_keys": account.first_sight_parse_calls,
        "payload_digest_conflicts": account.payload_digest_conflicts,
        "envelope_equality_checks": account.envelope_equality_checks,
        "divergent_repeats": account.divergent_repeats,
        "unkeyed_parse_calls": account.unkeyed_parse_calls,
        "identity_mismatches": account.identity_mismatches,
    }
    parse = projection.parse_event_envelope
    raised = 0
    gc.collect()
    started = time.perf_counter()
    for event_type, payload in account.parse_sequence:
        try:
            parse(event_type, payload)
        except Exception:  # pragma: no cover - a quiet ledger parses cleanly
            raised += 1
    elapsed = time.perf_counter() - started
    isolated: IsolatedParseCost = {
        "parse_calls": len(account.parse_sequence),
        "elapsed_seconds": elapsed,
        "parse_calls_raised": raised,
    }
    return purity, isolated


_ENVELOPE_REUSE_CAVEATS = (
    "Characterization only: no production envelope cache exists or is approved.",
    "Repeat parse calls are an upper bound on avoidable work, not a latency claim.",
    "Saving is quoted only as the paired account-minus-shadow median; the wrapper "
    "tax is the same order of magnitude as the effect, so cross-arm medians are "
    "never subtracted or summed.",
    "The shadow arm serves a cache with none of a production cache's key, "
    "lifetime or invalidation cost, so its saving is an upper bound.",
    "Reuse-arm tracemalloc peaks are inflated by payload pinning and are not "
    "comparable with the A6.3/A6.4 peaks.",
    "Synthetic fixtures characterize semantics, not cost: their filler events "
    "parse far faster than real ledger events.",
    "Parse cost still bundles the A5.2 family re-validation; fixing that double "
    "validation is an unmeasured alternative to reuse.",
    "The isolated parse loop replays the same calls back to back on warm data "
    "and is a floor, not a substitute for the in-replay parsing phase; where the "
    "two disagree the disagreement is reported, never averaged away.",
)


def _summarize_envelope_reuse(
    events: tuple[Mapping[str, Any], ...],
    *,
    known_manifest_ids: tuple[str, ...] | None,
    expected_integrity: ExtendedReplayIntegrity,
    repeats: int,
) -> EnvelopeReuseReport:
    """Run the three interleaved arms plus the untimed verification arms.

    Arms are interleaved inside each repeat so a machine that drifts during the
    run moves all three together.  If the serving arm ever disagrees with the
    baseline it is dropped and the accounting-only numbers ship alone: the
    integrity assertion is never relaxed to keep a measurement.
    """

    samples: list[EnvelopeReuseSample] = []
    paired: list[EnvelopeReusePairedSample] = []
    account_counts: list[EnvelopeReuseCounts] = []
    shadow_counts: list[EnvelopeReuseCounts] = []
    dropped_reason: str | None = None
    for repeat_index in range(repeats):
        baseline_sample = _envelope_reuse_sample(
            events,
            known_manifest_ids=known_manifest_ids,
            expected_integrity=expected_integrity,
            arm="reuse_baseline",
            account=None,
        )
        account_sample = _envelope_reuse_sample(
            events,
            known_manifest_ids=known_manifest_ids,
            expected_integrity=expected_integrity,
            arm="account",
            account=_EnvelopeReuseAccount(),
        )
        samples.extend((baseline_sample, account_sample))
        account_report = account_sample["counts"]
        if account_report is None:  # pragma: no cover - defensive against a broken arm
            raise AssertionError("the accounting arm must publish its counters")
        account_counts.append(account_report)
        shadow_sample: EnvelopeReuseSample | None = None
        if dropped_reason is None:
            try:
                shadow_sample = _envelope_reuse_sample(
                    events,
                    known_manifest_ids=known_manifest_ids,
                    expected_integrity=expected_integrity,
                    arm="shadow",
                    account=_EnvelopeReuseAccount(mode="shadow"),
                )
            except AssertionError as exc:
                dropped_reason = str(exc)
        if shadow_sample is not None:
            samples.append(shadow_sample)
            shadow_report = shadow_sample["counts"]
            if shadow_report is None:  # pragma: no cover - defensive against a broken arm
                raise AssertionError("the serving arm must publish its counters")
            shadow_counts.append(shadow_report)
        paired.append(
            {
                "repeat_index": repeat_index,
                "reuse_baseline_elapsed_seconds": baseline_sample["elapsed_seconds"],
                "account_elapsed_seconds": account_sample["elapsed_seconds"],
                "shadow_elapsed_seconds": (
                    shadow_sample["elapsed_seconds"] if shadow_sample is not None else None
                ),
                "paired_saving_seconds": (
                    account_sample["elapsed_seconds"] - shadow_sample["elapsed_seconds"]
                    if shadow_sample is not None
                    else None
                ),
                "wrapper_tax_seconds": (
                    account_sample["elapsed_seconds"] - baseline_sample["elapsed_seconds"]
                ),
            }
        )
    _assert_stable_reuse_counts(account_counts, arm="account")
    _assert_stable_reuse_counts(shadow_counts, arm="shadow")
    purity, isolated = _verify_envelope_reuse_purity(
        events,
        known_manifest_ids=known_manifest_ids,
        expected_integrity=expected_integrity,
    )
    serving_included = bool(shadow_counts) and dropped_reason is None
    savings = [
        sample["paired_saving_seconds"]
        for sample in paired
        if sample["paired_saving_seconds"] is not None
    ]
    return {
        "scope": "envelope reuse accounted inside one project_events() invocation",
        "key_strategy": account_counts[0]["key_strategy"],
        "repeats": repeats,
        "counts": account_counts[0],
        "samples": samples,
        "paired_samples": paired,
        "median_reuse_baseline_elapsed_seconds": statistics.median(
            sample["reuse_baseline_elapsed_seconds"] for sample in paired
        ),
        "median_account_elapsed_seconds": statistics.median(
            sample["account_elapsed_seconds"] for sample in paired
        ),
        "median_shadow_elapsed_seconds": (
            statistics.median(
                sample["elapsed_seconds"] for sample in samples if sample["arm"] == "shadow"
            )
            if serving_included
            else None
        ),
        "median_paired_saving_seconds": statistics.median(savings) if savings else None,
        "median_wrapper_tax_seconds": statistics.median(
            sample["wrapper_tax_seconds"] for sample in paired
        ),
        "max_peak_allocated_bytes": {
            arm: max(sample["peak_allocated_bytes"] for sample in samples if sample["arm"] == arm)
            for arm in dict.fromkeys(sample["arm"] for sample in samples)
        },
        "purity": purity,
        "isolated_parse_cost": isolated,
        "baseline_integrity": expected_integrity,
        "serving_arm_included": serving_included,
        "serving_arm_dropped_reason": dropped_reason,
        "caveats": list(_ENVELOPE_REUSE_CAVEATS),
    }


def _assert_stable_reuse_counts(counts: list[EnvelopeReuseCounts], *, arm: str) -> None:
    """Refuse a run whose parse accounting drifted between otherwise identical repeats."""

    for index, sample in enumerate(counts[1:], start=1):
        if sample != counts[0]:
            raise AssertionError(
                f"envelope reuse {arm} counters changed between repeats: "
                f"repeat 0 reported {counts[0]!r}, repeat {index} reported {sample!r}"
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
    with _instrumented_projection_context(collector):
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
    manager: CommonsManager,
    *,
    repeats: int,
    source: str,
    include_full_paths: bool = True,
    envelope_reuse: bool = True,
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
        extended_baseline_integrity = _extended_replay_integrity(baseline)
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
        envelope_reuse_profile = (
            _summarize_envelope_reuse(
                in_memory_events,
                known_manifest_ids=in_memory_manifests,
                expected_integrity=extended_baseline_integrity,
                repeats=repeats,
            )
            if envelope_reuse
            else None
        )
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
        "schema": "agent_commons.a6_read_path_profile.v2",
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
        "envelope_reuse_profile": envelope_reuse_profile,
    }


def profile_read_paths(
    *, event_count: int = 20_000, repeats: int = 3, envelope_reuse: bool = True
) -> ReadPathProfile:
    """Measure A6's three comparable warm read paths over one synthetic ledger."""

    with tempfile.TemporaryDirectory(prefix="agent-commons-a6-profile-") as temporary:
        manager = _write_workspace(Path(temporary), event_count=event_count)
        return _profile_manager_read_paths(
            manager,
            repeats=repeats,
            source="synthetic_two_pass",
            envelope_reuse=envelope_reuse,
        )


def profile_projection_components(
    *, event_count: int = 20_000, repeats: int = 3, envelope_reuse: bool = True
) -> ReadPathProfile:
    """Measure only the three separable verified-projection components synthetically."""

    with tempfile.TemporaryDirectory(prefix="agent-commons-a6-profile-") as temporary:
        manager = _write_workspace(Path(temporary), event_count=event_count)
        return _profile_manager_read_paths(
            manager,
            repeats=repeats,
            source="synthetic_two_pass",
            include_full_paths=False,
            envelope_reuse=envelope_reuse,
        )


def profile_workspace_read_paths(
    repo_root: Path,
    *,
    state_root: Path | None = None,
    repeats: int = 3,
    envelope_reuse: bool = True,
) -> ReadPathProfile:
    """Measure the same paths against one quiet, existing workspace ledger."""

    manager = CommonsManager(repo_root, state_root=state_root)
    return _profile_manager_read_paths(
        manager,
        repeats=repeats,
        source="existing_workspace",
        envelope_reuse=envelope_reuse,
    )


def profile_workspace_projection_components(
    repo_root: Path,
    *,
    state_root: Path | None = None,
    repeats: int = 3,
    envelope_reuse: bool = True,
) -> ReadPathProfile:
    """Measure only the three separable components on one quiet existing workspace."""

    manager = CommonsManager(repo_root, state_root=state_root)
    return _profile_manager_read_paths(
        manager,
        repeats=repeats,
        source="existing_workspace",
        include_full_paths=False,
        envelope_reuse=envelope_reuse,
    )


def main() -> None:
    """Parse the reproducible benchmark options and emit canonical JSON evidence."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--event-count", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--components-only", action="store_true")
    parser.add_argument(
        "--envelope-reuse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="account the envelope reuse a per-invocation cache could achieve",
    )
    arguments = parser.parse_args()
    if arguments.state_root is not None and arguments.repo is None:
        parser.error("--state-root requires --repo")
    try:
        if arguments.repo is None:
            if arguments.components_only:
                result = profile_projection_components(
                    event_count=arguments.event_count,
                    repeats=arguments.repeats,
                    envelope_reuse=arguments.envelope_reuse,
                )
            else:
                result = profile_read_paths(
                    event_count=arguments.event_count,
                    repeats=arguments.repeats,
                    envelope_reuse=arguments.envelope_reuse,
                )
        else:
            if arguments.components_only:
                result = profile_workspace_projection_components(
                    arguments.repo,
                    state_root=arguments.state_root,
                    repeats=arguments.repeats,
                    envelope_reuse=arguments.envelope_reuse,
                )
            else:
                result = profile_workspace_read_paths(
                    arguments.repo,
                    state_root=arguments.state_root,
                    repeats=arguments.repeats,
                    envelope_reuse=arguments.envelope_reuse,
                )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
