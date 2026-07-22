from __future__ import annotations

import sqlite3
from multiprocessing import get_context
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_commons.services.manager as manager_module
import agent_commons.storage.idempotency as idempotency_module
from agent_commons.domain.projection import ProjectionIssue, ProjectSnapshot
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
)
from agent_commons.services import CommonsManager


def _open(
    repo: Path,
    state_root: Path,
    *,
    name: str,
    role: str,
    capabilities: tuple[str, ...] = (),
) -> tuple[CommonsManager, dict]:
    manager = CommonsManager(repo, state_root=state_root)
    session = manager.start_session(
        stable_instance_id=f"agent-window-{name}-12345678",
        principal=f"operator-{name}",
        client="codex" if name != "reviewer" else "claude-code",
        software="agent-cli",
        role=role,
        capabilities=capabilities,
    )
    manager.session_id = session["session_id"]
    return manager, session


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, CommonsManager, CommonsManager]:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="service-tests")
    builder, _ = _open(repo, state_root, name="builder", role="builder")
    reviewer, _ = _open(repo, state_root, name="reviewer", role="reviewer")
    return repo, state_root, builder, reviewer


def _transition_process(
    repo: str,
    state_root: str,
    session_id: str,
    task_id: str,
    revision: str,
    action: str,
    start: object,
    results: object,
) -> None:
    manager = CommonsManager(repo, state_root=state_root, session_id=session_id)
    start.wait(timeout=10)  # type: ignore[attr-defined]
    try:
        if action == "start":
            manager.start_task(task_id, revision, idempotency_key="race-start")
        else:
            manager.cancel_task(
                task_id,
                revision,
                reason="race cancellation",
                idempotency_key="race-cancel",
            )
        results.put(("ok", action))  # type: ignore[attr-defined]
    except Exception as exc:  # process boundary reports a stable summary
        results.put((type(exc).__name__, str(exc)))  # type: ignore[attr-defined]


def test_idempotency_repairs_missing_receipt_and_defers_optional_index_sync(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    first = manager.create_objective(
        title="Ship service",
        description="Deliver the manager boundary",
        acceptance_criteria=("tests pass",),
        idempotency_key="objective-service",
    )
    namespace = manager._namespace(manager._active_session())
    receipt = manager.events.idempotency.lookup(
        namespace=namespace,
        key="objective-service",
    )
    assert receipt is not None
    receipt.path.unlink()
    assert manager.doctor()["ok"] is False

    repeated = manager.create_objective(
        title="Ship service",
        description="Deliver the manager boundary",
        acceptance_criteria=("tests pass",),
        idempotency_key="objective-service",
    )

    assert repeated["event_id"] == first["event_id"]
    assert len(list(manager.events.iter_events())) == 1
    assert manager.events.idempotency.lookup(
        namespace=namespace,
        key="objective-service",
    )
    assert repeated["index"]["mode"] == "deferred"
    report = manager.doctor()
    assert report["ok"] is True
    assert report["performance"]["canonical_write_index_policy"] == "deferred"
    with sqlite3.connect(manager.paths.index_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_one_write_reuses_a_bounded_number_of_receipt_scope_git_probes(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manager, _ = workspace
    real_descriptor = idempotency_module.receipt_scope_descriptor
    refreshes = 0

    def counted_descriptor(repo_root: str | Path, workspace_id: str) -> dict[str, str]:
        nonlocal refreshes
        refreshes += 1
        return real_descriptor(repo_root, workspace_id)

    monkeypatch.setattr(idempotency_module, "receipt_scope_descriptor", counted_descriptor)
    manager.create_objective(
        title="Bound Git probes",
        description="Reuse one receipt scope within the full write transaction.",
        acceptance_criteria=("scope probe count stays bounded",),
        idempotency_key="bounded-scope-probes",
    )

    assert refreshes == 2
    report = manager.doctor()
    assert refreshes == 3
    assert report["performance"]["receipt_scope_refreshes"] == 3
    assert report["performance"]["receipt_scope_git_probes"] <= 9


def test_orphan_receipt_blocks_competing_write_and_identical_retry_repairs(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manager, _ = workspace
    real_reserve = manager.events.idempotency.reserve

    def reserve_then_crash(**kwargs: object) -> object:
        real_reserve(**kwargs)  # type: ignore[arg-type]
        raise RuntimeError("simulated crash after durable receipt reservation")

    monkeypatch.setattr(manager.events.idempotency, "reserve", reserve_then_crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        manager.create_objective(
            title="Crash-safe objective",
            description="must repair from the same retry identity",
            acceptance_criteria=("one event",),
            idempotency_key="orphan-retry",
        )
    monkeypatch.setattr(manager.events.idempotency, "reserve", real_reserve)

    assert list(manager.events.iter_events()) == []
    report = manager.doctor()
    assert report["ok"] is False
    assert "orphan idempotency receipt" in report["issues"][0]
    with pytest.raises(IntegrityError, match="orphan idempotency receipt"):
        manager.create_objective(
            title="Competing objective",
            description="must wait for repair",
            acceptance_criteria=("blocked",),
            idempotency_key="competing-write",
        )

    repaired = manager.create_objective(
        title="Crash-safe objective",
        description="must repair from the same retry identity",
        acceptance_criteria=("one event",),
        idempotency_key="orphan-retry",
    )

    assert repaired["created"] is True
    assert len(list(manager.events.iter_events())) == 1
    assert manager.doctor()["ok"] is True


def test_orphan_receipt_can_be_audited_and_permanently_abandoned(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, state_root, manager, reviewer = workspace
    maintainer, _ = _open(
        repo,
        state_root,
        name="receipt-maintainer",
        role="maintainer",
        capabilities=("receipt:abandon",),
    )
    session = manager._active_session()
    namespace = manager._namespace(session)
    reservation = manager.events.idempotency.reserve(
        namespace=namespace,
        key="lost-original-operation",
        semantic_sha256="a" * 64,
    )
    assert manager.doctor()["ok"] is False

    with pytest.raises(LifecycleConflictError, match="receipt:abandon"):
        reviewer.abandon_idempotency_receipt(
            reservation.key_digest,
            reason="an ordinary writer cannot abandon a receipt",
        )
    abandonment = maintainer.abandon_idempotency_receipt(
        reservation.key_digest,
        reason="the original session and request payload are unavailable",
    )

    assert abandonment["event_id"] == reservation.event_id
    assert not reservation.path.exists()
    assert manager.doctor()["ok"] is True
    repeated = maintainer.abandon_idempotency_receipt(
        reservation.key_digest,
        reason="idempotent recovery retry",
    )
    assert repeated == abandonment
    with pytest.raises(IdempotencyConflictError, match="explicitly abandoned"):
        manager.events.idempotency.reserve(
            namespace=namespace,
            key="lost-original-operation",
            semantic_sha256="a" * 64,
        )

    created = maintainer.create_objective(
        title="Workspace recovered",
        description="a new idempotency identity remains usable",
        acceptance_criteria=("doctor passes",),
        idempotency_key="new-operation-after-abandonment",
    )
    with pytest.raises(LifecycleConflictError, match="canonical event"):
        maintainer.abandon_idempotency_receipt(
            maintainer.events.idempotency.key_digest(
                maintainer._namespace(maintainer._active_session()),
                "new-operation-after-abandonment",
            ),
            reason="must not abandon a receipt backed by an event",
        )
    assert created["event_type"] == "objective.created"
    assert maintainer.doctor()["ok"] is True


def test_task_acceptance_requires_current_independent_review(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, builder, reviewer = workspace
    created = builder.create_task(
        title="Implement",
        description="Implement and verify",
        acceptance_criteria=("reviewed",),
        idempotency_key="task-create",
    )
    task_id = created["entity_ref"]["id"]
    started = builder.start_task(task_id, created["revision"], idempotency_key="task-start")
    completed = builder.complete_task(
        task_id,
        started["revision"],
        summary="implemented",
        idempotency_key="task-complete",
    )
    submitted = builder.submit_task(
        task_id,
        completed["revision"],
        summary="ready",
        idempotency_key="task-submit",
    )
    with pytest.raises(LifecycleConflictError, match="independent review"):
        builder.accept_task(
            task_id,
            submitted["revision"],
            summary="premature",
            idempotency_key="task-accept-premature",
        )

    requested = builder.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="review-request",
    )
    valid_review = reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="approved",
        idempotency_key="review-complete",
    )
    self_review_request = reviewer.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="self-review-request",
    )
    with pytest.raises(LifecycleConflictError, match="work-author session"):
        builder.complete_review(
            self_review_request["entity_ref"]["id"],
            self_review_request["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="self-approved",
            idempotency_key="self-review-complete",
        )
    accepted = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="accepted",
        idempotency_key="task-accept",
    )
    assert accepted["event_type"] == "task.accepted"
    accepted_event = reviewer.show_event(accepted["event_id"])["event"]
    assert accepted_event["payload"]["acceptance_review"] == {
        "ref": {"kind": "review", "id": requested["entity_ref"]["id"]},
        "revision": valid_review["event_id"],
    }
    assert accepted_event["relations"] == [
        {
            "predicate": "depends_on",
            "subject": {"kind": "task", "id": task_id},
            "object": {"kind": "review", "id": requested["entity_ref"]["id"]},
        }
    ]

    retried = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="accepted",
        idempotency_key="task-accept",
    )
    assert retried["event_id"] == accepted["event_id"]
    assert retried["created"] is False

    canonical_acceptance = reviewer.events.get(accepted["event_id"])
    canonical_bytes = canonical_acceptance.path.read_bytes()
    canonical_acceptance.path.unlink()
    with pytest.raises(IntegrityError, match="anchored canonical event is missing"):
        reviewer.accept_task(
            task_id,
            submitted["revision"],
            summary="accepted",
            idempotency_key="task-accept",
        )
    canonical_acceptance.path.write_bytes(canonical_bytes)
    assert reviewer.doctor()["ok"] is True

    namespace = reviewer._namespace(reviewer._active_session())
    receipt = reviewer.events.idempotency.lookup(
        namespace=namespace,
        key="task-accept",
    )
    assert receipt is not None
    receipt.path.unlink()
    repaired_receipt = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="accepted",
        idempotency_key="task-accept",
    )
    assert repaired_receipt["event_id"] == accepted["event_id"]
    assert reviewer.doctor()["ok"] is True

    stored_review = reviewer.show_event(valid_review["event_id"])
    corrected_payload = dict(stored_review["event"]["payload"])
    corrected_payload["summary"] = "approved with clarified wording"
    correction = reviewer.correct_event(
        valid_review["event_id"],
        expected_target_sha256=stored_review["canonical_sha256"],
        replacement_payload=corrected_payload,
        idempotency_key="review-summary-correction",
    )
    corrected_snapshot = reviewer.snapshot()
    assert corrected_snapshot.tasks[task_id]["state"] == "review"
    assert ("event", accepted["event_id"]) in corrected_snapshot.stale_refs
    assert reviewer.doctor()["ok"] is True

    reaccepted = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="accepted after review correction",
        idempotency_key="task-reaccept-after-review-correction",
    )
    reaccepted_event = reviewer.show_event(reaccepted["event_id"])["event"]
    assert reaccepted_event["payload"]["acceptance_review"]["revision"] == correction["event_id"]
    assert reviewer.snapshot().tasks[task_id]["state"] == "accepted"

    reviewer.invalidate_event(
        valid_review["event_id"],
        reason="review completion is invalid",
        idempotency_key="invalidate-acceptance-review",
    )
    invalidated_snapshot = reviewer.snapshot()
    assert invalidated_snapshot.tasks[task_id]["state"] == "review"
    assert ("event", reaccepted["event_id"]) in invalidated_snapshot.stale_refs
    assert reviewer.doctor()["ok"] is True


def test_task_artifacts_are_revision_bound_and_revision_stales_acceptance(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, _, builder, reviewer = workspace
    source = repo / "task-result.txt"
    source.write_text("first result", encoding="utf-8")
    artifact = builder.register_artifact(
        source,
        media_type="text/plain",
        idempotency_key="task-bound-artifact",
    )
    artifact_ref = artifact["entity_ref"]
    expected_binding = {"ref": artifact_ref, "revision": artifact["revision"]}
    created = builder.create_task(
        title="Ship bound artifact",
        description="The accepted task depends on exact artifact bytes.",
        acceptance_criteria=("artifact remains current",),
        idempotency_key="bound-artifact-task",
    )
    started = builder.start_task(
        created["entity_ref"]["id"],
        created["revision"],
        idempotency_key="bound-artifact-task-start",
    )
    completed = builder.complete_task(
        created["entity_ref"]["id"],
        started["revision"],
        summary="result recorded",
        artifact_refs=(artifact_ref,),
        idempotency_key="bound-artifact-task-complete",
    )
    submitted = builder.submit_task(
        created["entity_ref"]["id"],
        completed["revision"],
        summary="ready for independent review",
        artifact_refs=(artifact_ref,),
        idempotency_key="bound-artifact-task-submit",
    )
    for result in (completed, submitted):
        payload = builder.show_event(result["event_id"])["event"]["payload"]
        assert payload["artifact_refs"] == [artifact_ref]
        assert payload["artifact_bindings"] == [expected_binding]

    requested = builder.request_review(
        target_ref=created["entity_ref"],
        target_revision=submitted["revision"],
        criteria=("artifact is current",),
        idempotency_key="bound-artifact-review",
    )
    reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="exact artifact approved",
        idempotency_key="bound-artifact-review-complete",
    )
    accepted = reviewer.accept_task(
        created["entity_ref"]["id"],
        submitted["revision"],
        summary="accepted with exact artifact",
        idempotency_key="bound-artifact-task-accept",
    )
    assert reviewer.snapshot().tasks[created["entity_ref"]["id"]]["state"] == "accepted"

    source.write_text("second result", encoding="utf-8")
    builder.revise_artifact(
        artifact_ref["id"],
        artifact["revision"],
        source,
        media_type="text/plain",
        idempotency_key="task-bound-artifact-revise",
    )

    snapshot = reviewer.snapshot()
    assert snapshot.tasks[created["entity_ref"]["id"]]["state"] == "review"
    assert snapshot.tasks[created["entity_ref"]["id"]]["artifact_stale"] is True
    assert snapshot.reviews[requested["entity_ref"]["id"]]["stale"] is True
    assert ("event", accepted["event_id"]) in snapshot.stale_refs


def test_task_author_cannot_review_after_another_session_submits(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, state_root, author, submitter = workspace
    independent, _ = _open(repo, state_root, name="independent", role="reviewer")
    created = author.create_task(
        title="Implement handoff",
        description="Author completes work before another session submits it",
        acceptance_criteria=("independent review",),
        idempotency_key="handoff-task-create",
    )
    task_id = created["entity_ref"]["id"]
    started = author.start_task(
        task_id,
        created["revision"],
        idempotency_key="handoff-task-start",
    )
    completed = author.complete_task(
        task_id,
        started["revision"],
        summary="authored work",
        idempotency_key="handoff-task-complete",
    )
    submitted = submitter.submit_task(
        task_id,
        completed["revision"],
        summary="submitted after handoff",
        idempotency_key="handoff-task-submit",
    )
    task = next(item for item in submitter.list_tasks() if item["id"] == task_id)
    assert task["work_author_session_ids"] == [author.session_id]

    requested = submitter.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("correctness",),
        idempotency_key="handoff-review-request",
    )
    with pytest.raises(LifecycleConflictError, match="work-author session"):
        author.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="self approval after handoff",
            idempotency_key="handoff-author-review",
        )

    approved = independent.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="independent approval",
        idempotency_key="handoff-independent-review",
    )
    accepted = submitter.accept_task(
        task_id,
        submitted["revision"],
        summary="accepted after independent approval",
        idempotency_key="handoff-task-accept",
    )
    accepted_event = submitter.show_event(accepted["event_id"])["event"]
    assert accepted_event["payload"]["acceptance_review"]["revision"] == approved["revision"]


def test_handoff_acknowledgement_is_recipient_only(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, state_root, builder, reviewer = workspace
    outsider, _ = _open(repo, state_root, name="outsider", role="observer")
    handoff = builder.create_handoff(
        to=("reviewer",),
        next_actions=("review the work",),
        idempotency_key="handoff-create",
    )
    handoff_id = handoff["entity_ref"]["id"]

    with pytest.raises(LifecycleConflictError, match="recipient"):
        outsider.acknowledge_handoff(
            handoff_id,
            handoff["revision"],
            note="not mine",
            idempotency_key="handoff-wrong-recipient",
        )
    acknowledged = reviewer.acknowledge_handoff(
        handoff_id,
        handoff["revision"],
        note="received",
        idempotency_key="handoff-ack",
    )
    assert acknowledged["event_type"] == "handoff.acknowledged"


def test_correction_cannot_rewrite_handoff_recipients(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, builder, _ = workspace
    handoff = builder.create_handoff(
        to=("reviewer",),
        next_actions=("review the work",),
        idempotency_key="handoff-immutable-recipient",
    )
    stored = builder.show_event(handoff["event_id"])
    replacement = dict(stored["event"]["payload"])
    replacement["to"] = ["observer"]

    with pytest.raises(LifecycleConflictError, match="reference or causal fields: to"):
        builder.correct_event(
            handoff["event_id"],
            expected_target_sha256=stored["canonical_sha256"],
            replacement_payload=replacement,
            idempotency_key="handoff-recipient-rewrite",
        )

    assert builder.list_handoffs()[0]["to"] == ["reviewer"]


def test_artifact_is_metadata_only_and_revision_stales_review(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, _, builder, reviewer = workspace
    source = repo / "result.txt"
    source.write_text("first", encoding="utf-8")
    registered = builder.register_artifact(
        source,
        media_type="text/plain",
        metadata={"purpose": "test"},
        idempotency_key="artifact-register",
    )
    artifact_id = registered["entity_ref"]["id"]
    manifest = builder.manifests.get(registered["manifest_id"]).manifest
    assert manifest["captured"] is False
    assert manifest["source"] == {"path": "result.txt"}
    assert list(builder.paths.blobs.iterdir()) == []

    request = builder.request_review(
        target_ref={"kind": "artifact", "id": artifact_id},
        target_revision=registered["revision"],
        criteria=("content",),
        idempotency_key="artifact-review-request",
    )
    reviewer.complete_review(
        request["entity_ref"]["id"],
        request["revision"],
        target_revision=registered["revision"],
        verdict="approved",
        summary="good",
        idempotency_key="artifact-review-complete",
    )
    source.write_text("second", encoding="utf-8")
    builder.revise_artifact(
        artifact_id,
        registered["revision"],
        source,
        media_type="text/plain",
        idempotency_key="artifact-revise",
    )
    assert builder.list_reviews()[0]["stale"] is True


def test_missing_artifact_manifest_fails_closed_and_stales_bound_evidence(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, _, builder, reviewer = workspace
    source = repo / "manifest-evidence.txt"
    source.write_text("evidence", encoding="utf-8")
    registered = builder.register_artifact(
        source,
        media_type="text/plain",
        idempotency_key="manifest-integrity-artifact",
    )
    artifact_id = registered["entity_ref"]["id"]
    manifest_ref = registered["manifest_id"]
    requested = builder.request_review(
        target_ref={"kind": "artifact", "id": artifact_id},
        target_revision=registered["revision"],
        criteria=("manifest integrity",),
        idempotency_key="manifest-integrity-review",
    )
    reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=registered["revision"],
        verdict="approved",
        summary="manifest is present",
        evidence_refs=({"kind": "manifest", "id": manifest_ref},),
        idempotency_key="manifest-integrity-review-complete",
    )
    assert reviewer.list_reviews()[0]["stale"] is False

    reviewer.manifests.get(manifest_ref).path.unlink()

    assert reviewer.list_reviews()[0]["stale"] is True
    report = reviewer.doctor()
    assert report["ok"] is False
    assert any("references missing manifest" in issue for issue in report["issues"])
    with pytest.raises(IntegrityError, match="references missing manifest"):
        reviewer.create_objective(
            title="Blocked while evidence is missing",
            description="canonical writes must fail closed",
            acceptance_criteria=("restore manifest",),
            idempotency_key="write-with-missing-manifest",
        )


def test_orphan_manifest_is_reported_without_blocking_safe_retry(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, manager, _ = workspace
    source = repo / "pending.txt"
    source.write_text("pending artifact", encoding="utf-8")
    real_append = manager.events.append_event

    def crash_before_event(**kwargs: object) -> object:
        raise RuntimeError("simulated crash after manifest publication")

    monkeypatch.setattr(manager.events, "append_event", crash_before_event)
    with pytest.raises(RuntimeError, match="simulated crash"):
        manager.register_artifact(
            source,
            media_type="text/plain",
            idempotency_key="orphan-manifest-retry",
        )
    monkeypatch.setattr(manager.events, "append_event", real_append)

    report = manager.doctor()

    assert report["ok"] is True
    assert any("orphan manifest" in warning for warning in report["warnings"])

    repaired = manager.register_artifact(
        source,
        media_type="text/plain",
        idempotency_key="orphan-manifest-retry",
    )
    assert repaired["event_type"] == "artifact.registered"
    assert not any("orphan manifest" in warning for warning in manager.doctor()["warnings"])


def test_manager_binds_evidence_revisions_and_stales_effective_truth(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, _, builder, reviewer = workspace
    source = repo / "evidence.txt"
    source.write_text("first", encoding="utf-8")
    registered = builder.register_artifact(
        source,
        media_type="text/plain",
        idempotency_key="evidence-artifact",
    )
    artifact_id = registered["entity_ref"]["id"]
    artifact_ref = {"kind": "artifact", "id": artifact_id}
    expected_bound = {"ref": artifact_ref, "revision": registered["revision"]}
    immutable_evidence = reviewer.report_finding(
        summary="immutable references are stable",
        severity="info",
        evidence_refs=(
            {"kind": "event", "id": registered["event_id"]},
            {"kind": "manifest", "id": registered["manifest_id"]},
        ),
        idempotency_key="immutable-evidence-semantics",
    )
    immutable_payload = builder.show_event(immutable_evidence["event_id"])["event"]["payload"]
    assert immutable_payload["evidence_refs"] == [
        {
            "ref": {"kind": "event", "id": registered["event_id"]},
            "revision": registered["event_id"],
        },
        {
            "ref": {"kind": "manifest", "id": registered["manifest_id"]},
            "revision": registered["manifest_id"],
        },
    ]

    requested = builder.request_review(
        target_ref=artifact_ref,
        target_revision=registered["revision"],
        criteria=("correctness",),
        independent=True,
        idempotency_key="evidence-review-request",
    )
    completed = reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=registered["revision"],
        verdict="approved",
        summary="approved",
        evidence_refs=(artifact_ref,),
        idempotency_key="evidence-review-complete",
    )
    verification = reviewer.record_verification(
        target_ref=artifact_ref,
        target_revision=registered["revision"],
        claim="content matches",
        evidence_refs=(artifact_ref,),
        method="sha256",
        outcome="pass",
        idempotency_key="evidence-verification",
    )
    reported = reviewer.report_finding(
        summary="content is stable",
        severity="info",
        evidence_refs=(artifact_ref,),
        idempotency_key="evidence-finding-report",
    )
    promoted = reviewer.promote_finding(
        reported["entity_ref"]["id"],
        reported["revision"],
        summary="independently verified",
        evidence_refs=(artifact_ref,),
        idempotency_key="evidence-finding-promote",
    )
    proposed = builder.propose_decision(
        scope="evidence.binding",
        proposal="retain the artifact",
        idempotency_key="evidence-decision-propose",
    )
    accepted = builder.accept_decision(
        proposed["entity_ref"]["id"],
        proposed["revision"],
        rationale="verified artifact",
        evidence_refs=(artifact_ref,),
        idempotency_key="evidence-decision-accept",
    )

    for result in (completed, verification, promoted, accepted):
        stored = builder.show_event(result["event_id"])["event"]
        assert stored["payload"]["evidence_refs"] == [expected_bound]

    source.write_text("second", encoding="utf-8")
    builder.revise_artifact(
        artifact_id,
        registered["revision"],
        source,
        media_type="text/plain",
        idempotency_key="evidence-artifact-revise",
    )
    snapshot = builder.snapshot()
    assert snapshot.reviews[requested["entity_ref"]["id"]]["stale"] is True
    assert snapshot.verifications[verification["entity_ref"]["id"]]["stale"] is True
    assert snapshot.findings[promoted["entity_ref"]["id"]]["state"] == "verified"
    assert snapshot.findings[promoted["entity_ref"]["id"]]["stale"] is True
    assert snapshot.decisions[accepted["entity_ref"]["id"]]["state"] == "accepted"
    assert snapshot.decisions[accepted["entity_ref"]["id"]]["stale"] is True
    effective = builder.orient()["effective_truth"]
    assert effective == {"decisions": [], "findings": []}


def test_artifact_idempotency_conflict_does_not_publish_orphan_manifest(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, _, manager, _ = workspace
    source = repo / "artifact.bin"
    source.write_bytes(b"first")
    manager.register_artifact(source, idempotency_key="same-artifact")
    source.write_bytes(b"different")
    with pytest.raises(IdempotencyConflictError):
        manager.register_artifact(source, idempotency_key="same-artifact")
    assert len(list(manager.events.iter_events())) == 1
    assert len(list(manager.manifests.iter_manifests())) == 1


def test_artifact_path_replacement_is_detected_before_canonical_write(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, manager, _ = workspace
    source = repo / "unstable.bin"
    source.write_bytes(b"stable bytes")
    real_stat = manager_module.os.stat

    def changed_stat(path: object, *args: object, **kwargs: object) -> object:
        value = real_stat(path, *args, **kwargs)
        if kwargs.get("follow_symlinks") is False:
            return SimpleNamespace(
                st_dev=value.st_dev,
                st_ino=value.st_ino + 1,
                st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
                st_mode=value.st_mode,
            )
        return value

    monkeypatch.setattr(manager_module.os, "stat", changed_stat)
    with pytest.raises(IntegrityError, match="path changed"):
        manager.register_artifact(source, idempotency_key="unstable-artifact")
    assert list(manager.events.iter_events()) == []
    assert list(manager.manifests.iter_manifests()) == []


def test_maintenance_validates_replacement_and_supports_revoke(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    created = manager.create_objective(
        title="Typo",
        description="before",
        acceptance_criteria=("correct",),
        idempotency_key="maintenance-root",
    )
    shown = manager.show_event(created["event_id"])
    invalid = dict(shown["event"]["payload"])
    invalid["objective_id"] = "objective.00000000000000000000000000"
    with pytest.raises(LifecycleConflictError, match="subject identity"):
        manager.correct_event(
            created["event_id"],
            expected_target_sha256=shown["canonical_sha256"],
            replacement_payload=invalid,
            idempotency_key="invalid-correction",
        )
    assert len(list(manager.events.iter_events())) == 1

    replacement = dict(shown["event"]["payload"])
    replacement["title"] = "Corrected"
    correction = manager.correct_event(
        created["event_id"],
        expected_target_sha256=shown["canonical_sha256"],
        replacement_payload=replacement,
        idempotency_key="valid-correction",
    )
    assert manager.list_objectives()[0]["title"] == "Corrected"
    assert manager.list_objectives()[0]["revision"] == created["revision"]
    assert manager.list_objectives()[0]["effective_revision"] == correction["revision"]
    with pytest.raises(LifecycleConflictError, match="current effective"):
        manager.request_review(
            target_ref={"kind": "objective", "id": created["entity_ref"]["id"]},
            target_revision=created["revision"],
            criteria=("correctness",),
            idempotency_key="review-old-corrected-revision",
        )
    manager.request_review(
        target_ref={"kind": "objective", "id": created["entity_ref"]["id"]},
        target_revision=correction["revision"],
        criteria=("correctness",),
        idempotency_key="review-current-corrected-revision",
    )
    invalidation = manager.invalidate_event(
        created["event_id"],
        reason="temporarily wrong",
        idempotency_key="invalidate",
    )
    assert manager.list_objectives() == []
    manager.revoke_invalidation(
        invalidation["event_id"],
        reason="restore corrected record",
        idempotency_key="revoke",
    )
    assert manager.list_objectives()[0]["title"] == "Corrected"


def test_correction_can_merge_all_active_heads_after_branch_reconciliation(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    created = manager.create_objective(
        title="Original",
        description="before branch reconciliation",
        acceptance_criteria=("one effective revision",),
        idempotency_key="correction-root",
    )
    shown = manager.show_event(created["event_id"])
    root_payload = dict(shown["event"]["payload"])

    first_payload = {**root_payload, "title": "First branch"}
    first = manager.correct_event(
        created["event_id"],
        expected_target_sha256=shown["canonical_sha256"],
        replacement_payload=first_payload,
        idempotency_key="first-branch-correction",
    )

    # Model a second valid correction committed independently in another Git
    # branch, then merged at the filesystem layer. The low-level append is
    # intentional: neither branch could have observed the other's head.
    second_payload = {**root_payload, "title": "Second branch"}
    second = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="event.corrected",
        payload_schema="commons.payload.maintenance.v1",
        payload={
            "target_event_id": created["event_id"],
            "expected_target_sha256": shown["canonical_sha256"],
            "replacement_payload": second_payload,
        },
        actor=manager._actor(),
        subject_refs=({"kind": "event", "id": created["event_id"]},),
        idempotency_namespace="merge-simulation",
        idempotency_key="second-branch-correction",
        provenance={
            "writer": "merge-test",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("maintenance", "correction"),
    )
    assert manager.doctor()["ok"] is False

    resolved_payload = {**root_payload, "title": "Reconciled"}
    resolution = manager.correct_event(
        created["event_id"],
        expected_target_sha256=shown["canonical_sha256"],
        replacement_payload=resolved_payload,
        superseded_correction_event_ids=(first["event_id"], second.event_id),
        idempotency_key="resolve-branch-corrections",
    )

    assert resolution["event_type"] == "event.corrected"
    assert manager.list_objectives()[0]["title"] == "Reconciled"
    assert manager.doctor()["ok"] is True


def test_invalidation_can_recover_merged_accepted_decision_conflict(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    unrelated = manager.create_objective(
        title="Unrelated work",
        description="must not be an integrity bypass",
        acceptance_criteria=("preserved",),
        idempotency_key="unrelated-objective",
    )
    first = manager.propose_decision(
        scope="architecture.database",
        proposal="PostgreSQL",
        idempotency_key="decision-first-proposed",
    )
    second = manager.propose_decision(
        scope="architecture.database",
        proposal="SQLite",
        idempotency_key="decision-second-proposed",
    )
    third = manager.propose_decision(
        scope="architecture.database",
        proposal="MySQL",
        idempotency_key="decision-third-proposed",
    )
    first_acceptance = manager.accept_decision(
        first["entity_ref"]["id"],
        first["revision"],
        rationale="selected on the first branch",
        idempotency_key="decision-first-accepted",
    )

    # Simulate a valid acceptance made independently on another Git branch.
    second_acceptance = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="decision.accepted",
        payload_schema="commons.payload.decision.v1",
        payload={
            "decision_id": second["entity_ref"]["id"],
            "expected_revision": second["revision"],
            "rationale": "selected on the second branch",
            "evidence_refs": [],
            "dissent": [],
        },
        actor=manager._actor(),
        subject_refs=(second["entity_ref"],),
        idempotency_namespace="merge-simulation",
        idempotency_key="decision-second-accepted",
        provenance={
            "writer": "merge-test",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("decision", "truth"),
    )
    third_acceptance = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="decision.accepted",
        payload_schema="commons.payload.decision.v1",
        payload={
            "decision_id": third["entity_ref"]["id"],
            "expected_revision": third["revision"],
            "rationale": "selected on a third branch",
            "evidence_refs": [],
            "dissent": [],
        },
        actor=manager._actor(),
        subject_refs=(third["entity_ref"],),
        idempotency_namespace="merge-simulation",
        idempotency_key="decision-third-accepted",
        provenance={
            "writer": "merge-test",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("decision", "truth"),
    )
    conflicted = {item["id"]: item["state"] for item in manager.list_decisions()}
    assert conflicted == {
        first["entity_ref"]["id"]: "conflicted",
        second["entity_ref"]["id"]: "conflicted",
        third["entity_ref"]["id"]: "conflicted",
    }
    assert manager.doctor()["ok"] is False
    with pytest.raises(IntegrityError, match="conflicting accepted decisions"):
        manager.invalidate_event(
            unrelated["event_id"],
            reason="does not resolve the decision conflict",
            idempotency_key="invalid-unrelated-recovery",
        )

    partial = manager.invalidate_event(
        third_acceptance.event_id,
        reason="reduce a three-way merge conflict before resolving the final pair",
        idempotency_key="invalidate-third-acceptance",
    )
    assert partial["event_type"] == "event.invalidated"
    partially_resolved = {item["id"]: item["state"] for item in manager.list_decisions()}
    assert partially_resolved == {
        first["entity_ref"]["id"]: "conflicted",
        second["entity_ref"]["id"]: "conflicted",
        third["entity_ref"]["id"]: "proposed",
    }
    assert manager.doctor()["ok"] is False

    invalidation = manager.invalidate_event(
        second_acceptance.event_id,
        reason="resolve independently accepted alternatives after branch merge",
        idempotency_key="invalidate-second-acceptance",
    )

    assert invalidation["event_type"] == "event.invalidated"
    resolved = {item["id"]: item["state"] for item in manager.list_decisions()}
    assert resolved == {
        first["entity_ref"]["id"]: "accepted",
        second["entity_ref"]["id"]: "proposed",
        third["entity_ref"]["id"]: "proposed",
    }
    assert first_acceptance["event_id"] not in manager.snapshot().invalid_event_ids
    assert second_acceptance.event_id in manager.snapshot().invalid_event_ids
    assert third_acceptance.event_id in manager.snapshot().invalid_event_ids
    assert manager.doctor()["ok"] is True


@pytest.mark.parametrize(
    ("code", "message"),
    (
        ("decision_scope_conflict", "conflicting accepted decisions for scope api"),
        ("lifecycle_rejected", "event rejected by lifecycle: stale revision"),
        ("correction_identity_change", "event correction cannot change task_id"),
    ),
)
def test_doctor_and_write_guard_fail_on_structured_projection_issues(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    message: str,
) -> None:
    _, _, manager, _ = workspace
    snapshot = ProjectSnapshot(
        workspace_id=manager.workspace_id,
        warnings=[message],
        issues=[ProjectionIssue(code, "error", message)],
    )
    monkeypatch.setattr(manager, "_records_and_snapshot", lambda: ([], snapshot))
    assert manager.doctor()["ok"] is False
    with pytest.raises(IntegrityError, match="rejected|conflict|cannot change"):
        manager._guard_integrity()


def test_warning_wording_does_not_control_integrity_status(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manager, _ = workspace
    snapshot = ProjectSnapshot(
        workspace_id=manager.workspace_id,
        warnings=["informational conflict wording is not a projection error"],
    )
    monkeypatch.setattr(manager, "_records_and_snapshot", lambda: ([], snapshot))

    assert manager.doctor()["ok"] is True
    assert manager._guard_integrity() is snapshot


def test_identity_changing_imported_correction_blocks_writes_until_superseded(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    created = manager.create_objective(
        title="Immutable identity",
        description="Imported corrections must preserve the subject.",
        acceptance_criteria=("doctor fails closed",),
        idempotency_key="identity-correction-root",
    )
    shown = manager.show_event(created["event_id"])
    invalid_payload = {
        **shown["event"]["payload"],
        "objective_id": "objective.00000000000000000000000000",
    }
    bad = manager.events.append_event(
        workspace_id=manager.workspace_id,
        event_type="event.corrected",
        payload_schema="commons.payload.maintenance.v1",
        payload={
            "target_event_id": created["event_id"],
            "expected_target_sha256": shown["canonical_sha256"],
            "replacement_payload": invalid_payload,
        },
        actor=manager._actor(),
        subject_refs=({"kind": "event", "id": created["event_id"]},),
        idempotency_namespace="imported-history",
        idempotency_key="identity-changing-correction",
        provenance={
            "writer": "merge-test",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        tags=("maintenance", "correction"),
    )

    report = manager.doctor()
    assert report["ok"] is False
    assert any("cannot change objective_id" in issue for issue in report["issues"])
    with pytest.raises(IntegrityError, match="cannot change objective_id"):
        manager.create_objective(
            title="Blocked unrelated write",
            description="The invalid projection must be repaired first.",
            acceptance_criteria=("blocked",),
            idempotency_key="blocked-by-identity-correction",
        )

    replacement = {**shown["event"]["payload"], "title": "Repaired identity"}
    manager.correct_event(
        created["event_id"],
        expected_target_sha256=shown["canonical_sha256"],
        replacement_payload=replacement,
        superseded_correction_event_ids=(bad.event_id,),
        idempotency_key="supersede-identity-changing-correction",
    )

    assert manager.list_objectives()[0]["title"] == "Repaired identity"
    assert manager.doctor()["ok"] is True


def test_security_rejection_leaves_no_event_or_receipt(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    secret = "sk-proj-" + "Z" * 24
    with pytest.raises(SecurityPolicyError):
        manager.create_objective(
            title=secret,
            description="must reject",
            acceptance_criteria=("safe",),
            idempotency_key="secret-objective",
        )
    assert list(manager.events.iter_events()) == []
    assert list(manager.paths.idempotency.rglob("*.json")) == []


def test_conflicting_transitions_have_one_winner_across_processes(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, state_root, manager, _ = workspace
    created = manager.create_task(
        title="Race",
        description="Exactly one transition wins",
        acceptance_criteria=("one winner",),
        idempotency_key="race-task",
    )
    task_id = created["entity_ref"]["id"]
    context = get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_transition_process,
            args=(
                str(repo),
                str(state_root),
                str(manager.session_id),
                task_id,
                created["revision"],
                action,
                start,
                results,
            ),
        )
        for action in ("start", "cancel")
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sum(outcome[0] == "ok" for outcome in outcomes) == 1
    assert sum(outcome[0] == "LifecycleConflictError" for outcome in outcomes) == 1
    assert len(list(manager.events.iter_events())) == 2
    assert manager.list_tasks()[0]["state"] in {"active", "cancelled"}
