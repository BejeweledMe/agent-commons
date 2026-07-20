from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_commons.core.canonical import canonical_json_file_bytes, load_json_strict
from agent_commons.errors import (
    IdempotencyConflictError,
    ImmutableCollisionError,
    IntegrityError,
    ValidationError,
)
from agent_commons.storage.atomic import atomic_write_immutable
from agent_commons.storage.events import EventStore
from agent_commons.storage.manifests import ManifestStore

from .helpers import event_document, make_kernel, manifest_document


def test_atomic_writer_never_overwrites(tmp_path) -> None:
    path = tmp_path / "immutable.json"
    assert atomic_write_immutable(path, b"one").created
    assert not atomic_write_immutable(path, b"one").created
    with pytest.raises(ImmutableCollisionError):
        atomic_write_immutable(path, b"two")
    assert path.read_bytes() == b"one"


def test_event_append_is_idempotent_conflicting_content_fails(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = EventStore(paths, schemas)
    first = store.append(event_document())
    retry = store.append(event_document())
    assert first.created
    assert not retry.created
    assert retry.event_id == first.event_id
    assert store.get(first.event_id).event == first.event

    with pytest.raises(IdempotencyConflictError):
        store.append(event_document("different text"))


def test_receipt_repairs_interrupted_event_publication(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = EventStore(paths, schemas)
    first = store.append(event_document())
    first.path.unlink()

    repaired = store.append(event_document())
    assert repaired.event_id == first.event_id
    assert repaired.repaired
    assert repaired.path.is_file()


def test_canonical_event_repairs_lost_operational_receipt_without_duplicate(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = EventStore(paths, schemas)
    first = store.append(event_document())
    receipt = store.idempotency.lookup(namespace="tests:note.recorded", key="note-1")
    assert receipt is not None
    receipt.path.unlink()

    retry = store.append(event_document())
    assert retry.event_id == first.event_id
    assert len(list(store.iter_events())) == 1


def test_tampered_idempotency_receipt_fails_closed(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = EventStore(paths, schemas)
    store.append(event_document())
    receipt = store.idempotency.lookup(namespace="tests:note.recorded", key="note-1")
    assert receipt is not None
    body = load_json_strict(receipt.path)
    body["key_digest"] = "0" * 64
    receipt.path.write_bytes(canonical_json_file_bytes(body))
    with pytest.raises(IdempotencyConflictError, match="requested identity"):
        store.append(event_document())


def test_scoped_receipt_store_rejects_symlinked_operational_parent(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    outside = tmp_path / "outside-receipts"
    outside.mkdir()
    (paths.idempotency_v2 / "scopes").symlink_to(outside, target_is_directory=True)
    store = EventStore(paths, schemas)

    with pytest.raises(IdempotencyConflictError, match="must not be a symlink"):
        store.append(event_document())
    assert list(outside.iterdir()) == []


def test_concurrent_identical_append_publishes_one_event(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = EventStore(paths, schemas)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.append(event_document()), range(16)))
    assert len({record.event_id for record in results}) == 1
    assert len(list(store.iter_events())) == 1


def test_event_store_has_no_domain_semantics_but_accepts_injected_policy(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)

    def reject_forbidden(event) -> None:
        if event["payload"]["text"] == "forbidden":
            raise ValidationError("domain policy rejected text")

    store = EventStore(paths, schemas, validators=[reject_forbidden])
    store.append(event_document("allowed"))
    with pytest.raises(ValidationError, match="domain policy"):
        store.append(event_document("forbidden", key="note-2"))


def test_event_store_owns_event_identity_and_timestamp(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    document = event_document()
    document["event_id"] = "evt.00000000000000000000000000"
    with pytest.raises(ValidationError, match="assigned by EventStore"):
        EventStore(paths, schemas).append(document)


def test_canonical_readers_reject_files_outside_their_store(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())
    manifest = manifests.put(manifest_document())
    event_copy = tmp_path / event.path.name
    manifest_copy = tmp_path / manifest.path.name
    event_copy.write_bytes(event.path.read_bytes())
    manifest_copy.write_bytes(manifest.path.read_bytes())
    with pytest.raises(IntegrityError, match="outside canonical storage"):
        events.read_path(event_copy)
    with pytest.raises(IntegrityError, match="outside canonical storage"):
        manifests.read_path(manifest_copy)


def test_stores_reject_semantically_valid_but_noncanonical_json(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    events = EventStore(paths, schemas)
    manifests = ManifestStore(paths, schemas)
    event = events.append(event_document())
    manifest = manifests.put(manifest_document())
    receipt = events.idempotency.lookup(namespace="tests:note.recorded", key="note-1")
    assert receipt is not None

    event.path.write_bytes(event.path.read_bytes() + b"\n")
    manifest.path.write_bytes(manifest.path.read_bytes() + b"\n")
    receipt.path.write_bytes(receipt.path.read_bytes() + b"\n")

    with pytest.raises(IntegrityError, match="not canonical JSON"):
        events.read_path(event.path)
    with pytest.raises(IntegrityError, match="not canonical JSON"):
        manifests.read_path(manifest.path)
    with pytest.raises(IdempotencyConflictError, match="not canonical JSON"):
        events.idempotency.lookup(namespace="tests:note.recorded", key="note-1")


def test_event_actor_accepts_registered_session_context(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    document = event_document(key="session-context")
    document["actor"] = {
        "principal_id": "person.owner",
        "session_id": "session.01",
        "stable_instance_id": "codex-thread-01",
        "client": "codex",
        "software": "codex-cli",
        "model_family": "gpt",
        "model": "gpt-test",
        "role_id": "builder",
        "capabilities": ["python", "review"],
        "source_producer": {
            "client": "claude-code",
            "software": "claude-cli",
            "model_family": "claude",
            "model": None,
            "principal": None,
            "external_session_id": None,
        },
    }
    assert EventStore(paths, schemas).append(document).event["actor"] == document["actor"]


def test_content_addressed_manifest_round_trip_and_schema_kind(tmp_path) -> None:
    paths, schemas = make_kernel(tmp_path)
    store = ManifestStore(paths, schemas)
    first = store.put(manifest_document())
    retry = store.put(manifest_document())
    assert first.manifest_id == retry.manifest_id
    assert first.created and not retry.created
    assert store.get(first.manifest_id).manifest == first.manifest

    invalid = manifest_document()
    invalid["kind"] = "wrong_kind"
    with pytest.raises(ValidationError, match="is for"):
        store.put(invalid)


def test_payload_schema_can_validate_an_explicit_event_family(tmp_path) -> None:
    paths, _ = make_kernel(tmp_path)
    family_root = tmp_path / "family-schemas"
    family_root.mkdir()
    family_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:agent-commons:test:event-payload:item-family:v1",
        "x-schema-name": "commons.event_payload.item_family.v1",
        "x-event-types": ["item.created", "item.revised"],
        "type": "object",
        "additionalProperties": False,
        "required": ["item_id"],
        "properties": {"item_id": {"type": "string", "minLength": 1}},
    }
    (family_root / "family.json").write_text(json.dumps(family_schema), encoding="utf-8")
    from agent_commons.core.schema_registry import SchemaRegistry

    store = EventStore(paths, SchemaRegistry([family_root]))
    base = event_document(key="family-created")
    base.update(
        {
            "payload_schema": "commons.event_payload.item_family.v1",
            "event_type": "item.created",
            "payload": {"item_id": "item.1"},
        }
    )
    store.append(base)
    wrong = {**base, "event_type": "item.deleted", "idempotency_key": "family-deleted"}
    with pytest.raises(ValidationError, match="permits"):
        store.append(wrong)
