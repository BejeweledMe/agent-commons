from __future__ import annotations

import sqlite3
from multiprocessing import get_context
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_commons.services.manager as manager_module
import agent_commons.storage.idempotency as idempotency_module
from agent_commons.core.canonical import canonical_json_bytes
from agent_commons.domain.envelopes import parse_event_envelope, serialize_event_envelope
from agent_commons.domain.projection import ProjectionIssue, ProjectSnapshot
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.services import CommonsManager
from agent_commons.views import inbox_view


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


def test_typed_delegation_and_maintenance_envelopes_round_trip_manager_events(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """The post-schema manager path preserves the canonical payload bytes."""

    _, _, manager, _ = workspace
    task = manager.create_task(
        title="Type the replay boundary",
        description="Exercise the existing immutable event path.",
        acceptance_criteria=("Payload bytes remain canonical.",),
        idempotency_key="typed-envelope-task",
    )
    stored_task = manager.show_event(task["event_id"])
    delegation = manager.create_delegation(
        target_ref={"kind": "task", "id": stored_task["event"]["payload"]["task_id"]},
        target_revision=task["event_id"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 900,
            "max_attempts": 2,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 10_000},
        },
        idempotency_key="typed-envelope-delegation",
    )
    objective = manager.create_objective(
        title="Maintain the typed replay boundary",
        description="Exercise the independent maintenance path.",
        acceptance_criteria=("Maintenance payload bytes remain canonical.",),
        idempotency_key="typed-envelope-objective",
    )
    stored_objective = manager.show_event(objective["event_id"])
    correction = manager.correct_event(
        objective["event_id"],
        expected_target_sha256=stored_objective["canonical_sha256"],
        replacement_payload={
            **stored_objective["event"]["payload"],
            "description": "Exercise the independent immutable correction path.",
        },
        idempotency_key="typed-envelope-correction",
    )
    invalidation = manager.invalidate_event(
        objective["event_id"],
        reason="Exercise the typed maintenance envelope.",
        idempotency_key="typed-envelope-invalidation",
    )
    revocation = manager.revoke_invalidation(
        invalidation["event_id"],
        reason="Keep the characterization fixture healthy.",
        idempotency_key="typed-envelope-revocation",
    )

    for result in (delegation, correction, invalidation, revocation):
        stored = manager.show_event(result["event_id"])["event"]
        payload = stored["payload"]
        envelope = parse_event_envelope(str(stored["event_type"]), payload)

        assert envelope is not None
        assert canonical_json_bytes(serialize_event_envelope(envelope)) == canonical_json_bytes(
            payload
        )


def test_typed_task_and_review_envelopes_round_trip_manager_events(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """Task and review lifecycle records keep their canonical payload bytes."""

    _, _, builder, reviewer = workspace
    created = builder.create_task(
        title="Type the task lifecycle",
        description="Exercise each existing task transition.",
        acceptance_criteria=("The stored payload round-trips exactly.",),
        idempotency_key="typed-task-created",
    )
    task_id = created["entity_ref"]["id"]
    revised = builder.revise_task(
        task_id,
        created["revision"],
        changes={"description": "Exercise each existing typed task transition."},
        idempotency_key="typed-task-revised",
    )
    taken = builder.take_task(task_id, revised["revision"], idempotency_key="typed-task-taken")
    started = builder.start_task(task_id, taken["revision"], idempotency_key="typed-task-started")
    blocked = builder.block_task(
        task_id,
        started["revision"],
        reason="Exercise the blocked envelope.",
        idempotency_key="typed-task-blocked",
    )
    unblocked = builder.unblock_task(
        task_id,
        blocked["revision"],
        resolution="Continue the characterization path.",
        idempotency_key="typed-task-unblocked",
    )
    completed = builder.complete_task(
        task_id,
        unblocked["revision"],
        summary="The typed task payload is complete.",
        idempotency_key="typed-task-completed",
    )
    submitted = builder.submit_task(
        task_id,
        completed["revision"],
        summary="The typed task payload is ready for review.",
        idempotency_key="typed-task-submitted",
    )
    requested = builder.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("canonical payload fidelity",),
        idempotency_key="typed-review-requested",
    )
    reviewed = reviewer.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="The review envelope round-trips.",
        idempotency_key="typed-review-completed",
    )
    accepted = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="The task envelope includes the acceptance review binding.",
        idempotency_key="typed-task-accepted",
    )

    cancelled = builder.create_task(
        title="Exercise cancellation",
        description="Reach the remaining task envelopes.",
        acceptance_criteria=("Cancellation remains canonical.",),
        idempotency_key="typed-task-cancel-created",
    )
    cancelled_id = cancelled["entity_ref"]["id"]
    cancelled = builder.cancel_task(
        cancelled_id,
        cancelled["revision"],
        reason="Exercise the cancellation envelope.",
        idempotency_key="typed-task-cancelled",
    )
    reopened = builder.reopen_task(
        cancelled_id,
        cancelled["revision"],
        reason="Exercise the reopened envelope.",
        idempotency_key="typed-task-reopened",
    )

    for result in (
        created,
        revised,
        taken,
        started,
        blocked,
        unblocked,
        completed,
        submitted,
        requested,
        reviewed,
        accepted,
        cancelled,
        reopened,
    ):
        stored = builder.show_event(result["event_id"])["event"]
        payload = stored["payload"]
        envelope = parse_event_envelope(str(stored["event_type"]), payload)

        assert envelope is not None
        assert canonical_json_bytes(serialize_event_envelope(envelope)) == canonical_json_bytes(
            payload
        )


def test_typed_thread_and_handoff_envelopes_round_trip_manager_events(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """Thread messages and handoff records preserve their canonical payloads."""

    _, _, builder, reviewer = workspace
    opened = builder.open_thread(
        thread_type="question",
        subject="Which task envelope should be replayed?",
        desired_outcome="Confirm the typed reducer path.",
        to=("reviewer",),
        idempotency_key="typed-thread-opened",
    )
    thread_id = opened["entity_ref"]["id"]
    replied = reviewer.reply_thread(
        thread_id,
        opened["revision"],
        body="Replay the validated typed envelope.",
        idempotency_key="typed-thread-replied",
    )
    resolved = reviewer.resolve_thread(
        thread_id,
        replied["revision"],
        resolution="resolved",
        summary="The typed replay path is confirmed.",
        idempotency_key="typed-thread-resolved",
    )
    handoff = builder.create_handoff(
        to=("reviewer",),
        completed=("typed thread envelope",),
        active=("typed handoff envelope",),
        next_actions=("acknowledge the handoff",),
        blockers=("none",),
        risks=("none",),
        open_questions=("none",),
        idempotency_key="typed-handoff-created",
    )
    acknowledged = reviewer.acknowledge_handoff(
        handoff["entity_ref"]["id"],
        handoff["revision"],
        note="The typed handoff envelope was received.",
        idempotency_key="typed-handoff-acknowledged",
    )

    for result in (opened, replied, resolved, handoff, acknowledged):
        stored = builder.show_event(result["event_id"])["event"]
        payload = stored["payload"]
        envelope = parse_event_envelope(str(stored["event_type"]), payload)

        assert envelope is not None
        assert canonical_json_bytes(serialize_event_envelope(envelope)) == canonical_json_bytes(
            payload
        )


def test_typed_truth_and_evidence_envelopes_round_trip_manager_events(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """Truth and evidence records preserve their canonical payloads."""

    repo, _, builder, reviewer = workspace
    source = repo / "typed-evidence.txt"
    source.write_text("first revision", encoding="utf-8")
    registered = builder.register_artifact(
        source,
        media_type="text/plain",
        classification="internal",
        idempotency_key="typed-artifact-registered",
    )
    source.write_text("second revision", encoding="utf-8")
    revised = builder.revise_artifact(
        registered["entity_ref"]["id"],
        registered["revision"],
        source,
        media_type="text/plain",
        classification="restricted",
        idempotency_key="typed-artifact-revised",
    )
    artifact_ref = revised["entity_ref"]
    verification = reviewer.record_verification(
        target_ref=artifact_ref,
        target_revision=revised["revision"],
        claim="The revised artifact is canonical evidence.",
        evidence_refs=(artifact_ref,),
        method="sha256",
        outcome="pass",
        idempotency_key="typed-verification-recorded",
    )
    reported = reviewer.report_finding(
        summary="The artifact evidence remains available.",
        severity="info",
        evidence_refs=(artifact_ref,),
        idempotency_key="typed-finding-reported",
    )
    contested = builder.contest_finding(
        reported["entity_ref"]["id"],
        reported["revision"],
        reason="Exercise the contested typed envelope.",
        idempotency_key="typed-finding-contested",
    )
    promoted = reviewer.promote_finding(
        reported["entity_ref"]["id"],
        contested["revision"],
        summary="The evidence was independently confirmed.",
        evidence_refs=(artifact_ref,),
        idempotency_key="typed-finding-promoted",
    )
    resolved = builder.resolve_finding(
        reported["entity_ref"]["id"],
        promoted["revision"],
        resolution="Resolved after typed replay.",
        idempotency_key="typed-finding-resolved",
    )
    proposed = builder.propose_decision(
        scope="typed.truth.evidence",
        proposal="Retain typed truth envelopes.",
        alternatives=("Use untyped mappings.",),
        idempotency_key="typed-decision-proposed",
    )
    deferred = builder.defer_decision(
        proposed["entity_ref"]["id"],
        proposed["revision"],
        reason="Exercise the deferred typed envelope.",
        idempotency_key="typed-decision-deferred",
    )
    accepted = reviewer.accept_decision(
        proposed["entity_ref"]["id"],
        deferred["revision"],
        rationale="Evidence supports the typed boundary.",
        evidence_refs=(artifact_ref,),
        dissent=("No dissent.",),
        idempotency_key="typed-decision-accepted",
    )
    replacement = builder.propose_decision(
        scope="typed.truth.evidence",
        proposal="Keep the typed boundary under its replacement decision.",
        idempotency_key="typed-decision-replacement",
    )
    superseded = builder.supersede_decision(
        proposed["entity_ref"]["id"],
        accepted["revision"],
        replacement_decision_id=replacement["entity_ref"]["id"],
        reason="Exercise the superseded typed envelope.",
        idempotency_key="typed-decision-superseded",
    )
    rejected_proposal = builder.propose_decision(
        scope="typed.truth.rejected",
        proposal="Reject the alternative typed decision.",
        idempotency_key="typed-decision-rejected-proposal",
    )
    rejected = reviewer.reject_decision(
        rejected_proposal["entity_ref"]["id"],
        rejected_proposal["revision"],
        rationale="Exercise the rejected typed envelope.",
        idempotency_key="typed-decision-rejected",
    )

    for result in (
        registered,
        revised,
        verification,
        reported,
        contested,
        promoted,
        resolved,
        proposed,
        deferred,
        accepted,
        replacement,
        superseded,
        rejected_proposal,
        rejected,
    ):
        stored = builder.show_event(result["event_id"])["event"]
        payload = stored["payload"]
        envelope = parse_event_envelope(str(stored["event_type"]), payload)

        assert envelope is not None
        assert canonical_json_bytes(serialize_event_envelope(envelope)) == canonical_json_bytes(
            payload
        )


def test_typed_role_and_agent_link_envelopes_round_trip_manager_events(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """Role and role-link records preserve their canonical payloads."""

    _, _, builder, _ = workspace
    source = builder.create_agent(
        name="Typed source role",
        profile_id="codex-builder",
        grants={"create_roles": "deny", "retire_roles": "deny", "open_links": "deny"},
        rationale="Exercise the typed role envelope.",
        skills=("code.review",),
        tool_allowlist=("repo.read",),
        turnover_budget=0,
        template=True,
        idempotency_key="typed-agent-source-created",
    )
    target = builder.create_agent(
        name="Typed target role",
        profile_id="claude-builder",
        rationale="Receive a typed role link.",
        idempotency_key="typed-agent-target-created",
    )
    reconfigured = builder.reconfigure_agent(
        source["entity_ref"]["id"],
        source["revision"],
        changes={
            "name": "Typed source role reconfigured",
            "skills": [],
            "tool_allowlist": ["repo.read", "repo.status"],
            "turnover_budget": None,
        },
        reason="Exercise the typed reconfiguration envelope.",
        idempotency_key="typed-agent-reconfigured",
    )
    opened = builder.open_agent_link(
        from_agent_id=source["entity_ref"]["id"],
        to_agent_id=target["entity_ref"]["id"],
        allowed_action="handoff_work",
        deadline_seconds=60,
        reason="Exercise the typed role-link envelope.",
        idempotency_key="typed-agent-link-opened",
    )
    closed = builder.close_agent_link(
        opened["entity_ref"]["id"],
        opened["revision"],
        reason="Exercise the typed closed-link envelope.",
        idempotency_key="typed-agent-link-closed",
    )
    retired = builder.retire_agent(
        target["entity_ref"]["id"],
        target["revision"],
        reason="Exercise the typed retirement envelope.",
        idempotency_key="typed-agent-retired",
    )["retired"][0]

    for result in (source, target, reconfigured, opened, closed, retired):
        stored = builder.show_event(result["event_id"])["event"]
        payload = stored["payload"]
        envelope = parse_event_envelope(str(stored["event_type"]), payload)

        assert envelope is not None
        assert canonical_json_bytes(serialize_event_envelope(envelope)) == canonical_json_bytes(
            payload
        )


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


def test_task_acceptance_requires_delegated_review_terminal_success(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    repo, state_root, builder, reviewer = workspace
    created = builder.create_task(
        title="Require the delegated terminal result",
        description="An orphan review approval must not become accepted work.",
        acceptance_criteria=("The delegated review succeeds canonically.",),
        idempotency_key="delegated-acceptance-task",
    )
    task_id = created["entity_ref"]["id"]
    started = builder.start_task(
        task_id, created["revision"], idempotency_key="delegated-acceptance-start"
    )
    completed = builder.complete_task(
        task_id,
        started["revision"],
        summary="Implementation is ready.",
        idempotency_key="delegated-acceptance-complete",
    )
    submitted = builder.submit_task(
        task_id,
        completed["revision"],
        summary="Ready for delegated review.",
        idempotency_key="delegated-acceptance-submit",
    )
    requested = builder.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("Check the exact submitted revision.",),
        idempotency_key="delegated-acceptance-review",
    )
    child_session = builder.start_session(
        stable_instance_id="delegated-acceptance-reviewer-12345678",
        principal="operator-delegated-acceptance-reviewer",
        client="claude-code",
        software="claude-cli",
        role="independent-reviewer",
    )
    delegation = builder.create_delegation(
        target_ref=requested["entity_ref"],
        target_revision=requested["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="delegated-acceptance-delegation",
    )
    active = builder.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child_session["session_id"],
        attempt=1,
        idempotency_key="delegated-acceptance-delegation-start",
    )
    child = CommonsManager(repo, state_root=state_root, session_id=child_session["session_id"])
    review = child.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="The exact delegated review is approved.",
        idempotency_key="delegated-acceptance-review-complete",
    )

    with pytest.raises(
        LifecycleConflictError,
        match="delegated review terminal result",
    ):
        reviewer.accept_task(
            task_id,
            submitted["revision"],
            summary="The orphan approval must not be accepted.",
            idempotency_key="delegated-acceptance-before-terminal",
        )

    child.succeed_delegation(
        delegation["entity_ref"]["id"],
        active["revision"],
        summary="The delegated review reached its terminal result.",
        result_refs=(review["entity_ref"],),
        idempotency_key="delegated-acceptance-terminal",
    )
    accepted = reviewer.accept_task(
        task_id,
        submitted["revision"],
        summary="The delegated approval is now terminal and admissible.",
        idempotency_key="delegated-acceptance-after-terminal",
    )
    assert accepted["event_type"] == "task.accepted"


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
    with pytest.raises(LifecycleConflictError, match="authored the subject"):
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
    with pytest.raises(LifecycleConflictError, match="authored the subject"):
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


def test_revising_task_text_makes_an_approved_review_stale(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, author, independent = workspace
    created = author.create_task(
        title="Ship the first draft",
        description="Render the original copy",
        acceptance_criteria=("the original copy is visible",),
        idempotency_key="revise-task-create",
    )
    task_id = created["entity_ref"]["id"]
    started = author.start_task(task_id, created["revision"], idempotency_key="revise-start")
    completed = author.complete_task(
        task_id,
        started["revision"],
        summary="original draft complete",
        idempotency_key="revise-complete",
    )
    submitted = author.submit_task(
        task_id,
        completed["revision"],
        summary="original draft submitted",
        idempotency_key="revise-submit",
    )
    requested = author.request_review(
        target_ref={"kind": "task", "id": task_id},
        target_revision=submitted["revision"],
        criteria=("the original copy is visible",),
        independent=True,
        idempotency_key="revise-review-request",
    )
    independent.complete_review(
        requested["entity_ref"]["id"],
        requested["revision"],
        target_revision=submitted["revision"],
        verdict="approved",
        summary="the original draft meets its criterion",
        idempotency_key="revise-review-complete",
    )

    revised = author.revise_task(
        task_id,
        submitted["revision"],
        changes={
            "description": "Render the corrected copy",
            "acceptance_criteria": ["the corrected copy is visible"],
        },
        idempotency_key="revise-task-content",
    )

    task = next(item for item in author.list_tasks() if item["id"] == task_id)
    review = next(
        item for item in author.list_reviews() if item["id"] == requested["entity_ref"]["id"]
    )
    assert revised["revision"] != submitted["revision"]
    assert task["description"] == "Render the corrected copy"
    assert task["acceptance_criteria"] == ["the corrected copy is visible"]
    assert review["state"] == "approved"
    assert review["stale"] is True
    with pytest.raises(
        LifecycleConflictError,
        match="task acceptance requires a current approved independent review",
    ):
        author.accept_task(
            task_id,
            revised["revision"],
            summary="must not accept using the old verdict",
            idempotency_key="revise-task-accept-stale",
        )


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


def test_a_role_prefixed_handoff_reaches_the_role_it_names(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """finding.7B0CXG5QTQ5SCY2JMCTW7W2SVH: a handoff addressed to
    "role:independent-reviewer" could never be acknowledged by anyone — the
    matcher compared bare role names only, so the prefixed spelling that every
    other reference uses produced a message with no reachable recipient.  Both
    spellings now reach the same session, and a session holding a different
    role is still refused."""

    repo, state_root, builder, reviewer = workspace
    outsider, _ = _open(repo, state_root, name="outsider", role="observer")

    prefixed = builder.create_handoff(
        to=("role:reviewer",),
        next_actions=("review the work",),
        idempotency_key="handoff-role-prefixed",
    )
    prefixed_id = prefixed["entity_ref"]["id"]

    with pytest.raises(LifecycleConflictError, match="recipient"):
        outsider.acknowledge_handoff(
            prefixed_id,
            prefixed["revision"],
            note="not mine either way",
            idempotency_key="handoff-prefixed-wrong-recipient",
        )
    acknowledged = reviewer.acknowledge_handoff(
        prefixed_id,
        prefixed["revision"],
        note="received via the prefixed spelling",
        idempotency_key="handoff-prefixed-ack",
    )
    assert acknowledged["event_type"] == "handoff.acknowledged"

    # And the prefixed spelling is visible where the recipient actually looks:
    # the inbox filters by the same addressed set as the acknowledgement gate.
    another = builder.create_handoff(
        to=("role:reviewer",),
        next_actions=("read the follow-up",),
        idempotency_key="handoff-role-prefixed-inbox",
    )
    listed = inbox_view(
        reviewer.snapshot(),
        session={"role_id": "reviewer", "session_id": "session.whoever"},
    )
    assert another["entity_ref"]["id"] in {item.get("id") for item in listed.get("handoffs", [])}


def test_an_acceptance_stamps_the_semantics_floor_exactly_once(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """The two-checkout incident, prevented forward: a reader older than the
    causal acceptance guard misjudges a healthy acceptance chain, so the
    first write that depends on that guard stamps the ledger with the
    semantics version it needs.  Never earlier — an untouched workspace stays
    readable by old code — and never twice."""

    _, _, builder, reviewer = workspace
    task_acceptance_semantics_version = 2
    assert builder.snapshot().semantics_required == 1

    def accepted_task(tag: str) -> None:
        created = builder.create_task(
            title=f"Stamped {tag}",
            description="acceptance raises the semantics floor",
            acceptance_criteria=("reviewed",),
            idempotency_key=f"stamp-task-{tag}",
        )
        task_id = created["entity_ref"]["id"]
        started = builder.start_task(
            task_id, created["revision"], idempotency_key=f"stamp-start-{tag}"
        )
        completed = builder.complete_task(
            task_id, started["revision"], summary="done", idempotency_key=f"stamp-complete-{tag}"
        )
        submitted = builder.submit_task(
            task_id, completed["revision"], summary="ready", idempotency_key=f"stamp-submit-{tag}"
        )
        requested = builder.request_review(
            target_ref={"kind": "task", "id": task_id},
            target_revision=submitted["revision"],
            criteria=("correctness",),
            idempotency_key=f"stamp-review-request-{tag}",
        )
        reviewer.complete_review(
            requested["entity_ref"]["id"],
            requested["revision"],
            target_revision=submitted["revision"],
            verdict="approved",
            summary="approved",
            idempotency_key=f"stamp-review-complete-{tag}",
        )
        reviewer.accept_task(
            task_id,
            submitted["revision"],
            summary="accepted",
            idempotency_key=f"stamp-accept-{tag}",
        )

    def stamp_count() -> int:
        return sum(
            1
            for item in builder.events.iter_events()
            if getattr(item, "event", item)["event_type"] == "workspace.semantics_required"
        )

    accepted_task("one")
    assert builder.snapshot().semantics_required == task_acceptance_semantics_version
    assert stamp_count() == 1
    assert builder.doctor()["ok"] is True

    # A second acceptance finds the floor already high enough and adds nothing.
    accepted_task("two")
    assert stamp_count() == 1
    assert builder.snapshot().semantics_required == task_acceptance_semantics_version


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


def test_warm_orient_and_inbox_use_verified_projection_without_canonical_reads(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manager, _ = workspace
    manager.create_objective(
        title="Fast brief",
        description="Prime the projection",
        acceptance_criteria=("warm reads avoid canonical contents",),
        idempotency_key="fast-brief-objective",
    )
    first = manager.orient()
    assert first["read_diagnostics"]["source"] == "sqlite"
    assert first["read_diagnostics"]["canonical_content_files_read"] == 1

    def unexpected_read(_: object) -> object:
        raise AssertionError("warm read must not load canonical file contents")

    monkeypatch.setattr(manager.events, "read_path", unexpected_read)
    warm = manager.orient()
    inbox = manager.inbox()

    assert warm["read_diagnostics"]["cache_hit"] is True
    assert warm["read_diagnostics"]["canonical_content_files_read"] == 0
    assert inbox["read_diagnostics"]["cache_hit"] is True
    assert inbox["read_diagnostics"]["canonical_content_files_read"] == 0
    with pytest.raises(AssertionError, match="warm read"):
        manager.orient(fresh=True)


def test_orient_rebuilds_internally_inconsistent_disposable_projection(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    manager.create_objective(
        title="Repair cache",
        description="Canonical history remains authoritative",
        acceptance_criteria=("projection rebuilds",),
        idempotency_key="repair-cache-objective",
    )
    manager.orient()
    with sqlite3.connect(manager.paths.index_db) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM events")
        connection.commit()

    repaired = manager.orient()

    assert repaired["read_diagnostics"]["source"] == "sqlite", repaired["read_diagnostics"]
    assert repaired["read_diagnostics"]["index_rebuilt"] is True
    assert repaired["counts"]["objectives"] == {"active": 1}


def test_read_only_orient_uses_canonical_fallback_without_creating_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="read-only-fast-path")
    writer, session = _open(repo, state_root, name="reader", role="builder")
    writer.create_objective(
        title="Read only",
        description="Do not mutate disposable state",
        acceptance_criteria=("no SQLite file",),
        idempotency_key="read-only-objective",
    )
    assert not writer.paths.index_db.exists()
    reader = CommonsManager(
        repo,
        state_root=state_root,
        session_id=session["session_id"],
        read_only=True,
    )

    brief = reader.orient()

    assert brief["read_diagnostics"]["source"] == "canonical"
    assert brief["read_diagnostics"]["reason"] == "read_only"
    assert not reader.paths.index_db.exists()


def test_unreadable_sqlite_projection_falls_back_to_canonical_history(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    _, _, manager, _ = workspace
    manager.create_objective(
        title="Corrupt cache",
        description="The disposable database is never authoritative",
        acceptance_criteria=("canonical replay succeeds",),
        idempotency_key="corrupt-cache-objective",
    )
    manager.orient()
    manager.paths.index_db.write_bytes(b"not a sqlite database")

    brief = manager.orient()

    assert brief["read_diagnostics"]["source"] == "canonical"
    assert brief["read_diagnostics"]["reason"] == "index_fallback"
    assert brief["read_diagnostics"]["fallback_error"] == "IntegrityError"
    assert brief["counts"]["objectives"] == {"active": 1}


def test_the_canonical_write_lock_is_reentrant_within_one_manager(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """A cascade holds one critical section across several writes.

    flock does not detect same-process nesting, so a naive nested acquisition
    would deadlock; a release-and-reacquire between writes is where a concurrent
    writer slipped into the cascade (M6, 2026-08-10 review).  The lock reuses the
    outer hold and records a canonical event from inside it.
    """

    _, _, builder, _ = workspace
    with builder._canonical_write_lock():
        assert builder._write_lock_depth == 1
        with builder._canonical_write_lock():
            assert builder._write_lock_depth == 2
            # A real canonical write from inside the nested hold must not block.
            created = builder.create_objective(
                title="Written under a nested lock",
                description="the reentrant path records without deadlocking",
                acceptance_criteria=("recorded",),
                idempotency_key="reentrant-write",
            )
            assert created["event_type"] == "objective.created"
        assert builder._write_lock_depth == 1
    assert builder._write_lock_depth == 0


def test_a_rejected_engagement_leaves_no_empty_thread_in_the_ledger(
    workspace: tuple[Path, Path, CommonsManager, CommonsManager],
) -> None:
    """Round 2: the thread.opened used to land before the reply was validated,
    so a bad message left an empty engagement thread in the immutable ledger."""

    _, _, builder, _ = workspace
    before = [record.event_id for record in builder.events.iter_events()]
    with pytest.raises(ValidationError, match="non-empty message"):
        builder.open_engagement(subject="start the work", body="   ")
    after = [record.event_id for record in builder.events.iter_events()]
    assert after == before
    assert builder.list_engagements() == []
