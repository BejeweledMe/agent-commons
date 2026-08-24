"""Data structures produced by canonical event projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .collections import collection_for

if TYPE_CHECKING:
    from .agent_projection import AgentRecord
    from .artifact_projection import ArtifactRecord
    from .handoff_projection import HandoffRecord
    from .review_projection import ReviewRecord
    from .task_projection import TaskRecord
    from .thread_projection import ThreadRecord
    from .verification_projection import VerificationRecord


@dataclass(frozen=True)
class ProjectionIssue:
    """Machine-readable projection failure used by integrity gates."""

    code: str
    severity: str
    message: str
    event_ids: tuple[str, ...] = ()
    repairable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "event_ids": list(self.event_ids),
            "repairable": self.repairable,
        }


@dataclass
class ProjectSnapshot:
    workspace_id: str | None = None
    objectives: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    threads: dict[str, ThreadRecord] = field(default_factory=dict)
    reviews: dict[str, ReviewRecord] = field(default_factory=dict)
    verifications: dict[str, VerificationRecord] = field(default_factory=dict)
    findings: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    handoffs: dict[str, HandoffRecord] = field(default_factory=dict)
    delegations: dict[str, dict[str, Any]] = field(default_factory=dict)
    agents: dict[str, AgentRecord] = field(default_factory=dict)
    agent_links: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    issues: list[ProjectionIssue] = field(default_factory=list)
    invalid_event_ids: set[str] = field(default_factory=set)
    stale_refs: set[tuple[str, str]] = field(default_factory=set)
    effective_event_revisions: dict[str, str] = field(default_factory=dict)
    known_event_ids: set[str] = field(default_factory=set)
    known_manifest_ids: set[str] = field(default_factory=set)
    # Replay-only identity facts from immutable event actors.  Session ids may
    # rotate while the identity stays byte-for-byte stable; keeping this out of
    # ``to_dict`` avoids turning an implementation guard into project data.
    session_identities: dict[str, dict[str, Any]] = field(default_factory=dict)
    replay_metrics: dict[str, int] = field(default_factory=dict)
    # The highest semantics version any effective stamp in this ledger names;
    # 1 is everything written before stamps existed.
    semantics_required: int = 1

    def entity_revision(self, kind: str, identifier: str) -> str | None:
        attribute = collection_for(kind)
        collection = getattr(self, attribute, None) if attribute is not None else None
        if not isinstance(collection, dict) or identifier not in collection:
            return None
        return str(collection[identifier]["revision"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "objectives": list(self.objectives.values()),
            "tasks": [record.to_dict() for record in self.tasks.values()],
            "threads": [record.to_dict() for record in self.threads.values()],
            "reviews": [record.to_dict() for record in self.reviews.values()],
            "verifications": [record.to_dict() for record in self.verifications.values()],
            "findings": list(self.findings.values()),
            "decisions": list(self.decisions.values()),
            "artifacts": [record.to_dict() for record in self.artifacts.values()],
            "handoffs": [record.to_dict() for record in self.handoffs.values()],
            "delegations": list(self.delegations.values()),
            "agents": [record.to_dict() for record in self.agents.values()],
            "agent_links": list(self.agent_links.values()),
            "warnings": sorted(set(self.warnings)),
            "issues": [issue.as_dict() for issue in self.issues],
            "invalid_event_ids": sorted(self.invalid_event_ids),
            "stale_refs": [
                {"kind": kind, "id": identifier} for kind, identifier in sorted(self.stale_refs)
            ],
            "semantics_required": self.semantics_required,
        }
