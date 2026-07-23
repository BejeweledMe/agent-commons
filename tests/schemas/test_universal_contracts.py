from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agent_commons.config import CommonsPaths
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.domain.lifecycle import validate_transition
from agent_commons.domain.projection import ProjectSnapshot
from agent_commons.domain.validation import EVENT_SPECS, validate_payload
from agent_commons.errors import SecurityPolicyError, ValidationError
from agent_commons.security import SecurityPolicy
from agent_commons.storage import EventStore, ManifestStore

ULID_0 = "0" * 26
ULID_1 = "0" * 25 + "1"
ULID_2 = "0" * 25 + "2"
EVENT_ID = f"evt.{ULID_1}"
TARGET_REVISION = f"evt.{ULID_2}"
WORKSPACE_ID = f"workspace.{ULID_0}"

OBJECTIVE_ID = f"objective.{ULID_0}"
TASK_ID = f"task.{ULID_0}"
THREAD_ID = f"thread.{ULID_0}"
MESSAGE_ID = f"message.{ULID_0}"
ARTIFACT_ID = f"artifact.{ULID_0}"
REVIEW_ID = f"review.{ULID_0}"
VERIFICATION_ID = f"verification.{ULID_0}"
FINDING_ID = f"finding.{ULID_0}"
DECISION_ID = f"decision.{ULID_0}"
REPLACEMENT_DECISION_ID = f"decision.{ULID_1}"
HANDOFF_ID = f"handoff.{ULID_0}"
DELEGATION_ID = f"delegation.{ULID_0}"
PARENT_SESSION_ID = "session." + "a" * 32
CHILD_SESSION_ID = "session." + "b" * 32
MANIFEST_REF = "mft.artifact.sha256." + "a" * 64
CONTENT_REVISION = "sha256:" + "a" * 64
TYPED_ARTIFACT_REF = {"kind": "artifact", "id": ARTIFACT_ID}
TYPED_EVENT_REF = {"kind": "event", "id": EVENT_ID}
BOUND_ARTIFACT_REF = {"ref": TYPED_ARTIFACT_REF, "revision": TARGET_REVISION}
BOUND_REVIEW_REF = {
    "ref": {"kind": "review", "id": REVIEW_ID},
    "revision": TARGET_REVISION,
}
DELEGATION_LIMITS = {
    "max_depth": 1,
    "wall_time_seconds": 900,
    "max_attempts": 2,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 10000},
}


PAYLOADS: dict[str, dict[str, Any]] = {
    "objective.created": {
        "objective_id": OBJECTIVE_ID,
        "title": "Ship a reliable service",
        "description": "Coordinate implementation and review",
        "acceptance_criteria": ["all acceptance tests pass"],
    },
    "objective.revised": {
        "objective_id": OBJECTIVE_ID,
        "expected_revision": EVENT_ID,
        "changes": {"description": "Coordinate implementation, security, and review"},
    },
    "objective.closed": {
        "objective_id": OBJECTIVE_ID,
        "expected_revision": EVENT_ID,
        "reason": "accepted",
    },
    "task.created": {
        "task_id": TASK_ID,
        "title": "Implement endpoint",
        "description": "Build the health endpoint",
        "acceptance_criteria": ["returns 200"],
        "priority": "normal",
    },
    "task.taken": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "owner_session_id": "session.builder-stable",
    },
    "task.started": {"task_id": TASK_ID, "expected_revision": EVENT_ID},
    "task.blocked": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "waiting for an interface",
    },
    "task.unblocked": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "resolution": "interface agreed",
    },
    "task.completed": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "implementation complete",
    },
    "task.submitted": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "ready for review",
    },
    "task.accepted": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "summary": "acceptance criteria met",
        "acceptance_review": BOUND_REVIEW_REF,
    },
    "task.cancelled": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "superseded",
    },
    "task.reopened": {
        "task_id": TASK_ID,
        "expected_revision": EVENT_ID,
        "reason": "regression found",
    },
    "thread.opened": {
        "thread_id": THREAD_ID,
        "thread_type": "question",
        "subject": "Which API shape should we use?",
        "desired_outcome": "choose one interface",
        "to": ["role.backend"],
    },
    "thread.replied": {
        "thread_id": THREAD_ID,
        "expected_revision": EVENT_ID,
        "message_id": MESSAGE_ID,
        "body": "Prefer the versioned endpoint.",
    },
    "thread.resolved": {
        "thread_id": THREAD_ID,
        "expected_revision": EVENT_ID,
        "resolution": "resolved",
        "summary": "Use the versioned endpoint.",
    },
    "artifact.registered": {
        "artifact_id": ARTIFACT_ID,
        "manifest_ref": MANIFEST_REF,
        "revision": CONTENT_REVISION,
        "classification": "internal",
    },
    "artifact.revised": {
        "artifact_id": ARTIFACT_ID,
        "expected_revision": EVENT_ID,
        "manifest_ref": MANIFEST_REF,
        "revision": CONTENT_REVISION,
        "classification": "internal",
    },
    "review.requested": {
        "review_id": REVIEW_ID,
        "target_ref": TYPED_ARTIFACT_REF,
        "target_revision": TARGET_REVISION,
        "criteria": ["correctness"],
        "independent": True,
    },
    "review.completed": {
        "review_id": REVIEW_ID,
        "expected_revision": EVENT_ID,
        "target_revision": TARGET_REVISION,
        "verdict": "approved",
        "summary": "criteria satisfied",
    },
    "verification.recorded": {
        "verification_id": VERIFICATION_ID,
        "target_ref": TYPED_ARTIFACT_REF,
        "target_revision": TARGET_REVISION,
        "claim": "The artifact passes its deterministic test.",
        "evidence_refs": [BOUND_ARTIFACT_REF],
    },
    "finding.reported": {
        "finding_id": FINDING_ID,
        "summary": "A retry can duplicate an operation.",
        "severity": "high",
        "evidence_refs": [BOUND_ARTIFACT_REF],
    },
    "finding.promoted": {
        "finding_id": FINDING_ID,
        "expected_revision": EVENT_ID,
        "evidence_refs": [BOUND_ARTIFACT_REF],
        "summary": "The retry defect is independently reproduced.",
    },
    "finding.contested": {
        "finding_id": FINDING_ID,
        "expected_revision": EVENT_ID,
        "reason": "the reproduction used a stale revision",
    },
    "finding.resolved": {
        "finding_id": FINDING_ID,
        "expected_revision": EVENT_ID,
        "resolution": "fixed and regression-tested",
    },
    "decision.proposed": {
        "decision_id": DECISION_ID,
        "scope": "architecture.persistence",
        "proposal": "Use an append-only ledger.",
        "alternatives": ["mutable shared document"],
    },
    "decision.accepted": {
        "decision_id": DECISION_ID,
        "expected_revision": EVENT_ID,
        "rationale": "preserves provenance",
        "evidence_refs": [BOUND_ARTIFACT_REF],
        "dissent": [],
    },
    "decision.rejected": {
        "decision_id": DECISION_ID,
        "expected_revision": EVENT_ID,
        "rationale": "fails crash-safety requirements",
    },
    "decision.deferred": {
        "decision_id": DECISION_ID,
        "expected_revision": EVENT_ID,
        "reason": "awaiting evidence",
    },
    "decision.superseded": {
        "decision_id": DECISION_ID,
        "expected_revision": EVENT_ID,
        "replacement_decision_id": REPLACEMENT_DECISION_ID,
        "reason": "new requirements",
    },
    "handoff.created": {
        "handoff_id": HANDOFF_ID,
        "to": ["role.reviewer"],
        "completed": ["schema implementation"],
        "active": ["contract testing"],
        "next_actions": ["run independent review"],
    },
    "handoff.acknowledged": {
        "handoff_id": HANDOFF_ID,
        "expected_revision": EVENT_ID,
        "note": "review accepted",
    },
    "delegation.requested": {
        "delegation_id": DELEGATION_ID,
        "target_ref": TYPED_ARTIFACT_REF,
        "target_revision": TARGET_REVISION,
        "target_profile": "claude-independent-reviewer",
        "purpose": "independent_review",
        "parent_session_id": PARENT_SESSION_ID,
        "root_delegation_id": DELEGATION_ID,
        "depth": 0,
        "limits": DELEGATION_LIMITS,
    },
    "delegation.started": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "child_session_id": CHILD_SESSION_ID,
        "attempt": 1,
    },
    "delegation.input_needed": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "summary": "Operator must choose a supported target.",
    },
    "delegation.resumed": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "resolution": "The operator selected the exact target revision.",
    },
    "delegation.succeeded": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "summary": "Independent review completed.",
        "result_refs": [TYPED_ARTIFACT_REF],
    },
    "delegation.failed": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "reason_code": "runtime_error",
        "summary": "The provider process exited unsuccessfully.",
    },
    "delegation.cancelled": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "reason": "Operator cancelled the requested work.",
    },
    "delegation.recovered": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "reason": "The requester session is unavailable.",
    },
    "delegation.timed_out": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "summary": "The hard wall-time limit elapsed.",
    },
    "delegation.needs_operator": {
        "delegation_id": DELEGATION_ID,
        "expected_revision": EVENT_ID,
        "reason_code": "orphaned",
        "summary": "Runtime state could not be reconciled automatically.",
    },
    "event.corrected": {
        "target_event_id": EVENT_ID,
        "expected_target_sha256": "a" * 64,
        "replacement_payload": {"summary": "corrected recording"},
    },
    "event.invalidated": {
        "target_ref": TYPED_EVENT_REF,
        "reason": "duplicate assertion",
    },
    "event.invalidation_revoked": {
        "invalidation_event_id": EVENT_ID,
        "reason": "new evidence restored the assertion",
    },
}


def payload_schema_index(registry: SchemaRegistry) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for schema_name in registry.schema_names:
        schema = registry.schema(schema_name)
        singular = schema.get("x-event-type")
        family = schema.get("x-event-types") or []
        event_types = [singular] if isinstance(singular, str) else list(family)
        for event_type in event_types:
            index.setdefault(str(event_type), []).append(schema_name)
    return index


def event_document(
    registry: SchemaRegistry,
    event_type: str,
    *,
    payload: Mapping[str, Any] | None = None,
    persisted: bool = True,
) -> dict[str, Any]:
    schema_name = payload_schema_index(registry)[event_type][0]
    spec = EVENT_SPECS[event_type]
    actual_payload = deepcopy(dict(payload if payload is not None else PAYLOADS[event_type]))
    subject_kind = spec.entity_kind or "event"
    subject_id = (
        str(actual_payload.get(spec.entity_id_field))
        if spec.entity_id_field
        else str(
            actual_payload.get("invalidation_event_id")
            or actual_payload.get("target_ref", {}).get("id")
            or EVENT_ID
        )
    )
    document: dict[str, Any] = {
        "schema": "commons.event.v1",
        "payload_schema": schema_name,
        "workspace_id": WORKSPACE_ID,
        "event_type": event_type,
        "actor": {
            "principal_id": "principal.local-operator",
            "session_id": "session.codex-stable",
            "stable_instance_id": "codex-thread-stable-12345678",
            "client": "codex",
            "software": "codex-cli",
            "role_id": "builder",
            "model_family": "gpt",
            "model": "test-model",
            "capabilities": ["event:write"],
            "source_producer": {
                "client": "claude-code",
                "software": "claude-cli",
                "model_family": "claude",
                "model": "test-source-model",
            },
        },
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "idempotency_namespace": f"tests:{event_type}",
        "idempotency_key": f"minimal:{event_type}",
        "relations": [],
        "tags": ["contract"],
        "provenance": {
            "writer": "schema-contract-tests",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        "payload": actual_payload,
    }
    if persisted:
        document["event_id"] = EVENT_ID
        document["recorded_at"] = "2026-07-14T12:00:00Z"
    return document


def lifecycle_snapshot(event_type: str, payload: Mapping[str, Any]) -> ProjectSnapshot:
    snapshot = ProjectSnapshot()
    if event_type in {
        "review.requested",
        "verification.recorded",
        "delegation.requested",
    }:
        snapshot.artifacts[ARTIFACT_ID] = {
            "id": ARTIFACT_ID,
            "state": "registered",
            "revision": TARGET_REVISION,
        }
        if event_type == "delegation.requested":
            # An independent_review delegation requires an open independent
            # review request bound to its exact target revision.
            snapshot.reviews[REVIEW_ID] = {
                "id": REVIEW_ID,
                "state": "requested",
                "independent": True,
                "target_ref": dict(TYPED_ARTIFACT_REF),
                "target_revision": TARGET_REVISION,
            }
        return snapshot

    current_states = {
        "objective.revised": "active",
        "objective.closed": "active",
        "task.taken": "ready",
        "task.started": "ready",
        "task.blocked": "active",
        "task.unblocked": "blocked",
        "task.completed": "active",
        "task.submitted": "completed",
        "task.accepted": "review",
        "task.cancelled": "ready",
        "task.reopened": "completed",
        "thread.replied": "open",
        "thread.resolved": "open",
        "artifact.revised": "registered",
        "review.completed": "requested",
        "finding.promoted": "reported",
        "finding.contested": "reported",
        "finding.resolved": "reported",
        "decision.accepted": "proposed",
        "decision.rejected": "proposed",
        "decision.deferred": "proposed",
        "decision.superseded": "accepted",
        "handoff.acknowledged": "open",
        "delegation.started": "requested",
        "delegation.input_needed": "active",
        "delegation.resumed": "input_needed",
        "delegation.succeeded": "active",
        "delegation.failed": "active",
        "delegation.cancelled": "requested",
        "delegation.recovered": "requested",
        "delegation.timed_out": "active",
        "delegation.needs_operator": "active",
    }
    state = current_states.get(event_type)
    if state is None:
        return snapshot
    family = event_type.split(".", 1)[0]
    collection_name = {
        "objective": "objectives",
        "task": "tasks",
        "thread": "threads",
        "artifact": "artifacts",
        "review": "reviews",
        "finding": "findings",
        "decision": "decisions",
        "handoff": "handoffs",
        "delegation": "delegations",
    }[family]
    identifier = str(payload[f"{family}_id"])
    current: dict[str, Any] = {
        "id": identifier,
        "state": state,
        "revision": EVENT_ID,
    }
    if event_type == "review.completed":
        current.update(
            {
                "target_revision": TARGET_REVISION,
                "independent": True,
                "actor": {"session_id": "session.requester"},
            }
        )
    if family == "decision":
        current["scope"] = "architecture.persistence"
    if family == "delegation":
        current.update(
            {
                "target_ref": TYPED_ARTIFACT_REF,
                "target_revision": TARGET_REVISION,
                "parent_session_id": PARENT_SESSION_ID,
                "root_delegation_id": DELEGATION_ID,
                "depth": 0,
                "limits": DELEGATION_LIMITS,
            }
        )
        if state != "requested":
            current["child_session_id"] = CHILD_SESSION_ID
        snapshot.artifacts[ARTIFACT_ID] = {
            "id": ARTIFACT_ID,
            "state": "registered",
            "revision": TARGET_REVISION,
        }
    getattr(snapshot, collection_name)[identifier] = current
    if event_type == "task.accepted":
        snapshot.reviews[REVIEW_ID] = {
            "id": REVIEW_ID,
            "state": "approved",
            "revision": TARGET_REVISION,
            "effective_revision": TARGET_REVISION,
            "independent": True,
            "stale": False,
            "target_ref": {"kind": "task", "id": TASK_ID},
            "target_revision": EVENT_ID,
        }
    if event_type == "decision.superseded":
        snapshot.decisions[REPLACEMENT_DECISION_ID] = {
            "id": REPLACEMENT_DECISION_ID,
            "state": "proposed",
            "revision": TARGET_REVISION,
            "scope": "architecture.persistence",
        }
    return snapshot


def artifact_manifest(*, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": "commons.manifest.artifact.v1",
        "kind": "artifact",
        "artifact_id": ARTIFACT_ID,
        "revision": CONTENT_REVISION,
        "source": {"path": "artifacts/contract-report.json"},
        "media_type": "application/json",
        "size_bytes": 128,
        "classification": "internal",
        "captured": False,
        "metadata": dict(metadata or {"purpose": "schema contract evidence"}),
    }


def files_under(path: Path) -> list[Path]:
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def test_every_event_spec_has_exactly_one_complete_payload_family() -> None:
    registry = SchemaRegistry()
    index = payload_schema_index(registry)

    assert set(index) == set(EVENT_SPECS)
    assert all(len(index[event_type]) == 1 for event_type in EVENT_SPECS)
    for event_type, spec in EVENT_SPECS.items():
        schema = registry.schema(index[event_type][0])
        assert set(spec.required) <= set(schema.get("properties", {})), event_type


@pytest.mark.parametrize("event_type", sorted(EVENT_SPECS))
def test_minimal_specimen_validates_schema_domain_and_lifecycle(event_type: str) -> None:
    registry = SchemaRegistry()
    payload = deepcopy(PAYLOADS[event_type])
    event = event_document(registry, event_type, payload=payload)

    registry.validate_event(event)
    validate_payload(event_type, payload)
    actor_session_id = (
        CHILD_SESSION_ID
        if event_type in {"delegation.input_needed", "delegation.succeeded"}
        else PARENT_SESSION_ID
    )
    validate_transition(
        lifecycle_snapshot(event_type, payload),
        event_type,
        payload,
        actor_session_id=actor_session_id,
    )


def test_event_type_and_payload_family_mismatch_fails_closed() -> None:
    registry = SchemaRegistry()
    task = event_document(registry, "task.created")
    task["payload_schema"] = payload_schema_index(registry)["objective.created"][0]

    with pytest.raises(ValidationError, match="not 'task.created'"):
        registry.validate_event(task)


def test_unknown_payload_and_envelope_fields_are_rejected() -> None:
    registry = SchemaRegistry()
    event = event_document(registry, "task.created")
    event["payload"]["surprise"] = "schema drift"
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate_event(event)

    event = event_document(registry, "task.created")
    event["surprise"] = "envelope drift"
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate_event(event)


def test_typed_entity_ids_and_reference_shapes_are_enforced() -> None:
    registry = SchemaRegistry()
    event = event_document(registry, "task.created")
    event["payload"]["task_id"] = "task.not-a-sortable-id"
    with pytest.raises(ValidationError, match="does not match"):
        registry.validate_event(event)

    event = event_document(registry, "review.requested")
    event["payload"]["target_ref"] = {
        "kind": "artifact",
        "id": ARTIFACT_ID,
        "display_name": "must not enter a typed ref",
    }
    with pytest.raises(ValidationError, match="Additional properties"):
        registry.validate_event(event)

    event = event_document(registry, "review.requested")
    event["payload"]["target_ref"] = {"kind": "Bad Kind", "id": ARTIFACT_ID}
    with pytest.raises(ValidationError, match="does not match"):
        registry.validate_event(event)

    event = event_document(registry, "review.requested")
    event["payload"]["target_ref"] = {"kind": "artifact", "id": ""}
    with pytest.raises(ValidationError, match="should be non-empty"):
        registry.validate_event(event)


@pytest.mark.parametrize(
    "event_type",
    (
        "review.completed",
        "verification.recorded",
        "finding.reported",
        "decision.accepted",
    ),
)
def test_canonical_evidence_requires_an_exact_revision_binding(event_type: str) -> None:
    registry = SchemaRegistry()
    event = event_document(registry, event_type)
    event["payload"]["evidence_refs"] = [TYPED_ARTIFACT_REF]

    with pytest.raises(ValidationError, match="'ref' is a required property"):
        registry.validate_event(event)
    with pytest.raises(ValidationError, match="exactly 'ref' and 'revision'"):
        validate_payload(event_type, event["payload"])


def test_artifact_manifest_and_security_policy_fail_before_publication(
    tmp_path: Path,
) -> None:
    paths = CommonsPaths.for_workspace(
        tmp_path / "repo",
        state_root=tmp_path / "state",
    )
    paths.ensure_layout()
    registry = SchemaRegistry()
    policy = SecurityPolicy(detect_free_text_pii=False)
    store = ManifestStore(paths, registry, validators=(policy.assert_safe,))

    valid = artifact_manifest()
    registry.validate_manifest(valid)

    secret = "sk-proj-" + "A" * 24
    unsafe_secret = artifact_manifest(metadata={"nested": [{"credentials": {"api_key": secret}}]})
    with pytest.raises(SecurityPolicyError) as caught:
        store.put(unsafe_secret)
    assert secret not in str(caught.value)
    assert files_under(paths.manifests) == []

    unsafe_pii = artifact_manifest(
        metadata={"validation": {"check_id": "directly-identifying-unit"}}
    )
    with pytest.raises(SecurityPolicyError, match="pii:classified_field"):
        store.put(unsafe_pii)
    assert files_under(paths.manifests) == []

    record = store.put(valid)
    assert record.manifest_id.startswith("mft.artifact.sha256.")
    assert len(files_under(paths.manifests)) == 1


def test_event_security_and_schema_failures_leave_no_receipt_or_event(
    tmp_path: Path,
) -> None:
    paths = CommonsPaths.for_workspace(
        tmp_path / "repo",
        state_root=tmp_path / "state",
    )
    paths.ensure_layout()
    registry = SchemaRegistry()
    policy = SecurityPolicy(detect_free_text_pii=False)
    store = EventStore(paths, registry, validators=(policy.assert_safe,))

    secret_event = event_document(
        registry,
        "thread.replied",
        payload={
            **PAYLOADS["thread.replied"],
            "body": "password: x",
        },
        persisted=False,
    )
    with pytest.raises(SecurityPolicyError):
        store.append(secret_event)
    assert files_under(paths.events) == []
    assert files_under(paths.idempotency) == []

    malformed_event = event_document(registry, "task.created", persisted=False)
    malformed_event["payload"]["unknown"] = True
    with pytest.raises(ValidationError, match="Additional properties"):
        store.append(malformed_event)
    assert files_under(paths.events) == []
    assert files_under(paths.idempotency) == []
