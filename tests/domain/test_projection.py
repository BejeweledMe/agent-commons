from __future__ import annotations

from agent_commons.core.canonical import canonical_sha256
from agent_commons.domain.projection import project_events
from agent_commons.views import orientation


def event(number: int, event_type: str, payload: dict, subject_kind: str, subject_id: str) -> dict:
    return {
        "event_id": f"evt.{number:026d}",
        "workspace_id": "workspace.00000000000000000000000001",
        "event_type": event_type,
        "recorded_at": f"2026-01-01T00:00:{number:02d}Z",
        "actor": {"session_id": "session.test", "role_id": "builder"},
        "payload": payload,
        "subject_refs": [{"kind": subject_kind, "id": subject_id}],
        "relations": [],
    }


def test_task_lifecycle_and_revision() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Implement API",
            "description": "Build one endpoint",
            "acceptance_criteria": ["tests pass"],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    started = event(
        2,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    completed = event(
        3,
        "task.completed",
        {
            "task_id": task_id,
            "expected_revision": started["event_id"],
            "summary": "Implemented and tested",
        },
        "task",
        task_id,
    )
    snapshot = project_events([completed, created, started])
    assert snapshot.tasks[task_id]["state"] == "completed"
    assert snapshot.tasks[task_id]["revision"] == completed["event_id"]
    assert snapshot.tasks[task_id]["work_author_session_ids"] == ["session.test"]


def test_projection_indexes_corrections_by_target_and_reports_bounded_work() -> None:
    roots = [
        event(
            number,
            "objective.created",
            {
                "objective_id": f"objective.{number:026d}",
                "title": f"Objective {number}",
                "description": "bounded replay",
                "acceptance_criteria": ["projected"],
            },
            "objective",
            f"objective.{number:026d}",
        )
        for number in range(1, 101)
    ]
    corrections = [
        event(
            100 + offset,
            "event.corrected",
            {
                "target_event_id": root["event_id"],
                "expected_target_sha256": canonical_sha256(root),
                "replacement_payload": {
                    **root["payload"],
                    "title": f"Corrected {offset}",
                },
            },
            "event",
            root["event_id"],
        )
        for offset, root in enumerate(roots[:20], start=1)
    ]

    snapshot = project_events([*roots, *corrections])

    assert snapshot.replay_metrics == {
        "events_replayed": 120,
        "corrections_indexed": 20,
        "correction_targets": 20,
        "correction_candidates_examined": 20,
        "fixed_point_passes": 1,
    }


def test_conflicting_accepted_decisions_warn_fail_closed() -> None:
    one = "decision.00000000000000000000000001"
    two = "decision.00000000000000000000000002"
    events = []
    for offset, identifier in enumerate((one, two), 1):
        proposed = event(
            offset * 2 - 1,
            "decision.proposed",
            {
                "decision_id": identifier,
                "scope": "architecture.database",
                "proposal": identifier,
                "alternatives": [],
            },
            "decision",
            identifier,
        )
        accepted = event(
            offset * 2,
            "decision.accepted",
            {
                "decision_id": identifier,
                "expected_revision": proposed["event_id"],
                "rationale": "selected",
                "evidence_refs": [],
                "dissent": [],
            },
            "decision",
            identifier,
        )
        events.extend((proposed, accepted))
    snapshot = project_events(events)
    assert any("conflicting accepted decisions" in warning for warning in snapshot.warnings)
    assert snapshot.decisions[one]["state"] == "conflicted"
    assert snapshot.decisions[two]["state"] == "conflicted"


def test_review_becomes_stale_after_artifact_revision() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    first = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    request = event(
        2,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": first["event_id"],
            "criteria": ["correctness"],
            "independent": True,
        },
        "review",
        review_id,
    )
    complete = event(
        3,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": request["event_id"],
            "target_revision": first["event_id"],
            "verdict": "approved",
            "summary": "looks good",
        },
        "review",
        review_id,
    )
    second = event(
        4,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": first["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    snapshot = project_events([first, request, complete, second])
    assert snapshot.reviews[review_id]["stale"] is True


def test_invalidation_removes_event_from_effective_state() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Duplicate",
            "description": "Should disappear",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    invalidated = event(
        2,
        "event.invalidated",
        {"target_ref": {"kind": "event", "id": created["event_id"]}, "reason": "duplicate"},
        "event",
        created["event_id"],
    )
    snapshot = project_events([created, invalidated])
    assert task_id not in snapshot.tasks


def test_single_correction_replaces_payload() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Typo",
            "description": "before",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    replacement = {**created["payload"], "title": "Corrected"}
    correction = event(
        2,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": replacement,
        },
        "event",
        created["event_id"],
    )
    snapshot = project_events([created, correction])
    assert snapshot.tasks[task_id]["title"] == "Corrected"


def test_objective_revision_applies_changes_instead_of_only_storing_them() -> None:
    objective_id = "objective.00000000000000000000000001"
    created = event(
        1,
        "objective.created",
        {
            "objective_id": objective_id,
            "title": "Initial title",
            "description": "Initial description",
            "acceptance_criteria": ["old criterion"],
        },
        "objective",
        objective_id,
    )
    revised = event(
        2,
        "objective.revised",
        {
            "objective_id": objective_id,
            "expected_revision": created["event_id"],
            "changes": {
                "title": "Revised title",
                "acceptance_criteria": ["new criterion"],
            },
        },
        "objective",
        objective_id,
    )

    snapshot = project_events([created, revised])

    assert snapshot.objectives[objective_id]["title"] == "Revised title"
    assert snapshot.objectives[objective_id]["description"] == "Initial description"
    assert snapshot.objectives[objective_id]["acceptance_criteria"] == ["new criterion"]
    assert "changes" not in snapshot.objectives[objective_id]


def test_objective_cannot_be_revised_after_it_is_closed() -> None:
    objective_id = "objective.00000000000000000000000001"
    created = event(
        1,
        "objective.created",
        {
            "objective_id": objective_id,
            "title": "Immutable closed objective",
            "description": "Close before revision",
            "acceptance_criteria": [],
        },
        "objective",
        objective_id,
    )
    closed = event(
        2,
        "objective.closed",
        {
            "objective_id": objective_id,
            "expected_revision": created["event_id"],
            "reason": "done",
        },
        "objective",
        objective_id,
    )
    revised = event(
        3,
        "objective.revised",
        {
            "objective_id": objective_id,
            "expected_revision": closed["event_id"],
            "changes": {"title": "Must not apply"},
        },
        "objective",
        objective_id,
    )

    snapshot = project_events([created, closed, revised])

    assert snapshot.objectives[objective_id]["state"] == "closed"
    assert snapshot.objectives[objective_id]["title"] == "Immutable closed objective"
    assert any("objective.revised is not allowed" in item for item in snapshot.warnings)


def test_concurrent_entity_transitions_fail_closed_instead_of_timestamp_winning() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Concurrent task",
            "description": "Two branches race",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    started = event(
        2,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    cancelled = event(
        3,
        "task.cancelled",
        {
            "task_id": task_id,
            "expected_revision": created["event_id"],
            "reason": "competing branch",
        },
        "task",
        task_id,
    )

    snapshot = project_events([created, started, cancelled])

    assert snapshot.tasks[task_id]["state"] == "ready"
    assert snapshot.tasks[task_id]["revision"] == created["event_id"]
    assert any("concurrent task transitions" in item for item in snapshot.warnings)
    assert any("conflict" in item for item in snapshot.warnings)


def test_invalidating_causal_root_suppresses_all_descendant_transitions() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Invalid chain",
            "description": "Root is invalidated",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    started = event(
        2,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    completed = event(
        3,
        "task.completed",
        {
            "task_id": task_id,
            "expected_revision": started["event_id"],
            "summary": "must also disappear",
        },
        "task",
        task_id,
    )
    invalidated = event(
        4,
        "event.invalidated",
        {"target_ref": {"kind": "event", "id": created["event_id"]}, "reason": "bad root"},
        "event",
        created["event_id"],
    )

    snapshot = project_events([created, started, completed, invalidated])

    assert task_id not in snapshot.tasks
    assert ("event", started["event_id"]) in snapshot.stale_refs
    assert ("event", completed["event_id"]) in snapshot.stale_refs


def test_invalidated_correction_cannot_change_effective_payload() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Original",
            "description": "before",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    correction = event(
        2,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {**created["payload"], "title": "Invalid correction"},
        },
        "event",
        created["event_id"],
    )
    invalidated = event(
        3,
        "event.invalidated",
        {
            "target_ref": {"kind": "event", "id": correction["event_id"]},
            "reason": "correction was wrong",
        },
        "event",
        correction["event_id"],
    )

    snapshot = project_events([created, correction, invalidated])

    assert snapshot.tasks[task_id]["title"] == "Original"


def test_malformed_or_identity_changing_corrections_fail_closed() -> None:
    task_id = "task.00000000000000000000000001"
    other_task_id = "task.00000000000000000000000002"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Original",
            "description": "before",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    missing_required = event(
        2,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {"task_id": task_id, "title": "Incomplete"},
        },
        "event",
        created["event_id"],
    )
    malformed_snapshot = project_events([created, missing_required])
    assert task_id not in malformed_snapshot.tasks
    assert any("missing required fields" in item for item in malformed_snapshot.warnings)
    assert any(
        issue.code == "domain_validation_rejected" and issue.severity == "error"
        for issue in malformed_snapshot.issues
    )

    changed_identity = event(
        3,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {**created["payload"], "task_id": other_task_id},
        },
        "event",
        created["event_id"],
    )
    identity_snapshot = project_events([created, changed_identity])
    assert task_id not in identity_snapshot.tasks
    assert other_task_id not in identity_snapshot.tasks
    assert any("cannot change task_id" in item for item in identity_snapshot.warnings)
    assert [issue.code for issue in identity_snapshot.issues] == ["correction_identity_change"]
    assert identity_snapshot.to_dict()["issues"][0]["repairable"] is True


def test_artifact_preserves_content_hash_separately_from_event_revision() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    registered = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )

    artifact = project_events([registered]).artifacts[artifact_id]

    assert artifact["revision"] == registered["event_id"]
    assert artifact["content_revision"] == "sha256:" + "2" * 64


def test_artifact_change_or_manifest_loss_revokes_task_acceptance() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    task_id = "task.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    first_manifest = "mft.artifact.sha256." + "1" * 64
    artifact = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": first_manifest,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    created = event(
        2,
        "task.created",
        {
            "task_id": task_id,
            "title": "Revision-bound result",
            "description": "Acceptance depends on exact artifact bytes.",
            "acceptance_criteria": ["artifact is current"],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    started = event(
        3,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    binding = {
        "ref": {"kind": "artifact", "id": artifact_id},
        "revision": artifact["event_id"],
    }
    completed = event(
        4,
        "task.completed",
        {
            "task_id": task_id,
            "expected_revision": started["event_id"],
            "summary": "complete",
            "artifact_refs": [binding["ref"]],
            "artifact_bindings": [binding],
        },
        "task",
        task_id,
    )
    submitted = event(
        5,
        "task.submitted",
        {
            "task_id": task_id,
            "expected_revision": completed["event_id"],
            "summary": "ready",
            "artifact_refs": [binding["ref"]],
            "artifact_bindings": [binding],
        },
        "task",
        task_id,
    )
    requested = event(
        6,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "task", "id": task_id},
            "target_revision": submitted["event_id"],
            "criteria": ["artifact is current"],
            "independent": True,
        },
        "review",
        review_id,
    )
    approved = event(
        7,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": requested["event_id"],
            "target_revision": submitted["event_id"],
            "verdict": "approved",
            "summary": "approved",
        },
        "review",
        review_id,
    )
    approved["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    accepted = event(
        8,
        "task.accepted",
        {
            "task_id": task_id,
            "expected_revision": submitted["event_id"],
            "summary": "accepted",
            "acceptance_review": {
                "ref": {"kind": "review", "id": review_id},
                "revision": approved["event_id"],
            },
        },
        "task",
        task_id,
    )
    base = [artifact, created, started, completed, submitted, requested, approved, accepted]
    revised = event(
        9,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": artifact["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    corrected = event(
        9,
        "event.corrected",
        {
            "target_event_id": artifact["event_id"],
            "expected_target_sha256": canonical_sha256(artifact),
            "replacement_payload": {
                **artifact["payload"],
                "extensions": {"note": "metadata correction"},
            },
        },
        "event",
        artifact["event_id"],
    )
    invalidated = event(
        9,
        "event.invalidated",
        {
            "target_ref": {"kind": "event", "id": artifact["event_id"]},
            "reason": "artifact registration invalid",
        },
        "event",
        artifact["event_id"],
    )

    snapshots = (
        project_events([*base, revised]),
        project_events([*base, corrected]),
        project_events([*base, invalidated]),
        project_events(base, known_manifest_ids=()),
    )
    for snapshot in snapshots:
        assert snapshot.tasks[task_id]["state"] == "review"
        assert snapshot.tasks[task_id]["artifact_stale"] is True
        assert snapshot.reviews[review_id]["stale"] is True
        assert ("event", accepted["event_id"]) in snapshot.stale_refs


def test_verification_and_pending_review_become_stale_after_target_revision() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    verification_id = "verification.00000000000000000000000001"
    first = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    review = event(
        2,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": first["event_id"],
            "criteria": ["correctness"],
            "independent": True,
        },
        "review",
        review_id,
    )
    verification = event(
        3,
        "verification.recorded",
        {
            "verification_id": verification_id,
            "target_ref": {"kind": "artifact", "id": artifact_id},
            "target_revision": first["event_id"],
            "claim": "passes",
            "evidence_refs": [
                {
                    "ref": {"kind": "artifact", "id": artifact_id},
                    "revision": first["event_id"],
                }
            ],
        },
        "verification",
        verification_id,
    )
    second = event(
        4,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": first["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )

    snapshot = project_events([first, review, verification, second])

    assert snapshot.reviews[review_id]["stale"] is True
    assert snapshot.verifications[verification_id]["stale"] is True


def test_task_acceptance_keeps_qualifying_review_fresh_until_reopen() -> None:
    task_id = "task.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Reviewed task",
            "description": "Acceptance is governance-only",
            "acceptance_criteria": ["review approved"],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    started = event(
        2,
        "task.started",
        {"task_id": task_id, "expected_revision": created["event_id"]},
        "task",
        task_id,
    )
    completed = event(
        3,
        "task.completed",
        {
            "task_id": task_id,
            "expected_revision": started["event_id"],
            "summary": "complete",
        },
        "task",
        task_id,
    )
    submitted = event(
        4,
        "task.submitted",
        {
            "task_id": task_id,
            "expected_revision": completed["event_id"],
            "summary": "ready for review",
        },
        "task",
        task_id,
    )
    requested = event(
        5,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": {"kind": "task", "id": task_id},
            "target_revision": submitted["event_id"],
            "criteria": ["correctness"],
            "independent": True,
        },
        "review",
        review_id,
    )
    approved = event(
        6,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": requested["event_id"],
            "target_revision": submitted["event_id"],
            "verdict": "approved",
            "summary": "approved",
        },
        "review",
        review_id,
    )
    approved["actor"] = {"session_id": "session.reviewer", "role_id": "reviewer"}
    accepted = event(
        7,
        "task.accepted",
        {
            "task_id": task_id,
            "expected_revision": submitted["event_id"],
            "summary": "governance accepted",
            "acceptance_review": {
                "ref": {"kind": "review", "id": review_id},
                "revision": approved["event_id"],
            },
        },
        "task",
        task_id,
    )
    accepted["relations"] = [
        {
            "predicate": "depends_on",
            "subject": {"kind": "task", "id": task_id},
            "object": {"kind": "review", "id": review_id},
        }
    ]

    accepted_snapshot = project_events(
        [created, started, completed, submitted, requested, approved, accepted]
    )
    assert accepted_snapshot.tasks[task_id]["state"] == "accepted"
    assert accepted_snapshot.reviews[review_id]["stale"] is False

    corrected_review = event(
        8,
        "event.corrected",
        {
            "target_event_id": approved["event_id"],
            "expected_target_sha256": canonical_sha256(approved),
            "replacement_payload": {
                **approved["payload"],
                "summary": "approved with clarified wording",
            },
        },
        "event",
        approved["event_id"],
    )
    corrected_snapshot = project_events(
        [
            created,
            started,
            completed,
            submitted,
            requested,
            approved,
            accepted,
            corrected_review,
        ]
    )
    assert corrected_snapshot.tasks[task_id]["state"] == "review"
    assert (
        corrected_snapshot.reviews[review_id]["effective_revision"] == corrected_review["event_id"]
    )
    assert ("event", accepted["event_id"]) in corrected_snapshot.stale_refs
    assert any(
        f"task acceptance event {accepted['event_id']} is stale" in warning
        for warning in corrected_snapshot.warnings
    )
    assert not any("rejected by lifecycle" in warning for warning in corrected_snapshot.warnings)

    invalidated_review = event(
        8,
        "event.invalidated",
        {
            "target_ref": {"kind": "event", "id": approved["event_id"]},
            "reason": "review was not valid",
        },
        "event",
        approved["event_id"],
    )
    invalidated_snapshot = project_events(
        [
            created,
            started,
            completed,
            submitted,
            requested,
            approved,
            accepted,
            invalidated_review,
        ]
    )
    assert invalidated_snapshot.tasks[task_id]["state"] == "review"
    assert invalidated_snapshot.reviews[review_id]["state"] == "requested"
    assert approved["event_id"] in invalidated_snapshot.invalid_event_ids
    assert ("event", accepted["event_id"]) in invalidated_snapshot.stale_refs
    assert ("task", task_id) not in invalidated_snapshot.stale_refs
    assert not any(
        marker in warning.lower()
        for warning in invalidated_snapshot.warnings
        for marker in ("conflict", "rejected by lifecycle", "rejected by domain validation")
    )

    reopened = event(
        8,
        "task.reopened",
        {
            "task_id": task_id,
            "expected_revision": accepted["event_id"],
            "reason": "new defect",
        },
        "task",
        task_id,
    )
    reopened_snapshot = project_events(
        [created, started, completed, submitted, requested, approved, accepted, reopened]
    )
    assert reopened_snapshot.tasks[task_id]["state"] == "ready"
    assert reopened_snapshot.reviews[review_id]["stale"] is True


def test_conflicting_corrections_fail_closed_until_one_event_supersedes_all_heads() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Original",
            "description": "before",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    corrections = []
    for number, title in ((2, "Branch A"), (3, "Branch B")):
        corrections.append(
            event(
                number,
                "event.corrected",
                {
                    "target_event_id": created["event_id"],
                    "expected_target_sha256": canonical_sha256(created),
                    "replacement_payload": {**created["payload"], "title": title},
                },
                "event",
                created["event_id"],
            )
        )

    conflicted = project_events([created, *corrections])
    assert task_id not in conflicted.tasks
    assert any("multiple active heads" in item for item in conflicted.warnings)

    resolution = event(
        4,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {**created["payload"], "title": "Resolved"},
            "superseded_correction_event_ids": [
                corrections[0]["event_id"],
                corrections[1]["event_id"],
            ],
        },
        "event",
        created["event_id"],
    )
    resolved = project_events([created, *corrections, resolution])
    assert resolved.tasks[task_id]["title"] == "Resolved"


def test_correction_cycle_and_wrong_root_hash_fail_closed() -> None:
    task_id = "task.00000000000000000000000001"
    created = event(
        1,
        "task.created",
        {
            "task_id": task_id,
            "title": "Original",
            "description": "before",
            "acceptance_criteria": [],
            "priority": "normal",
        },
        "task",
        task_id,
    )
    wrong_hash = event(
        2,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": "0" * 64,
            "replacement_payload": {**created["payload"], "title": "Wrong hash"},
        },
        "event",
        created["event_id"],
    )
    wrong = project_events([created, wrong_hash])
    assert task_id not in wrong.tasks
    assert any("does not match immutable root" in item for item in wrong.warnings)

    first = event(
        3,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {**created["payload"], "title": "First"},
            "superseded_correction_event_ids": ["evt.00000000000000000000000004"],
        },
        "event",
        created["event_id"],
    )
    second = event(
        4,
        "event.corrected",
        {
            "target_event_id": created["event_id"],
            "expected_target_sha256": canonical_sha256(created),
            "replacement_payload": {**created["payload"], "title": "Second"},
            "superseded_correction_event_ids": [first["event_id"]],
        },
        "event",
        created["event_id"],
    )
    cycle = project_events([created, first, second])
    assert task_id not in cycle.tasks
    assert any("contains a cycle" in item for item in cycle.warnings)


def test_revision_bound_evidence_stales_all_judgments_and_effective_truth() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    review_id = "review.00000000000000000000000001"
    verification_id = "verification.00000000000000000000000001"
    finding_id = "finding.00000000000000000000000001"
    decision_id = "decision.00000000000000000000000001"
    artifact_ref = {"kind": "artifact", "id": artifact_id}
    registered = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    evidence = {"ref": artifact_ref, "revision": registered["event_id"]}
    review_requested = event(
        2,
        "review.requested",
        {
            "review_id": review_id,
            "target_ref": artifact_ref,
            "target_revision": registered["event_id"],
            "criteria": ["correctness"],
            "independent": False,
        },
        "review",
        review_id,
    )
    review_completed = event(
        3,
        "review.completed",
        {
            "review_id": review_id,
            "expected_revision": review_requested["event_id"],
            "target_revision": registered["event_id"],
            "verdict": "approved",
            "summary": "approved",
            "evidence_refs": [evidence],
        },
        "review",
        review_id,
    )
    verification = event(
        4,
        "verification.recorded",
        {
            "verification_id": verification_id,
            "target_ref": artifact_ref,
            "target_revision": registered["event_id"],
            "claim": "verified",
            "evidence_refs": [evidence],
        },
        "verification",
        verification_id,
    )
    finding_reported = event(
        5,
        "finding.reported",
        {
            "finding_id": finding_id,
            "summary": "finding",
            "severity": "high",
            "evidence_refs": [evidence],
        },
        "finding",
        finding_id,
    )
    finding_promoted = event(
        6,
        "finding.promoted",
        {
            "finding_id": finding_id,
            "expected_revision": finding_reported["event_id"],
            "summary": "verified finding",
            "evidence_refs": [evidence],
        },
        "finding",
        finding_id,
    )
    decision_proposed = event(
        7,
        "decision.proposed",
        {
            "decision_id": decision_id,
            "scope": "architecture.storage",
            "proposal": "use immutable storage",
            "alternatives": [],
        },
        "decision",
        decision_id,
    )
    decision_accepted = event(
        8,
        "decision.accepted",
        {
            "decision_id": decision_id,
            "expected_revision": decision_proposed["event_id"],
            "rationale": "evidence",
            "evidence_refs": [evidence],
            "dissent": [],
        },
        "decision",
        decision_id,
    )
    history = [
        registered,
        review_requested,
        review_completed,
        verification,
        finding_reported,
        finding_promoted,
        decision_proposed,
        decision_accepted,
    ]
    fresh = project_events(history)
    assert fresh.reviews[review_id]["stale"] is False
    assert fresh.verifications[verification_id]["stale"] is False
    assert fresh.findings[finding_id]["state"] == "verified"
    assert fresh.findings[finding_id]["stale"] is False
    assert fresh.decisions[decision_id]["state"] == "accepted"
    assert fresh.decisions[decision_id]["stale"] is False

    revised = event(
        9,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": registered["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    stale = project_events([*history, revised])
    assert stale.reviews[review_id]["stale"] is True
    assert stale.verifications[verification_id]["stale"] is True
    assert stale.findings[finding_id]["state"] == "verified"
    assert stale.findings[finding_id]["stale"] is True
    assert stale.decisions[decision_id]["state"] == "accepted"
    assert stale.decisions[decision_id]["stale"] is True
    effective_truth = orientation(stale)["effective_truth"]
    assert effective_truth == {"decisions": [], "findings": []}


def test_correction_changes_effective_not_causal_revision_and_stales_old_review() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    artifact_ref = {"kind": "artifact", "id": artifact_id}
    registered = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    old_review = event(
        2,
        "review.requested",
        {
            "review_id": "review.00000000000000000000000001",
            "target_ref": artifact_ref,
            "target_revision": registered["event_id"],
            "criteria": ["correctness"],
            "independent": False,
        },
        "review",
        "review.00000000000000000000000001",
    )
    corrected_payload = {**registered["payload"], "extensions": {"note": "recording fix"}}
    correction = event(
        3,
        "event.corrected",
        {
            "target_event_id": registered["event_id"],
            "expected_target_sha256": canonical_sha256(registered),
            "replacement_payload": corrected_payload,
        },
        "event",
        registered["event_id"],
    )
    new_review = event(
        4,
        "review.requested",
        {
            "review_id": "review.00000000000000000000000002",
            "target_ref": artifact_ref,
            "target_revision": correction["event_id"],
            "criteria": ["correctness"],
            "independent": False,
        },
        "review",
        "review.00000000000000000000000002",
    )
    corrected = project_events([registered, old_review, correction, new_review])
    assert corrected.artifacts[artifact_id]["revision"] == registered["event_id"]
    assert corrected.artifacts[artifact_id]["effective_revision"] == correction["event_id"]
    assert corrected.reviews[old_review["payload"]["review_id"]]["stale"] is True
    assert corrected.reviews[new_review["payload"]["review_id"]]["stale"] is False

    revised = event(
        5,
        "artifact.revised",
        {
            "artifact_id": artifact_id,
            "expected_revision": registered["event_id"],
            "manifest_ref": "mft.artifact.sha256." + "2" * 64,
            "revision": "sha256:" + "2" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    after_transition = project_events([registered, old_review, correction, new_review, revised])
    assert after_transition.artifacts[artifact_id]["revision"] == revised["event_id"]


def test_correction_cannot_rewrite_revision_bound_evidence_graph() -> None:
    finding_id = "finding.00000000000000000000000001"
    reported = event(
        1,
        "finding.reported",
        {
            "finding_id": finding_id,
            "summary": "reported",
            "severity": "high",
            "evidence_refs": [],
        },
        "finding",
        finding_id,
    )
    promoted = event(
        2,
        "finding.promoted",
        {
            "finding_id": finding_id,
            "expected_revision": reported["event_id"],
            "summary": "verified",
            "evidence_refs": [],
        },
        "finding",
        finding_id,
    )
    replacement = {
        **promoted["payload"],
        "evidence_refs": [
            {
                "ref": {"kind": "event", "id": "evt.00000000000000000000000999"},
                "revision": "evt.00000000000000000000000999",
            }
        ],
    }
    correction = event(
        3,
        "event.corrected",
        {
            "target_event_id": promoted["event_id"],
            "expected_target_sha256": canonical_sha256(promoted),
            "replacement_payload": replacement,
        },
        "event",
        promoted["event_id"],
    )

    snapshot = project_events([reported, promoted, correction])

    assert snapshot.findings[finding_id]["state"] == "reported"
    assert any("cannot change structural fields" in item for item in snapshot.warnings)


def test_event_correction_and_invalidation_stale_bound_truth_but_keep_history() -> None:
    artifact_id = "artifact.00000000000000000000000001"
    finding_id = "finding.00000000000000000000000001"
    decision_id = "decision.00000000000000000000000001"
    registered = event(
        1,
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "manifest_ref": "mft.artifact.sha256." + "1" * 64,
            "revision": "sha256:" + "1" * 64,
            "classification": "internal",
        },
        "artifact",
        artifact_id,
    )
    event_evidence = {
        "ref": {"kind": "event", "id": registered["event_id"]},
        "revision": registered["event_id"],
    }
    finding_reported = event(
        2,
        "finding.reported",
        {
            "finding_id": finding_id,
            "summary": "finding",
            "severity": "high",
            "evidence_refs": [event_evidence],
        },
        "finding",
        finding_id,
    )
    finding_promoted = event(
        3,
        "finding.promoted",
        {
            "finding_id": finding_id,
            "expected_revision": finding_reported["event_id"],
            "summary": "verified",
            "evidence_refs": [event_evidence],
        },
        "finding",
        finding_id,
    )
    decision_proposed = event(
        4,
        "decision.proposed",
        {
            "decision_id": decision_id,
            "scope": "event.evidence",
            "proposal": "trust recorded event",
            "alternatives": [],
        },
        "decision",
        decision_id,
    )
    decision_accepted = event(
        5,
        "decision.accepted",
        {
            "decision_id": decision_id,
            "expected_revision": decision_proposed["event_id"],
            "rationale": "event evidence",
            "evidence_refs": [event_evidence],
            "dissent": [],
        },
        "decision",
        decision_id,
    )
    history = [
        registered,
        finding_reported,
        finding_promoted,
        decision_proposed,
        decision_accepted,
    ]
    correction = event(
        6,
        "event.corrected",
        {
            "target_event_id": registered["event_id"],
            "expected_target_sha256": canonical_sha256(registered),
            "replacement_payload": {
                **registered["payload"],
                "extensions": {"note": "corrected"},
            },
        },
        "event",
        registered["event_id"],
    )
    corrected = project_events([*history, correction])
    assert corrected.findings[finding_id]["state"] == "verified"
    assert corrected.findings[finding_id]["stale"] is True
    assert corrected.decisions[decision_id]["state"] == "accepted"
    assert corrected.decisions[decision_id]["stale"] is True

    invalidation = event(
        7,
        "event.invalidated",
        {
            "target_ref": {"kind": "event", "id": registered["event_id"]},
            "reason": "invalid evidence",
        },
        "event",
        registered["event_id"],
    )
    invalidated = project_events([*history, invalidation])
    assert invalidated.findings[finding_id]["state"] == "verified"
    assert invalidated.findings[finding_id]["stale"] is True
    assert invalidated.decisions[decision_id]["state"] == "accepted"
    assert invalidated.decisions[decision_id]["stale"] is True
