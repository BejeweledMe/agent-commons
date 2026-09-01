"""Bounded, provider-specific projection of packaged Agent Commons skills.

Skill source and projected text are deliberately ephemeral.  Only their
digests cross the launch metadata boundary; the exact projected bytes travel
to the provider in the already-fingerprinted invocation stdin.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice

from agent_commons.errors import ConfigurationError
from agent_commons.skill_manifest import (
    MAX_PROJECTED_SKILLS,
    MAX_SKILL_BUNDLE_BYTES,
    compute_skill_projection_digests,
    load_skill_manifest,
    project_skill_bytes,
    read_skill_resource,
)
from agent_commons.skill_manifest import (
    MAX_SKILL_SOURCE_BYTES as _MAX_SKILL_SOURCE_BYTES,
)
from agent_commons.skill_manifest import (
    SKILL_PROJECTION_VERSION as _SKILL_PROJECTION_VERSION,
)

from .model import Provider

BUILTIN_SKILL_IDS = load_skill_manifest().skill_ids
MAX_SKILL_SOURCE_BYTES = _MAX_SKILL_SOURCE_BYTES
SKILL_PROJECTION_VERSION = _SKILL_PROJECTION_VERSION


def _bounded_collection(value: object, *, label: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConfigurationError(f"{label} must be a bounded collection")
    try:
        items = tuple(islice(iter(value), MAX_PROJECTED_SKILLS + 1))
    except Exception as exc:
        raise ConfigurationError(f"{label} must be a bounded collection") from exc
    if len(items) > MAX_PROJECTED_SKILLS:
        raise ConfigurationError(f"{label} exceeds the collection-size limit")
    return items


def _bounded_skill_ids(value: object) -> tuple[str, ...]:
    items = _bounded_collection(value, label="skill identities")
    if any(type(item) is not str for item in items):
        raise ConfigurationError("skill identities must contain only strings")
    return items  # type: ignore[return-value]


def _bounded_projected_instructions(value: object) -> tuple[bytes, ...]:
    items = _bounded_collection(value, label="projected skill instructions")
    if any(type(item) is not bytes for item in items):
        raise ConfigurationError("projected skill instructions must contain only bytes")
    return items  # type: ignore[return-value]


def _source(skill_id: str) -> bytes:
    manifest = load_skill_manifest()
    return read_skill_resource(manifest, skill_id, "SKILL.md")


def _project_one(provider: Provider, skill_id: str, source: bytes) -> bytes:
    return project_skill_bytes(load_skill_manifest(), provider.value, skill_id, source)


@dataclass(frozen=True, slots=True)
class EphemeralSkillBundle:
    """Exact in-memory projection plus safe digests bound to one provider."""

    provider: Provider
    skill_ids: tuple[str, ...]
    projected_instructions: tuple[bytes, ...]
    source_digest: str | None
    projection_digest: str | None
    installer_digest: str | None

    @classmethod
    def empty(cls, provider: Provider) -> EphemeralSkillBundle:
        return cls(Provider(provider), (), (), None, None, None)

    @property
    def is_empty(self) -> bool:
        return not self.skill_ids

    def __post_init__(self) -> None:
        provider = Provider(self.provider)
        skill_ids = _bounded_skill_ids(self.skill_ids)
        projected = _bounded_projected_instructions(self.projected_instructions)
        if len(skill_ids) != len(projected) or len(skill_ids) > MAX_PROJECTED_SKILLS:
            raise ConfigurationError("ephemeral skill bundle shape is invalid")
        if len(set(skill_ids)) != len(skill_ids):
            raise ConfigurationError("ephemeral skill bundle repeats a skill")
        if not skill_ids:
            if any(
                value is not None
                for value in (self.source_digest, self.projection_digest, self.installer_digest)
            ):
                raise ConfigurationError("empty skill bundle cannot carry digests")
        else:
            if any(skill_id not in BUILTIN_SKILL_IDS for skill_id in skill_ids):
                raise ConfigurationError("ephemeral skill bundle is not allowlisted")
            digests = (self.source_digest, self.projection_digest, self.installer_digest)
            if any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in digests
            ):
                raise ConfigurationError("ephemeral skill bundle digest is invalid")
            if sum(len(value) for value in projected) > MAX_SKILL_BUNDLE_BYTES:
                raise ConfigurationError("ephemeral skill bundle is oversized")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "skill_ids", skill_ids)
        object.__setattr__(self, "projected_instructions", projected)


def project_builtin_skills(provider: Provider, skill_refs: Sequence[str]) -> EphemeralSkillBundle:
    """Resolve only packaged identities and project exact provider-owned bytes."""

    provider = Provider(provider)
    manifest = load_skill_manifest()
    skill_ids = _bounded_skill_ids(skill_refs)
    if not skill_ids:
        return EphemeralSkillBundle.empty(provider)
    if (
        len(skill_ids) > MAX_PROJECTED_SKILLS
        or len(set(skill_ids)) != len(skill_ids)
        or any(skill_id not in manifest.skill_ids for skill_id in skill_ids)
    ):
        raise ConfigurationError("requested skill projection is not allowlisted")
    sources = tuple(_source(skill_id) for skill_id in skill_ids)
    projected = tuple(
        _project_one(provider, skill_id, source)
        for skill_id, source in zip(skill_ids, sources, strict=True)
    )
    if sum(len(value) for value in projected) > MAX_SKILL_BUNDLE_BYTES:
        raise ConfigurationError("requested skill projection is oversized")
    digests = compute_skill_projection_digests(provider.value, skill_ids)
    expected_sources = tuple(
        read_skill_resource(manifest, skill_id, "SKILL.md") for skill_id in skill_ids
    )
    if sources != expected_sources:
        raise ConfigurationError("requested skill source changed during projection")
    return EphemeralSkillBundle(
        provider=provider,
        skill_ids=skill_ids,
        projected_instructions=projected,
        source_digest=digests.source_digest,
        projection_digest=digests.projection_digest,
        installer_digest=digests.installer_digest,
    )


def verify_skill_bundle(bundle: EphemeralSkillBundle) -> bool:
    """Detect source, projection, provider, or installer drift before build."""

    if bundle.is_empty:
        return bundle == EphemeralSkillBundle.empty(bundle.provider)
    try:
        expected = project_builtin_skills(bundle.provider, bundle.skill_ids)
    except ConfigurationError:
        return False
    return bundle == expected


def compile_skill_bundle(plan_instruction: str, bundle: EphemeralSkillBundle) -> str:
    """Append exact projections without changing byte-stable empty launches."""

    if bundle.is_empty:
        return plan_instruction
    if not verify_skill_bundle(bundle):
        raise ConfigurationError("ephemeral skill projection is missing, stale, or altered")
    marker = (
        "\n\nProvider-projected packaged skills "
        f"(source_sha256={bundle.source_digest}, "
        f"projection_sha256={bundle.projection_digest}, "
        f"installer_sha256={bundle.installer_digest}):\n"
    ).encode()
    compiled = (
        plan_instruction.encode("utf-8") + marker + b"\n\n".join(bundle.projected_instructions)
    )
    if len(compiled) > 1_000_000:
        raise ConfigurationError("compiled provider instruction exceeds the one-megabyte limit")
    return compiled.decode("utf-8")
