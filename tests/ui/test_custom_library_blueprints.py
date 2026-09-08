from __future__ import annotations

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_commons.library import LibraryError, LibraryStore
from agent_commons.library_blueprint_store import BlueprintStore
from agent_commons.ui.library_blueprints import (
    apply_blueprint,
    blueprint_catalog,
    register_blueprint_reads,
)
from agent_commons.ui.library_routes import register_library_routes
from tests.ui.test_library_blueprints import _selection


def custom_body(store, *, id="our-workflow", key="create"):
    source = blueprint_catalog(store)["blueprints"][0]
    return {
        "id": id,
        "expected_version": None,
        "idempotency_key": key,
        "fork_ref": {"source": "builtin", "id": source["id"], "version": source["version"]},
        "definition": {
            field: copy.deepcopy(source[field])
            for field in ("name", "description", "slots", "tasks")
        },
    }


def test_custom_apply_retries_original_graph_after_edit_archive_and_reopen(
    writable, tmp_path, monkeypatch
):
    writable._library_root = tmp_path / "library"
    store = writable.library_store()
    blueprints = BlueprintStore(store)
    create = custom_body(store)
    # Custom definitions need not arrive in execution order.
    create["definition"]["tasks"].reverse()
    original = blueprints.save(create)
    assert blueprints.save(create) == original
    body = _selection(original)
    manager = writable.writer()
    monkeypatch.setattr(writable, "writer", lambda: manager)
    original_create = manager.create_task
    calls = 0

    def interrupt(**fields):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return original_create(**fields)

    monkeypatch.setattr(manager, "create_task", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        apply_blueprint(writable, original["id"], body)
    monkeypatch.setattr(manager, "create_task", original_create)
    edit = {key: value for key, value in create.items() if key != "fork_ref"}
    edit = copy.deepcopy(edit)
    edit.update(expected_version=original["version"], idempotency_key="edit")
    edit["definition"]["tasks"][0]["title"]["en"] = "Changed later"
    updated = blueprints.save(edit)
    blueprints.archive(
        original["id"],
        {"expected_version": updated["version"], "archived": True, "idempotency_key": "archive"},
    )
    assert not BlueprintStore(LibraryStore(store.root)).catalog()
    result = apply_blueprint(writable, original["id"], body)
    assert apply_blueprint(writable, original["id"], body) == result
    snapshot = manager.snapshot()
    assert len(snapshot.agents) == len(original["slots"])
    assert len(snapshot.tasks) == len(original["tasks"])
    assert not snapshot.delegations
    mapping = {row["node_id"]: row["task_id"] for row in result["tasks"]}
    for node in original["tasks"]:
        task = snapshot.tasks[mapping[node["id"]]]
        assert task["dependencies"] == [mapping[dep] for dep in node["depends_on"]]
        assert "Changed later" not in task["title"]
    role_refs = {slot["role_ref"]["id"]: slot["role_ref"] for slot in original["slots"]}
    assert all(
        role["specialization_ref"] == role_refs[role["specialization_ref"]["id"]]
        for role in snapshot.agents.values()
    )


@pytest.mark.parametrize("fault", ["cycle", "missing", "slot", "duplicate", "role", "criteria"])
def test_invalid_blueprint_dag_or_refs_does_not_publish_head(tmp_path, fault):
    store = LibraryStore(tmp_path / "library")
    body = custom_body(store)
    tasks = body["definition"]["tasks"]
    if fault == "cycle":
        tasks[0]["depends_on"] = [tasks[-1]["id"]]
    elif fault == "missing":
        tasks[0]["depends_on"] = ["absent"]
    elif fault == "slot":
        tasks[0]["slot_id"] = "absent"
    elif fault == "duplicate":
        tasks.append(copy.deepcopy(tasks[0]))
    elif fault == "role":
        body["definition"]["slots"][0]["role_ref"]["version"] = "0" * 64
    else:
        tasks[0]["acceptance_criteria"]["en"] = []
    with pytest.raises(LibraryError):
        BlueprintStore(store).save(body)
    assert not BlueprintStore(store).catalog()


def test_blueprint_http_detail_authority_bounds_and_immutable_builtins(tmp_path):
    store = LibraryStore(tmp_path / "library")
    app = FastAPI()
    enabled = True

    def authorize():
        if not enabled:
            raise LibraryError("library_editor_required", "Editor required.", 403)

    register_blueprint_reads(
        app, store_factory=lambda: store, dependencies=[], authorize_content=authorize
    )
    register_library_routes(
        app,
        dependencies=[],
        write_dependencies=[],
        store_factory=lambda: store,
        authorize_edit=authorize,
        editing_enabled=lambda: enabled,
    )
    client = TestClient(app)
    body = custom_body(store)
    response = client.post("/api/library/blueprints/custom", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()
    path = f"/api/library/blueprints/custom/{plan['id']}/{plan['version']}"
    assert client.get(path).json() == plan
    assert client.post("/api/library/blueprints/custom", json=body).json() == plan
    changed = copy.deepcopy(body)
    changed["definition"]["name"]["en"] = "Different"
    assert client.post("/api/library/blueprints/custom", json=changed).status_code == 409
    builtin = {**body, "id": "web-app", "idempotency_key": "builtin"}
    assert client.post("/api/library/blueprints/custom", json=builtin).status_code == 422
    assert client.post("/api/library/blueprints/custom", content=b"x" * 524289).status_code == 413
    enabled = False
    assert client.post("/api/library/blueprints/custom", json=body).status_code == 403
    assert client.get(path).status_code == 403


def test_blueprint_copy_retry_does_not_reresolve_its_source(tmp_path, monkeypatch):
    store = LibraryStore(tmp_path / "library")
    blueprints = BlueprintStore(store)
    body = custom_body(store)
    first = blueprints.save(body)

    def unavailable(*args, **kwargs):
        raise LibraryError("blueprint_version_unavailable", "Source changed.", 409)

    monkeypatch.setattr(blueprints, "resolve", unavailable)
    assert blueprints.save(body) == first


def test_group_http_routes_return_closed_catalog_and_enforce_editor(tmp_path):
    from tests.ui.test_library_routes import _client

    client = _client(tmp_path)
    before = client.get("/api/library").json()
    body = {
        "id": "our-group",
        "name": {"en": "Our group", "ru": "Наша группа"},
        "expected_revision": before["skill_organization"]["revision"],
        "idempotency_key": "group",
    }
    response = client.post("/api/library/organization/groups", json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    ref = before["skills"][0]["ref"]
    moved = client.post(
        "/api/library/organization/move",
        json={
            "skill": {"source": ref["source"], "id": ref["id"]},
            "group_id": "our-group",
            "expected_revision": created["revision"],
            "idempotency_key": "move",
        },
    )
    assert moved.status_code == 200, moved.text
    assert "instruction" not in moved.text and "system_prompt" not in moved.text
    readonly = _client(tmp_path, editing=False)
    assert readonly.get("/api/library").json()["skill_organization"] == moved.json()
    assert readonly.post("/api/library/organization/groups", json=body).status_code == 404
    assert (
        client.post(
            "/api/library/organization/groups", json={**body, "unexpected": True}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("permission", ["absent", "denied", "allowed", "unavailable"])
def test_custom_blueprint_catalog_has_an_explicit_content_boundary(tmp_path, permission):
    from agent_commons.errors import ConfigurationError

    store = LibraryStore(tmp_path / "library")
    body = custom_body(store)
    marker = "Fixture private workflow instruction"
    body["definition"]["description"] = {"en": marker, "ru": marker}
    body["definition"]["tasks"][0]["description"]["en"] = marker
    body["definition"]["tasks"][0]["acceptance_criteria"]["en"] = [marker]
    body["definition"]["slots"][0]["name"] = marker
    plan = BlueprintStore(store).save(body)
    app = FastAPI()

    def authorize():
        if permission == "denied":
            raise LibraryError("library_editor_required", "Editor required.", 403)
        if permission == "unavailable":
            raise ConfigurationError("Fixture authority check unavailable")

    register_blueprint_reads(
        app,
        store_factory=lambda: store,
        dependencies=[],
        authorize_content=None if permission == "absent" else authorize,
    )
    client = TestClient(app)
    response = client.get("/api/library/blueprints")
    assert response.status_code == 200
    custom = next(row for row in response.json()["blueprints"] if row["source"] == "custom")
    if permission == "allowed":
        assert custom == plan
        assert marker in response.text
    else:
        assert marker not in response.text
        assert custom == {
            "id": plan["id"],
            "source": "custom",
            "version": plan["version"],
            "name": plan["name"],
            "archived": False,
            "slot_count": len(plan["slots"]),
            "task_count": len(plan["tasks"]),
            "content_available": False,
        }
    builtin = next(row for row in response.json()["blueprints"] if row["source"] == "builtin")
    assert builtin["tasks"] and builtin["slots"]
    assert response.headers["cache-control"] == "no-store"


def test_custom_detail_uses_content_authority_independently_of_editing_flag(tmp_path):
    store = LibraryStore(tmp_path / "library")
    plan = BlueprintStore(store).save(custom_body(store))
    builtin = blueprint_catalog(store, include_custom=False)["blueprints"][0]
    allowed = False

    def content_gate():
        if not allowed:
            raise LibraryError("library_content_required", "Content access required.", 403)

    app = FastAPI()
    register_library_routes(
        app,
        dependencies=[],
        write_dependencies=[],
        store_factory=lambda: store,
        authorize_edit=lambda: None,
        authorize_content=content_gate,
        editing_enabled=lambda: False,
        register_writes=False,
    )
    client = TestClient(app)
    path = f"/api/library/blueprints/custom/{plan['id']}/{plan['version']}"
    response = client.get(path)
    assert response.status_code == 403
    assert "tasks" not in response.json()
    builtin_path = f"/api/library/blueprints/builtin/{builtin['id']}/{builtin['version']}"
    assert client.get(builtin_path).status_code == 200
    allowed = True
    assert client.get(path).json() == plan


@pytest.mark.parametrize("retain", [False, True])
def test_retained_builtin_apply_survives_removal_from_current_catalog(
    tmp_path, monkeypatch, retain
):
    store = LibraryStore(tmp_path / "library")
    original = blueprint_catalog(store, include_custom=False)["blueprints"][0]
    BlueprintStore(store).for_apply(original["id"], original["version"], retain=True)
    reopened = BlueprintStore(LibraryStore(store.root))

    def forbidden():
        pytest.fail("Exact retained recovery must not consult the current builtin catalog")

    monkeypatch.setattr(reopened, "_builtins", forbidden)
    assert reopened.for_apply(original["id"], original["version"], retain=retain) == original


def test_retained_custom_apply_does_not_change_source_after_catalog_id_collision(
    tmp_path, monkeypatch
):
    store = LibraryStore(tmp_path / "library")
    blueprints = BlueprintStore(store)
    plan = blueprints.save(custom_body(store))
    blueprints.archive(
        plan["id"],
        {"expected_version": plan["version"], "archived": True, "idempotency_key": "archive"},
    )
    monkeypatch.setattr(blueprints, "_builtins", lambda: [{"id": plan["id"]}])
    resolved = blueprints.for_apply(plan["id"], plan["version"])
    assert resolved["source"] == "custom"
    assert resolved["archived"] is True
    assert resolved["tasks"] == plan["tasks"]


def test_builtin_hashes_keep_the_pre_metadata_identity_formula(tmp_path):
    from agent_commons.core.canonical import canonical_sha256

    plans = blueprint_catalog(LibraryStore(tmp_path / "library"), include_custom=False)[
        "blueprints"
    ]
    assert len(plans) == 5
    for plan in plans:
        original_payload = {
            key: value
            for key, value in plan.items()
            if key not in {"version", "source", "archived"}
        }
        assert plan["version"] == canonical_sha256(original_payload)
