"""Work projection and explicit materialization for bundled Starter Pack examples."""

from __future__ import annotations

from agent_commons.domain.roles import DENY_ALL
from agent_commons.errors import ValidationError
from agent_commons.integrations.starter_packs import (
    Blueprint,
    BlueprintRole,
    StarterPackManifest,
    StarterPackValidationError,
    get_bundled_pack,
    list_bundled_packs,
)
from agent_commons.services import CommonsManager
from agent_commons.ui.starter_pack_dtos import (
    AppliedStarterPackRoleDTO,
    StarterPackApplyDTO,
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


class StarterPackApplyRefusal(ValidationError):
    """A stable, browser-safe refusal for applying one bundled blueprint."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def apply_starter_pack_blueprint(
    manager: CommonsManager,
    *,
    pack_id: str,
    blueprint_id: str,
    confirmed: bool,
    idempotency_key: str | None = None,
) -> StarterPackApplyDTO:
    """Create ordinary role templates from one bundled blueprint after confirmation."""

    if not confirmed:
        raise StarterPackApplyRefusal(
            "starter_pack_apply_confirmation_required",
            "applying a Starter Pack requires explicit confirmation",
        )
    if not idempotency_key or not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise StarterPackApplyRefusal(
            "starter_pack_apply_unavailable",
            "applying a Starter Pack requires a non-empty idempotency_key",
        )
    try:
        manifest = get_bundled_pack(pack_id)
    except StarterPackValidationError as exc:
        if exc.code == "starter_pack_not_found":
            raise StarterPackApplyRefusal(
                "starter_pack_apply_not_found",
                "the requested bundled Starter Pack does not exist",
            ) from exc
        raise StarterPackApplyRefusal(
            "starter_pack_apply_unavailable",
            "the bundled Starter Pack could not be verified",
        ) from exc
    blueprint = next((item for item in manifest.blueprints if item.id == blueprint_id), None)
    if blueprint is None:
        raise StarterPackApplyRefusal(
            "starter_pack_apply_not_found",
            "the requested bundled Starter Pack blueprint does not exist",
        )

    applied: list[AppliedStarterPackRoleDTO] = []
    for role in blueprint.roles:
        event = manager.create_agent(
            name=role.name,
            profile_id=role.profile_id,
            grants=DENY_ALL,
            context_mode="fresh",
            rationale=f"Starter Pack {manifest.id}/{blueprint.id}: {role.purpose}",
            skills=role.skill_refs,
            template=True,
            idempotency_key=f"{idempotency_key}:{manifest.id}:{blueprint.id}:{role.id}",
        )
        agent_id = str(event["entity_ref"]["id"])
        record = manager.get_agent(agent_id)
        applied.append(
            AppliedStarterPackRoleDTO(
                source_role_id=role.id,
                agent_id=agent_id,
                revision=str(event["revision"]),
                name=str(record["name"]),
                profile_id=str(record["profile_id"]),
                grants=dict(record["grants"]),
                skills=tuple(str(item) for item in record.get("skills") or ()),
            )
        )
    return StarterPackApplyDTO(
        pack_id=manifest.id,
        blueprint_id=blueprint.id,
        roles=tuple(applied),
    )


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
        profile_id=role.profile_id,
        skills=role.skill_refs,
    )
