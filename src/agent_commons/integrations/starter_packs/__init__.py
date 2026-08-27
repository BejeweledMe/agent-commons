"""Read-only validation for bundled Starter Pack examples.

The package deliberately exposes metadata only.  Applying a pack, copying it
into a project, resolving remote releases, or creating roles belongs to later
explicitly-confirmed product slices.
"""

from .bundled import get_bundled_pack, list_bundled_packs
from .manifest import (
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
    "StarterPackFile",
    "StarterPackManifest",
    "StarterPackValidationError",
    "get_bundled_pack",
    "list_bundled_packs",
    "parse_manifest_bytes",
]
