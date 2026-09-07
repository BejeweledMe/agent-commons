from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.services import CommonsManager


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[CommonsManager, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=())
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    session = manager.start_session(
        stable_instance_id="artifact-expected-bytes-12345678",
        principal="operator",
        client="codex",
        software="tests",
        role="builder",
    )
    manager.session_id = session["session_id"]
    path = repo / "selected-image.png"
    path.write_bytes(b"original content")
    return manager, path


def revision(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_expected_content_registers_one_manifest_and_retries_exactly(workspace) -> None:
    manager, source = workspace
    expected = {
        "expected_revision": revision(source.read_bytes()),
        "expected_size": source.stat().st_size,
    }
    event = manager.register_artifact(source, **expected, idempotency_key="exact-content")
    retry = manager.register_artifact(source, **expected, idempotency_key="exact-content")
    assert event["event_id"] == retry["event_id"]
    bundle = manager.get_artifact_bundle(event["entity_ref"]["id"])
    assert bundle["manifest"]["revision"] == expected["expected_revision"]
    assert bundle["manifest"]["size_bytes"] == expected["expected_size"]
    assert len(manager.snapshot().artifacts) == 1


@pytest.mark.parametrize("content", [b"different bytes!", b"different longer content"])
def test_replaced_source_is_refused_before_manifest_or_event_publication(
    workspace, content: bytes
) -> None:
    manager, source = workspace
    selected = source.read_bytes()
    source.write_bytes(content)
    with pytest.raises(IntegrityError, match="expected content"):
        manager.register_artifact(
            source,
            expected_revision=revision(selected),
            expected_size=len(selected),
            idempotency_key="replacement",
        )
    assert not manager.snapshot().artifacts
    assert not list(manager.manifests.iter_manifests())


def test_size_must_match_even_when_hash_is_correct(workspace) -> None:
    manager, source = workspace
    with pytest.raises(IntegrityError, match="expected content"):
        manager.register_artifact(
            source,
            expected_revision=revision(source.read_bytes()),
            expected_size=0,
            idempotency_key="size-mismatch",
        )
    assert not manager.snapshot().artifacts


@pytest.mark.parametrize(
    "expectations",
    [
        {"expected_revision": "latest", "expected_size": 16},
        {"expected_revision": "sha256:" + "A" * 64, "expected_size": 16},
        {"expected_revision": "sha256:" + "0" * 64},
        {"expected_size": 16},
        {"expected_revision": "sha256:" + "0" * 64, "expected_size": True},
        {"expected_revision": "sha256:" + "0" * 64, "expected_size": -1},
    ],
)
def test_malformed_expectations_refuse_without_registration(workspace, expectations: dict) -> None:
    manager, source = workspace
    with pytest.raises(ValidationError):
        manager.register_artifact(source, **expectations, idempotency_key="invalid-expectation")
    assert not manager.snapshot().artifacts
