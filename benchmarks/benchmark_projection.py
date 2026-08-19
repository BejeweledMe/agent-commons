"""Measure the two- and three-pass ledger replay paths and peak allocations.

Run with ``uv run --locked python benchmarks/benchmark_projection.py`` from a
source checkout. Wall-clock and allocation output are descriptive baselines;
the fixed-point pass assertions make workload drift fail visibly.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Sequence
from typing import Any

from agent_commons.domain.projection import project_events

WORKSPACE_ID = "workspace.00000000000000000000000001"


def _identifier(kind: str, number: int) -> str:
    return f"{kind}.{number:026d}"


def _event(
    number: int,
    event_type: str,
    payload: dict[str, Any],
    *,
    subject_kind: str,
    subject_id: str,
    reviewer: bool = False,
) -> dict[str, Any]:
    return {
        "event_id": _identifier("evt", number),
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "recorded_at": "2026-01-01T00:00:00Z",
        "actor": {
            "session_id": "session.benchmark-reviewer" if reviewer else "session.benchmark",
            "role_id": "reviewer" if reviewer else "builder",
        },
        "payload": payload,
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "relations": [],
    }


def _reviewed_task_with_reopen(start: int) -> list[dict[str, Any]]:
    task_id = _identifier("task", start)
    review_id = _identifier("review", start)

    def task(number: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _event(
            number,
            event_type,
            {"task_id": task_id, **payload},
            subject_kind="task",
            subject_id=task_id,
        )

    created = task(
        start,
        "task.created",
        {
            "title": "Two-pass benchmark task",
            "description": "Acceptance is followed by an applied reopen.",
            "acceptance_criteria": ["replayed"],
            "priority": "normal",
        },
    )
    started = task(start + 1, "task.started", {"expected_revision": created["event_id"]})
    completed = task(
        start + 2,
        "task.completed",
        {"expected_revision": started["event_id"], "summary": "done"},
    )
    submitted = task(
        start + 3,
        "task.submitted",
        {"expected_revision": completed["event_id"], "summary": "ready"},
    )
    requested = _event(
        start + 4,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "task", "id": task_id},
            "target_revision": submitted["event_id"],
            "criteria": ["correctness"],
            "independent": True,
        },
        subject_kind="review",
        subject_id=review_id,
    )
    approved = _event(
        start + 5,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": requested["event_id"],
            "target_revision": submitted["event_id"],
            "verdict": "approved",
            "summary": "approved",
        },
        subject_kind="review",
        subject_id=review_id,
        reviewer=True,
    )
    accepted = task(
        start + 6,
        "task.accepted",
        {
            "expected_revision": submitted["event_id"],
            "summary": "accepted",
            "acceptance_review": {
                "ref": {"kind": "review", "id": review_id},
                "revision": approved["event_id"],
            },
        },
    )
    reopened = task(
        start + 7,
        "task.reopened",
        {"expected_revision": accepted["event_id"], "reason": "benchmark successor"},
    )
    return [created, started, completed, submitted, requested, approved, accepted, reopened]


def _accepted_task_with_stale_artifact(start: int) -> list[dict[str, Any]]:
    artifact_id = _identifier("artifact", start)
    task_id = _identifier("task", start)
    review_id = _identifier("review", start)
    artifact = _event(
        start,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        subject_kind="artifact",
        subject_id=artifact_id,
    )
    binding = {"ref": {"kind": "artifact", "id": artifact_id}, "revision": artifact["event_id"]}

    def task(number: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _event(
            number,
            event_type,
            {"task_id": task_id, **payload},
            subject_kind="task",
            subject_id=task_id,
        )

    created = task(
        start + 1,
        "task.created",
        {
            "title": "Three-pass benchmark task",
            "description": "Artifact staleness is discovered after replay.",
            "acceptance_criteria": ["artifact remains current"],
            "priority": "normal",
        },
    )
    started = task(start + 2, "task.started", {"expected_revision": created["event_id"]})
    completed = task(
        start + 3,
        "task.completed",
        {
            "expected_revision": started["event_id"],
            "summary": "done",
            "artifact_refs": [binding["ref"]],
            "artifact_bindings": [binding],
        },
    )
    submitted = task(
        start + 4,
        "task.submitted",
        {
            "expected_revision": completed["event_id"],
            "summary": "ready",
            "artifact_refs": [binding["ref"]],
            "artifact_bindings": [binding],
        },
    )
    requested = _event(
        start + 5,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "task", "id": task_id},
            "target_revision": submitted["event_id"],
            "criteria": ["artifact is current"],
            "independent": True,
        },
        subject_kind="review",
        subject_id=review_id,
    )
    approved = _event(
        start + 6,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": requested["event_id"],
            "target_revision": submitted["event_id"],
            "verdict": "approved",
            "summary": "approved",
        },
        subject_kind="review",
        subject_id=review_id,
        reviewer=True,
    )
    accepted = task(
        start + 7,
        "task.accepted",
        {
            "expected_revision": submitted["event_id"],
            "summary": "accepted",
            "acceptance_review": {
                "ref": {"kind": "review", "id": review_id},
                "revision": approved["event_id"],
            },
        },
    )
    revised = _event(
        start + 8,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": artifact["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        subject_kind="artifact",
        subject_id=artifact_id,
    )
    return [
        artifact,
        created,
        started,
        completed,
        submitted,
        requested,
        approved,
        accepted,
        revised,
    ]


def workload(*, event_count: int, expected_passes: int) -> list[dict[str, Any]]:
    if expected_passes not in {2, 3}:
        raise ValueError("expected_passes must be 2 or 3")
    events = _reviewed_task_with_reopen(1)
    if expected_passes == 3:
        events.extend(_accepted_task_with_stale_artifact(len(events) + 1))
    if event_count < len(events):
        raise ValueError(f"event_count must be at least {len(events)}")
    for number in range(len(events) + 1, event_count + 1):
        objective_id = _identifier("objective", number)
        events.append(
            _event(
                number,
                "objective.created",
                {
                    "objective_id": objective_id,
                    "title": f"Objective {number}",
                    "description": "projection benchmark filler",
                    "acceptance_criteria": ["replayed"],
                },
                subject_kind="objective",
                subject_id=objective_id,
            )
        )
    return events


def _measure(events: Sequence[dict[str, Any]], *, expected_passes: int) -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    snapshot = project_events(events)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    actual_passes = snapshot.replay_metrics["fixed_point_passes"]
    if actual_passes != expected_passes:
        raise AssertionError(f"expected {expected_passes} replay passes, got {actual_passes}")
    return {"elapsed_seconds": elapsed, "peak_allocated_bytes": peak}


def _scenario(*, event_count: int, expected_passes: int, repeats: int) -> dict[str, Any]:
    events = workload(event_count=event_count, expected_passes=expected_passes)
    samples = [_measure(events, expected_passes=expected_passes) for _ in range(repeats)]
    elapsed = [sample["elapsed_seconds"] for sample in samples]
    peaks = [sample["peak_allocated_bytes"] for sample in samples]
    return {
        "event_count": event_count,
        "fixed_point_passes": expected_passes,
        "repeats": repeats,
        "median_elapsed_seconds": round(statistics.median(elapsed), 6),
        "max_peak_allocated_bytes": max(peaks),
        "samples": [
            {
                "elapsed_seconds": round(sample["elapsed_seconds"], 6),
                "peak_allocated_bytes": sample["peak_allocated_bytes"],
            }
            for sample in samples
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-count", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.repeats < 1:
        parser.error("--repeats must be positive")
    print(
        json.dumps(
            {
                "schema": "agent_commons.projection_benchmark.v2",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "scenarios": {
                    "two_pass": _scenario(
                        event_count=arguments.event_count,
                        expected_passes=2,
                        repeats=arguments.repeats,
                    ),
                    "three_pass": _scenario(
                        event_count=arguments.event_count,
                        expected_passes=3,
                        repeats=arguments.repeats,
                    ),
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
