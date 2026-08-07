"""Typed records for offline evaluation cases and privacy-safe aggregates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Final

from agent_commons.errors import ValidationError


class EvalStatus(StrEnum):
    """Execution state, deliberately separating deferred work from a pass."""

    IMPLEMENTED = "implemented"
    PLANNED = "planned"
    UNSUPPORTED = "unsupported"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


ALLOWED_GRADERS: Final[frozenset[str]] = frozenset(
    {"deterministic_state", "deterministic_cli", "fake_provider_contract"}
)
TERMINAL_RESULT_STATUSES: Final[frozenset[EvalStatus]] = frozenset(
    {
        EvalStatus.PASSED,
        EvalStatus.FAILED,
        EvalStatus.ERROR,
        EvalStatus.PLANNED,
        EvalStatus.UNSUPPORTED,
    }
)


@dataclass(frozen=True)
class EvalCase:
    """A versioned case definition with no prompt, transcript, or user data."""

    case_id: str
    title: str
    scenario: str
    status: EvalStatus
    graders: tuple[str, ...]
    failure_tags: tuple[str, ...]
    owner: str = "agent-commons"

    def __post_init__(self) -> None:
        if not self.case_id.startswith("eval."):
            raise ValidationError("evaluation case_id must start with 'eval.'")
        if self.status not in {
            EvalStatus.IMPLEMENTED,
            EvalStatus.PLANNED,
            EvalStatus.UNSUPPORTED,
        }:
            raise ValidationError("evaluation case status must describe planned support")
        if not self.title or not self.scenario:
            raise ValidationError("evaluation cases require a title and scenario")
        if not self.graders or any(grader not in ALLOWED_GRADERS for grader in self.graders):
            raise ValidationError("evaluation case uses an unsupported grader type")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvalResult:
    """One safe, inspectable outcome without retaining provider or user content."""

    case_id: str
    status: EvalStatus
    outcome_code: str
    latency_ms: int
    provider_units: int = 0
    needs_operator: bool = False
    handoff_loops: int = 0
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        if not self.case_id.startswith("eval."):
            raise ValidationError("evaluation result case_id must start with 'eval.'")
        if self.status not in TERMINAL_RESULT_STATUSES:
            raise ValidationError("evaluation result must be terminal")
        if not self.outcome_code.replace("_", "").isalnum():
            raise ValidationError("evaluation outcome_code must be a bounded identifier")
        if self.latency_ms < 0 or self.provider_units < 0 or self.handoff_loops < 0:
            raise ValidationError("evaluation metrics cannot be negative")
        if self.evidence_digest and (
            len(self.evidence_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_digest)
        ):
            raise ValidationError("evaluation evidence_digest must be a SHA-256 hex digest")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MetricsAggregate:
    """Low-cardinality run metrics suitable for local or CI summaries."""

    catalog_version: str
    total_cases: int
    executed_cases: int
    passed_cases: int
    failed_cases: int
    planned_cases: int
    unsupported_cases: int
    error_cases: int
    pass_at_1: float | None
    pass_power_k: float | None
    needs_operator_rate: float | None
    latency_ms_total: int
    provider_units_total: int
    handoff_loops_total: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
