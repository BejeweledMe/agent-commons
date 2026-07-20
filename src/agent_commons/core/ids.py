"""Sortable and deterministic typed identifiers."""

from __future__ import annotations

import hashlib
import re
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TYPED_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[0-9A-HJKMNP-TV-Z]{26}$")


def _validate_prefix(prefix: str) -> None:
    if not isinstance(prefix, str) or _PREFIX_RE.fullmatch(prefix) is None:
        raise ValueError(f"invalid ID prefix: {prefix!r}")


def _encode_crockford(value: int, length: int) -> str:
    characters = ["0"] * length
    for index in range(length - 1, -1, -1):
        characters[index] = _ALPHABET[value & 31]
        value >>= 5
    if value:
        raise ValueError("value does not fit the requested Crockford length")
    return "".join(characters)


def new_sortable_id(prefix: str) -> str:
    """Return a typed ULID-compatible identifier."""

    _validate_prefix(prefix)
    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    randomness = int.from_bytes(secrets.token_bytes(10), "big")
    return f"{prefix}.{_encode_crockford((timestamp_ms << 80) | randomness, 26)}"


def stable_id(prefix: str, seed: str | bytes) -> str:
    """Map stable input to a typed 128-bit content identity."""

    _validate_prefix(prefix)
    if isinstance(seed, str):
        material = seed.encode("utf-8")
    elif isinstance(seed, bytes):
        material = seed
    else:
        raise TypeError("stable ID seed must be str or bytes")
    if not material:
        raise ValueError("stable ID seed must not be empty")
    value = int.from_bytes(hashlib.sha256(material).digest()[-16:], "big")
    return f"{prefix}.{_encode_crockford(value, 26)}"


def is_typed_id(value: object, prefix: str | None = None) -> bool:
    if not isinstance(value, str) or _TYPED_ID_RE.fullmatch(value) is None:
        return False
    return prefix is None or value.startswith(prefix + ".")
