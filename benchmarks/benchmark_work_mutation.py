"""Baseline populated-ledger mutation latency (measurement only).

Run from a source checkout with::

    uv run --locked python benchmarks/benchmark_work_mutation.py --profile small --repeats 2
    AGENT_COMMONS_LARGE_BENCHMARK=1 uv run --locked python benchmarks/benchmark_work_mutation.py \\
        --profile large --repeats 5 --write-docs

The benchmark builds a disposable synthetic workspace in a temporary directory.
Fixture construction is never included in the mutation timings.  The checkout
state root and the operator workspace are never opened.

Measured mutations go through ``CommonsManager`` (task create/take/complete/
submit and decision propose) plus one panel HTTP ``POST /api/tasks``.  Each
sample is split into exclusive phases: lock wait, lock hold residual, canonical
reads/validation, projection replay, receipt preparation, append, and
reconcile.  Warm is the second call on the same manager; cold is a fresh
manager in the already-imported process.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from agent_commons import __version__
from agent_commons.core.canonical import canonical_json_file_bytes
from agent_commons.core.ids import stable_id
from agent_commons.domain import lifecycle, projection
from agent_commons.services import manager as manager_module
from agent_commons.services.manager import PAYLOAD_SCHEMAS, CommonsManager
from agent_commons.storage.events import EventStore
from agent_commons.storage.manifests import ManifestStore
from agent_commons.storage.receipt_recovery import ReceiptRecovery
from agent_commons.ui.security import SESSION_COOKIE_NAME

SCHEMA = "agent_commons.work_mutation_profile.v1"
DOCS_PATH = Path("docs/performance/2026-09-10-mutation-baseline.md")
LARGE_BENCHMARK_ENV = "AGENT_COMMONS_LARGE_BENCHMARK"
_BASE_RECORDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_LIMITS = {
    "max_depth": 0,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "provider_units", "limit": 1},
}

PHASE_LABELS = (
    "lock_wait",
    "lock_hold",
    "canonical_reads_validation",
    "projection_replay",
    "receipt_preparation",
    "append",
    "reconcile",
)

MUTATION_LABELS = (
    "task_create",
    "task_take",
    "task_complete",
    "task_submit",
    "decision_propose",
    "panel_http_task_create",
)


class PhaseTiming(TypedDict):
    calls: int
    exclusive_elapsed_seconds: float


class MutationSample(TypedDict):
    elapsed_seconds: float
    residual_elapsed_seconds: float
    read_calls: int
    read_bytes: int
    phases: dict[str, PhaseTiming]


class MutationAggregate(TypedDict):
    samples: int
    min_seconds: float
    median_seconds: float
    p95_seconds: float
    median_read_calls: float
    median_read_bytes: float
    median_phase_exclusive_seconds: dict[str, float]
    p95_phase_exclusive_seconds: dict[str, float]
    median_phase_calls: dict[str, float]


class MutationReport(TypedDict):
    cold: MutationAggregate
    warm: MutationAggregate
    samples: dict[str, list[MutationSample]]


class WorkMutationProfile(TypedDict):
    schema: str
    python: str
    platform: str
    hardware: dict[str, Any]
    profile: str
    repeats: int
    fixture: dict[str, Any]
    fixture_build_seconds: float
    mutations: dict[str, MutationReport]


@dataclass
class ProfileSpec:
    name: str
    leftover_ready: int
    leftover_active: int
    leftover_completed: int
    artifacts: int
    decisions: int
    delegations: int
    accepted_cycles: int


PROFILES = {
    "small": ProfileSpec(
        name="small",
        leftover_ready=260,
        leftover_active=20,
        leftover_completed=20,
        artifacts=8,
        decisions=8,
        delegations=4,
        accepted_cycles=0,
    ),
    "large": ProfileSpec(
        name="large",
        leftover_ready=24,
        leftover_active=24,
        leftover_completed=24,
        artifacts=350,
        decisions=80,
        delegations=40,
        accepted_cycles=318,
    ),
}


@dataclass(frozen=True)
class TaskRef:
    task_id: str
    revision: str


@dataclass
class MutationFixture:
    repo: Path
    state_root: Path
    session_id: str
    workspace_id: str
    event_count: int
    manifest_count: int
    task_count: int
    review_count: int
    decision_count: int
    delegation_count: int
    artifact_count: int
    ready: deque[TaskRef]
    active: deque[TaskRef]
    completed: deque[TaskRef]
    build_seconds: float


@dataclass
class _PhaseFrame:
    label: str
    started_at: float
    child_elapsed_seconds: float = 0.0


@dataclass
class _PhaseCollector:
    phase_elapsed_seconds: dict[str, float] = field(
        default_factory=lambda: {label: 0.0 for label in PHASE_LABELS}
    )
    phase_call_counts: dict[str, int] = field(
        default_factory=lambda: {label: 0 for label in PHASE_LABELS}
    )
    read_calls: int = 0
    read_bytes: int = 0
    _stack: list[_PhaseFrame] = field(default_factory=list)

    @contextmanager
    def phase(self, label: str) -> Iterator[None]:
        if label not in self.phase_elapsed_seconds:
            raise ValueError(f"unknown mutation phase {label}")
        frame = _PhaseFrame(label=label, started_at=time.perf_counter())
        self._stack.append(frame)
        self.phase_call_counts[label] += 1
        try:
            yield
        finally:
            elapsed = time.perf_counter() - frame.started_at
            popped = self._stack.pop()
            if popped is not frame:  # pragma: no cover - defensive against broken wrappers
                raise RuntimeError("mutation phase timing stack became unbalanced")
            exclusive = max(0.0, elapsed - frame.child_elapsed_seconds)
            self.phase_elapsed_seconds[label] += exclusive
            if self._stack:
                self._stack[-1].child_elapsed_seconds += elapsed

    def add(self, label: str, elapsed_seconds: float) -> None:
        if label not in self.phase_elapsed_seconds:
            raise ValueError(f"unknown mutation phase {label}")
        self.phase_call_counts[label] += 1
        self.phase_elapsed_seconds[label] += max(0.0, elapsed_seconds)

    def report(self) -> dict[str, PhaseTiming]:
        return {
            label: {
                "calls": self.phase_call_counts[label],
                "exclusive_elapsed_seconds": self.phase_elapsed_seconds[label],
            }
            for label in PHASE_LABELS
        }


_ACTIVE_COLLECTOR: ContextVar[_PhaseCollector | None] = ContextVar(
    "agent_commons_work_mutation_collector", default=None
)


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile of empty sample")
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def runtime_metadata() -> dict[str, Any]:
    """Collect cheap local hardware/runtime facts. Never probes the network."""

    metadata: dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "",
        "cpu_count": os.cpu_count(),
        "system": platform.system(),
    }
    brand_path = Path("/usr/sbin/sysctl")
    if brand_path.is_file() and sys.platform == "darwin":
        try:
            brand = subprocess.run(
                [str(brand_path), "-n", "machdep.cpu.brand_string"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if brand.returncode == 0 and brand.stdout.strip():
                metadata["cpu_brand"] = brand.stdout.strip()
        except (OSError, TimeoutError):
            pass
    return metadata


def _actor_document(session: Mapping[str, Any]) -> dict[str, Any]:
    actor = {
        "principal_id": str(session["principal"]),
        "session_id": str(session["session_id"]),
        "stable_instance_id": str(session["stable_instance_id"]),
        "role_id": str(session["role"]),
        "client": str(session["client"]),
        "software": str(session["software"]),
        "capabilities": list(session.get("capabilities") or ()),
    }
    return actor


def _namespace(workspace_id: str, session_id: str) -> str:
    return f"commons:{workspace_id}:{session_id}"


class _LedgerWriter:
    """Write valid canonical events without going through record_event."""

    def __init__(
        self,
        *,
        workspace_id: str,
        builder_actor: Mapping[str, Any],
        reviewer_actor: Mapping[str, Any],
        event_root: Path,
    ) -> None:
        self.workspace_id = workspace_id
        self.builder_actor = dict(builder_actor)
        self.reviewer_actor = dict(reviewer_actor)
        self.builder_namespace = _namespace(workspace_id, str(builder_actor["session_id"]))
        self.reviewer_namespace = _namespace(workspace_id, str(reviewer_actor["session_id"]))
        self.event_root = event_root
        self.seq = 0
        self.event_count = 0

    def entity_id(self, kind: str, label: str) -> str:
        return stable_id(kind, f"{self.workspace_id}:{kind}:{label}")

    def add(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        subject: Mapping[str, str],
        reviewer: bool = False,
        relations: Sequence[Mapping[str, Any]] = (),
        tags: Sequence[str] = (),
    ) -> str:
        self.seq += 1
        event_id = stable_id("evt", f"{self.workspace_id}:evt:{self.seq:08d}")
        recorded_at = (_BASE_RECORDED_AT + timedelta(seconds=self.seq)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        family = event_type.split(".", 1)[0]
        actor = self.reviewer_actor if reviewer else self.builder_actor
        namespace = self.reviewer_namespace if reviewer else self.builder_namespace
        document = {
            "schema": "commons.event.v1",
            "payload_schema": PAYLOAD_SCHEMAS[family],
            "event_id": event_id,
            "workspace_id": self.workspace_id,
            "event_type": event_type,
            "recorded_at": recorded_at,
            "actor": dict(actor),
            "subject_refs": [dict(subject)],
            "idempotency_namespace": namespace,
            "idempotency_key": f"fixture:{event_type}:{self.seq:08d}",
            "provenance": {
                "writer": "agent-commons-benchmark",
                "writer_version": __version__,
                "source_kind": "synthetic",
                "source_refs": [],
            },
            "payload": dict(payload),
            "relations": [dict(item) for item in relations],
            "tags": sorted(set(tags)),
        }
        path = self.event_root / f"{event_id}.json"
        path.write_bytes(canonical_json_file_bytes(document))
        self.event_count += 1
        return event_id


def _relation(
    subject: Mapping[str, str], predicate: str, object_ref: Mapping[str, str]
) -> dict[str, Any]:
    return {"subject": dict(subject), "predicate": predicate, "object": dict(object_ref)}


def _add_created_task(writer: _LedgerWriter, label: str, *, title: str) -> tuple[str, str]:
    task_id = writer.entity_id("task", label)
    subject = {"kind": "task", "id": task_id}
    created = writer.add(
        "task.created",
        {
            "task_id": task_id,
            "title": title,
            "description": f"Synthetic mutation-baseline task {label}",
            "acceptance_criteria": ["the fixture task projects"],
            "priority": "normal",
            "dependencies": [],
        },
        subject=subject,
        tags=("task",),
    )
    return task_id, created


def _advance_task(
    writer: _LedgerWriter,
    task_id: str,
    revision: str,
    action: str,
    extra: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "task_id": task_id,
        "expected_revision": revision,
        **dict(extra or {}),
    }
    return writer.add(
        f"task.{action}",
        payload,
        subject={"kind": "task", "id": task_id},
        tags=("task",),
    )


def _seed_ready(writer: _LedgerWriter, count: int, *, prefix: str) -> tuple[TaskRef, ...]:
    refs: list[TaskRef] = []
    for index in range(count):
        task_id, created = _add_created_task(
            writer,
            f"{prefix}-ready-{index:04d}",
            title=f"Ready fixture task {prefix} {index:04d}",
        )
        refs.append(TaskRef(task_id, created))
    return tuple(refs)


def _seed_active(writer: _LedgerWriter, count: int, *, prefix: str, owner_session_id: str) -> None:
    for index in range(count):
        task_id, revision = _add_created_task(
            writer,
            f"{prefix}-active-{index:04d}",
            title=f"Active fixture task {prefix} {index:04d}",
        )
        revision = _advance_task(
            writer,
            task_id,
            revision,
            "taken",
            {"owner_session_id": owner_session_id},
        )
        _advance_task(writer, task_id, revision, "started")


def _seed_completed(
    writer: _LedgerWriter, count: int, *, prefix: str, owner_session_id: str
) -> None:
    for index in range(count):
        task_id, revision = _add_created_task(
            writer,
            f"{prefix}-completed-{index:04d}",
            title=f"Completed fixture task {prefix} {index:04d}",
        )
        revision = _advance_task(
            writer,
            task_id,
            revision,
            "taken",
            {"owner_session_id": owner_session_id},
        )
        revision = _advance_task(writer, task_id, revision, "started")
        _advance_task(
            writer,
            task_id,
            revision,
            "completed",
            {"summary": "fixture completed", "artifact_refs": [], "artifact_bindings": []},
        )


def _seed_accepted_cycle(writer: _LedgerWriter, count: int, *, owner_session_id: str) -> None:
    for index in range(count):
        task_id, revision = _add_created_task(
            writer,
            f"accepted-{index:04d}",
            title=f"Accepted fixture task {index:04d}",
        )
        subject = {"kind": "task", "id": task_id}
        revision = _advance_task(
            writer, task_id, revision, "taken", {"owner_session_id": owner_session_id}
        )
        revision = _advance_task(writer, task_id, revision, "started")
        revision = _advance_task(
            writer,
            task_id,
            revision,
            "completed",
            {"summary": "fixture work finished", "artifact_refs": [], "artifact_bindings": []},
        )
        submitted = _advance_task(
            writer,
            task_id,
            revision,
            "submitted",
            {"summary": "fixture work submitted", "artifact_refs": [], "artifact_bindings": []},
        )
        review_id = writer.entity_id("review", f"accepted-{index:04d}")
        review_subject = {"kind": "review", "id": review_id}
        requested = writer.add(
            "review.requested",
            {
                "review_id": review_id,
                "target_ref": dict(subject),
                "target_revision": submitted,
                "criteria": ["fixture correctness"],
                "independent": True,
            },
            subject=review_subject,
            relations=(_relation(review_subject, "reviews", subject),),
            tags=("review",),
        )
        completed = writer.add(
            "review.completed",
            {
                "review_id": review_id,
                "expected_revision": requested,
                "target_revision": submitted,
                "verdict": "approved",
                "summary": "fixture review approved",
                "evidence_refs": [],
            },
            subject=review_subject,
            reviewer=True,
            tags=("review",),
        )
        writer.add(
            "task.accepted",
            {
                "task_id": task_id,
                "expected_revision": submitted,
                "summary": "fixture accepted",
                "acceptance_review": {
                    "ref": dict(review_subject),
                    "revision": completed,
                },
            },
            subject=subject,
            tags=("task",),
        )


def _seed_artifacts(manager: CommonsManager, writer: _LedgerWriter, count: int) -> int:
    source_root = manager.repo_root / "fixture-artifacts"
    source_root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        relative = Path("fixture-artifacts") / f"item-{index:04d}.txt"
        source = manager.repo_root / relative
        source.write_text(f"fixture artifact {index:04d}\n", encoding="utf-8")
        artifact_id = writer.entity_id("artifact", f"item-{index:04d}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        revision = f"sha256:{digest}"
        manifest = {
            "schema": "commons.manifest.artifact.v1",
            "kind": "artifact",
            "artifact_id": artifact_id,
            "revision": revision,
            "source": {"path": relative.as_posix()},
            "media_type": "text/plain",
            "size_bytes": source.stat().st_size,
            "classification": "internal",
            "captured": False,
            "metadata": {},
        }
        record = manager.manifests.put(manifest)
        subject = {"kind": "artifact", "id": artifact_id}
        writer.add(
            "artifact.registered",
            {
                "artifact_id": artifact_id,
                "manifest_ref": record.manifest_id,
                "revision": revision,
                "classification": "internal",
            },
            subject=subject,
            relations=(_relation(subject, "uses", {"kind": "manifest", "id": record.manifest_id}),),
            tags=("artifact",),
        )
    return count


def _seed_decisions(writer: _LedgerWriter, count: int) -> None:
    for index in range(count):
        decision_id = writer.entity_id("decision", f"item-{index:04d}")
        writer.add(
            "decision.proposed",
            {
                "decision_id": decision_id,
                "scope": f"fixture-scope-{index:04d}",
                "proposal": f"Keep the synthetic fixture decision {index:04d}",
                "alternatives": ["leave the fixture unchanged"],
            },
            subject={"kind": "decision", "id": decision_id},
            tags=("decision",),
        )


def _seed_delegations_from_refs(
    writer: _LedgerWriter,
    count: int,
    *,
    parent_session_id: str,
    targets: Sequence[TaskRef],
) -> None:
    if count and not targets:
        raise ValueError("delegation seeding requires at least one ready task")
    for index in range(count):
        target = targets[index % len(targets)]
        delegation_id = writer.entity_id("delegation", f"item-{index:04d}")
        subject = {"kind": "delegation", "id": delegation_id}
        target_ref = {"kind": "task", "id": target.task_id}
        writer.add(
            "delegation.requested",
            {
                "delegation_id": delegation_id,
                "target_ref": target_ref,
                "target_revision": target.revision,
                "target_profile": "grok-builder",
                "purpose": "implementation",
                "parent_session_id": parent_session_id,
                "root_delegation_id": delegation_id,
                "depth": 0,
                "limits": dict(_LIMITS),
            },
            subject=subject,
            relations=(_relation(subject, "targets", target_ref),),
            tags=("delegation", "implementation"),
        )


def _init_git(repo: Path) -> None:
    """Make the disposable repo a git checkout so the panel write path can run."""

    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "Benchmark",
            "GIT_AUTHOR_EMAIL": "benchmark@example.invalid",
            "GIT_COMMITTER_NAME": "Benchmark",
            "GIT_COMMITTER_EMAIL": "benchmark@example.invalid",
        },
    )
    (repo / "README.md").write_text("mutation-baseline fixture\n", encoding="utf-8")


def _bind_manager(fixture: MutationFixture) -> CommonsManager:
    return CommonsManager(
        fixture.repo, state_root=fixture.state_root, session_id=fixture.session_id
    )


def _task_refs_by_state(snapshot: Any, state: str) -> deque[TaskRef]:
    refs: deque[TaskRef] = deque()
    for task in snapshot.tasks.values():
        if task.get("state") == state:
            refs.append(TaskRef(str(task["id"]), str(task["revision"])))
    return refs


def build_mutation_fixture(root: Path, *, profile: str) -> MutationFixture:
    """Create one disposable workspace. Time spent here is not a mutation sample."""

    spec = PROFILES[profile]
    started = time.perf_counter()
    repo = root / "repo"
    state_root = root / "state"
    repo.mkdir()
    _init_git(repo)
    CommonsManager.initialize(
        repo, integrations=(), workspace_name=f"mutation-baseline-{spec.name}"
    )
    opener = CommonsManager(repo, state_root=state_root)
    builder_session = opener.start_session(
        stable_instance_id="mutation-baseline-builder",
        principal="local-operator",
        client="benchmark",
        software="agent-commons-benchmark",
        role="operator",
    )
    reviewer_session = opener.start_session(
        stable_instance_id="mutation-baseline-reviewer",
        principal="independent-reviewer",
        client="benchmark",
        software="agent-commons-benchmark",
        role="independent-reviewer",
    )
    manager = CommonsManager(
        repo, state_root=state_root, session_id=str(builder_session["session_id"])
    )
    event_root = manager.paths.events / "2026" / "01" / "01"
    event_root.mkdir(parents=True, exist_ok=True)
    writer = _LedgerWriter(
        workspace_id=manager.workspace_id,
        builder_actor=_actor_document(builder_session),
        reviewer_actor=_actor_document(reviewer_session),
        event_root=event_root,
    )
    owner = str(builder_session["session_id"])
    ready_targets = _seed_ready(writer, spec.leftover_ready, prefix=spec.name)
    _seed_active(writer, spec.leftover_active, prefix=spec.name, owner_session_id=owner)
    _seed_completed(writer, spec.leftover_completed, prefix=spec.name, owner_session_id=owner)
    _seed_accepted_cycle(writer, spec.accepted_cycles, owner_session_id=owner)
    _seed_artifacts(manager, writer, spec.artifacts)
    _seed_decisions(writer, spec.decisions)
    _seed_delegations_from_refs(
        writer,
        spec.delegations,
        parent_session_id=owner,
        targets=ready_targets,
    )
    records = list(manager.events.iter_events())
    manager.receipt_recovery.reconcile(records, actor=manager._actor())
    snapshot = projection.project_events(
        (record.event for record in records),
        known_manifest_ids=(record.manifest_id for record in manager.manifests.iter_manifests()),
    )
    if snapshot.issues:
        codes = ", ".join(sorted({issue.code for issue in snapshot.issues}))
        raise AssertionError(f"synthetic fixture projected with issues: {codes}")
    build_seconds = time.perf_counter() - started
    return MutationFixture(
        repo=repo,
        state_root=state_root,
        session_id=str(builder_session["session_id"]),
        workspace_id=manager.workspace_id,
        event_count=len(snapshot.known_event_ids),
        manifest_count=len(snapshot.known_manifest_ids),
        task_count=len(snapshot.tasks),
        review_count=len(snapshot.reviews),
        decision_count=len(snapshot.decisions),
        delegation_count=len(snapshot.delegations),
        artifact_count=len(snapshot.artifacts),
        ready=_task_refs_by_state(snapshot, "ready"),
        active=_task_refs_by_state(snapshot, "active"),
        completed=_task_refs_by_state(snapshot, "completed"),
        build_seconds=build_seconds,
    )


def _timed_wrapper(
    collector: _PhaseCollector, label: str, original: Callable[..., Any]
) -> Callable[..., Any]:
    def wrapped(*args: Any, **keywords: Any) -> Any:
        if _ACTIVE_COLLECTOR.get() is not collector:
            return original(*args, **keywords)
        with collector.phase(label):
            return original(*args, **keywords)

    return wrapped


def _read_wrapper(collector: _PhaseCollector, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(self: Any, path: str | Path) -> Any:
        if _ACTIVE_COLLECTOR.get() is not collector:
            return original(self, path)
        with collector.phase("canonical_reads_validation"):
            record = original(self, path)
        collector.read_calls += 1
        size = getattr(getattr(record, "path", None), "stat", None)
        if size is not None:
            collector.read_bytes += int(record.path.stat().st_size)
        return record

    return wrapped


@contextmanager
def _instrument_write_path(collector: _PhaseCollector) -> Iterator[None]:
    originals: list[tuple[object, str, object]] = []

    def replace(module: object, name: str, label: str) -> None:
        original = getattr(module, name)
        originals.append((module, name, original))
        setattr(module, name, _timed_wrapper(collector, label, original))

    original_lock = CommonsManager._canonical_write_lock

    @contextmanager
    def wrapped_lock(self: CommonsManager) -> Iterator[None]:
        if _ACTIVE_COLLECTOR.get() is not collector:
            with original_lock(self):
                yield
            return
        handle = original_lock(self)
        waited = time.perf_counter()
        handle.__enter__()
        collector.add("lock_wait", time.perf_counter() - waited)
        try:
            with collector.phase("lock_hold"):
                yield
        except BaseException as exc:
            swallowed = handle.__exit__(type(exc), exc, exc.__traceback__)
            if swallowed:
                return
            raise
        else:
            handle.__exit__(None, None, None)

    try:
        originals.append((CommonsManager, "_canonical_write_lock", original_lock))
        CommonsManager._canonical_write_lock = wrapped_lock  # type: ignore[method-assign]
        originals.append((EventStore, "read_path", EventStore.read_path))
        EventStore.read_path = _read_wrapper(collector, EventStore.read_path)  # type: ignore[method-assign]
        originals.append((ManifestStore, "read_path", ManifestStore.read_path))
        ManifestStore.read_path = _read_wrapper(collector, ManifestStore.read_path)  # type: ignore[method-assign]
        replace(manager_module, "project_events", "projection_replay")
        replace(projection, "project_events", "projection_replay")
        replace(manager_module, "validate_transition", "canonical_reads_validation")
        replace(lifecycle, "validate_transition", "canonical_reads_validation")
        replace(ReceiptRecovery, "prepare_for_write", "receipt_preparation")
        replace(EventStore, "append_event", "append")
        replace(ReceiptRecovery, "reconcile", "reconcile")
        token = _ACTIVE_COLLECTOR.set(collector)
        try:
            yield
        finally:
            _ACTIVE_COLLECTOR.reset(token)
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)


def _pop_task(pool: deque[TaskRef], *, label: str) -> TaskRef:
    if not pool:
        raise AssertionError(f"fixture has no remaining {label} tasks for measurement")
    return pool.popleft()


def _measure_sample(operation: Callable[[], object]) -> MutationSample:
    collector = _PhaseCollector()
    gc.collect()
    started = time.perf_counter()
    with _instrument_write_path(collector):
        operation()
    elapsed = time.perf_counter() - started
    phases = collector.report()
    accounted = sum(item["exclusive_elapsed_seconds"] for item in phases.values())
    residual = max(0.0, elapsed - accounted)
    return {
        "elapsed_seconds": elapsed,
        "residual_elapsed_seconds": residual,
        "read_calls": collector.read_calls,
        "read_bytes": collector.read_bytes,
        "phases": phases,
    }


def _aggregate(samples: Sequence[MutationSample]) -> MutationAggregate:
    totals = [sample["elapsed_seconds"] for sample in samples]
    phase_series = {
        label: [sample["phases"][label]["exclusive_elapsed_seconds"] for sample in samples]
        for label in PHASE_LABELS
    }
    call_series = {
        label: [float(sample["phases"][label]["calls"]) for sample in samples]
        for label in PHASE_LABELS
    }
    return {
        "samples": len(samples),
        "min_seconds": min(totals) if totals else 0.0,
        "median_seconds": _median(totals),
        "p95_seconds": _quantile(totals, 0.95) if totals else 0.0,
        "median_read_calls": _median([float(sample["read_calls"]) for sample in samples]),
        "median_read_bytes": _median([float(sample["read_bytes"]) for sample in samples]),
        "median_phase_exclusive_seconds": {
            label: _median(values) for label, values in phase_series.items()
        },
        "p95_phase_exclusive_seconds": {
            label: _quantile(values, 0.95) for label, values in phase_series.items()
        },
        "median_phase_calls": {label: _median(values) for label, values in call_series.items()},
    }


def _http_client(fixture: MutationFixture):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from agent_commons.ui.context import UIContext
    from agent_commons.ui.server import create_app

    context = UIContext(
        fixture.repo,
        state_root=fixture.state_root,
        writer_session_id=fixture.session_id,
    )
    app = create_app(
        context,
        token="mutation-baseline-token",
        exchange_code="mutation-baseline-exchange",
        port=51981,
        api_base="/api",
    )
    return TestClient(app, base_url="http://127.0.0.1:51981")


def _http_create(client: Any, *, key: str) -> None:
    response = client.post(
        "/api/tasks",
        json={
            "title": f"Panel create {key[-12:]}",
            "description": "HTTP path of the mutation baseline",
            "acceptance_criteria": ["the panel records the task"],
            "idempotency_key": key,
        },
        headers={"Cookie": f"{SESSION_COOKIE_NAME}=mutation-baseline-token"},
    )
    if response.status_code != 200:
        raise AssertionError(f"panel task create failed: {response.status_code} {response.text}")


def _mutation_operation(
    fixture: MutationFixture,
    manager: CommonsManager,
    label: str,
    *,
    key: str,
    client: Any | None,
) -> Callable[[], object]:
    if label == "task_create":
        return lambda: manager.create_task(
            title=f"Measured create {key[-12:]}",
            description="Measured CommonsManager task create",
            acceptance_criteria=("the create is timed",),
            idempotency_key=key,
        )
    if label == "task_take":
        target = _pop_task(fixture.ready, label="ready")
        return lambda: manager.take_task(target.task_id, target.revision, idempotency_key=key)
    if label == "task_complete":
        target = _pop_task(fixture.active, label="active")
        return lambda: manager.complete_task(
            target.task_id,
            target.revision,
            summary="measured complete",
            artifact_refs=(),
            idempotency_key=key,
        )
    if label == "task_submit":
        target = _pop_task(fixture.completed, label="completed")
        return lambda: manager.submit_task(
            target.task_id,
            target.revision,
            summary="measured submit",
            artifact_refs=(),
            idempotency_key=key,
        )
    if label == "decision_propose":
        return lambda: manager.propose_decision(
            scope=f"measured-scope-{key[-12:]}",
            proposal="Record the measured decision",
            alternatives=("skip the measured decision",),
            idempotency_key=key,
        )
    if label == "panel_http_task_create":
        if client is None:
            raise AssertionError("HTTP mutation requires a TestClient")
        return lambda: _http_create(client, key=key)
    raise ValueError(f"unknown mutation {label}")


def profile_work_mutations(
    fixture: MutationFixture,
    *,
    repeats: int = 5,
    mutations: Sequence[str] = MUTATION_LABELS,
    profile: str = "small",
) -> WorkMutationProfile:
    """Measure cold and warm samples for each mutation. Fixture build is excluded."""

    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    reports: dict[str, MutationReport] = {}
    for label in mutations:
        cold_samples: list[MutationSample] = []
        warm_samples: list[MutationSample] = []
        for index in range(repeats):
            client = None
            if label == "panel_http_task_create":
                client = _http_client(fixture)
            manager = _bind_manager(fixture)
            cold_key = f"cold:{label}:{index}"
            warm_key = f"warm:{label}:{index}"
            if label == "panel_http_task_create":
                with client:
                    cold_samples.append(
                        _measure_sample(
                            _mutation_operation(
                                fixture, manager, label, key=cold_key, client=client
                            )
                        )
                    )
                    warm_samples.append(
                        _measure_sample(
                            _mutation_operation(
                                fixture, manager, label, key=warm_key, client=client
                            )
                        )
                    )
            else:
                cold_samples.append(
                    _measure_sample(
                        _mutation_operation(fixture, manager, label, key=cold_key, client=None)
                    )
                )
                warm_samples.append(
                    _measure_sample(
                        _mutation_operation(fixture, manager, label, key=warm_key, client=None)
                    )
                )
        reports[label] = {
            "cold": _aggregate(cold_samples),
            "warm": _aggregate(warm_samples),
            "samples": {"cold": cold_samples, "warm": warm_samples},
        }
    hardware = runtime_metadata()
    return {
        "schema": SCHEMA,
        "python": str(hardware["python"]),
        "platform": str(hardware["platform"]),
        "hardware": hardware,
        "profile": profile,
        "repeats": repeats,
        "fixture": {
            "event_count": fixture.event_count,
            "manifest_count": fixture.manifest_count,
            "task_count": fixture.task_count,
            "review_count": fixture.review_count,
            "decision_count": fixture.decision_count,
            "delegation_count": fixture.delegation_count,
            "artifact_count": fixture.artifact_count,
        },
        "fixture_build_seconds": fixture.build_seconds,
        "mutations": reports,
    }


def run_profile(profile: str, *, repeats: int) -> WorkMutationProfile:
    spec = PROFILES[profile]
    with tempfile.TemporaryDirectory(prefix=f"ac-mutation-{spec.name}-") as temporary:
        fixture = build_mutation_fixture(Path(temporary), profile=profile)
        result = profile_work_mutations(fixture, repeats=repeats, profile=profile)
    return result


def _fmt_seconds(value: float) -> str:
    if value >= 10:
        return f"{value:.2f} s"
    if value >= 1:
        return f"{value:.3f} s"
    return f"{value * 1000:.1f} ms"


def _top_phase(aggregate: MutationAggregate) -> tuple[str, float]:
    items = sorted(
        aggregate["median_phase_exclusive_seconds"].items(),
        key=lambda item: item[1],
        reverse=True,
    )
    label, seconds = items[0]
    share = seconds / aggregate["median_seconds"] if aggregate["median_seconds"] else 0.0
    return label, share


def render_baseline_markdown(
    *, small: WorkMutationProfile, large: WorkMutationProfile | None
) -> str:
    hardware = (large or small)["hardware"]
    lines = [
        "# Mutation latency baseline",
        "",
        "Date: 10 September 2026. Measurement only; no production optimization.",
        "Synthetic ledgers in temporary directories. The operator workspace and",
        "this checkout's state root were not opened.",
        "",
        "## Hardware and runtime",
        "",
        f"- Python: `{hardware.get('python')}` ({hardware.get('python_implementation')})",
        f"- Platform: `{hardware.get('platform')}`",
        f"- Machine: `{hardware.get('machine')}`",
        f"- CPU: `{hardware.get('cpu_brand') or hardware.get('processor') or 'unreported'}`",
        f"- CPU count: `{hardware.get('cpu_count')}`",
        "",
        "Cold = first mutation on a fresh `CommonsManager` in an already-imported",
        "process. Warm = the second mutation on that same manager. OS page cache",
        "is therefore warm after the first sample; a brand-new process would pay",
        "import cost on top of these numbers.",
        "",
        "## Fixture sizes",
        "",
        "| Profile | Events | Manifests | Tasks | Reviews | Decisions | "
        "Delegations | Artifacts | Build |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for report in (small, large):
        if report is None:
            continue
        fixture = report["fixture"]
        lines.append(
            "| {profile} | {event_count} | {manifest_count} | {task_count} | {review_count} | "
            "{decision_count} | {delegation_count} | {artifact_count} | {build} |".format(
                profile=report["profile"],
                build=_fmt_seconds(report["fixture_build_seconds"]),
                **fixture,
            )
        )
    lines.extend(["", "Fixture construction is excluded from every mutation sample.", ""])

    def phase_table(report: WorkMutationProfile, temperature: str) -> None:
        lines.append(f"### {report['profile']} fixture, {temperature} samples")
        lines.append("")
        header = (
            "| Mutation | min | median | p95 | "
            + " | ".join(PHASE_LABELS)
            + " | residual | reads | bytes |"
        )
        lines.append(header)
        lines.append("|---|" + "---:|" * (3 + len(PHASE_LABELS) + 3))
        for label, mutation in report["mutations"].items():
            agg = mutation[temperature]  # type: ignore[index]
            phase_cells = " | ".join(
                _fmt_seconds(agg["median_phase_exclusive_seconds"][phase]) for phase in PHASE_LABELS
            )
            residual = _median(
                [item["residual_elapsed_seconds"] for item in mutation["samples"][temperature]]
            )
            lines.append(
                f"| `{label}` | {_fmt_seconds(agg['min_seconds'])} | "
                f"{_fmt_seconds(agg['median_seconds'])} | {_fmt_seconds(agg['p95_seconds'])} | "
                f"{phase_cells} | {_fmt_seconds(residual)} | "
                f"{agg['median_read_calls']:.0f} | {agg['median_read_bytes']:.0f} |"
            )
        lines.append("")

    lines.append("## Phase tables")
    lines.append("")
    phase_table(small, "cold")
    phase_table(small, "warm")
    if large is not None:
        phase_table(large, "cold")
        phase_table(large, "warm")

    focus = large or small
    warm_create = focus["mutations"]["task_create"]["warm"]
    top_label, top_share = _top_phase(warm_create)
    lines.extend(
        [
            "## Where the time goes",
            "",
            f"On the {focus['profile']} fixture, warm `task_create` median is "
            f"{_fmt_seconds(warm_create['median_seconds'])}. The largest exclusive "
            f"phase is `{top_label}` at about {top_share:.0%} of that median. "
            "Canonical writes re-read every event file under the write lock "
            "(`iter_events` via `_records_and_snapshot`), replay the whole ledger, "
            "then `EventStore.append` looks up a missing idempotency receipt and "
            "scans the ledger again through `_find_existing`. Receipt preparation "
            "and the post-append reconcile sit on the same lock.",
            "",
            "The panel HTTP path is the same `record_event` plus FastAPI TestClient "
            "overhead; it is not a browser paint measurement.",
            "",
            "## Smallest optimization proposed",
            "",
            "Do not implement this here. The next write-path change should be the",
            "smallest one that the phases actually name:",
            "",
            "1. **Stop the second full ledger scan inside `append`.** When "
            "`idempotency.lookup` misses, `_find_existing` walks every canonical "
            "event. That work is already paid by `_records_and_snapshot` a few "
            "lines earlier. Passing the in-memory records, or treating a lookup "
            "miss as authoritative after a just-reconciled ledger, removes one "
            f"full `{warm_create['median_read_calls']:.0f}-call` read pass from "
            "every mutation.",
            "2. **Do not re-validate immutable event bytes under the write lock.** "
            "`EventStore.read_path` re-runs schema and envelope validation on "
            "every read. Append-time validation already accepted those files. A "
            "content-hash / mtime cache scoped to one lock hold would drop the "
            "`canonical_reads_validation` share without changing replay.",
            "3. **Keep receipt status checks off the exclusive lock when the "
            "anchor already matches.** `prepare_for_write` plus post-append "
            "`reconcile` re-derive completeness on every write. If the phases "
            "show those two as the bulk of lock hold, compute status once and "
            "refresh the anchor incrementally for the single new event.",
            "",
            "Suggested regression bound around the first change: warm "
            f"`task_create` median on the {focus['profile']} fixture must stay at or below "
            "the measured median, and the median `read_calls` must fall by "
            "approximately half (one `iter_events` pass instead of two). Keep "
            "the small 300-task test as a shape/sum guard, not an SLO.",
            "",
            "No p50/p95 product target is set here. Decision `ux/latency-thresholds` "
            "waits on this evidence.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="small")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--write-docs", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.profile == "large" and os.environ.get(LARGE_BENCHMARK_ENV) != "1":
        print(
            f"refusing the large fixture without {LARGE_BENCHMARK_ENV}=1",
            file=sys.stderr,
        )
        return 2
    report = run_profile(args.profile, repeats=args.repeats)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"profile={report['profile']} events={report['fixture']['event_count']} "
            f"manifests={report['fixture']['manifest_count']} "
            f"build={_fmt_seconds(report['fixture_build_seconds'])}"
        )
        for label, mutation in report["mutations"].items():
            cold = mutation["cold"]
            warm = mutation["warm"]
            print(
                f"  {label:24} cold median={_fmt_seconds(cold['median_seconds'])} "
                f"p95={_fmt_seconds(cold['p95_seconds'])}  "
                f"warm median={_fmt_seconds(warm['median_seconds'])} "
                f"p95={_fmt_seconds(warm['p95_seconds'])}"
            )
    if args.write_docs:
        small = (
            report
            if args.profile == "small"
            else run_profile("small", repeats=max(1, min(args.repeats, 2)))
        )
        large = report if args.profile == "large" else None
        if args.profile == "small" and os.environ.get(LARGE_BENCHMARK_ENV) == "1":
            large = run_profile("large", repeats=args.repeats)
        DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOCS_PATH.write_text(render_baseline_markdown(small=small, large=large), encoding="utf-8")
        print(f"wrote {DOCS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
