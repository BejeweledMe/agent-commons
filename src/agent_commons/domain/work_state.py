"""Immutable, provider-safe derived views for the operator work surface.

These records are read models.  They never become canonical project truth and
they deliberately retain no source mappings, provider output, process
arguments, environment, or credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import islice

MAX_IDENTIFIER_BYTES = 256
MAX_ROLE_NAME_BYTES = 160
MAX_MISSING_FIELDS = 24
MAX_BLOCKING_DEPENDENCIES = 256
MAX_RUNS = 2_000
MAX_ACCEPTANCES = 2_000
MAX_REVIEW_GAPS = 2_000
MAX_SOURCE_GAPS = 16


class EvidenceState(StrEnum):
    """How completely the available sources support one derived view."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"


class FreshnessState(StrEnum):
    """Age classification for the newest evidence used by a view."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class RunPhase(StrEnum):
    """Closed run phases proven by canonical or operational state."""

    REQUESTED = "requested"
    RESERVED = "reserved"
    LAUNCHING = "launching"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    INPUT_NEEDED = "input_needed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEEDS_OPERATOR = "needs_operator"
    UNKNOWN = "unknown"


class NextAction(StrEnum):
    """Maintainer-authored actions; no provider prose crosses the boundary."""

    WAIT_FOR_RUN = "wait_for_run"
    START_READY_WORK = "start_ready_work"
    RESOLVE_DEPENDENCIES = "resolve_dependencies"
    ANSWER_OPERATOR_REQUEST = "answer_operator_request"
    INSPECT_FAILURE = "inspect_failure"
    RETRY_NEW_RUN = "retry_new_run"
    REQUEST_REVIEW = "request_review"
    WAIT_FOR_REVIEW = "wait_for_review"
    REVISE_WORK = "revise_work"
    ACCEPT_TASK = "accept_task"
    INSPECT_MISSING_EVIDENCE = "inspect_missing_evidence"
    NONE = "none"


class AcceptanceState(StrEnum):
    """Revision-bound acceptance posture of a task."""

    NOT_READY = "not_ready"
    REVIEW_REQUIRED = "review_required"
    REVIEW_PENDING = "review_pending"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"


class ReviewGapKind(StrEnum):
    """A concrete reason the current task revision is not accept-ready."""

    MISSING_REVIEW = "missing_review"
    STALE_REVIEW = "stale_review"
    TARGET_REVISION_MISMATCH = "target_revision_mismatch"
    NON_INDEPENDENT_REVIEW = "non_independent_review"
    CHANGES_REQUESTED = "changes_requested"
    REVIEW_EVIDENCE_MISSING = "review_evidence_missing"


class WorkHealthState(StrEnum):
    """Operator-level summary without a fabricated progress score."""

    EMPTY = "empty"
    HEALTHY = "healthy"
    ATTENTION_REQUIRED = "attention_required"
    STALE = "stale"
    PARTIAL = "partial"


class WorkSourceGap(StrEnum):
    """Closed aggregate diagnostics for incomplete derived sources."""

    GRAPH_TRUNCATED = "graph_truncated"
    GRAPH_FRESHNESS_MISSING = "graph_freshness_missing"
    ATTEMPT_CORRELATION_MISSING = "attempt_correlation_missing"
    ORPHAN_OPERATIONAL_ATTEMPT = "orphan_operational_attempt"
    SOURCE_TIMESTAMP_INVALID = "source_timestamp_invalid"
    SOURCE_TIMESTAMP_FUTURE = "source_timestamp_future"
    SOURCE_TIMESTAMP_ORDER_INVALID = "source_timestamp_order_invalid"
    TASK_DEPENDENCIES_INVALID = "task_dependencies_invalid"
    TASK_DEPENDENCIES_TRUNCATED = "task_dependencies_truncated"
    ATTEMPT_EVIDENCE_MISSING = "attempt_evidence_missing"


class EvidenceGap(StrEnum):
    """Closed field-level reasons why a view is partial or missing."""

    TASK = "task"
    AGENT = "agent"
    PROFILE_ID = "profile_id"
    KNOWN_PROFILE = "known_profile"
    PROVIDER_PROFILE_MATCH = "provider_profile_match"
    ATTEMPT_PROFILE_MATCH = "attempt_profile_match"
    ROLE_PROFILE_MATCH = "role_profile_match"
    TASK_DEPENDENCIES = "task_dependencies"
    BLOCKING_DEPENDENCIES_TRUNCATED = "blocking_dependencies_truncated"
    KNOWN_PHASE = "known_phase"
    ATTEMPT = "attempt"
    ATTEMPT_ID = "attempt_id"
    ATTEMPT_NUMBER = "attempt_number"
    ATTEMPT_STATE = "attempt_state"
    ATTEMPT_PROFILE_ID = "attempt_profile_id"
    ATTEMPT_PROVIDER = "attempt_provider"
    CANONICAL_ATTEMPT_STATE_MATCH = "canonical_attempt_state_match"
    ATTEMPT_CREATED_AT = "attempt_created_at"
    ATTEMPT_UPDATED_AT = "attempt_updated_at"
    ATTEMPT_TIMESTAMP_ORDER = "attempt_timestamp_order"
    FRESHNESS = "freshness"
    TASK_REVISION = "task_revision"
    REVIEW_ID = "review_id"
    REVIEW_REVISION = "review_revision"
    REVIEW_STATE = "review_state"
    REVIEW_TARGET_REVISION = "review_target_revision"
    REVIEW_INDEPENDENCE = "review_independence"
    REVIEW_STALE = "review_stale"
    ACCEPTANCE_REVIEW_REF = "acceptance_review_ref"
    ACCEPTANCE_REVIEW_REVISION = "acceptance_review_revision"
    ACCEPTANCE_REVIEW_REVISION_MATCH = "acceptance_review_revision_match"


_CANONICAL_RUN_STATES = frozenset(
    {
        "requested",
        "active",
        "input_needed",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "needs_operator",
        "unknown",
    }
)
_TASK_STATES = frozenset(
    {"ready", "assigned", "active", "blocked", "completed", "review", "accepted", "cancelled"}
)
_REVIEW_STATES = frozenset({"requested", "approved", "changes_requested"})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


def _bounded_text(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or None")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{field} exceeds its {maximum}-byte bound")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} contains control characters")
    return value


def _identifier(value: str | None, field: str) -> str | None:
    result = _bounded_text(value, field=field, maximum=MAX_IDENTIFIER_BYTES)
    if result == "":
        raise ValueError(f"{field} cannot be empty")
    if result is not None and _SAFE_IDENTIFIER.fullmatch(result) is None:
        raise ValueError(f"{field} is not a safe identifier")
    return result


def _required_identifier(value: str, field: str) -> str:
    result = _identifier(value, field)
    if result is None:
        raise ValueError(f"{field} is required")
    return result


def _timestamp(value: str | None, field: str, *, required: bool = False) -> datetime | None:
    result = _bounded_text(value, field=field, maximum=64)
    if result is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _bool_or_none(value: bool | None, field: str) -> None:
    if value is not None and type(value) is not bool:
        raise TypeError(f"{field} must be a bool or None")


def _non_negative_int(value: int, field: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{field} must be an int")
    if value < 0:
        raise ValueError(f"{field} cannot be negative")


def _string_tuple(
    values: tuple[str, ...], *, field: str, maximum_items: int, maximum_bytes: int
) -> tuple[str, ...]:
    owned = _bounded_tuple(values, field=field, maximum_items=maximum_items)
    for item in owned:
        _bounded_text(item, field=field, maximum=maximum_bytes)  # type: ignore[arg-type]
        _required_identifier(item, field)  # type: ignore[arg-type]
    return owned


def _bounded_tuple(values: object, *, field: str, maximum_items: int) -> tuple[object, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be a collection, not text")
    try:
        owned = tuple(islice(iter(values), maximum_items + 1))  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{field} must be iterable") from exc
    if len(owned) > maximum_items:
        raise ValueError(f"{field} exceeds its {maximum_items}-item bound")
    return owned


@dataclass(frozen=True, slots=True)
class RunView:
    """One canonical delegation joined to at most one latest attempt."""

    delegation_id: str
    task_id: str | None
    agent_id: str | None
    role_name: str | None
    provider: str | None
    profile_id: str | None
    canonical_state: str
    phase: RunPhase
    attempt_id: str | None
    attempt_number: int | None
    started_at: str | None
    updated_at: str | None
    finished_at: str | None
    duration_seconds: int | None
    awaits_human: bool
    blocking_dependencies: tuple[str, ...]
    next_action: NextAction
    freshness: FreshnessState
    evidence_state: EvidenceState
    missing_fields: tuple[EvidenceGap, ...] = ()

    def __post_init__(self) -> None:
        _required_identifier(self.delegation_id, "delegation_id")
        _identifier(self.task_id, "task_id")
        _identifier(self.agent_id, "agent_id")
        _bounded_text(self.role_name, field="role_name", maximum=MAX_ROLE_NAME_BYTES)
        _identifier(self.provider, "provider")
        _identifier(self.profile_id, "profile_id")
        if self.provider not in {None, "codex", "claude", "grok"}:
            raise ValueError("provider must be codex, claude, grok, or None")
        if self.profile_id not in {
            None,
            "codex-builder",
            "codex-independent-reviewer",
            "claude-builder",
            "claude-independent-reviewer",
            "grok-builder",
            "grok-independent-reviewer",
        }:
            raise ValueError("profile_id is not a built-in provider profile")
        if self.profile_id is not None and self.provider != self.profile_id.split("-", 1)[0]:
            raise ValueError("provider and profile_id do not match")
        _required_identifier(self.canonical_state, "canonical_state")
        if self.canonical_state not in _CANONICAL_RUN_STATES:
            raise ValueError("canonical_state is not a known delegation state")
        _identifier(self.attempt_id, "attempt_id")
        if self.attempt_number is not None:
            if type(self.attempt_number) is not int:
                raise TypeError("attempt_number must be an int or None")
            if self.attempt_number < 1:
                raise ValueError("attempt_number must be positive")
        if self.duration_seconds is not None:
            _non_negative_int(self.duration_seconds, "duration_seconds")
        if type(self.awaits_human) is not bool:
            raise TypeError("awaits_human must be a bool")
        started = _timestamp(self.started_at, "started_at")
        updated = _timestamp(self.updated_at, "updated_at")
        finished = _timestamp(self.finished_at, "finished_at")
        if started is not None and updated is not None and updated < started:
            raise ValueError("updated_at cannot precede started_at")
        if started is not None and finished is not None and finished < started:
            raise ValueError("finished_at cannot precede started_at")
        object.__setattr__(self, "phase", RunPhase(self.phase))
        object.__setattr__(self, "next_action", NextAction(self.next_action))
        object.__setattr__(self, "freshness", FreshnessState(self.freshness))
        object.__setattr__(self, "evidence_state", EvidenceState(self.evidence_state))
        object.__setattr__(
            self,
            "blocking_dependencies",
            _string_tuple(
                self.blocking_dependencies,
                field="blocking_dependencies",
                maximum_items=MAX_BLOCKING_DEPENDENCIES,
                maximum_bytes=MAX_IDENTIFIER_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "missing_fields",
            tuple(
                EvidenceGap(value)
                for value in _bounded_tuple(
                    self.missing_fields,
                    field="missing_fields",
                    maximum_items=MAX_MISSING_FIELDS,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class AcceptanceView:
    """Exact task/review binding used to explain acceptance readiness."""

    task_id: str
    task_state: str
    task_revision: str
    state: AcceptanceState
    review_id: str | None
    review_state: str | None
    review_revision: str | None
    review_target_revision: str | None
    independent: bool | None
    stale: bool | None
    next_action: NextAction
    evidence_state: EvidenceState
    missing_fields: tuple[EvidenceGap, ...] = ()

    def __post_init__(self) -> None:
        for field, value in (
            ("task_id", self.task_id),
            ("task_state", self.task_state),
            ("task_revision", self.task_revision),
            ("review_id", self.review_id),
            ("review_state", self.review_state),
            ("review_revision", self.review_revision),
            ("review_target_revision", self.review_target_revision),
        ):
            if field in {"task_id", "task_state", "task_revision"}:
                _required_identifier(value, field)  # type: ignore[arg-type]
            else:
                _identifier(value, field)
        if self.task_state not in _TASK_STATES:
            raise ValueError("task_state is not a known task state")
        if self.review_state is not None and self.review_state not in _REVIEW_STATES:
            raise ValueError("review_state is not a known review state")
        _bool_or_none(self.independent, "independent")
        _bool_or_none(self.stale, "stale")
        object.__setattr__(self, "state", AcceptanceState(self.state))
        object.__setattr__(self, "next_action", NextAction(self.next_action))
        object.__setattr__(self, "evidence_state", EvidenceState(self.evidence_state))
        object.__setattr__(
            self,
            "missing_fields",
            tuple(
                EvidenceGap(value)
                for value in _bounded_tuple(
                    self.missing_fields,
                    field="missing_fields",
                    maximum_items=MAX_MISSING_FIELDS,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewLoopGap:
    """One revision-specific review gap, safe to render without source prose."""

    task_id: str
    task_revision: str
    kind: ReviewGapKind
    review_id: str | None
    review_revision: str | None
    next_action: NextAction

    def __post_init__(self) -> None:
        _required_identifier(self.task_id, "task_id")
        _required_identifier(self.task_revision, "task_revision")
        _identifier(self.review_id, "review_id")
        _identifier(self.review_revision, "review_revision")
        object.__setattr__(self, "kind", ReviewGapKind(self.kind))
        object.__setattr__(self, "next_action", NextAction(self.next_action))


@dataclass(frozen=True, slots=True)
class WorkHealth:
    """Bounded aggregate of all W1 views at one injected observation time."""

    generated_at: str
    source_updated_at: str | None
    state: WorkHealthState
    freshness: FreshnessState
    evidence_state: EvidenceState
    task_count: int
    run_count: int
    attention_count: int
    blocked_task_count: int
    stale_evidence_count: int
    terminal_failure_count: int
    runs: tuple[RunView, ...]
    acceptances: tuple[AcceptanceView, ...]
    review_gaps: tuple[ReviewLoopGap, ...]
    source_gaps: tuple[WorkSourceGap, ...] = ()

    def __post_init__(self) -> None:
        generated = _timestamp(self.generated_at, "generated_at", required=True)
        source_updated = _timestamp(self.source_updated_at, "source_updated_at")
        if generated is not None and source_updated is not None and source_updated > generated:
            raise ValueError("source_updated_at cannot be later than generated_at")
        for field, value in (
            ("task_count", self.task_count),
            ("run_count", self.run_count),
            ("attention_count", self.attention_count),
            ("blocked_task_count", self.blocked_task_count),
            ("stale_evidence_count", self.stale_evidence_count),
            ("terminal_failure_count", self.terminal_failure_count),
        ):
            _non_negative_int(value, field)
        runs = _bounded_tuple(self.runs, field="runs", maximum_items=MAX_RUNS)
        acceptances = _bounded_tuple(
            self.acceptances, field="acceptances", maximum_items=MAX_ACCEPTANCES
        )
        review_gaps = _bounded_tuple(
            self.review_gaps, field="review_gaps", maximum_items=MAX_REVIEW_GAPS
        )
        source_gaps = tuple(
            WorkSourceGap(value)
            for value in _bounded_tuple(
                self.source_gaps,
                field="source_gaps",
                maximum_items=MAX_SOURCE_GAPS,
            )
        )
        if any(type(value) is not RunView for value in runs):
            raise TypeError("runs must contain exact RunView records")
        if any(type(value) is not AcceptanceView for value in acceptances):
            raise TypeError("acceptances must contain exact AcceptanceView records")
        if any(type(value) is not ReviewLoopGap for value in review_gaps):
            raise TypeError("review_gaps must contain exact ReviewLoopGap records")
        run_ids = tuple(run.delegation_id for run in runs)
        task_ids = tuple(view.task_id for view in acceptances)
        gap_task_ids = tuple(gap.task_id for gap in review_gaps)
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("runs contain duplicate delegation_id values")
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("acceptances contain duplicate task_id values")
        if len(set(gap_task_ids)) != len(gap_task_ids):
            raise ValueError("review_gaps contain duplicate task_id values")
        acceptance_by_task = {view.task_id: view for view in acceptances}
        for gap in review_gaps:
            acceptance = acceptance_by_task.get(gap.task_id)
            if acceptance is None or acceptance.task_revision != gap.task_revision:
                raise ValueError("review gap does not bind an acceptance task revision")
            if acceptance.next_action is not gap.next_action:
                raise ValueError("review gap next_action disagrees with acceptance view")
        if len(set(source_gaps)) != len(source_gaps):
            raise ValueError("source_gaps contains duplicates")
        expected_attention = sum(run.awaits_human for run in runs) + len(review_gaps)
        expected_blocked = sum(view.task_state == "blocked" for view in acceptances)
        expected_stale = sum(run.evidence_state is EvidenceState.STALE for run in runs) + sum(
            view.evidence_state is EvidenceState.STALE for view in acceptances
        )
        expected_failures = sum(
            run.phase in {RunPhase.FAILED, RunPhase.TIMED_OUT, RunPhase.NEEDS_OPERATOR}
            for run in runs
        )
        for field, actual, expected in (
            ("task_count", self.task_count, len(acceptances)),
            ("run_count", self.run_count, len(runs)),
            ("attention_count", self.attention_count, expected_attention),
            ("blocked_task_count", self.blocked_task_count, expected_blocked),
            ("stale_evidence_count", self.stale_evidence_count, expected_stale),
            ("terminal_failure_count", self.terminal_failure_count, expected_failures),
        ):
            if actual != expected:
                raise ValueError(f"{field} does not match nested read models")
        object.__setattr__(self, "runs", runs)
        object.__setattr__(self, "acceptances", acceptances)
        object.__setattr__(self, "review_gaps", review_gaps)
        object.__setattr__(self, "source_gaps", source_gaps)
        object.__setattr__(self, "state", WorkHealthState(self.state))
        object.__setattr__(self, "freshness", FreshnessState(self.freshness))
        object.__setattr__(self, "evidence_state", EvidenceState(self.evidence_state))
