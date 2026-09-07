"""The picker exposes eligible exact identities, never generic source records."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_commons.core.ids import stable_id
from agent_commons.domain.collections import collection_for
from agent_commons.domain.context_pack import ContextPackDraft, ContextPackRefusal
from agent_commons.domain.snapshot import ProjectSnapshot
from agent_commons.services.context_packs import ContextPackCommands
from agent_commons.ui.context_source_dtos import (
    MAX_CONTEXT_SOURCES,
    SOURCE_KINDS,
    context_source_catalog_payload,
)
from tests.ui.conftest import authorized, tree_digest


def source(snapshot: ProjectSnapshot, kind: str, seed: str, **fields: Any) -> dict[str, Any]:
    identifier = stable_id(kind, seed)
    record = {
        "id": identifier,
        "revision": stable_id("evt", seed),
        "title": "A bounded source title",
        **fields,
    }
    getattr(snapshot, str(collection_for(kind)))[identifier] = record
    return record


@pytest.mark.parametrize("kind", sorted(SOURCE_KINDS))
def test_catalog_returns_each_eligible_kind_as_closed_exact_metadata(kind: str) -> None:
    snapshot = ProjectSnapshot()
    manifest = stable_id("manifest", "source")
    snapshot.known_manifest_ids.add(manifest)
    current = stable_id("evt", "corrected-source")
    record = source(
        snapshot,
        kind,
        "source",
        effective_revision=current,
        classification="internal",
        manifest_ref=manifest,
        path="PRIVATE_PATH_SENTINEL",
        body="PRIVATE_BODY_SENTINEL",
        runtime_instruction="PRIVATE_INSTRUCTION_SENTINEL",
        provider_output="PRIVATE_OUTPUT_SENTINEL",
        actor={"session_id": "PRIVATE_SESSION_SENTINEL"},
    )
    payload = context_source_catalog_payload(snapshot)
    assert payload == {
        "schema": "agent-commons.ui.context-sources.v1",
        "state": "ready",
        "sources": [
            {
                "ref": {"kind": kind, "id": record["id"], "revision": current},
                "label": "A bounded source title",
                "freshness": "current",
            }
        ],
        "truncated": False,
    }
    assert "PRIVATE_" not in json.dumps(payload)


def test_catalog_excludes_restricted_missing_manifest_and_malformed_sources() -> None:
    snapshot = ProjectSnapshot()
    manifest = stable_id("manifest", "known")
    snapshot.known_manifest_ids.add(manifest)
    source(snapshot, "artifact", "restricted", classification="restricted", manifest_ref=manifest)
    source(snapshot, "artifact", "missing", classification="internal")
    source(snapshot, "task", "invalid-revision", revision="evt.not-exact")
    snapshot.tasks["task.invalid"] = {"revision": stable_id("evt", "valid")}
    assert context_source_catalog_payload(snapshot) == {
        "schema": "agent-commons.ui.context-sources.v1",
        "state": "empty",
        "sources": [],
        "truncated": False,
    }


def test_catalog_bounds_and_sorts_eligible_rows_without_truncating_at_ineligible_rows() -> None:
    snapshot = ProjectSnapshot()
    source(snapshot, "artifact", "ineligible", classification="restricted")
    ids = [source(snapshot, "task", str(index))["id"] for index in range(MAX_CONTEXT_SOURCES)]
    full = context_source_catalog_payload(snapshot)
    assert len(full["sources"]) == MAX_CONTEXT_SOURCES
    assert full["truncated"] is False
    source(snapshot, "task", "extra")
    bounded = context_source_catalog_payload(snapshot)
    assert bounded["truncated"] is True
    assert len(bounded["sources"]) == MAX_CONTEXT_SOURCES
    assert [row["ref"]["id"] for row in full["sources"]] == sorted(ids)
    assert [row["ref"]["id"] for row in bounded["sources"]] == sorted(snapshot.tasks)[:256]


def test_catalog_labels_are_printable_utf8_bounded_and_never_use_paths() -> None:
    snapshot = ProjectSnapshot()
    task = source(snapshot, "task", "unicode", title="\n\u202e\x00" + "界" * 500)
    artifact = source(
        snapshot,
        "artifact",
        "path",
        title="\x00\n",
        path="PRIVATE_PATH_SENTINEL",
        manifest_ref=stable_id("manifest", "known"),
    )
    snapshot.known_manifest_ids.add(artifact["manifest_ref"])
    rows = {row["ref"]["id"]: row for row in context_source_catalog_payload(snapshot)["sources"]}
    assert rows[artifact["id"]]["label"] == artifact["id"]
    label = rows[task["id"]]["label"]
    assert label and label.isprintable() and len(label.encode("utf-8")) <= 256
    assert label == "界" * 85


def test_catalog_refresh_does_not_authorize_a_previously_selected_revision() -> None:
    snapshot = ProjectSnapshot()
    record = source(snapshot, "task", "mutable")
    selected = context_source_catalog_payload(snapshot)["sources"][0]["ref"]
    draft = ContextPackDraft.from_payload(
        {
            "summary": "A baseline",
            "facts": [
                {
                    "statement": "An exact fact",
                    "source_refs": [
                        {
                            "ref": {"kind": selected["kind"], "id": selected["id"]},
                            "revision": selected["revision"],
                        }
                    ],
                }
            ],
            "decision_refs": [],
            "open_questions": [],
        }
    )
    ContextPackCommands.validate_sources(draft, snapshot)
    record["effective_revision"] = stable_id("evt", "later")
    assert context_source_catalog_payload(snapshot)["sources"][0]["ref"] != selected
    with pytest.raises(ContextPackRefusal) as refusal:
        ContextPackCommands.validate_sources(draft, snapshot)
    assert refusal.value.code == "context_pack_stale"
    snapshot.tasks.clear()  # An invalidated source disappears from the current snapshot.
    assert context_source_catalog_payload(snapshot)["state"] == "empty"


def test_source_route_is_authenticated_read_only_and_has_no_canonical_side_effect(
    client, writable_client, writable, workspace
) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/work/context-sources").status_code == 401
    created = writable.writer().create_task(
        title="Recognizable task", description="Not transported", acceptance_criteria=["Done"]
    )
    before = tree_digest(workspace["commons_root"])
    for session_client in (client, writable_client):
        response = session_client.get("/api/work/context-sources", headers=authorized())
        assert response.status_code == 200
        rows = response.json()["sources"]
        assert rows == [
            {
                "ref": {
                    "kind": "task",
                    "id": created["entity_ref"]["id"],
                    "revision": created["revision"],
                },
                "label": "Recognizable task",
                "freshness": "current",
            }
        ]
        assert "Not transported" not in response.text
    assert tree_digest(workspace["commons_root"]) == before
