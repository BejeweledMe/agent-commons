"""Closed, typed manifest format for read-only Starter Pack metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ValidationError

PACK_FORMAT: Final = "agent-commons.starter-pack.v1"
REGISTRY_FORMAT: Final = "agent-commons.starter-pack-registry.v1"
MAX_MANIFEST_BYTES: Final = 64 * 1024
MAX_PACK_BYTES: Final = 64 * 1024
MAX_RUNTIME_INSTRUCTION_BYTES: Final = 4 * 1024
MAX_BLUEPRINTS: Final = 16
MAX_ROLES_PER_BLUEPRINT: Final = 16
MAX_SKILL_REFS_PER_ROLE: Final = 16
MAX_FILES: Final = 32
_PACK_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")
_ITEM_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StarterPackValidationError(ValidationError):
    """A stable, safe refusal emitted before any pack can be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StarterPackFile:
    """One future-materializable payload, verified by bytes and digest."""

    path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class BlueprintRole:
    """A role example that may later prefill ordinary role creation."""

    id: str
    name: str
    purpose: str
    fresh_context: bool
    skill_refs: tuple[str, ...]
    runtime_instruction: str


@dataclass(frozen=True, slots=True)
class Blueprint:
    """A visible team scenario made of ordinary, not-yet-created roles."""

    id: str
    title: str
    summary: str
    roles: tuple[BlueprintRole, ...]


@dataclass(frozen=True, slots=True)
class StarterPackManifest:
    """Validated metadata for a local Starter Pack resource."""

    id: str
    version: str
    title: str
    summary: str
    blueprints: tuple[Blueprint, ...]
    files: tuple[StarterPackFile, ...]
    source_resource: str
    agent_commons_compatibility: str


def parse_manifest_bytes(raw: bytes) -> StarterPackManifest:
    """Parse one bounded, closed-schema manifest without side effects."""

    if len(raw) > MAX_MANIFEST_BYTES:
        _reject("starter_pack_manifest_too_large")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject("starter_pack_manifest_invalid")
    try:
        value: object = loads_json_strict(decoded)
    except ValidationError as exc:
        _reject("starter_pack_manifest_invalid", exc)
    return _parse_manifest(_mapping(value, "starter_pack_manifest_invalid"))


def parse_registry_bytes(raw: bytes) -> tuple[str, ...]:
    """Parse the bounded registry of bundled manifest resource paths."""

    if len(raw) > MAX_MANIFEST_BYTES:
        _reject("starter_pack_registry_too_large")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject("starter_pack_registry_invalid")
    try:
        value: object = loads_json_strict(decoded)
    except ValidationError as exc:
        _reject("starter_pack_registry_invalid", exc)
    mapping = _mapping(value, "starter_pack_registry_invalid")
    _closed_keys(
        mapping,
        frozenset({"format", "packs"}),
        unknown_code="starter_pack_registry_unknown_field",
        missing_code="starter_pack_registry_invalid",
    )
    if mapping.get("format") != REGISTRY_FORMAT:
        _reject("starter_pack_registry_invalid")
    packs = _list(mapping.get("packs"), "starter_pack_registry_invalid")
    if len(packs) != 2:
        _reject("starter_pack_registry_invalid")
    names = tuple(_safe_relative_path(item, "starter_pack_registry_invalid") for item in packs)
    if len(set(names)) != len(names):
        _reject("starter_pack_duplicate_id")
    return names


def _parse_manifest(value: dict[str, object]) -> StarterPackManifest:
    _closed_keys(
        value,
        frozenset(
            {
                "format",
                "id",
                "version",
                "title",
                "summary",
                "blueprints",
                "files",
                "source",
                "compatibility",
            }
        ),
        unknown_code="starter_pack_manifest_unknown_field",
    )
    if value.get("format") != PACK_FORMAT:
        _reject("starter_pack_manifest_invalid")
    pack_id = _required_id(value.get("id"), namespaced=True)
    version = _required_string(value.get("version"), 32, "starter_pack_manifest_invalid")
    if _SEMVER.fullmatch(version) is None:
        _reject("starter_pack_manifest_invalid")
    title = _required_plain_text(value.get("title"), 160)
    summary = _required_plain_text(value.get("summary"), 1_000)
    blueprints = _parse_blueprints(value.get("blueprints"))
    files = _parse_files(value.get("files"))
    source_resource = _parse_source(value.get("source"))
    compatibility = _parse_compatibility(value.get("compatibility"))
    return StarterPackManifest(
        id=pack_id,
        version=version,
        title=title,
        summary=summary,
        blueprints=blueprints,
        files=files,
        source_resource=source_resource,
        agent_commons_compatibility=compatibility,
    )


def _parse_blueprints(value: object) -> tuple[Blueprint, ...]:
    items = _list(value, "starter_pack_manifest_invalid")
    if not items or len(items) > MAX_BLUEPRINTS:
        _reject("starter_pack_manifest_invalid")
    blueprints: list[Blueprint] = []
    identifiers: set[str] = set()
    role_identifiers: set[str] = set()
    for item in items:
        mapping = _mapping(item, "starter_pack_manifest_invalid")
        _closed_keys(
            mapping,
            frozenset({"id", "title", "summary", "roles"}),
            unknown_code="starter_pack_manifest_unknown_field",
        )
        identifier = _required_id(mapping.get("id"))
        _unique(identifier, identifiers)
        roles = _parse_roles(mapping.get("roles"), role_identifiers)
        blueprints.append(
            Blueprint(
                id=identifier,
                title=_required_plain_text(mapping.get("title"), 160),
                summary=_required_plain_text(mapping.get("summary"), 1_000),
                roles=roles,
            )
        )
    return tuple(blueprints)


def _parse_roles(value: object, pack_role_identifiers: set[str]) -> tuple[BlueprintRole, ...]:
    items = _list(value, "starter_pack_manifest_invalid")
    if not items or len(items) > MAX_ROLES_PER_BLUEPRINT:
        _reject("starter_pack_manifest_invalid")
    roles: list[BlueprintRole] = []
    identifiers: set[str] = set()
    for item in items:
        mapping = _mapping(item, "starter_pack_manifest_invalid")
        _closed_keys(
            mapping,
            frozenset(
                {"id", "name", "purpose", "fresh_context", "skill_refs", "runtime_instruction"}
            ),
            unknown_code="starter_pack_manifest_unknown_field",
        )
        identifier = _required_id(mapping.get("id"))
        _unique(identifier, identifiers)
        _unique(identifier, pack_role_identifiers)
        fresh_context = mapping.get("fresh_context")
        if fresh_context is not True:
            _reject("starter_pack_manifest_invalid")
        skill_refs = _parse_skill_refs(mapping.get("skill_refs"))
        instruction = _required_plain_text(mapping.get("runtime_instruction"), 8_192)
        if len(instruction.encode("utf-8")) > MAX_RUNTIME_INSTRUCTION_BYTES:
            _reject("starter_pack_instruction_too_large")
        roles.append(
            BlueprintRole(
                id=identifier,
                name=_required_plain_text(mapping.get("name"), 160),
                purpose=_required_plain_text(mapping.get("purpose"), 1_000),
                fresh_context=True,
                skill_refs=skill_refs,
                runtime_instruction=instruction,
            )
        )
    return tuple(roles)


def _parse_skill_refs(value: object) -> tuple[str, ...]:
    items = _list(value, "starter_pack_manifest_invalid")
    if len(items) > MAX_SKILL_REFS_PER_ROLE:
        _reject("starter_pack_manifest_invalid")
    skills = tuple(_required_id(item) for item in items)
    if len(set(skills)) != len(skills):
        _reject("starter_pack_duplicate_id")
    return skills


def _parse_files(value: object) -> tuple[StarterPackFile, ...]:
    items = _list(value, "starter_pack_manifest_invalid")
    if not items or len(items) > MAX_FILES:
        _reject("starter_pack_manifest_invalid")
    files: list[StarterPackFile] = []
    paths: set[str] = set()
    total_size = 0
    for item in items:
        mapping = _mapping(item, "starter_pack_manifest_invalid")
        _closed_keys(
            mapping,
            frozenset({"path", "sha256", "size"}),
            unknown_code="starter_pack_manifest_unknown_field",
        )
        path = _safe_relative_path(mapping.get("path"), "starter_pack_payload_path_invalid")
        _unique(path, paths)
        digest = _required_string(mapping.get("sha256"), 64, "starter_pack_manifest_invalid")
        if _SHA256.fullmatch(digest) is None:
            _reject("starter_pack_manifest_invalid")
        size = mapping.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _reject("starter_pack_manifest_invalid")
        total_size += size
        if total_size > MAX_PACK_BYTES:
            _reject("starter_pack_payload_too_large")
        files.append(StarterPackFile(path=path, sha256=digest, size=size))
    return tuple(files)


def _parse_source(value: object) -> str:
    mapping = _mapping(value, "starter_pack_manifest_invalid")
    _closed_keys(
        mapping,
        frozenset({"kind", "resource"}),
        unknown_code="starter_pack_manifest_unknown_field",
    )
    if mapping.get("kind") != "bundled":
        _reject("starter_pack_manifest_invalid")
    return _safe_relative_path(mapping.get("resource"), "starter_pack_manifest_invalid")


def _parse_compatibility(value: object) -> str:
    mapping = _mapping(value, "starter_pack_manifest_invalid")
    _closed_keys(
        mapping,
        frozenset({"agent_commons"}),
        unknown_code="starter_pack_manifest_unknown_field",
    )
    return _required_string(mapping.get("agent_commons"), 160, "starter_pack_manifest_invalid")


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _reject(code)
    return value


def _list(value: object, code: str) -> list[object]:
    if not isinstance(value, list):
        _reject(code)
    return value


def _closed_keys(
    value: dict[str, object],
    allowed: frozenset[str],
    *,
    unknown_code: str,
    missing_code: str = "starter_pack_manifest_invalid",
) -> None:
    if set(value) - allowed:
        _reject(unknown_code)
    if set(value) != allowed:
        _reject(missing_code)


def _required_id(value: object, *, namespaced: bool = False) -> str:
    identifier = _required_string(value, 160, "starter_pack_manifest_invalid")
    pattern = _PACK_ID if namespaced else _ITEM_ID
    if pattern.fullmatch(identifier) is None:
        _reject("starter_pack_manifest_invalid")
    return identifier


def _required_plain_text(value: object, maximum: int) -> str:
    text = _required_string(value, maximum, "starter_pack_manifest_invalid")
    if any(ord(character) < 32 for character in text):
        _reject("starter_pack_manifest_invalid")
    return text


def _required_string(value: object, maximum: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _reject(code)
    return value


def _safe_relative_path(value: object, code: str) -> str:
    path = _required_string(value, 512, code)
    pure_path = PurePosixPath(path)
    if (
        pure_path.is_absolute()
        or not pure_path.parts
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        _reject(code)
    return pure_path.as_posix()


def _unique(value: str, seen: set[str]) -> None:
    if value in seen:
        _reject("starter_pack_duplicate_id")
    seen.add(value)


def _reject(code: str, cause: Exception | None = None) -> None:
    if cause is None:
        raise StarterPackValidationError(code)
    raise StarterPackValidationError(code) from cause
