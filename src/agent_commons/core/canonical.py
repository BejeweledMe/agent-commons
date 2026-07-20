"""Strict, deterministic JSON serialization for canonical records."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from agent_commons.errors import ValidationError


def _assert_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"non-finite JSON number at {path}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_json_value(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"JSON object key at {path} is not a string")
            _assert_json_value(child, f"{path}.{key}")
        return
    raise ValidationError(f"unsupported JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a strict JSON value with one stable byte representation."""

    _assert_json_value(value)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:  # defensive; validation runs first
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def canonical_json_file_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def sha256_bytes(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValidationError(f"duplicate JSON object key: {key!r}")
        value[key] = child
    return value


def loads_json_strict(data: str | bytes) -> Any:
    def reject_constant(token: str) -> None:
        raise ValidationError(f"non-finite JSON number is forbidden: {token}")

    try:
        value = json.loads(
            data,
            parse_constant=reject_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except ValidationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc
    _assert_json_value(value)
    return value


def load_json_strict(path: str | Path) -> Any:
    return loads_json_strict(Path(path).read_bytes())
