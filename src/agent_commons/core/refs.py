"""Explicit typed references; field names never imply dependency edges."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_commons.errors import ValidationError

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, order=True)
class TypedRef:
    kind: str
    id: str

    def __post_init__(self) -> None:
        if _KIND_RE.fullmatch(self.kind) is None:
            raise ValidationError(f"invalid reference kind: {self.kind!r}")
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValidationError("reference id must be a non-empty string")

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


def normalize_ref(value: TypedRef | Mapping[str, Any]) -> dict[str, str]:
    if isinstance(value, TypedRef):
        return value.as_dict()
    if not isinstance(value, Mapping) or set(value) != {"kind", "id"}:
        raise ValidationError("reference must contain exactly 'kind' and 'id'")
    return TypedRef(str(value["kind"]), value["id"]).as_dict()


def parse_ref(value: str) -> TypedRef:
    if not isinstance(value, str) or ":" not in value:
        raise ValidationError("reference must use '<kind>:<id>' syntax")
    kind, identifier = value.split(":", 1)
    return TypedRef(kind, identifier)


def ref_key(value: TypedRef | Mapping[str, Any]) -> tuple[str, str]:
    normalized = normalize_ref(value)
    return normalized["kind"], normalized["id"]


def iter_typed_refs(value: Any) -> Iterable[TypedRef]:
    """Yield only explicit `{kind, id}` objects, never suffix-inferred strings."""

    if isinstance(value, Mapping):
        if set(value) == {"kind", "id"}:
            yield TypedRef(str(value["kind"]), value["id"])
            return
        for child in value.values():
            yield from iter_typed_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_typed_refs(child)
