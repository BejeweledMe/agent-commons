from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_commons.core.ids import is_typed_id
from agent_commons.core.refs import normalize_ref
from agent_commons.errors import ValidationError


@dataclass(frozen=True)
class EventSpec:
    required: tuple[str, ...]
    entity_kind: str | None = None
    entity_id_field: str | None = None
    truth_layer: str = "working"


EVENT_SPECS: dict[str, EventSpec] = {
    "objective.created": EventSpec(
        ("objective_id", "title", "description", "acceptance_criteria"),
        "objective",
        "objective_id",
        "policy",
    ),
    "objective.revised": EventSpec(
        ("objective_id", "expected_revision", "changes"), "objective", "objective_id", "policy"
    ),
    "objective.closed": EventSpec(
        ("objective_id", "expected_revision", "reason"), "objective", "objective_id", "policy"
    ),
    "task.created": EventSpec(
        ("task_id", "title", "description", "acceptance_criteria", "priority"), "task", "task_id"
    ),
    "task.taken": EventSpec(
        ("task_id", "expected_revision", "owner_session_id"), "task", "task_id"
    ),
    "task.started": EventSpec(("task_id", "expected_revision"), "task", "task_id"),
    "task.blocked": EventSpec(("task_id", "expected_revision", "reason"), "task", "task_id"),
    "task.unblocked": EventSpec(("task_id", "expected_revision", "resolution"), "task", "task_id"),
    "task.completed": EventSpec(("task_id", "expected_revision", "summary"), "task", "task_id"),
    "task.submitted": EventSpec(("task_id", "expected_revision", "summary"), "task", "task_id"),
    "task.accepted": EventSpec(
        ("task_id", "expected_revision", "summary", "acceptance_review"),
        "task",
        "task_id",
        "truth",
    ),
    "task.cancelled": EventSpec(("task_id", "expected_revision", "reason"), "task", "task_id"),
    "task.reopened": EventSpec(("task_id", "expected_revision", "reason"), "task", "task_id"),
    "thread.opened": EventSpec(
        ("thread_id", "thread_type", "subject", "desired_outcome", "to"), "thread", "thread_id"
    ),
    "thread.replied": EventSpec(
        ("thread_id", "expected_revision", "message_id", "body"),
        "thread",
        "thread_id",
    ),
    "thread.resolved": EventSpec(
        ("thread_id", "expected_revision", "resolution", "summary"), "thread", "thread_id"
    ),
    "artifact.registered": EventSpec(
        ("artifact_id", "manifest_ref", "revision", "classification"),
        "artifact",
        "artifact_id",
        "evidence",
    ),
    "artifact.revised": EventSpec(
        ("artifact_id", "expected_revision", "manifest_ref", "revision", "classification"),
        "artifact",
        "artifact_id",
        "evidence",
    ),
    "review.requested": EventSpec(
        ("review_id", "target_ref", "target_revision", "criteria", "independent"),
        "review",
        "review_id",
    ),
    "review.completed": EventSpec(
        ("review_id", "expected_revision", "target_revision", "verdict", "summary"),
        "review",
        "review_id",
        "evidence",
    ),
    "verification.recorded": EventSpec(
        ("verification_id", "target_ref", "target_revision", "claim", "evidence_refs"),
        "verification",
        "verification_id",
        "evidence",
    ),
    "finding.reported": EventSpec(
        ("finding_id", "summary", "severity", "evidence_refs"), "finding", "finding_id"
    ),
    "finding.promoted": EventSpec(
        ("finding_id", "expected_revision", "evidence_refs", "summary"),
        "finding",
        "finding_id",
        "truth",
    ),
    "finding.contested": EventSpec(
        ("finding_id", "expected_revision", "reason"), "finding", "finding_id"
    ),
    "finding.resolved": EventSpec(
        ("finding_id", "expected_revision", "resolution"), "finding", "finding_id", "truth"
    ),
    "decision.proposed": EventSpec(
        ("decision_id", "scope", "proposal", "alternatives"), "decision", "decision_id"
    ),
    "decision.accepted": EventSpec(
        ("decision_id", "expected_revision", "rationale", "evidence_refs", "dissent"),
        "decision",
        "decision_id",
        "truth",
    ),
    "decision.rejected": EventSpec(
        ("decision_id", "expected_revision", "rationale"), "decision", "decision_id", "truth"
    ),
    "decision.deferred": EventSpec(
        ("decision_id", "expected_revision", "reason"), "decision", "decision_id"
    ),
    "decision.superseded": EventSpec(
        ("decision_id", "expected_revision", "replacement_decision_id", "reason"),
        "decision",
        "decision_id",
        "truth",
    ),
    "handoff.created": EventSpec(
        ("handoff_id", "to", "completed", "active", "next_actions"), "handoff", "handoff_id"
    ),
    "handoff.acknowledged": EventSpec(
        ("handoff_id", "expected_revision", "note"), "handoff", "handoff_id"
    ),
    "delegation.requested": EventSpec(
        (
            "delegation_id",
            "target_ref",
            "target_revision",
            "target_profile",
            "purpose",
            "parent_session_id",
            "root_delegation_id",
            "depth",
            "limits",
        ),
        "delegation",
        "delegation_id",
    ),
    "delegation.started": EventSpec(
        ("delegation_id", "expected_revision", "child_session_id", "attempt"),
        "delegation",
        "delegation_id",
    ),
    "delegation.input_needed": EventSpec(
        ("delegation_id", "expected_revision", "summary"),
        "delegation",
        "delegation_id",
    ),
    "delegation.resumed": EventSpec(
        ("delegation_id", "expected_revision", "resolution"),
        "delegation",
        "delegation_id",
    ),
    "delegation.succeeded": EventSpec(
        ("delegation_id", "expected_revision", "summary", "result_refs"),
        "delegation",
        "delegation_id",
    ),
    "delegation.failed": EventSpec(
        ("delegation_id", "expected_revision", "reason_code", "summary"),
        "delegation",
        "delegation_id",
    ),
    "delegation.cancelled": EventSpec(
        ("delegation_id", "expected_revision", "reason"),
        "delegation",
        "delegation_id",
    ),
    "delegation.recovered": EventSpec(
        ("delegation_id", "expected_revision", "reason"),
        "delegation",
        "delegation_id",
    ),
    "delegation.timed_out": EventSpec(
        ("delegation_id", "expected_revision", "summary"),
        "delegation",
        "delegation_id",
    ),
    "delegation.needs_operator": EventSpec(
        ("delegation_id", "expected_revision", "reason_code", "summary"),
        "delegation",
        "delegation_id",
    ),
    "event.corrected": EventSpec(
        ("target_event_id", "expected_target_sha256", "replacement_payload"),
        "event",
        "target_event_id",
    ),
    "event.invalidated": EventSpec(("target_ref", "reason"), "event", None, "truth"),
    "event.invalidation_revoked": EventSpec(
        ("invalidation_event_id", "reason"), "event", None, "truth"
    ),
}


_STRING_FIELDS = {
    "objective_id",
    "task_id",
    "thread_id",
    "message_id",
    "artifact_id",
    "manifest_ref",
    "review_id",
    "verification_id",
    "finding_id",
    "decision_id",
    "handoff_id",
    "delegation_id",
    "title",
    "description",
    "summary",
    "reason",
    "resolution",
    "scope",
    "proposal",
    "expected_revision",
    "target_revision",
    "revision",
    "classification",
    "body",
    "desired_outcome",
    "invalidation_event_id",
    "target_event_id",
    "expected_target_sha256",
    "owner_session_id",
    "replacement_decision_id",
    "rationale",
    "claim",
    "note",
    "target_profile",
    "purpose",
    "parent_session_id",
    "parent_delegation_id",
    "root_delegation_id",
    "child_session_id",
    "reason_code",
}

_STRING_LIST_FIELDS = {
    "acceptance_criteria",
    "criteria",
    "alternatives",
    "dissent",
    "to",
    "completed",
    "active",
    "next_actions",
    "blockers",
    "risks",
    "open_questions",
    "dependencies",
    "superseded_correction_event_ids",
}

_REF_LIST_FIELDS = {"artifact_refs", "related_refs", "result_refs"}

_OBJECTIVE_CHANGE_FIELDS = {"title", "description", "acceptance_criteria", "extensions"}

_DELEGATION_TARGET_PROFILES = {
    "codex-builder",
    "codex-independent-reviewer",
    "claude-builder",
    "claude-independent-reviewer",
}
_DELEGATION_PURPOSES = {"implementation", "independent_review", "verification"}
_DELEGATION_REASON_CODES = {
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
_DELEGATION_BUDGET_UNITS = {"tokens", "micro_usd", "provider_units"}


def _validate_ref(value: Any, field: str) -> None:
    try:
        normalize_ref(value)
    except ValidationError as exc:
        raise ValidationError(f"{field} must be a valid typed reference: {exc}") from exc


def _validate_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(f"{field}[{index}] must be a non-empty string")


def _validate_ref_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    for index, item in enumerate(value):
        _validate_ref(item, f"{field}[{index}]")


def _validate_revision_bound_ref(value: Any, field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"ref", "revision"}:
        raise ValidationError(f"{field} must contain exactly 'ref' and 'revision'")
    _validate_ref(value["ref"], f"{field}.ref")
    revision = value["revision"]
    if not isinstance(revision, str) or not revision.strip():
        raise ValidationError(f"{field}.revision must be a non-empty string")


def _validate_evidence_ref_list(value: Any) -> None:
    if not isinstance(value, list):
        raise ValidationError("evidence_refs must be a list")
    for index, item in enumerate(value):
        _validate_revision_bound_ref(item, f"evidence_refs[{index}]")


def _validate_artifact_binding_list(value: Any) -> None:
    if not isinstance(value, list):
        raise ValidationError("artifact_bindings must be a list")
    for index, item in enumerate(value):
        _validate_revision_bound_ref(item, f"artifact_bindings[{index}]")
        if item["ref"].get("kind") != "artifact":
            raise ValidationError(f"artifact_bindings[{index}].ref.kind must be artifact")


def _validate_objective_changes(value: Any) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValidationError("changes must be a non-empty object")
    unsupported = sorted(set(value).difference(_OBJECTIVE_CHANGE_FIELDS))
    if unsupported:
        raise ValidationError(
            "changes contains unsupported objective fields: " + ", ".join(unsupported)
        )
    for field in ("title", "description"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise ValidationError(f"changes.{field} must be a non-empty string")
    if "acceptance_criteria" in value:
        _validate_string_list(value["acceptance_criteria"], "changes.acceptance_criteria")
    if "extensions" in value and not isinstance(value["extensions"], Mapping):
        raise ValidationError("changes.extensions must be an object")


def _bounded_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_delegation_limits(value: Any) -> None:
    expected = {
        "max_depth",
        "wall_time_seconds",
        "max_attempts",
        "max_concurrency",
        "budget",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValidationError(
            "limits must contain exactly max_depth, wall_time_seconds, max_attempts, "
            "max_concurrency, and budget"
        )
    _bounded_integer(value["max_depth"], "limits.max_depth", minimum=0, maximum=8)
    _bounded_integer(
        value["wall_time_seconds"],
        "limits.wall_time_seconds",
        minimum=1,
        maximum=86400,
    )
    _bounded_integer(value["max_attempts"], "limits.max_attempts", minimum=1, maximum=32)
    _bounded_integer(
        value["max_concurrency"],
        "limits.max_concurrency",
        minimum=1,
        maximum=32,
    )
    budget = value["budget"]
    if not isinstance(budget, Mapping) or set(budget) != {"unit", "limit"}:
        raise ValidationError("limits.budget must contain exactly unit and limit")
    if budget["unit"] not in _DELEGATION_BUDGET_UNITS:
        raise ValidationError("invalid delegation budget unit")
    _bounded_integer(
        budget["limit"],
        "limits.budget.limit",
        minimum=1,
        maximum=1_000_000_000_000,
    )


def validate_payload(event_type: str, payload: Mapping[str, Any]) -> EventSpec:
    try:
        spec = EVENT_SPECS[event_type]
    except KeyError as exc:
        raise ValidationError(f"unsupported event type: {event_type}") from exc
    if not isinstance(payload, Mapping):
        raise ValidationError("event payload must be an object")
    missing = [field for field in spec.required if field not in payload]
    if missing:
        raise ValidationError(f"{event_type} is missing required fields: {', '.join(missing)}")
    for field in _STRING_FIELDS.intersection(payload):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field} must be a non-empty string")
    for field in _STRING_LIST_FIELDS.intersection(payload):
        _validate_string_list(payload[field], field)
    for field in _REF_LIST_FIELDS.intersection(payload):
        _validate_ref_list(payload[field], field)
    if "evidence_refs" in payload:
        _validate_evidence_ref_list(payload["evidence_refs"])
    if "artifact_bindings" in payload:
        _validate_artifact_binding_list(payload["artifact_bindings"])
    if "acceptance_review" in payload:
        _validate_revision_bound_ref(payload["acceptance_review"], "acceptance_review")
        if payload["acceptance_review"]["ref"].get("kind") != "review":
            raise ValidationError("acceptance_review.ref.kind must be review")
    for field in ("target_ref",):
        if field in payload:
            _validate_ref(payload[field], field)
    if "extensions" in payload and not isinstance(payload["extensions"], Mapping):
        raise ValidationError("extensions must be an object")
    if event_type == "objective.revised":
        _validate_objective_changes(payload["changes"])
    if event_type == "event.corrected":
        if not isinstance(payload["replacement_payload"], Mapping):
            raise ValidationError("replacement_payload must be an object")
        digest = payload["expected_target_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValidationError("expected_target_sha256 must be a lowercase SHA-256 digest")
        for correction_id in payload.get("superseded_correction_event_ids", []):
            if not is_typed_id(correction_id, "evt"):
                raise ValidationError("superseded_correction_event_ids must contain evt.<ULID> IDs")
    if event_type == "event.invalidated":
        _validate_ref(payload["target_ref"], "target_ref")
        if payload["target_ref"].get("kind") != "event":
            raise ValidationError("event.invalidated target_ref.kind must be event")
    if event_type == "review.requested" and not isinstance(payload["independent"], bool):
        raise ValidationError("independent must be a boolean")
    if event_type == "review.completed" and (
        not isinstance(payload["verdict"], str)
        or payload["verdict"] not in {"approved", "changes_requested", "rejected", "abstained"}
    ):
        raise ValidationError("invalid review verdict")
    if event_type == "thread.opened" and (
        not isinstance(payload["thread_type"], str)
        or payload["thread_type"]
        not in {
            "question",
            "proposal",
            "critique",
            "risk",
            "help_request",
            "review_discussion",
            "decision_request",
        }
    ):
        raise ValidationError("invalid thread type")
    if event_type == "thread.resolved" and (
        not isinstance(payload["resolution"], str)
        or payload["resolution"] not in {"resolved", "accepted", "rejected", "deferred", "archived"}
    ):
        raise ValidationError("invalid thread resolution")
    if event_type == "delegation.requested":
        if payload["target_profile"] not in _DELEGATION_TARGET_PROFILES:
            raise ValidationError("invalid delegation target_profile")
        if payload["purpose"] not in _DELEGATION_PURPOSES:
            raise ValidationError("invalid delegation purpose")
        reviewer_profile = str(payload["target_profile"]).endswith("-independent-reviewer")
        if payload["purpose"] == "implementation" and reviewer_profile:
            raise ValidationError("implementation delegations require a builder profile")
        if payload["purpose"] in {"independent_review", "verification"} and not reviewer_profile:
            raise ValidationError(
                "review and verification delegations require an independent-reviewer profile"
            )
        _bounded_integer(payload["depth"], "depth", minimum=0, maximum=8)
        _validate_delegation_limits(payload["limits"])
    if event_type == "delegation.started":
        _bounded_integer(payload["attempt"], "attempt", minimum=1, maximum=32)
    if (
        event_type in {"delegation.failed", "delegation.needs_operator"}
        and payload["reason_code"] not in _DELEGATION_REASON_CODES
    ):
        raise ValidationError("invalid delegation reason_code")
    return spec
