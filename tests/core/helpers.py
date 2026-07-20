from __future__ import annotations

import json
from pathlib import Path

from agent_commons.config import CommonsPaths
from agent_commons.core.ids import stable_id
from agent_commons.core.schema_registry import SchemaRegistry

EVENT_PAYLOAD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:agent-commons:test:event-payload:note-recorded:v1",
    "x-schema-name": "commons.event_payload.note.recorded.v1",
    "x-event-type": "note.recorded",
    "type": "object",
    "additionalProperties": False,
    "required": ["note_id", "text"],
    "properties": {
        "note_id": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1},
        "target_ref": {"$ref": "urn:agent-commons:schema:common:v1#/$defs/typedRef"},
    },
}

MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:agent-commons:test:manifest:document:v1",
    "x-schema-name": "commons.manifest.test_document.v1",
    "x-manifest-kind": "test_document",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "kind", "title", "content"],
    "properties": {
        "schema": {"const": "commons.manifest.test_document.v1"},
        "kind": {"const": "test_document"},
        "title": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
        "related_ref": {"$ref": "urn:agent-commons:schema:common:v1#/$defs/typedRef"},
    },
}


def make_kernel(tmp_path: Path) -> tuple[CommonsPaths, SchemaRegistry]:
    schema_root = tmp_path / "schemas"
    schema_root.mkdir(parents=True)
    (schema_root / "event.json").write_text(json.dumps(EVENT_PAYLOAD_SCHEMA), encoding="utf-8")
    (schema_root / "manifest.json").write_text(json.dumps(MANIFEST_SCHEMA), encoding="utf-8")
    paths = CommonsPaths.for_workspace(
        tmp_path / "project",
        state_root=tmp_path / "state",
    )
    paths.ensure_layout()
    return paths, SchemaRegistry([schema_root])


def workspace_id() -> str:
    return stable_id("workspace", "test-workspace")


def event_document(text: str = "hello", *, key: str = "note-1") -> dict:
    return {
        "schema": "commons.event.v1",
        "payload_schema": "commons.event_payload.note.recorded.v1",
        "workspace_id": workspace_id(),
        "event_type": "note.recorded",
        "actor": {
            "principal_id": "principal.test",
            "session_id": "session.test",
            "role_id": "builder",
            "software": "test-agent",
            "model_family": "test-family",
        },
        "subject_refs": [{"kind": "note", "id": "note.1"}],
        "idempotency_namespace": "tests:note.recorded",
        "idempotency_key": key,
        "relations": [],
        "tags": ["test"],
        "provenance": {
            "writer": "tests",
            "writer_version": "1",
            "source_kind": "manual",
            "source_refs": [],
        },
        "payload": {"note_id": "note.1", "text": text},
    }


def manifest_document(*, related_ref: dict | None = None) -> dict:
    document = {
        "schema": "commons.manifest.test_document.v1",
        "kind": "test_document",
        "title": "A document",
        "content": "immutable content",
    }
    if related_ref is not None:
        document["related_ref"] = related_ref
    return document
