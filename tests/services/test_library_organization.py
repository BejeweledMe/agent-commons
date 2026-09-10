from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_commons.library import LibraryError, LibraryStore
from agent_commons.library_organization import SkillOrganization


def test_groups_survive_reopen_and_moves_leave_exact_skill_hashes_unchanged(tmp_path):
    store = LibraryStore(tmp_path / "library")
    before = store.catalog()
    assert not store.root.exists()
    original = before["skills"][0]["ref"]
    org = SkillOrganization(store)
    body = {
        "id": "our-methods",
        "name": {"en": "Our methods", "ru": "Наши методы"},
        "expected_revision": before["skill_organization"]["revision"],
        "idempotency_key": "new-group",
    }
    created = org.save("groups", body)
    assert any(row["id"] == "our-methods" for row in created["groups"])
    assert not any(row["group_id"] == "our-methods" for row in created["assignments"])
    moved = org.save(
        "move",
        {
            "skill": {"source": original["source"], "id": original["id"]},
            "group_id": "our-methods",
            "expected_revision": created["revision"],
            "idempotency_key": "move",
        },
    )
    reopened = LibraryStore(store.root).catalog()
    assert reopened["skill_organization"] == moved
    assert reopened["skills"] == before["skills"]
    assert store.detail(original)["ref"] == original
    assert org.save("groups", body) == created
    with pytest.raises(LibraryError, match="another edit"):
        org.save("groups", {**body, "name": {"en": "Changed", "ru": "Изменено"}})
    with pytest.raises(LibraryError, match="changed"):
        org.save("groups", {**body, "idempotency_key": "stale"})


def test_group_writes_compare_revisions_and_reject_builtin_edits(tmp_path):
    store = LibraryStore(tmp_path / "library")
    revision = store.catalog()["skill_organization"]["revision"]

    def create(number):
        try:
            return SkillOrganization(store).save(
                "groups",
                {
                    "id": f"group-{number}",
                    "name": {"en": "Group", "ru": "Группа"},
                    "expected_revision": revision,
                    "idempotency_key": f"operation-{number}",
                },
            )
        except LibraryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "library_revision_conflict" in results
    org = SkillOrganization(store)
    current = store.catalog()["skill_organization"]["revision"]
    with pytest.raises(LibraryError, match="immutable"):
        org.save(
            "groups",
            {
                "id": "ungrouped",
                "name": {"en": "Changed", "ru": "Изменено"},
                "expected_revision": current,
                "idempotency_key": "builtin",
            },
        )
    with pytest.raises(LibraryError, match="unavailable"):
        org.save(
            "move",
            {
                "skill": {"source": "custom", "id": "missing"},
                "group_id": "ungrouped",
                "expected_revision": current,
                "idempotency_key": "missing",
            },
        )


def test_custom_group_archive_moves_members_restores_and_reads_legacy_shape(tmp_path):
    store = LibraryStore(tmp_path / "library")
    org = SkillOrganization(store)
    initial = store.catalog()["skill_organization"]
    first = org.save(
        "groups",
        {
            "id": "old",
            "name": {"en": "Old", "ru": "Старая"},
            "expected_revision": initial["revision"],
            "idempotency_key": "old",
        },
    )
    second = org.save(
        "groups",
        {
            "id": "new",
            "name": {"en": "New", "ru": "Новая"},
            "expected_revision": first["revision"],
            "idempotency_key": "new",
        },
    )
    ref = store.catalog()["skills"][0]["ref"]
    skill = {"source": ref["source"], "id": ref["id"]}
    moved = org.save(
        "move",
        {
            "skill": skill,
            "group_id": "old",
            "expected_revision": second["revision"],
            "idempotency_key": "move",
        },
    )
    with pytest.raises(LibraryError, match="has members"):
        org.archive(
            "old",
            {
                "expected_revision": moved["revision"],
                "archived": True,
                "idempotency_key": "missing-target",
            },
        )
    archived = org.archive(
        "old",
        {
            "expected_revision": moved["revision"],
            "archived": True,
            "move_to_group_id": "new",
            "idempotency_key": "archive",
        },
    )
    assert next(group for group in archived["groups"] if group["id"] == "old")["archived"] is True
    assert (
        next(item for item in archived["assignments"] if item["id"] == ref["id"])["group_id"]
        == "new"
    )
    assert (
        org.archive(
            "old",
            {
                "expected_revision": archived["revision"],
                "archived": False,
                "idempotency_key": "restore",
            },
        )["revision"]
        != archived["revision"]
    )


def test_organization_legacy_sidecar_loads(tmp_path):
    store = LibraryStore(tmp_path / "library")
    store.root.mkdir(parents=True)
    (store.root / "skill-organization.json").write_text(
        '{"schema":"agent_commons.skill-organization-state.v1","groups":{},"assignments":{},"receipts":{}}'
    )
    assert SkillOrganization(store).catalog(store.catalog()["skills"])["groups"]
