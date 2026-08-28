"""Read-only Work projection for the two bundled Starter Pack examples."""

from __future__ import annotations

from agent_commons.integrations.starter_packs import (
    Blueprint,
    BlueprintRole,
    StarterPackManifest,
    StarterPackValidationError,
    list_bundled_packs,
)
from agent_commons.ui.starter_pack_dtos import (
    StarterPackBlueprintDTO,
    StarterPackCatalogDTO,
    StarterPackDTO,
    StarterPackRoleDTO,
)

_BUNDLED_MOCK_IDS = (
    "starter.feature-delivery.mock",
    "starter.product-discovery.mock",
)


class StarterPackCatalogUnavailable(Exception):
    """The packaged example catalogue was absent, invalid, or not the P2 set."""


def read_starter_pack_catalog() -> StarterPackCatalogDTO:
    """Return exactly the two verified, bundled mock packs for Work.

    This operation deliberately calls only the package-resource reader.  It
    does not need a workspace, runtime configuration, operator catalogue, or
    browser input, and it cannot install, materialize, or launch anything.
    """

    try:
        manifests = list_bundled_packs()
    except (OSError, ValueError, StarterPackValidationError) as exc:
        raise StarterPackCatalogUnavailable from exc
    if tuple(manifest.id for manifest in manifests) != _BUNDLED_MOCK_IDS:
        raise StarterPackCatalogUnavailable
    return StarterPackCatalogDTO(packs=tuple(_pack_dto(manifest) for manifest in manifests))


def _pack_dto(manifest: StarterPackManifest) -> StarterPackDTO:
    """Project one already-validated bundled manifest into Work's read model."""

    return StarterPackDTO(
        id=manifest.id,
        version=manifest.version,
        title=manifest.title,
        summary=manifest.summary,
        blueprints=tuple(_blueprint_dto(blueprint) for blueprint in manifest.blueprints),
    )


def _blueprint_dto(blueprint: Blueprint) -> StarterPackBlueprintDTO:
    """Project only the fields that describe an example team scenario."""

    return StarterPackBlueprintDTO(
        id=blueprint.id,
        title=blueprint.title,
        summary=blueprint.summary,
        roles=tuple(_role_dto(role) for role in blueprint.roles),
    )


def _role_dto(role: BlueprintRole) -> StarterPackRoleDTO:
    """Project a descriptive role card, excluding its runtime instruction."""

    return StarterPackRoleDTO(
        id=role.id,
        name=role.name,
        purpose=role.purpose,
        skills=role.skill_refs,
    )
