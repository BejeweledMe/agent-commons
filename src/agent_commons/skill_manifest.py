"""Neutral packaged skill manifest and deterministic projection digests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath

from agent_commons.errors import ConfigurationError

SKILL_MANIFEST_SCHEMA = "agent-commons.skill-manifest.v1"
SKILL_PROJECTION_VERSION = "agent-commons-skill-projection.v1"
MAX_SKILL_MANIFEST_BYTES = 16 * 1024
MAX_SKILL_SOURCE_BYTES = 64 * 1024
MAX_PROJECTED_SKILLS = 8
MAX_SKILL_BUNDLE_BYTES = 256 * 1024
_PROVIDERS = ("codex", "claude", "grok")
_REQUIRED_FILES = ("SKILL.md", "agents/openai.yaml")
_SKILL_ID = re.compile(r"[a-z][a-z0-9-]{0,63}").fullmatch


def hash_parts(*parts: bytes) -> str:
    """Hash length-framed values so concatenation boundaries stay unambiguous."""

    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _safe_relative(value: object, *, label: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise ConfigurationError(f"skill manifest {label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ConfigurationError(f"skill manifest {label} is invalid")
    return value


def _resource_bytes(*parts: str, limit: int, label: str) -> bytes:
    resource = resources.files("agent_commons").joinpath("resources", "skills", *parts)
    if isinstance(resource, Path):
        current = resource
        skill_root = resources.files("agent_commons").joinpath("resources", "skills")
        while isinstance(skill_root, Path) and current != skill_root:
            if current.is_symlink():
                raise ConfigurationError(f"packaged {label} is symlinked")
            current = current.parent
    try:
        value = resource.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise ConfigurationError(f"packaged {label} is unavailable") from exc
    if not value or len(value) > limit:
        raise ConfigurationError(f"packaged {label} is empty or oversized")
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"packaged {label} is not UTF-8") from exc
    return value


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """Validated neutral identities, files, and provider install roots."""

    projection_version: str
    provider_roots: tuple[tuple[str, str], ...]
    skill_files: tuple[str, ...]
    skill_ids: tuple[str, ...]

    def provider_root(self, provider: str) -> str:
        for name, root in self.provider_roots:
            if name == provider:
                return root
        raise ConfigurationError("skill projection provider is unsupported")


@dataclass(frozen=True, slots=True)
class SkillProjectionDigests:
    """Safe report metadata for one exact provider projection."""

    provider: str
    skill_ids: tuple[str, ...]
    source_digest: str
    projection_digest: str
    installer_digest: str


def load_skill_manifest() -> SkillManifest:
    """Load and strictly validate the packaged neutral skill manifest."""

    raw = _resource_bytes(
        "manifest.json",
        limit=MAX_SKILL_MANIFEST_BYTES,
        label="skill manifest",
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("packaged skill manifest is invalid JSON") from exc
    if type(payload) is not dict or set(payload) != {
        "schema",
        "projection_version",
        "providers",
        "skill_files",
        "skill_ids",
    }:
        raise ConfigurationError("packaged skill manifest shape is invalid")
    if payload["schema"] != SKILL_MANIFEST_SCHEMA:
        raise ConfigurationError("packaged skill manifest schema is unsupported")
    if payload["projection_version"] != SKILL_PROJECTION_VERSION:
        raise ConfigurationError("packaged skill projection version is unsupported")

    providers = payload["providers"]
    if type(providers) is not dict or tuple(providers) != _PROVIDERS:
        raise ConfigurationError("packaged skill manifest providers are invalid")
    provider_roots = tuple(
        (provider, _safe_relative(providers[provider], label="provider root"))
        for provider in _PROVIDERS
    )
    if len({root for _, root in provider_roots}) != len(provider_roots):
        raise ConfigurationError("packaged skill manifest provider roots repeat")

    skill_files = payload["skill_files"]
    if type(skill_files) is not list or tuple(skill_files) != _REQUIRED_FILES:
        raise ConfigurationError("packaged skill manifest files are invalid")
    normalized_files = tuple(_safe_relative(value, label="skill file") for value in skill_files)

    skill_ids = payload["skill_ids"]
    if (
        type(skill_ids) is not list
        or not 0 < len(skill_ids) <= MAX_PROJECTED_SKILLS
        or any(type(value) is not str or _SKILL_ID(value) is None for value in skill_ids)
        or len(set(skill_ids)) != len(skill_ids)
        or skill_ids != sorted(skill_ids)
    ):
        raise ConfigurationError("packaged skill manifest identities are invalid")
    return SkillManifest(
        projection_version=payload["projection_version"],
        provider_roots=provider_roots,
        skill_files=normalized_files,
        skill_ids=tuple(skill_ids),
    )


def read_skill_resource(manifest: SkillManifest, skill_id: str, relative_path: str) -> bytes:
    """Read one allowlisted bounded skill resource without following symlinks."""

    if skill_id not in manifest.skill_ids or relative_path not in manifest.skill_files:
        raise ConfigurationError("skill resource is not present in the neutral manifest")
    return _resource_bytes(
        skill_id,
        *relative_path.split("/"),
        limit=MAX_SKILL_SOURCE_BYTES,
        label="skill resource",
    )


def project_skill_bytes(
    manifest: SkillManifest, provider: str, skill_id: str, source: bytes
) -> bytes:
    """Wrap exact neutral source bytes for one provider-specific install root."""

    target = f"{manifest.provider_root(provider)}/{skill_id}/SKILL.md"
    opening = (
        f'<agent-commons-skill provider="{provider}" id="{skill_id}" installed-as="{target}">\n'
    )
    return opening.encode() + source.rstrip(b"\n") + b"\n</agent-commons-skill>"


def compute_skill_projection_digests(
    provider: str, skill_ids: tuple[str, ...]
) -> SkillProjectionDigests:
    """Compute the exact safe digests shared by launch and installer reports."""

    manifest = load_skill_manifest()
    if (
        len(skill_ids) > MAX_PROJECTED_SKILLS
        or len(set(skill_ids)) != len(skill_ids)
        or any(skill_id not in manifest.skill_ids for skill_id in skill_ids)
    ):
        raise ConfigurationError("requested skill projection is not allowlisted")
    sources = tuple(read_skill_resource(manifest, skill_id, "SKILL.md") for skill_id in skill_ids)
    projected = tuple(
        project_skill_bytes(manifest, provider, skill_id, source)
        for skill_id, source in zip(skill_ids, sources, strict=True)
    )
    if sum(len(value) for value in projected) > MAX_SKILL_BUNDLE_BYTES:
        raise ConfigurationError("requested skill projection is oversized")
    source_digest = hash_parts(
        *(
            part
            for pair in zip((item.encode() for item in skill_ids), sources, strict=True)
            for part in pair
        )
    )
    installer_parts = [manifest.projection_version.encode(), provider.encode()]
    for skill_id in skill_ids:
        for relative_path in manifest.skill_files:
            target = f"{manifest.provider_root(provider)}/{skill_id}/{relative_path}"
            installer_parts.extend(
                (
                    target.encode(),
                    read_skill_resource(manifest, skill_id, relative_path),
                )
            )
    return SkillProjectionDigests(
        provider=provider,
        skill_ids=skill_ids,
        source_digest=source_digest,
        projection_digest=hash_parts(*projected),
        installer_digest=hash_parts(*installer_parts),
    )
