"""Fail-closed loader for the synthetic W0 work-metrics corpus.

This module deliberately lives under ``tests/``.  The fixture is a small,
closed semantic DSL rather than a ledger export: no task prose, user data,
provider output, filesystem path, or credential can cross this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from re import compile as compile_pattern
from typing import Final, Literal, NotRequired, TypedDict, cast

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict, sha256_bytes
from agent_commons.errors import ValidationError

FIXTURE_SCHEMA: Final = "agent_commons.work_metrics_fixture.v1"
FIXTURE_ID: Final = "work_metrics_v1"
FIXTURE_NOW: Final = "2026-08-25T12:00:00Z"
LEDGER_SEMANTICS_VERSION: Final = 2

CaseKind = Literal[
    "review_pair",
    "handoff",
    "delegation",
    "strict_acceptance",
    "replay",
    "empty",
]
MetricState = Literal["complete", "empty", "unsupported"]
ActorKind = Literal["builder", "reviewer", "operator"]
EventType = Literal[
    "task.created",
    "task.started",
    "task.completed",
    "task.submitted",
    "task.accepted",
    "review.requested",
    "review.completed",
    "handoff.created",
    "handoff.acknowledged",
    "delegation.requested",
    "delegation.succeeded",
    "delegation.failed",
    "delegation.cancelled",
    "delegation.timed_out",
    "delegation.needs_operator",
    "event.corrected",
    "event.retry",
]
MetricId = Literal[
    "current_review_coverage",
    "review_disposition_latency",
    "handoff_acknowledgement_latency",
    "needs_operator_rate",
    "needs_operator_taxonomy_completeness",
    "false_strict_acceptance",
]
ReviewVerdict = Literal["approved", "changes_requested"]
ReasonCode = Literal[
    "provider_unavailable",
    "provider_auth",
    "rate_limited",
    "policy_denied",
    "launch_failed",
    "runtime_error",
    "invalid_result",
    "integrity_error",
    "budget_exhausted",
    "orphaned",
    "unknown",
]


class FixtureEventWire(TypedDict):
    event_type: EventType
    offset_seconds: int
    actor_kind: ActorKind
    independent: NotRequired[bool]
    verdict: NotRequired[ReviewVerdict]
    reason_code: NotRequired[ReasonCode]


class FixtureExpectationWire(TypedDict):
    metric_id: MetricId
    state: MetricState
    numerator: int | None
    denominator: int | None


class FixtureCaseWire(TypedDict):
    case_id: str
    kind: CaseKind
    events: list[FixtureEventWire]
    expectation: FixtureExpectationWire


class FixtureDocumentWire(TypedDict):
    schema: str
    fixture_id: str
    fixed_now: str
    ledger_semantics_version: int
    cases: list[FixtureCaseWire]


@dataclass(frozen=True, slots=True)
class FixtureEvent:
    """One content-free semantic event in the W0 synthetic DSL."""

    event_type: EventType
    offset_seconds: int
    actor_kind: ActorKind
    independent: bool | None = None
    verdict: ReviewVerdict | None = None
    reason_code: ReasonCode | None = None


@dataclass(frozen=True, slots=True)
class FixtureExpectation:
    """A bounded expected metric state, never free-form evaluation output."""

    metric_id: MetricId
    state: MetricState
    numerator: int | None
    denominator: int | None


@dataclass(frozen=True, slots=True)
class FixtureCase:
    """A deterministic scenario comprising only typed identifiers and codes."""

    case_id: str
    kind: CaseKind
    events: tuple[FixtureEvent, ...]
    expectation: FixtureExpectation


@dataclass(frozen=True, slots=True)
class WorkMetricsFixture:
    """Validated frozen corpus, plus its canonical content digest."""

    fixture_id: str
    fixed_now: datetime
    ledger_semantics_version: int
    cases: tuple[FixtureCase, ...]
    fixture_sha256: str


_CASE_KINDS: Final[frozenset[str]] = frozenset(
    {"review_pair", "handoff", "delegation", "strict_acceptance", "replay", "empty"}
)
_METRIC_STATES: Final[frozenset[str]] = frozenset({"complete", "empty", "unsupported"})
_ACTOR_KINDS: Final[frozenset[str]] = frozenset({"builder", "reviewer", "operator"})
_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "task.created",
        "task.started",
        "task.completed",
        "task.submitted",
        "task.accepted",
        "review.requested",
        "review.completed",
        "handoff.created",
        "handoff.acknowledged",
        "delegation.requested",
        "delegation.succeeded",
        "delegation.failed",
        "delegation.cancelled",
        "delegation.timed_out",
        "delegation.needs_operator",
        "event.corrected",
        "event.retry",
    }
)
_METRIC_IDS: Final[frozenset[str]] = frozenset(
    {
        "current_review_coverage",
        "review_disposition_latency",
        "handoff_acknowledgement_latency",
        "needs_operator_rate",
        "needs_operator_taxonomy_completeness",
        "false_strict_acceptance",
    }
)
_REVIEW_VERDICTS: Final[frozenset[str]] = frozenset({"approved", "changes_requested"})
_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "provider_unavailable",
        "provider_auth",
        "rate_limited",
        "policy_denied",
        "launch_failed",
        "runtime_error",
        "invalid_result",
        "integrity_error",
        "budget_exhausted",
        "orphaned",
        "unknown",
    }
)
_SENSITIVE_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "prompt",
        "message",
        "transcript",
        "reasoning",
        "stdout",
        "stderr",
        "tool_arguments",
        "credential",
        "token",
        "secret",
        "title",
        "description",
        "summary",
        "body",
        "note",
        "path",
        "name",
        "provider_output",
        "output",
        "argument",
    }
)
_CASE_ID_PATTERN: Final = compile_pattern(r"^[a-z][a-z0-9_]{2,63}$")
_WINDOWS_ABSOLUTE_PATH: Final = compile_pattern(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_REQUIRED_CASE_IDS: Final[frozenset[str]] = frozenset(
    {
        "review_current_pair",
        "review_missing_pair",
        "review_stale_pair",
        "review_nonindependent_pair",
        "review_changes_requested",
        "handoff_acknowledged",
        "handoff_open_fresh",
        "handoff_open_aged",
        "delegation_terminals",
        "empty_review_queue",
        "strict_acceptance_valid",
        "strict_acceptance_invalid",
        "correction_and_retry",
        "reordered_retry",
    }
)


def fixture_path() -> Path:
    """Return the repository-independent path to the committed synthetic corpus."""

    return Path(__file__).with_name("fixtures") / "work_metrics_v1.json"


def load_work_metrics_fixture(path: Path | None = None) -> WorkMetricsFixture:
    """Load one exact, fail-closed W0 fixture without accessing project state."""

    source = fixture_path() if path is None else path
    raw = loads_json_strict(source.read_bytes())
    document = _require_mapping(raw, "$")
    _require_exact_keys(
        document,
        {"schema", "fixture_id", "fixed_now", "ledger_semantics_version", "cases"},
        "$",
    )
    _reject_unsafe_tree(document, "$")

    _require_literal(document["schema"], {FIXTURE_SCHEMA}, "$.schema")
    fixture_id = _require_literal(document["fixture_id"], {FIXTURE_ID}, "$.fixture_id")
    fixed_now = _parse_fixed_now(document["fixed_now"])
    semantics_version = _require_int(
        document["ledger_semantics_version"], "$.ledger_semantics_version"
    )
    if semantics_version != LEDGER_SEMANTICS_VERSION:
        raise ValidationError("W0 fixture uses an unsupported ledger semantics version")

    raw_cases = _require_list(document["cases"], "$.cases")
    if not raw_cases:
        raise ValidationError("W0 fixture requires at least one case")
    cases = tuple(_parse_case(value, index) for index, value in enumerate(raw_cases))
    case_ids = tuple(case.case_id for case in cases)
    if set(case_ids) != _REQUIRED_CASE_IDS or len(case_ids) != len(_REQUIRED_CASE_IDS):
        raise ValidationError("W0 fixture must contain exactly the documented synthetic case IDs")

    return WorkMetricsFixture(
        fixture_id=fixture_id,
        fixed_now=fixed_now,
        ledger_semantics_version=semantics_version,
        cases=cases,
        fixture_sha256=sha256_bytes(canonical_json_bytes(document)),
    )


def _parse_case(value: object, index: int) -> FixtureCase:
    path = f"$.cases[{index}]"
    mapping = _require_mapping(value, path)
    _require_exact_keys(mapping, {"case_id", "kind", "events", "expectation"}, path)
    case_id = _require_string(mapping["case_id"], f"{path}.case_id")
    if not _CASE_ID_PATTERN.fullmatch(case_id):
        raise ValidationError("W0 fixture case_id must be a bounded identifier")
    kind = cast(CaseKind, _require_literal(mapping["kind"], _CASE_KINDS, f"{path}.kind"))
    events = tuple(
        _parse_event(event, f"{path}.events[{event_index}]")
        for event_index, event in enumerate(_require_list(mapping["events"], f"{path}.events"))
    )
    expectation = _parse_expectation(mapping["expectation"], f"{path}.expectation")
    return FixtureCase(case_id=case_id, kind=kind, events=events, expectation=expectation)


def _parse_event(value: object, path: str) -> FixtureEvent:
    mapping = _require_mapping(value, path)
    event_type = cast(
        EventType,
        _require_literal(mapping.get("event_type"), _EVENT_TYPES, f"{path}.event_type"),
    )
    required = {"event_type", "offset_seconds", "actor_kind"}
    if event_type == "review.requested":
        required.add("independent")
    elif event_type == "review.completed":
        required.add("verdict")
    elif event_type in {"delegation.failed", "delegation.needs_operator"}:
        required.add("reason_code")
    _require_exact_keys(mapping, required, path)
    offset_seconds = _require_int(mapping["offset_seconds"], f"{path}.offset_seconds")
    if not -2_592_000 <= offset_seconds <= 2_592_000:
        raise ValidationError("W0 fixture event offset_seconds exceeds the 30-day window")
    actor_kind = cast(
        ActorKind, _require_literal(mapping["actor_kind"], _ACTOR_KINDS, f"{path}.actor_kind")
    )
    independent: bool | None = None
    verdict: ReviewVerdict | None = None
    reason_code: ReasonCode | None = None
    if event_type == "review.requested":
        independent = _require_bool(mapping["independent"], f"{path}.independent")
    if event_type == "review.completed":
        verdict = cast(
            ReviewVerdict,
            _require_literal(mapping["verdict"], _REVIEW_VERDICTS, f"{path}.verdict"),
        )
    if event_type in {"delegation.failed", "delegation.needs_operator"}:
        reason_code = cast(
            ReasonCode,
            _require_literal(mapping["reason_code"], _REASON_CODES, f"{path}.reason_code"),
        )
    return FixtureEvent(
        event_type=event_type,
        offset_seconds=offset_seconds,
        actor_kind=actor_kind,
        independent=independent,
        verdict=verdict,
        reason_code=reason_code,
    )


def _parse_expectation(value: object, path: str) -> FixtureExpectation:
    mapping = _require_mapping(value, path)
    _require_exact_keys(mapping, {"metric_id", "state", "numerator", "denominator"}, path)
    metric_id = cast(
        MetricId,
        _require_literal(mapping["metric_id"], _METRIC_IDS, f"{path}.metric_id"),
    )
    state = cast(MetricState, _require_literal(mapping["state"], _METRIC_STATES, f"{path}.state"))
    numerator = _require_optional_nonnegative_int(mapping["numerator"], f"{path}.numerator")
    denominator = _require_optional_nonnegative_int(mapping["denominator"], f"{path}.denominator")
    if state == "complete" and (numerator is None or denominator is None):
        raise ValidationError("complete W0 expectations require numerator and denominator")
    if state == "empty" and (numerator is not None or denominator is not None):
        raise ValidationError("empty W0 expectations cannot report a numeric value")
    return FixtureExpectation(
        metric_id=metric_id,
        state=state,
        numerator=numerator,
        denominator=denominator,
    )


def _parse_fixed_now(value: object) -> datetime:
    timestamp = _require_literal(value, {FIXTURE_NOW}, "$.fixed_now")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise ValidationError("W0 fixture fixed_now must use UTC Z")
    return parsed


def _reject_unsafe_tree(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise ValidationError(f"unsafe W0 fixture key at {path}")
            _reject_unsafe_tree(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_tree(child, f"{path}[{index}]")
    elif isinstance(value, str) and (
        value.startswith(("/", "file:")) or _WINDOWS_ABSOLUTE_PATH.match(value)
    ):
        raise ValidationError(f"absolute path is forbidden in W0 fixture at {path}")


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValidationError(f"W0 fixture object required at {path}")
    return cast(Mapping[str, object], value)


def _require_list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ValidationError(f"W0 fixture list required at {path}")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValidationError(f"W0 fixture has unsupported or missing fields at {path}")


def _require_literal(value: object, allowed: frozenset[str] | set[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"W0 fixture has unsupported value at {path}")
    return value


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"W0 fixture string required at {path}")
    return value


def _require_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"W0 fixture integer required at {path}")
    return value


def _require_optional_nonnegative_int(value: object, path: str) -> int | None:
    if value is None:
        return None
    parsed = _require_int(value, path)
    if parsed < 0:
        raise ValidationError(f"W0 fixture non-negative integer required at {path}")
    return parsed


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"W0 fixture boolean required at {path}")
    return value
