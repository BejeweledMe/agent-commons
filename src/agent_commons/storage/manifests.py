"""Content-addressed, schema-validated generic manifests."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_commons.config import CommonsPaths
from agent_commons.core.canonical import (
    canonical_json_bytes,
    canonical_json_file_bytes,
    loads_json_strict,
    sha256_bytes,
)
from agent_commons.core.schema_registry import SchemaRegistry
from agent_commons.errors import IntegrityError, ValidationError
from agent_commons.storage.atomic import atomic_write_immutable

ManifestValidator = Callable[[Mapping[str, Any]], None]
_MANIFEST_ID = re.compile(r"^mft\.([a-z][a-z0-9_]*)\.sha256\.([a-f0-9]{64})$")


@dataclass(frozen=True)
class ManifestRecord:
    manifest_id: str
    kind: str
    sha256: str
    path: Path
    manifest: Mapping[str, Any]
    created: bool

    @property
    def body(self) -> Mapping[str, Any]:
        """Compatibility alias for callers that call the document a body."""

        return self.manifest


class ManifestStore:
    def __init__(
        self,
        paths: CommonsPaths,
        schemas: SchemaRegistry,
        *,
        validators: Iterable[ManifestValidator] = (),
    ) -> None:
        self.paths = paths
        self.schemas = schemas
        self.validators = tuple(validators)

    def put(self, manifest: Mapping[str, Any]) -> ManifestRecord:
        document = dict(manifest)
        self._validate(document)
        kind = document.get("kind")
        if not isinstance(kind, str) or re.fullmatch(r"[a-z][a-z0-9_]*", kind) is None:
            raise ValidationError("manifest kind must be a normalized identifier")
        digest = sha256_bytes(canonical_json_bytes(document))
        manifest_id = f"mft.{kind}.sha256.{digest}"
        path = self.paths.manifests / kind / digest[:2] / f"{digest}.json"
        result = atomic_write_immutable(path, canonical_json_file_bytes(document))
        return ManifestRecord(manifest_id, kind, digest, path, document, result.created)

    def get(self, manifest_id: str) -> ManifestRecord:
        match = _MANIFEST_ID.fullmatch(manifest_id)
        if match is None:
            raise FileNotFoundError(f"invalid manifest ID: {manifest_id}")
        kind, digest = match.groups()
        path = self.paths.manifests / kind / digest[:2] / f"{digest}.json"
        if not path.is_file():
            raise FileNotFoundError(f"manifest not found: {manifest_id}")
        return self.read_path(path)

    def iter_manifests(self) -> Iterable[ManifestRecord]:
        if not self.paths.manifests.exists():
            return
        for path in sorted(self.paths.manifests.glob("*/*/*.json")):
            yield self.read_path(path)

    def read_path(self, path: str | Path) -> ManifestRecord:
        path = Path(path)
        if path.is_symlink():
            raise IntegrityError(f"canonical manifest path must not be a symlink: {path}")
        try:
            relative_parts = path.resolve().relative_to(self.paths.manifests.resolve()).parts
        except ValueError as exc:
            raise IntegrityError(f"manifest path is outside canonical storage: {path}") from exc
        if len(relative_parts) != 3:
            raise IntegrityError(f"manifest path has an invalid layout: {path}")
        raw = path.read_bytes()
        value = loads_json_strict(raw)
        if not isinstance(value, dict):
            raise IntegrityError(f"manifest is not an object: {path}")
        if raw != canonical_json_file_bytes(value):
            raise IntegrityError(f"manifest file is not canonical JSON: {path}")
        self._validate(value)
        kind = value.get("kind")
        digest = sha256_bytes(canonical_json_bytes(value))
        if (
            not isinstance(kind, str)
            or relative_parts[0] != kind
            or relative_parts[1] != digest[:2]
            or path.stem != digest
        ):
            raise IntegrityError(f"content-addressed manifest integrity failure: {path}")
        return ManifestRecord(
            f"mft.{kind}.sha256.{digest}",
            kind,
            digest,
            path,
            value,
            False,
        )

    def _validate(self, manifest: Mapping[str, Any]) -> None:
        self.schemas.validate_manifest(manifest)
        for validator in self.validators:
            validator(manifest)
