"""Closed, browser-safe DTOs for the task execution tracker.

The tracker is a derived surface.  These records deliberately have no fields
for provider output, stderr, prompts, tool arguments, tokens, money, progress
percentages, or estimated completion times.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

TrackerSurfaceState = Literal["loading", "empty", "ready", "partial", "stale", "error"]


class TrackerTaskPayload(TypedDict):
    task_id: str
    title: str
    task_state: str
    readiness: str
    dependency_task_ids: list[str]
    blocking_dependency_ids: list[str]
    owner_session_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    phase: str | None
    awaits_human: bool
    next_action: str
    freshness: str
    evidence_state: str
    gaps: list[str]


class TrackerEdgePayload(TypedDict):
    prerequisite_task_id: str
    dependent_task_id: str
    prerequisite_missing: bool


class TrackerRunPayload(TypedDict):
    delegation_id: str
    task_id: str | None
    agent_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    phase: str
    attempt_id: str | None
    attempt_number: int | None
    started_at: str | None
    updated_at: str | None
    finished_at: str | None
    duration_seconds: int | None
    awaits_human: bool
    next_action: str
    freshness: str
    evidence_state: str


class TrackerAttentionPayload(TypedDict):
    kind: Literal["run", "review"]
    item_id: str
    task_id: str | None
    reason_code: str
    next_action: str


class TrackerCapacityPayload(TypedDict):
    state: str
    active: int | None
    limit: int | None
    queued: int | None
    queue_capacity: int | None


class TrackerFreshnessPayload(TypedDict):
    generated_at: str
    source_updated_at: str | None
    state: str
    resume_gap: bool


class TrackerSnapshotPayload(TypedDict):
    schema: Literal["agent-commons.tracker.v1"]
    sequence: int
    state: TrackerSurfaceState
    tasks: list[TrackerTaskPayload]
    edges: list[TrackerEdgePayload]
    runs: list[TrackerRunPayload]
    attention: list[TrackerAttentionPayload]
    capacity: TrackerCapacityPayload
    freshness: TrackerFreshnessPayload
    focus_task_ids: list[str]
    critical_path_task_ids: list[str]
    critical_path_basis: Literal["dependency_depth_only"]
    critical_path_predictive: Literal[False]
    gaps: list[str]


@dataclass(frozen=True, slots=True)
class TrackerTaskDTO:
    task_id: str
    title: str
    task_state: str
    readiness: str
    dependency_task_ids: tuple[str, ...]
    blocking_dependency_ids: tuple[str, ...]
    owner_session_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    phase: str | None
    awaits_human: bool
    next_action: str
    freshness: str
    evidence_state: str
    gaps: tuple[str, ...]

    def to_wire(self) -> TrackerTaskPayload:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "task_state": self.task_state,
            "readiness": self.readiness,
            "dependency_task_ids": list(self.dependency_task_ids),
            "blocking_dependency_ids": list(self.blocking_dependency_ids),
            "owner_session_id": self.owner_session_id,
            "role_name": self.role_name,
            "provider": self.provider,
            "profile_id": self.profile_id,
            "phase": self.phase,
            "awaits_human": self.awaits_human,
            "next_action": self.next_action,
            "freshness": self.freshness,
            "evidence_state": self.evidence_state,
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class TrackerEdgeDTO:
    prerequisite_task_id: str
    dependent_task_id: str
    prerequisite_missing: bool

    def to_wire(self) -> TrackerEdgePayload:
        return {
            "prerequisite_task_id": self.prerequisite_task_id,
            "dependent_task_id": self.dependent_task_id,
            "prerequisite_missing": self.prerequisite_missing,
        }


@dataclass(frozen=True, slots=True)
class TrackerRunDTO:
    delegation_id: str
    task_id: str | None
    agent_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    phase: str
    attempt_id: str | None
    attempt_number: int | None
    started_at: str | None
    updated_at: str | None
    finished_at: str | None
    duration_seconds: int | None
    awaits_human: bool
    next_action: str
    freshness: str
    evidence_state: str

    def to_wire(self) -> TrackerRunPayload:
        return {
            "delegation_id": self.delegation_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "role_name": self.role_name,
            "provider": self.provider,
            "profile_id": self.profile_id,
            "phase": self.phase,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "awaits_human": self.awaits_human,
            "next_action": self.next_action,
            "freshness": self.freshness,
            "evidence_state": self.evidence_state,
        }


@dataclass(frozen=True, slots=True)
class TrackerAttentionDTO:
    kind: Literal["run", "review"]
    item_id: str
    task_id: str | None
    reason_code: str
    next_action: str

    def to_wire(self) -> TrackerAttentionPayload:
        return {
            "kind": self.kind,
            "item_id": self.item_id,
            "task_id": self.task_id,
            "reason_code": self.reason_code,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class TrackerCapacityDTO:
    state: str
    active: int | None
    limit: int | None
    queued: int | None
    queue_capacity: int | None

    def to_wire(self) -> TrackerCapacityPayload:
        return {
            "state": self.state,
            "active": self.active,
            "limit": self.limit,
            "queued": self.queued,
            "queue_capacity": self.queue_capacity,
        }


@dataclass(frozen=True, slots=True)
class TrackerFreshnessDTO:
    generated_at: str
    source_updated_at: str | None
    state: str
    resume_gap: bool

    def to_wire(self) -> TrackerFreshnessPayload:
        return {
            "generated_at": self.generated_at,
            "source_updated_at": self.source_updated_at,
            "state": self.state,
            "resume_gap": self.resume_gap,
        }


@dataclass(frozen=True, slots=True)
class TrackerSnapshotDTO:
    sequence: int
    state: TrackerSurfaceState
    tasks: tuple[TrackerTaskDTO, ...]
    edges: tuple[TrackerEdgeDTO, ...]
    runs: tuple[TrackerRunDTO, ...]
    attention: tuple[TrackerAttentionDTO, ...]
    capacity: TrackerCapacityDTO
    freshness: TrackerFreshnessDTO
    focus_task_ids: tuple[str, ...]
    critical_path_task_ids: tuple[str, ...]
    gaps: tuple[str, ...]

    def to_wire(self) -> TrackerSnapshotPayload:
        return {
            "schema": "agent-commons.tracker.v1",
            "sequence": self.sequence,
            "state": self.state,
            "tasks": [item.to_wire() for item in self.tasks],
            "edges": [item.to_wire() for item in self.edges],
            "runs": [item.to_wire() for item in self.runs],
            "attention": [item.to_wire() for item in self.attention],
            "capacity": self.capacity.to_wire(),
            "freshness": self.freshness.to_wire(),
            "focus_task_ids": list(self.focus_task_ids),
            "critical_path_task_ids": list(self.critical_path_task_ids),
            "critical_path_basis": "dependency_depth_only",
            "critical_path_predictive": False,
            "gaps": list(self.gaps),
        }
