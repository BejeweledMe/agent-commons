"""Typed browser-safe read DTOs for bundled Starter Pack examples.

The first Starter Pack surface is intentionally descriptive.  It lets Work
show example packs, blueprints, and role cards without exposing a runtime
instruction, selecting a profile, or providing data that could create a role.
Those capabilities have their own future confirmation and policy boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

StarterPackSourceKind = Literal["bundled"]
StarterPackContextMode = Literal["fresh"]
StarterPackCatalogErrorCode = Literal["starter_pack_catalog_unavailable"]


class StarterPackRolePayload(TypedDict):
    """A read-only role card within one bundled example."""

    id: str
    name: str
    purpose: str
    context_mode: StarterPackContextMode
    skills: list[str]


class StarterPackBlueprintPayload(TypedDict):
    """A read-only team scenario made up of example role cards."""

    id: str
    title: str
    summary: str
    roles: list[StarterPackRolePayload]


class StarterPackPayload(TypedDict):
    """A bundled, uninstalled Starter Pack shown in Work."""

    id: str
    version: str
    title: str
    summary: str
    source_kind: StarterPackSourceKind
    example: Literal[True]
    blueprints: list[StarterPackBlueprintPayload]


class StarterPackCatalogPayload(TypedDict):
    """The complete browser payload for the bundled example catalogue."""

    packs: list[StarterPackPayload]


class StarterPackErrorPayload(TypedDict):
    """One safe, closed-vocabulary Starter Pack browser refusal."""

    code: StarterPackCatalogErrorCode
    message: str


class StarterPackCatalogRefusalPayload(TypedDict):
    """Wire-compatible typed refusal wrapper used by Work routes."""

    error: StarterPackErrorPayload


@dataclass(frozen=True, slots=True)
class StarterPackRoleDTO:
    """An immutable browser-safe projection of one blueprint role."""

    id: str
    name: str
    purpose: str
    skills: tuple[str, ...]

    def to_wire(self) -> StarterPackRolePayload:
        """Return fresh standard containers for JSON serialization."""

        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "context_mode": "fresh",
            "skills": list(self.skills),
        }


@dataclass(frozen=True, slots=True)
class StarterPackBlueprintDTO:
    """An immutable browser-safe projection of one team scenario."""

    id: str
    title: str
    summary: str
    roles: tuple[StarterPackRoleDTO, ...]

    def to_wire(self) -> StarterPackBlueprintPayload:
        """Return fresh standard containers for JSON serialization."""

        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "roles": [role.to_wire() for role in self.roles],
        }


@dataclass(frozen=True, slots=True)
class StarterPackDTO:
    """An immutable browser-safe projection of one bundled mock pack."""

    id: str
    version: str
    title: str
    summary: str
    blueprints: tuple[StarterPackBlueprintDTO, ...]

    def to_wire(self) -> StarterPackPayload:
        """Return fresh standard containers for JSON serialization."""

        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "source_kind": "bundled",
            "example": True,
            "blueprints": [blueprint.to_wire() for blueprint in self.blueprints],
        }


@dataclass(frozen=True, slots=True)
class StarterPackCatalogDTO:
    """The immutable, non-installing Starter Pack catalogue for Work."""

    packs: tuple[StarterPackDTO, ...]

    def to_wire(self) -> StarterPackCatalogPayload:
        """Return a fresh JSON payload without any mutable internal state."""

        return {"packs": [pack.to_wire() for pack in self.packs]}


@dataclass(frozen=True, slots=True)
class StarterPackCatalogRefusalDTO:
    """A stable refusal that does not leak validator or resource details."""

    def to_wire(self) -> StarterPackCatalogRefusalPayload:
        """Return Work's fixed safe refusal body."""

        return {
            "error": {
                "code": "starter_pack_catalog_unavailable",
                "message": "bundled Starter Pack examples could not be verified",
            }
        }
