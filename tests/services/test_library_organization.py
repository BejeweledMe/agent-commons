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
