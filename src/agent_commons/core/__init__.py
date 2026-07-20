"""Domain-neutral primitives used by Agent Commons."""

from .canonical import (
    canonical_json_bytes,
    canonical_json_file_bytes,
    canonical_sha256,
    load_json_strict,
    loads_json_strict,
    sha256_bytes,
)
from .ids import is_typed_id, new_sortable_id, stable_id
from .refs import TypedRef, iter_typed_refs, normalize_ref, parse_ref, ref_key
from .schema_registry import SchemaRegistry

__all__ = [
    "SchemaRegistry",
    "TypedRef",
    "canonical_json_bytes",
    "canonical_json_file_bytes",
    "canonical_sha256",
    "is_typed_id",
    "iter_typed_refs",
    "load_json_strict",
    "loads_json_strict",
    "new_sortable_id",
    "normalize_ref",
    "parse_ref",
    "ref_key",
    "sha256_bytes",
    "stable_id",
]
