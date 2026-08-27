from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_commons.integrations.starter_packs import (
    StarterPackValidationError,
    get_bundled_pack,
    list_bundled_packs,
    parse_manifest_bytes,
)
from agent_commons.integrations.starter_packs.bundled import load_bundled_packs_from_directory
from agent_commons.integrations.starter_packs.manifest import (
    MAX_MANIFEST_BYTES,
    MAX_RUNTIME_INSTRUCTION_BYTES,
)


def test_bundled_registry_exposes_exactly_two_verified_mock_metadata_entries() -> None:
    packs = list_bundled_packs()

    assert [pack.id for pack in packs] == [
        "starter.feature-delivery.mock",
        "starter.product-discovery.mock",
    ]
    assert all(pack.version == "0.1.0" for pack in packs)
    assert all(
        role.fresh_context
        for pack in packs
        for blueprint in pack.blueprints
        for role in blueprint.roles
    )
    assert get_bundled_pack("starter.feature-delivery.mock") == packs[0]
    with pytest.raises(StarterPackValidationError, match="starter_pack_not_found"):
        get_bundled_pack("starter.unknown.mock")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda document: document.__setitem__("unexpected", True),
            "starter_pack_manifest_unknown_field",
        ),
        (
            lambda document: document["files"][0].__setitem__("path", "../outside.md"),
            "starter_pack_payload_path_invalid",
        ),
        (
            lambda document: document["blueprints"][0]["roles"][0].__setitem__(
                "runtime_instruction", "x" * (MAX_RUNTIME_INSTRUCTION_BYTES + 1)
            ),
            "starter_pack_instruction_too_large",
        ),
    ],
)
def test_parser_fails_closed_for_unknown_paths_and_oversized_instruction(
    mutate: object, code: str
) -> None:
    document = _manifest("starter.feature-delivery.mock", "payloads/example.md", b"example\n")
    mutate(document)  # type: ignore[operator]

    with pytest.raises(StarterPackValidationError, match=code):
        parse_manifest_bytes(json.dumps(document).encode("utf-8"))


def test_parser_rejects_duplicate_keys_and_oversized_manifest_before_interpretation() -> None:
    duplicate = b'{"format":"agent-commons.starter-pack.v1","format":"other"}'

    with pytest.raises(StarterPackValidationError, match="starter_pack_manifest_invalid"):
        parse_manifest_bytes(duplicate)
    with pytest.raises(StarterPackValidationError, match="starter_pack_manifest_too_large"):
        parse_manifest_bytes(b" " * (MAX_MANIFEST_BYTES + 1))


def test_parser_rejects_a_role_id_reused_by_two_blueprints() -> None:
    document = _manifest("starter.feature-delivery.mock", "payloads/example.md", b"example\n")
    duplicate_blueprint = deepcopy(document["blueprints"][0])  # type: ignore[index]
    duplicate_blueprint["id"] = "second-delivery"
    document["blueprints"].append(duplicate_blueprint)  # type: ignore[index]

    with pytest.raises(StarterPackValidationError, match="starter_pack_duplicate_id"):
        parse_manifest_bytes(json.dumps(document).encode("utf-8"))


def test_directory_registry_rejects_tampered_payload_without_any_write(tmp_path: Path) -> None:
    root = _write_valid_bundle(tmp_path / "starter-packs")
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    (root / "payloads" / "one.md").write_bytes(b"tampered\n")

    with pytest.raises(StarterPackValidationError, match="starter_pack_payload_hash_mismatch"):
        load_bundled_packs_from_directory(root)

    assert (root / "payloads" / "one.md").read_bytes() == b"tampered\n"
    assert {path.relative_to(root) for path in root.rglob("*") if path.is_file()} == set(before)


def test_directory_registry_refuses_a_symlinked_intermediate_payload_directory(
    tmp_path: Path,
) -> None:
    root = _write_valid_bundle(tmp_path / "starter-packs")
    real_payloads = root / "real-payloads"
    (root / "payloads").rename(real_payloads)
    (root / "payloads").symlink_to(real_payloads.name, target_is_directory=True)

    with pytest.raises(StarterPackValidationError, match="starter_pack_payload_symlink"):
        load_bundled_packs_from_directory(root)


def test_directory_registry_rejects_duplicate_pack_ids() -> None:
    first = _manifest("starter.same.mock", "payloads/one.md", b"one\n")
    second = _manifest("starter.same.mock", "payloads/two.md", b"two\n")

    with pytest.raises(StarterPackValidationError, match="starter_pack_duplicate_id"):
        _load_documents(first, second)


def _load_documents(first: dict[str, object], second: dict[str, object]) -> tuple[object, ...]:
    """Exercise duplicate ID parsing through the real registry reader."""

    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "starter-packs"
        _write_bundle(root, first, second)
        return load_bundled_packs_from_directory(root)


def _write_valid_bundle(root: Path) -> Path:
    return _write_bundle(
        root,
        _manifest("starter.one.mock", "payloads/one.md", b"one\n"),
        _manifest("starter.two.mock", "payloads/two.md", b"two\n"),
    )


def _write_bundle(root: Path, first: dict[str, object], second: dict[str, object]) -> Path:
    root.mkdir(parents=True)
    payloads = root / "payloads"
    payloads.mkdir()
    first_name = "one.json"
    second_name = "two.json"
    first["source"] = {"kind": "bundled", "resource": first_name}
    second["source"] = {"kind": "bundled", "resource": second_name}
    for document in (first, second):
        file_entry = document["files"][0]  # type: ignore[index]
        path = payloads / Path(file_entry["path"]).name  # type: ignore[index]
        payload = b"one\n" if path.name == "one.md" else b"two\n"
        path.write_bytes(payload)
        file_entry["size"] = len(payload)  # type: ignore[index]
        file_entry["sha256"] = hashlib.sha256(payload).hexdigest()  # type: ignore[index]
    (root / first_name).write_text(json.dumps(first), encoding="utf-8")
    (root / second_name).write_text(json.dumps(second), encoding="utf-8")
    (root / "registry.json").write_text(
        json.dumps(
            {
                "format": "agent-commons.starter-pack-registry.v1",
                "packs": [first_name, second_name],
            }
        ),
        encoding="utf-8",
    )
    return root


def _manifest(pack_id: str, payload_path: str, payload: bytes) -> dict[str, object]:
    return {
        "format": "agent-commons.starter-pack.v1",
        "id": pack_id,
        "version": "0.1.0",
        "title": "Example",
        "summary": "A bounded mock example.",
        "blueprints": [
            {
                "id": "delivery",
                "title": "Delivery",
                "summary": "Build and review a small change.",
                "roles": [
                    {
                        "id": "implementer",
                        "name": "Implementer",
                        "purpose": "Build the bounded change.",
                        "fresh_context": True,
                        "skill_refs": ["software-engineering"],
                        "runtime_instruction": "Implement the approved scope.",
                    }
                ],
            }
        ],
        "files": [
            {
                "path": payload_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        ],
        "source": {"kind": "bundled", "resource": "placeholder.json"},
        "compatibility": {"agent_commons": ">=0.1.0,<1.0.0"},
    }
