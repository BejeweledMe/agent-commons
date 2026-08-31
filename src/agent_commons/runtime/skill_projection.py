"""Bounded, provider-specific projection of packaged Agent Commons skills.

Skill source and projected text are deliberately ephemeral.  Only their
digests cross the launch metadata boundary; the exact projected bytes travel
to the provider in the already-fingerprinted invocation stdin.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from itertools import islice

from agent_commons.errors import ConfigurationError

from .model import Provider

BUILTIN_SKILL_IDS = (
    "commons-coordinate",
    "commons-delegate",
    "commons-handoff",
    "commons-record",
    "commons-review",
    "commons-share",
    "commons-start",
)
MAX_PROJECTED_SKILLS = 8
MAX_SKILL_SOURCE_BYTES = 64 * 1024
MAX_SKILL_BUNDLE_BYTES = 256 * 1024
SKILL_PROJECTION_VERSION = "agent-commons-skill-projection.v1"

_INSTALLER_ROOT = {
    Provider.CODEX: ".agents/skills",
    Provider.CLAUDE: ".claude/skills",
}


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


def _hash_parts(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _resource_bytes(skill_id: str, relative_path: str) -> bytes:
    if skill_id not in BUILTIN_SKILL_IDS:
        raise ConfigurationError("skill identity is not an allowlisted built-in")
    resource = resources.files("agent_commons").joinpath(
        "resources", "skills", skill_id, *relative_path.split("/")
    )
    try:
        value = resource.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise ConfigurationError("packaged skill resource is unavailable") from exc
    if not value or len(value) > MAX_SKILL_SOURCE_BYTES:
        raise ConfigurationError("packaged skill source is empty or oversized")
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("packaged skill source is not UTF-8") from exc
    return value


def _source(skill_id: str) -> bytes:
    return _resource_bytes(skill_id, "SKILL.md")


def _installer_digest(provider: Provider, skill_ids: tuple[str, ...]) -> str:
    parts = [SKILL_PROJECTION_VERSION.encode(), provider.value.encode()]
    for skill_id in skill_ids:
        for relative_path in ("SKILL.md", "agents/openai.yaml"):
            target = f"{_INSTALLER_ROOT[provider]}/{skill_id}/{relative_path}"
            parts.extend((target.encode(), _resource_bytes(skill_id, relative_path)))
    return _hash_parts(*parts)


def _project_one(provider: Provider, skill_id: str, source: bytes) -> bytes:
    target = f"{_INSTALLER_ROOT[provider]}/{skill_id}/SKILL.md"
    if provider is Provider.CODEX:
        opening = (
            f'<agent-commons-skill provider="codex" id="{skill_id}" installed-as="{target}">\n'
        )
        closing = "\n</agent-commons-skill>"
    else:
        opening = (
            f'<agent-commons-skill provider="claude" id="{skill_id}" installed-as="{target}">\n'
        )
        closing = "\n</agent-commons-skill>"
    return opening.encode() + source.rstrip(b"\n") + closing.encode()


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
    skill_ids = _bounded_skill_ids(skill_refs)
    if not skill_ids:
        return EphemeralSkillBundle.empty(provider)
    if (
        len(skill_ids) > MAX_PROJECTED_SKILLS
        or len(set(skill_ids)) != len(skill_ids)
        or any(skill_id not in BUILTIN_SKILL_IDS for skill_id in skill_ids)
    ):
        raise ConfigurationError("requested skill projection is not allowlisted")
    sources = tuple(_source(skill_id) for skill_id in skill_ids)
    projected = tuple(
        _project_one(provider, skill_id, source)
        for skill_id, source in zip(skill_ids, sources, strict=True)
    )
    if sum(len(value) for value in projected) > MAX_SKILL_BUNDLE_BYTES:
        raise ConfigurationError("requested skill projection is oversized")
    source_digest = _hash_parts(
        *(
            part
            for pair in zip((item.encode() for item in skill_ids), sources, strict=True)
            for part in pair
        )
    )
    projection_digest = _hash_parts(*projected)
    return EphemeralSkillBundle(
        provider=provider,
        skill_ids=skill_ids,
        projected_instructions=projected,
        source_digest=source_digest,
        projection_digest=projection_digest,
        installer_digest=_installer_digest(provider, skill_ids),
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
