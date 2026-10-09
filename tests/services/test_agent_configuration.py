from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from agent_commons.domain.agent_role_envelopes import parse_agent_role_envelope
from agent_commons.domain.projection import project_events
from agent_commons.domain.revisions import structural_correction_changes
from agent_commons.domain.roles import descendants, effective_grants, turnover_used
from agent_commons.errors import LifecycleConflictError, ValidationError
from agent_commons.library import LibraryError, LibraryStore
from agent_commons.services import CommonsManager


@pytest.fixture
def manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="agent-configuration")
    value = CommonsManager(repo, state_root=tmp_path / "state")
    session = value.start_session(
        stable_instance_id="operator",
        principal="operator",
        client="codex",
        software="test",
        role="operator",
    )
    value.session_id = session["session_id"]
    return value


def hire(manager: CommonsManager, name: str, **options):  # type: ignore[no-untyped-def]
    return manager.create_agent(
        name=name,
        profile_id=options.pop("profile_id", "codex-builder"),
        rationale="Synthetic configuration test",
        idempotency_key=name,
        **options,
    )


def ref(store: LibraryStore, name: str) -> dict[str, str]:
    return next(item["ref"] for item in store.catalog()["roles"] if item["ref"]["id"] == name)


def specialized(manager: CommonsManager, tmp_path: Path, **options):  # type: ignore[no-untyped-def]
    store = LibraryStore(tmp_path / "library", workspace_root=manager.repo_root)
    old = ref(store, "frontend-engineer")
    content = deepcopy(store.resolve(old)["content"])
    content["system_prompt"] += "\nA distinct synthetic specialization revision."
    new = store.save(
        kind="role",
        id="custom-frontender",
        content=content,
        expected_version=None,
        idempotency_key="custom-frontender",
        fork_ref=old,
    )["ref"]
    agent = hire(manager, "Specialist", specialization_ref=old, library_store=store, **options)
    return store, old, new, agent


def revise(manager: CommonsManager, agent, changes, key="configuration", **options):  # type: ignore[no-untyped-def]
    return manager.reconfigure_agent(
        agent["entity_ref"]["id"],
        agent["revision"],
        changes=changes,
        reason="Human configuration change",
        idempotency_key=key,
        **options,
    )


def request(manager: CommonsManager, agent=None, *, review=False):  # type: ignore[no-untyped-def]
    task = manager.create_task(
        title="Synthetic work",
        description="No provider will run",
        acceptance_criteria=("Check configuration guards",),
    )
    target = (
        manager.request_review(
            target_ref=task["entity_ref"],
            target_revision=task["revision"],
            criteria=("Review synthetic work",),
        )
        if review
        else task
    )
    return manager.create_delegation(
        target_ref=target["entity_ref"],
        target_revision=target["revision"],
        target_profile="codex-independent-reviewer" if review else "codex-builder",
        purpose="independent_review" if review else "implementation",
        on_behalf_of_agent_id=agent["entity_ref"]["id"] if agent else None,
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )


def child(manager: CommonsManager, delegation):  # type: ignore[no-untyped-def]
    session = manager.start_session(
        stable_instance_id="synthetic-child",
        principal="worker",
        client="codex",
        software="test",
        role="worker",
    )
    started = manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=session["session_id"],
        attempt=1,
    )
    return CommonsManager(
        manager.repo_root, state_root=manager.paths.state_root, session_id=session["session_id"]
    ), started


def test_human_create_reparent_and_clear_are_not_security_lineage(manager: CommonsManager) -> None:
    first, second = hire(manager, "First"), hire(manager, "Second")
    agent = hire(
        manager,
        "Child",
        supervisor_agent_id=first["entity_ref"]["id"],
        grants={"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
        turnover_budget=3,
        model="gpt-5",
    )
    event = manager.show_event(agent["event_id"])["event"]
    assert event["payload_schema"] == "commons.payload.agent.v3"
    assert event["payload"]["origin"] == event["payload"]["approval"] == "human"
    assert event["payload"]["created_by_agent_id"] is None
    assert "proposal_ref" not in event["payload"]
    assert not event["relations"]
    original = manager.get_agent(agent["entity_ref"]["id"])
    moved = revise(manager, agent, {"supervisor_agent_id": second["entity_ref"]["id"]})
    assert (
        manager.get_agent(agent["entity_ref"]["id"])["supervisor_agent_id"]
        == second["entity_ref"]["id"]
    )
    assert (
        revise(manager, agent, {"supervisor_agent_id": second["entity_ref"]["id"]})["event_id"]
        == moved["event_id"]
    )
    with pytest.raises(LifecycleConflictError):
        revise(manager, agent, {"supervisor_agent_id": None}, "stale")
    cleared = revise(manager, moved, {"supervisor_agent_id": None}, "clear")
    payload = manager.show_event(cleared["event_id"])["event"]["payload"]
    assert payload["changes"] == {"supervisor_agent_id": None}
    record = manager.get_agent(agent["entity_ref"]["id"])
    assert record["supervisor_agent_id"] is None
    for field in (
        "created_by_agent_id",
        "origin",
        "approval",
        "grants",
        "profile_id",
        "lifetime",
        "extensions",
    ):
        assert record[field] == original[field]
    snapshot = manager.snapshot()
    for supervisor in (first, second):
        identifier = supervisor["entity_ref"]["id"]
        assert descendants(snapshot.agents, identifier) == ()
        assert turnover_used(snapshot.agents, identifier) == 0
    assert effective_grants(snapshot.agents, agent["entity_ref"]["id"])["create_roles"] == "ask"
    assert snapshot.semantics_required == 8
    assert not snapshot.issues
    assert manager.show_event(agent["event_id"])["event"] == event


def test_supervisors_reject_self_cycle_missing_retired_and_ineligible_roles(
    manager: CommonsManager,
) -> None:
    parent = hire(manager, "Parent")
    node = hire(manager, "Node", supervisor_agent_id=parent["entity_ref"]["id"])
    retired = hire(manager, "Retired")
    manager.retire_agent(retired["entity_ref"]["id"], retired["revision"], reason="Done")
    template = hire(manager, "Template", template=True)
    task = manager.create_task(
        title="Lifetime", description="Synthetic", acceptance_criteria=("Done",)
    )
    temporary = hire(
        manager, "Temporary", lifetime={"kind": "task_scoped", "task_id": task["entity_ref"]["id"]}
    )
    for target in (
        node["entity_ref"]["id"],
        "agent." + "Z" * 26,
        retired["entity_ref"]["id"],
        template["entity_ref"]["id"],
        temporary["entity_ref"]["id"],
    ):
        with pytest.raises(LifecycleConflictError):
            revise(manager, node, {"supervisor_agent_id": target}, "reject-" + target)
    with pytest.raises(LifecycleConflictError, match="cycle"):
        revise(manager, parent, {"supervisor_agent_id": node["entity_ref"]["id"]}, "cycle")
    for target in (template, temporary):
        with pytest.raises(LifecycleConflictError, match="persistent non-template"):
            revise(
                manager, target, {"supervisor_agent_id": None}, "ineligible-" + target["event_id"]
            )
    with pytest.raises(ValidationError):
        revise(manager, node, {"supervisor_agent_id": "bad"})


def test_concurrent_reparenting_cannot_commit_a_cycle(manager: CommonsManager) -> None:
    a, b = hire(manager, "A"), hire(manager, "B")
    other = CommonsManager(
        manager.repo_root, session_id=manager.session_id, state_root=manager.paths.state_root
    )

    def write(pair):  # type: ignore[no-untyped-def]
        client, own, target = pair
        try:
            revise(
                client, own, {"supervisor_agent_id": target["entity_ref"]["id"]}, own["event_id"]
            )
            return "created"
        except LifecycleConflictError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [(manager, a, b), (other, b, a)])) == ["created", "refused"]
    assert not manager.snapshot().issues


def test_specialization_retains_exact_bundle_and_receipt_survives_new_work(
    manager: CommonsManager, tmp_path: Path
) -> None:
    store, old, new, agent = specialized(manager, tmp_path)
    historical = deepcopy([row.event for row in manager.events.iter_events()])
    changed = revise(manager, agent, {"specialization_ref": new}, library_store=store)
    assert (
        manager.show_event(changed["event_id"])["event"]["payload_schema"]
        == "commons.payload.agent.v3"
    )
    assert manager.get_agent(agent["entity_ref"]["id"])["specialization_ref"] == new
    bundle = store.compose_role(new)
    assert bundle.role_ref.as_dict() == new
    assert store.compose_role(old).role_ref.as_dict() == old
    for dependency in bundle.skill_refs:
        assert store.resolve(dependency.as_dict())["ref"] == dependency.as_dict()
    pending = request(manager, agent)
    replayed = revise(manager, agent, {"specialization_ref": new}, library_store=store)
    assert replayed["event_id"] == changed["event_id"]
    assert manager.get_delegation(pending["entity_ref"]["id"])["state"] == "requested"
    current = [row.event for row in manager.events.iter_events()]
    assert all(event in current for event in historical)
    assert not project_events(current).issues
    assert project_events(historical).agents[agent["entity_ref"]["id"]]["specialization_ref"] == old
    assert project_events(current).agents[agent["entity_ref"]["id"]]["specialization_ref"] == new


@pytest.mark.parametrize("state", ["requested", "active", "input_needed", "review"])
def test_specialization_cannot_change_while_work_or_review_is_owed(
    manager: CommonsManager, tmp_path: Path, state: str
) -> None:
    store, _, new, agent = specialized(
        manager,
        tmp_path,
        profile_id="codex-independent-reviewer" if state == "review" else "codex-builder",
    )
    delegation = request(manager, agent, review=state == "review")
    if state in ("active", "input_needed"):
        worker, started = child(manager, delegation)
        if state == "input_needed":
            worker.mark_delegation_input_needed(
                delegation["entity_ref"]["id"], started["revision"], summary="Synthetic pause"
            )
    if state == "review":
        manager.cancel_delegation(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            reason="Provider not launched; review still owed",
        )
    with pytest.raises(LifecycleConflictError, match="owing live work"):
        revise(manager, agent, {"specialization_ref": new}, library_store=store)


@pytest.mark.parametrize("historical", [False, True])
def test_unroled_child_cannot_reconfigure_or_create_organization(
    manager: CommonsManager, tmp_path: Path, historical: bool
) -> None:
    store, _, new, agent = specialized(manager, tmp_path)
    supervisor = hire(manager, "Supervisor")
    delegation = request(manager)
    worker, started = child(manager, delegation)
    if historical:
        worker.fail_delegation(
            delegation["entity_ref"]["id"],
            started["revision"],
            reason_code="runtime_error",
            summary="Synthetic terminal",
        )
    for changes in ({"specialization_ref": new}, {"supervisor_agent_id": None}):
        with pytest.raises(LifecycleConflictError, match="unbound human operator"):
            revise(worker, agent, changes, library_store=store)
    with pytest.raises(LifecycleConflictError, match="unbound human operator"):
        hire(worker, "False operator", supervisor_agent_id=supervisor["entity_ref"]["id"])


def test_missing_specialization_legacy_override_and_missing_skill_reader_refuse(
    manager: CommonsManager, tmp_path: Path
) -> None:
    store, _, new, agent = specialized(manager, tmp_path)
    for value in (None, {**new, "version": "latest"}, {**new, "version": "0" * 64}):
        with pytest.raises((LibraryError, ValidationError)):
            revise(manager, agent, {"specialization_ref": value}, library_store=store)
    legacy = hire(manager, "Legacy")
    with pytest.raises(LifecycleConflictError, match="only a specialized role"):
        revise(manager, legacy, {"specialization_ref": new}, library_store=store)
    for selection in ({"skills": ["commons-start"]}, {"tool_allowlist": ["commons_repo_read"]}):
        with pytest.raises(ValidationError):
            revise(manager, agent, {"specialization_ref": new, **selection}, library_store=store)
    for immutable in ("profile_id", "model", "created_by_agent_id", "lifetime"):
        with pytest.raises(ValidationError, match="immutable"):
            revise(manager, agent, {immutable: "forbidden"}, library_store=store)


def test_typed_v3_round_trip_and_correction_do_not_drop_or_rewrite_fields(
    manager: CommonsManager, tmp_path: Path
) -> None:
    store, _, new, agent = specialized(manager, tmp_path)
    supervisor = hire(manager, "Supervisor")
    created = hire(manager, "Organized", supervisor_agent_id=supervisor["entity_ref"]["id"])
    payload = manager.show_event(created["event_id"])["event"]["payload"]
    for value in (payload, {**payload, "supervisor_agent_id": None}):
        assert parse_agent_role_envelope("agent.created", value).to_payload() == value
    for changes in (
        {"supervisor_agent_id": None},
        {"specialization_ref": new},
        {"supervisor_agent_id": supervisor["entity_ref"]["id"], "specialization_ref": new},
    ):
        payload = {
            "agent_id": agent["entity_ref"]["id"],
            "expected_revision": agent["revision"],
            "changes": changes,
            "reason": "Exact configuration",
        }
        assert parse_agent_role_envelope("agent.reconfigured", payload).to_payload() == payload
        assert structural_correction_changes(payload, {**payload, "changes": {}})
    before = json.dumps(manager.show_event(agent["event_id"])["event"], sort_keys=True)
    changed = revise(
        manager,
        agent,
        {"specialization_ref": new, "supervisor_agent_id": supervisor["entity_ref"]["id"]},
        library_store=store,
    )
    assert json.dumps(manager.show_event(agent["event_id"])["event"], sort_keys=True) == before
    assert (
        manager.get_agent(changed["entity_ref"]["id"])["supervisor_agent_id"]
        == supervisor["entity_ref"]["id"]
    )


def test_reparent_preserves_agent_creator_and_does_not_add_cascade(manager: CommonsManager) -> None:
    creator = hire(
        manager,
        "Creator",
        grants={"create_roles": "ask", "retire_roles": "deny", "open_links": "deny"},
        turnover_budget=4,
    )
    worker, _ = child(manager, request(manager, creator))
    proposal = worker.propose_agent(
        name="Proposed role", profile_id="codex-builder", rationale="A bounded helper"
    )
    created = manager.approve_agent_proposal(proposal["entity_ref"]["id"])
    # Approval returns the created agent alongside its resolved proposal.
    if "agent" in created:
        created = created["agent"]
    subordinate_id = created["entity_ref"]["id"]
    current = manager.get_agent(subordinate_id)
    supervisor = hire(manager, "Organizational supervisor")
    revise(
        manager,
        {"entity_ref": {"id": subordinate_id}, "revision": current["revision"]},
        {"supervisor_agent_id": supervisor["entity_ref"]["id"]},
    )
    record = manager.get_agent(subordinate_id)
    assert record["created_by_agent_id"] == creator["entity_ref"]["id"]
    assert record["origin"] == "agent"
    assert record["grants"] == current["grants"]
    assert descendants(manager.snapshot().agents, supervisor["entity_ref"]["id"]) == ()
    manager.retire_agent(
        supervisor["entity_ref"]["id"],
        supervisor["revision"],
        reason="Separate organization",
        cascade=True,
    )
    assert manager.get_agent(subordinate_id)["state"] == "active"


def test_live_work_arriving_after_library_preparation_is_checked_under_lock(
    manager: CommonsManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, old, new, agent = specialized(manager, tmp_path)
    record = manager.record_event
    inserted = False

    def interleave(event_type, payload, **options):  # type: ignore[no-untyped-def]
        nonlocal inserted
        if event_type == "agent.reconfigured" and not inserted:
            inserted = True
            request(manager, agent)
        return record(event_type, payload, **options)

    monkeypatch.setattr(manager, "record_event", interleave)
    with pytest.raises(LifecycleConflictError, match="owing live work"):
        revise(manager, agent, {"specialization_ref": new}, library_store=store)
    assert inserted
    assert manager.get_agent(agent["entity_ref"]["id"])["specialization_ref"] == old
    assert not manager.snapshot().issues


@pytest.mark.parametrize("operation", ["create", "reparent", "specialization"])
def test_v3_stamp_refuses_old_reader_and_old_schemas_stay_closed(
    manager: CommonsManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from agent_commons.domain import projection

    supervisor = hire(manager, "Supervisor")
    if operation == "create":
        agent = hire(manager, "Organized", supervisor_agent_id=supervisor["entity_ref"]["id"])
    elif operation == "reparent":
        agent = revise(
            manager,
            hire(manager, "Organized"),
            {"supervisor_agent_id": supervisor["entity_ref"]["id"]},
        )
    else:
        store, _, new, original = specialized(manager, tmp_path)
        agent = revise(manager, original, {"specialization_ref": new}, library_store=store)
    event = manager.show_event(agent["event_id"])["event"]
    assert event["payload_schema"] == "commons.payload.agent.v3"
    with pytest.raises(ValidationError):
        manager.schemas.validate("commons.payload.agent.v1", event["payload"])
    monkeypatch.setattr(projection, "LEDGER_SEMANTICS_VERSION", 7)
    snapshot = projection.project_events([row.event for row in manager.events.iter_events()])
    assert snapshot.semantics_required == 8
    assert [issue.code for issue in snapshot.issues] == ["ledger_ahead_of_code"]


@pytest.mark.parametrize("modern", [False, True])
def test_same_specialization_and_legacy_configuration_stay_available_during_work(
    manager: CommonsManager, tmp_path: Path, modern: bool
) -> None:
    options = {}
    changes = {"name": "Saved while busy"}
    if modern:
        store, old, _, agent = specialized(manager, tmp_path)
        options["library_store"] = store
        changes["specialization_ref"] = old
    else:
        agent = hire(manager, "Legacy")
    delegation = request(manager, agent)
    changed = revise(manager, agent, changes, **options)
    manager.cancel_delegation(
        delegation["entity_ref"]["id"], delegation["revision"], reason="Synthetic work finished"
    )
    revise(manager, changed, {"name": "Later name"}, "later")
    request(manager, agent)
    retry = revise(manager, agent, changes, **options)
    assert retry["event_id"] == changed["event_id"]
    assert manager.get_agent(agent["entity_ref"]["id"])["name"] == "Later name"
    event = manager.show_event(changed["event_id"])["event"]
    assert event["payload_schema"] == (
        "commons.payload.agent.v3" if modern else "commons.payload.agent.v1"
    )
    assert not manager.snapshot().issues


def test_rebinding_retains_new_builtin_role_and_all_dependencies_without_pack(
    manager: CommonsManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_commons import library

    store, old, _, agent = specialized(manager, tmp_path)
    new = next(item["ref"] for item in store.catalog()["roles"] if item["ref"] != old)
    expected = store.compose_role(new)
    assert not store._version_path(library.LibraryRef.parse(new)).exists()
    changed = revise(manager, agent, {"specialization_ref": new}, library_store=store)
    monkeypatch.setattr(library, "_manifest", lambda: {"entries": []})
    assert store.compose_role(new) == expected
    assert all(store.read_file(skill.as_dict())["text"] for skill in expected.skill_refs)
    assert (
        revise(manager, agent, {"specialization_ref": new}, library_store=store)["event_id"]
        == changed["event_id"]
    )
    assert manager.get_agent(agent["entity_ref"]["id"])["specialization_ref"] == new
