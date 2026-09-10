"""Parity for the two former Starter Pack examples after their migration.

The former packs (starter.feature-delivery.mock, starter.product-discovery.mock)
became the built-in blueprints ``feature-delivery`` and ``product-discovery``.
Their roles map to built-in library specializations; the commons-* workflow
skills the packs named are runtime skills that a specialization agent never
carries (domain rule in ``validate_specialization_selection``): every launched
worker is told to use ``commons-start`` by the delegation instruction instead.
"""

from __future__ import annotations

import pytest

from agent_commons.ui.library_blueprints import apply_blueprint, blueprint_catalog
from tests.ui.test_library_blueprints import _selection


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (
            "feature-delivery",
            {
                "roles": ("delivery-tech-lead", "qa-engineer"),
                "tasks": 2,
            },
        ),
        (
            "product-discovery",
            {
                "roles": ("product-manager", "strategy-advisor"),
                "tasks": 2,
            },
        ),
    ],
)
def test_migrated_blueprint_preserves_literal_role_parity(writable, tmp_path, identifier, expected):
    writable._library_root = tmp_path / "library"
    plan = next(
        item
        for item in blueprint_catalog(writable.library_store())["blueprints"]
        if item["id"] == identifier
    )
    result = apply_blueprint(writable, identifier, _selection(plan))
    snapshot = writable.writer().snapshot()
    assert len(result["roles"]) == len(expected["roles"])
    assert len(result["tasks"]) == expected["tasks"]
    assert tuple(slot["id"] for slot in plan["slots"]) == expected["roles"]
    for slot in plan["slots"]:
        agent_id = next(row["agent_id"] for row in result["roles"] if row["slot_id"] == slot["id"])
        agent = snapshot.agents[agent_id]
        assert agent["specialization_ref"] == slot["role_ref"]
        assert list(agent.get("skills", [])) == [], (
            "a specialization agent carries no runtime skills"
        )
        assert agent["grants"] == {
            "create_roles": "deny",
            "retire_roles": "deny",
            "open_links": "deny",
        }


def test_launched_workers_are_still_told_to_use_the_commons_workflow_skill() -> None:
    import inspect

    from agent_commons.services import delegation_instruction

    assert "use commons-start" in inspect.getsource(delegation_instruction)


def test_former_starter_pack_paths_are_gone_and_refuse_without_writing(
    writable, writable_client
) -> None:
    from conftest import authorized

    from agent_commons.ui.context import ledger_fingerprint

    before = ledger_fingerprint(writable.paths())
    listing = writable_client.get("/api/work/starter-packs", headers=authorized())
    assert listing.status_code == 404, listing.text
    apply = writable_client.post(
        "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/feature-delivery/apply",
        json={"confirmed": True, "idempotency_key": "former-route"},
        headers=authorized(),
    )
    assert apply.status_code in (404, 405), apply.text
    assert ledger_fingerprint(writable.paths()) == before, "a removed route never writes"
