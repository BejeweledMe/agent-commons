from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import agent_commons.library as library_module
from agent_commons.errors import ConfigurationError, LifecycleConflictError, ValidationError
from agent_commons.library import LibraryError, LibraryStore, validate_library_ref
from agent_commons.services import CommonsManager


def _ref(store: LibraryStore, kind: str, identifier: str) -> dict[str, str]:
    return next(
        item["ref"] for item in store.catalog()[kind + "s"] if item["ref"]["id"] == identifier
    )


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="library-tests")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="library-test-operator",
        principal="operator",
        client="codex",
        software="test",
        role="operator",
    )
    manager.session_id = session["session_id"]
    return manager


def test_builtin_inventory_is_complete_private_and_available_without_setup(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library")
    catalog = store.catalog()
    assert len(catalog["roles"]) == 30
    assert len(catalog["skills"]) == 45
    assert len({role["group"] for role in catalog["roles"]}) == 8
    assert not (tmp_path / "library").exists()
    assert "system_prompt" not in json.dumps(catalog)
    assert "instruction" not in json.dumps(catalog)
    resources = 0
    for entry in [*catalog["roles"], *catalog["skills"]]:
        detail = store.detail(entry["ref"], editable=True)
        resources += len(detail["files"])
        assert detail["ref"] == entry["ref"]
    assert resources == 357


def test_skill_fork_keeps_reference_closure_and_retains_all_old_versions(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library")
    original = _ref(store, "skill", "web-frontend-engineering")
    detail = store.detail(original, editable=True)
    first = store.save(
        kind="skill",
        id="our-frontend",
        content={**detail["content"], "instruction": "First method"},
        expected_version=None,
        idempotency_key="fork",
        fork_ref=original,
    )
    assert len(store.detail(first["ref"])["files"]) == len(detail["files"])
    second = store.save(
        kind="skill",
        id="our-frontend",
        content={**first["content"], "instruction": "Second method"},
        expected_version=first["ref"]["version"],
        idempotency_key="edit",
    )
    assert store.read_file(first["ref"])["text"] == "First method"
    assert store.read_file(second["ref"])["text"] == "Second method"
    assert store.read_file(original)["text"] == detail["content"]["instruction"]
    assert len(store.detail(second["ref"])["files"]) == len(detail["files"])


def test_save_is_retry_convergent_and_concurrent_edits_compare_versions(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library")
    args = {
        "kind": "skill",
        "id": "checklist",
        "content": {"name": "Checklist", "description": "", "instruction": "Check the result"},
        "expected_version": None,
        "idempotency_key": "create",
    }
    first = store.save(**args)
    assert store.save(**args) == first
    with pytest.raises(LibraryError, match="another edit"):
        store.save(**{**args, "content": {**args["content"], "instruction": "Different"}})

    def edit(number: int) -> object:
        try:
            return store.save(
                **{
                    **args,
                    "expected_version": first["ref"]["version"],
                    "idempotency_key": f"edit-{number}",
                    "content": {**args["content"], "instruction": f"Edit {number}"},
                }
            )
        except LibraryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, (1, 2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "library_revision_conflict" in results


def test_retained_hire_survives_pack_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LibraryStore(tmp_path / "library")
    role = _ref(store, "role", "frontend-engineer")
    before = store.compose_role(role)
    store.retain(role)
    monkeypatch.setattr(library_module, "_manifest", lambda: {"entries": []})
    assert store.compose_role(role) == before
    assert all(store.read_file(ref)["text"] for ref in store.allowed_skill_refs(role))


def test_corrupt_retained_bytes_refuse_instead_of_falling_back(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library")
    ref = _ref(store, "skill", "web-frontend-engineering")
    store.retain(ref)
    path = store._version_path(library_module.LibraryRef.parse(ref))
    content = json.loads(path.read_text())
    content["files"]["SKILL.md"] += "changed"
    path.write_text(json.dumps(content))
    with pytest.raises(LibraryError):
        store.read_file(ref)


@pytest.mark.parametrize(
    "path", ["../../secret.md", "/etc/secret.md", "references/../SKILL.md", "x\\SKILL.md"]
)
def test_reader_accepts_only_manifest_relative_resources(tmp_path: Path, path: str) -> None:
    store = LibraryStore(tmp_path / "library")
    with pytest.raises(LibraryError):
        store.read_file(_ref(store, "skill", "qa-testing"), path)


def test_library_storage_cannot_enter_workspace_or_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ConfigurationError):
        LibraryStore(repo / "library", workspace_root=repo)
    with pytest.raises(ConfigurationError):
        LibraryStore(tmp_path / "state" / "library", state_root=tmp_path / "state")
    with pytest.raises(ConfigurationError):
        LibraryStore(tmp_path / "base" / "library", state_base=tmp_path / "base")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LibraryError):
        LibraryStore(link)


def test_custom_role_pins_methods_without_authority_fields(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path / "library")
    ref = _ref(store, "role", "frontend-engineer")
    content = store.detail(ref, editable=True)["content"]
    saved = store.save(
        kind="role",
        id="team-frontender",
        content=content,
        expected_version=None,
        idempotency_key="role",
    )
    assert saved["ref"]["source"] == "custom"
    assert store.allowed_skill_refs(saved["ref"]) == store.allowed_skill_refs(ref)
    with pytest.raises(LibraryError):
        store.save(
            kind="role",
            id="authority",
            content={**content, "profile_id": "claude-builder"},
            expected_version=None,
            idempotency_key="bad",
        )
    with pytest.raises(LibraryError):
        validate_library_ref({**ref, "version": "latest"})


def test_hiring_same_specialization_separates_provider_and_preserves_exact_refs(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    store = LibraryStore(tmp_path / "library", workspace_root=manager.repo_root)
    ref = _ref(store, "role", "frontend-engineer")
    roles = []
    for provider in ("claude", "codex"):
        created = manager.create_agent(
            name=f"{provider} Frontender",
            profile_id=f"{provider}-builder",
            rationale="Implement the frontend",
            specialization_ref=ref,
            library_store=store,
            idempotency_key=provider,
        )
        event = manager.show_event(created["event_id"])["event"]
        assert event["payload_schema"] == "commons.payload.agent.v2"
        assert "system_prompt" not in json.dumps(event)
        record = manager.get_agent(created["entity_ref"]["id"])
        assert dict(record["specialization_ref"]) == ref
        assert all(value == "deny" for value in record["grants"].values())
        roles.append(record)
    assert roles[0]["id"] != roles[1]["id"]
    assert manager.snapshot().semantics_required == 5
    with pytest.raises(LifecycleConflictError):
        manager.create_agent(
            name="Ambiguous preset",
            profile_id="codex-builder",
            rationale="No",
            template=True,
            specialization_ref=ref,
            library_store=store,
            idempotency_key="no-preset",
        )
    for choices in ({"skills": ["commons-start"]}, {"tool_allowlist": ["commons_repo_read"]}):
        with pytest.raises(ValidationError):
            manager.create_agent(
                name="Invalid mixed role",
                profile_id="codex-builder",
                rationale="Refuse mix",
                specialization_ref=ref,
                library_store=store,
                idempotency_key="mixed-" + next(iter(choices)),
                **choices,
            )
    with pytest.raises(ValidationError):
        manager.reconfigure_agent(
            roles[0]["id"],
            roles[0]["revision"],
            changes={"tool_allowlist": ["commons_repo_read"]},
            reason="Cannot remove methods",
            idempotency_key="remove-reader",
        )


def test_legacy_hires_keep_v1_and_do_not_raise_reader_floor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    created = manager.create_agent(
        name="Legacy",
        profile_id="codex-builder",
        rationale="Existing flow",
        idempotency_key="legacy",
    )
    event = manager.show_event(created["event_id"])["event"]
    assert event["payload_schema"] == "commons.payload.agent.v1"
    assert "specialization_ref" not in event["payload"]
    assert manager.snapshot().semantics_required < 5


def test_missing_exact_method_fails_before_provider_probe_child_or_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_commons.services.delegation_runtime import DelegationRuntimeService

    manager = _manager(tmp_path)
    store = LibraryStore(tmp_path / "library")
    ref = _ref(store, "role", "frontend-engineer")
    hired = manager.create_agent(
        name="Frontender",
        profile_id="codex-builder",
        rationale="Test early refusal",
        specialization_ref=ref,
        library_store=store,
        idempotency_key="hire",
    )
    task = manager.create_task(
        title="A browser fix",
        description="A bounded implementation",
        acceptance_criteria=("fixed",),
        idempotency_key="task",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        on_behalf_of_agent_id=hired["entity_ref"]["id"],
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="delegation",
    )
    runtime = DelegationRuntimeService(manager, library_store=store)
    sessions_before = manager.sessions.list_sessions(active_only=False)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider probe must not run")

    monkeypatch.setattr(runtime, "_assert_provider_initialized", unexpected)
    monkeypatch.setattr(runtime, "_assert_provider_authenticated", unexpected)
    method = store.allowed_skill_refs(ref)[-1]
    path = store._version_path(library_module.LibraryRef.parse(method))
    changed = json.loads(path.read_text())
    changed["content"]["instruction"] = "Corrupted retained method"
    path.write_text(json.dumps(changed))
    with pytest.raises(LibraryError):
        runtime.run(
            delegation["entity_ref"]["id"], delegation["revision"], idempotency_key="launch"
        )
    assert runtime.attempts.list_attempts() == ()
    assert manager.sessions.list_sessions(active_only=False) == sessions_before


def test_unroled_bound_child_cannot_hire_specialization_through_service_or_raw_event(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    store = LibraryStore(tmp_path / "library")
    ref = _ref(store, "role", "frontend-engineer")
    legacy = manager.create_agent(
        name="Legacy",
        profile_id="codex-builder",
        rationale="Existing operator role",
    )
    task = manager.create_task(
        title="Worker task",
        description="A delegation without a standing role.",
        acceptance_criteria=("Perform the scoped work.",),
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
    )
    child_session = manager.start_session(
        stable_instance_id="unroled-library-worker",
        principal="worker",
        client="codex",
        software="test",
        role="builder",
    )
    manager.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child_session["session_id"],
        attempt=1,
    )
    child = CommonsManager(
        manager.repo_root,
        session_id=child_session["session_id"],
        state_root=manager.paths.state_root,
    )
    before = len(list(manager.events.iter_events()))
    with pytest.raises(LifecycleConflictError, match="unbound human operator"):
        child.create_agent(
            name="False human hire",
            profile_id="codex-builder",
            rationale="Must refuse",
            specialization_ref=ref,
            library_store=store,
            idempotency_key="refused-hire",
        )
    assert not store.root.exists()
    payload = dict(manager.show_event(legacy["event_id"])["event"]["payload"])
    payload.update(agent_id="agent." + "1" * 26, specialization_ref=ref)
    with pytest.raises(LifecycleConflictError, match="unbound human operator"):
        child.record_event("agent.created", payload, idempotency_key="refused-raw-hire")
    assert len(list(manager.events.iter_events())) == before
    assert manager.snapshot().semantics_required < 5
