from __future__ import annotations


def test_group_archive_route_moves_members_restores_and_is_closed_to_readers(tmp_path):
    from tests.ui.test_library_routes import _client

    client = _client(tmp_path)
    before = client.get("/api/library").json()
    created = client.post(
        "/api/library/organization/groups",
        json={
            "id": "our-group",
            "name": {"en": "Our group", "ru": "Наша группа"},
            "expected_revision": before["skill_organization"]["revision"],
            "idempotency_key": "group",
        },
    ).json()
    ref = before["skills"][0]["ref"]
    moved = client.post(
        "/api/library/organization/move",
        json={
            "skill": {"source": ref["source"], "id": ref["id"]},
            "group_id": "our-group",
            "expected_revision": created["revision"],
            "idempotency_key": "move",
        },
    ).json()
    archive = "/api/library/organization/groups/our-group/archive"
    without_target = client.post(
        archive,
        json={"expected_revision": moved["revision"], "archived": True, "idempotency_key": "a1"},
    )
    assert without_target.status_code == 422, without_target.text
    assert client.post(archive, json={"archived": True, "unexpected": 1}).status_code == 422
    archived = client.post(
        archive,
        json={
            "expected_revision": moved["revision"],
            "archived": True,
            "move_to_group_id": "application",
            "idempotency_key": "a2",
        },
    )
    assert archived.status_code == 200, archived.text
    catalog = archived.json()
    row = next(group for group in catalog["groups"] if group["id"] == "our-group")
    assert row["archived"] is True
    assert all(
        group["archived"] is False for group in catalog["groups"] if group["id"] != "our-group"
    )
    assert not any(item["group_id"] == "our-group" for item in catalog["assignments"])
    stale = client.post(
        archive,
        json={"expected_revision": moved["revision"], "archived": False, "idempotency_key": "a3"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "library_revision_conflict"
    into_archived = client.post(
        "/api/library/organization/move",
        json={
            "skill": {"source": ref["source"], "id": ref["id"]},
            "group_id": "our-group",
            "expected_revision": catalog["revision"],
            "idempotency_key": "move-archived",
        },
    )
    assert into_archived.status_code == 422
    restored = client.post(
        archive,
        json={"expected_revision": catalog["revision"], "archived": False, "idempotency_key": "a4"},
    )
    assert restored.status_code == 200, restored.text
    assert next(g for g in restored.json()["groups"] if g["id"] == "our-group")["archived"] is False
    readonly = _client(tmp_path, editing=False)
    assert readonly.get("/api/library").json()["skill_organization"] == restored.json()
    # The read-only panel registers no write here; the GET detail route with four
    # path segments shadows the path, so the refusal is 405 rather than 404.
    assert readonly.post(archive, json={"archived": True}).status_code in (404, 405)
    builtin = client.post(
        "/api/library/organization/groups/application/archive",
        json={
            "expected_revision": restored.json()["revision"],
            "archived": True,
            "idempotency_key": "a5",
        },
    )
    assert builtin.status_code == 422
