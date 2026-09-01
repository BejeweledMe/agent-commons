"""Validation for bundled Starter Pack examples.

The package validates the packaged manifests and their payload digests.  The
UI layer owns any explicitly-confirmed materialization into ordinary canonical
roles.
"""

from .bundled import get_bundled_pack, list_bundled_packs
from .manifest import (
    STARTER_PACK_ALLOWED_PROFILE_IDS,
    STARTER_PACK_ALLOWED_SKILL_REFS,
    Blueprint,
    BlueprintRole,
    StarterPackFile,
    StarterPackManifest,
    StarterPackValidationError,
    parse_manifest_bytes,
)

__all__ = [
    "Blueprint",
    "BlueprintRole",
    "STARTER_PACK_ALLOWED_PROFILE_IDS",
    "STARTER_PACK_ALLOWED_SKILL_REFS",
    "StarterPackFile",
    "StarterPackManifest",
    "StarterPackValidationError",
    "get_bundled_pack",
    "list_bundled_packs",
    "parse_manifest_bytes",
]
