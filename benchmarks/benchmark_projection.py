"""Repeatable ledger replay benchmark with correctness-visible work counters.

Run with ``python benchmarks/benchmark_projection.py`` from a source checkout.
Wall-clock output is descriptive; CI asserts the deterministic work counters.
"""

from __future__ import annotations

import json
import time
from typing import Any

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.projection import project_events


def _event(number: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    subject = payload.get("objective_id") or payload.get("target_event_id")
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": "workspace.00000000000000000000000001",
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:{number // 60:02d}:{number % 60:02d}Z",
        "actor": {"session_id": "session.benchmark", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": "event", "id": str(subject)}],
        "relations": [],
    }


def workload(*, event_count: int = 10_000, correction_count: int = 1_000) -> list[dict]:
    roots: list[dict[str, Any]] = []
    for number in range(1, event_count + 1):
        identifier = f"objective.{number:026d}"
        roots.append(
            _event(
                number,
                "objective.created",
                {
                    "objective_id": identifier,
                    "title": f"Objective {number}",
                    "description": "projection benchmark",
                    "acceptance_criteria": ["replayed"],
                },
            )
        )
    corrections = [
        _event(
            event_count + offset,
            "event.corrected",
            {
                "target_event_id": root["event_id"],
                "expected_target_sha256": canonical_sha256(root),
                "replacement_payload": {**root["payload"], "title": f"Corrected {offset}"},
            },
        )
        for offset, root in enumerate(roots[:correction_count], start=1)
    ]
    return [*roots, *corrections]


def main() -> None:
    events = workload()
    started = time.perf_counter()
    snapshot = project_events(events)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "schema": "agent_commons.projection_benchmark.v1",
                "elapsed_seconds": round(elapsed, 6),
                "objective_count": len(snapshot.objectives),
                "metrics": snapshot.replay_metrics,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
