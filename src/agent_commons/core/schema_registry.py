"""JSON Schema registry with packaged core schemas and optional extensions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ValidationError


class _Traversable(Protocol):
    @property
    def name(self) -> str: ...

    def is_dir(self) -> bool: ...

    def is_file(self) -> bool: ...

    def iterdir(self) -> Iterable[_Traversable]: ...

    def read_bytes(self) -> bytes: ...


def _iter_json_resources(root: _Traversable) -> Iterable[tuple[str, bytes]]:
    if root.is_file():
        if root.name.endswith(".json"):
            yield root.name, root.read_bytes()
        return
    if not root.is_dir():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        for relative, raw in _iter_json_resources(child):
            yield f"{child.name}/{relative}" if child.is_dir() else relative, raw


class SchemaRegistry:
    """Load core and extension schemas by stable logical name."""

    def __init__(self, extension_roots: Iterable[str | Path] = ()) -> None:
        self.extension_roots = tuple(Path(item) for item in extension_roots)
        self._schemas: dict[str, dict[str, Any]] = {}
        self._resources = Registry()
        self.reload()

    @staticmethod
    def _packaged_root() -> _Traversable:
        return resources.files("agent_commons.resources").joinpath("schemas")

    def reload(self) -> None:
        schemas: dict[str, dict[str, Any]] = {}
        registry = Registry()
        documents: list[tuple[str, Any]] = list(_iter_json_resources(self._packaged_root()))
        for root in self.extension_roots:
            if not root.is_dir():
                raise ValidationError(f"schema extension directory does not exist: {root}")
            documents.extend(
                (str(path), path.read_bytes()) for path in sorted(root.rglob("*.json"))
            )

        for source, raw in documents:
            value = loads_json_strict(raw)
            if not isinstance(value, dict):
                raise ValidationError(f"schema root must be an object: {source}")
            logical_name = value.get("x-schema-name")
            schema_id = value.get("$id")
            if not isinstance(logical_name, str) or not logical_name:
                raise ValidationError(f"schema x-schema-name is required: {source}")
            if not isinstance(schema_id, str) or not schema_id:
                raise ValidationError(f"schema $id is required: {source}")
            if logical_name in schemas:
                raise ValidationError(f"duplicate logical schema name: {logical_name}")
            try:
                Draft202012Validator.check_schema(value)
                registry = registry.with_resource(schema_id, Resource.from_contents(value))
            except Exception as exc:
                raise ValidationError(f"invalid JSON Schema {logical_name}: {exc}") from exc
            schemas[logical_name] = value

        self._schemas = schemas
        self._resources = registry

    @property
    def schema_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))

    def schema(self, logical_name: str) -> Mapping[str, Any]:
        try:
            return self._schemas[logical_name]
        except KeyError as exc:
            raise ValidationError(f"unknown schema: {logical_name}") from exc

    def validate(self, logical_name: str, instance: Any) -> None:
        schema = self.schema(logical_name)
        validator = Draft202012Validator(
            schema,
            registry=self._resources,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
        if errors:
            details = []
            for error in errors:
                location = "/" + "/".join(str(part) for part in error.absolute_path)
                details.append(f"{location}: {error.message}")
            raise ValidationError(f"{logical_name}: " + "; ".join(details))

    def validate_event(self, event: Mapping[str, Any]) -> None:
        self.validate("commons.event.v1", event)
        payload_schema = event.get("payload_schema")
        if not isinstance(payload_schema, str):
            raise ValidationError("event payload_schema must be a string")
        schema = self.schema(payload_schema)
        singular = schema.get("x-event-type")
        family = schema.get("x-event-types")
        if singular is not None and family is not None:
            raise ValidationError(
                f"payload schema {payload_schema} cannot declare both "
                "x-event-type and x-event-types"
            )
        if isinstance(singular, str) and singular:
            allowed_types = (singular,)
        elif (
            isinstance(family, list)
            and family
            and all(isinstance(item, str) and item for item in family)
            and len(set(family)) == len(family)
        ):
            allowed_types = tuple(family)
        else:
            raise ValidationError(
                f"payload schema {payload_schema} must declare x-event-type "
                "or a unique non-empty x-event-types list"
            )
        if event.get("event_type") not in allowed_types:
            raise ValidationError(
                f"payload schema {payload_schema} permits {allowed_types!r}, "
                f"not {event.get('event_type')!r}"
            )
        self.validate(payload_schema, event.get("payload"))

    def validate_manifest(self, manifest: Mapping[str, Any]) -> None:
        schema_name = manifest.get("schema")
        if not isinstance(schema_name, str):
            raise ValidationError("manifest schema must be a string")
        schema = self.schema(schema_name)
        expected_kind = schema.get("x-manifest-kind")
        if not isinstance(expected_kind, str):
            raise ValidationError(f"schema is not a manifest schema: {schema_name}")
        if expected_kind != manifest.get("kind"):
            raise ValidationError(
                f"manifest schema {schema_name} is for {expected_kind!r}, "
                f"not {manifest.get('kind')!r}"
            )
        self.validate(schema_name, manifest)

    @classmethod
    def from_schema_files(cls, paths: Iterable[str | Path]) -> SchemaRegistry:
        """Convenience constructor for tests and embedders with loose files."""

        roots = {Path(path).parent for path in paths}
        return cls(sorted(roots))
