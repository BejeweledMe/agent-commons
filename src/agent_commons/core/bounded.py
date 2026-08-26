"""Bounded copying and UTF-8-safe truncation for presentation consumers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from typing import Any

_TRUNCATION_MARKER = " …[truncated]"


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = _TRUNCATION_MARKER.encode("utf-8")
    prefix_limit = max(0, max_bytes - len(marker))
    prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER


@dataclass
class _CopyBudget:
    remaining_bytes: int
    remaining_nodes: int
    max_text_bytes: int
    max_children: int
    max_depth: int


def _bounded_copy(value: Any, budget: _CopyBudget, *, depth: int = 0) -> Any:
    if budget.remaining_nodes <= 0 or depth > budget.max_depth:
        return _TRUNCATION_MARKER.strip()
    budget.remaining_nodes -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = max(0, min(budget.max_text_bytes, budget.remaining_bytes))
        result = _truncate_utf8(value, limit)
        budget.remaining_bytes = max(0, budget.remaining_bytes - len(result.encode("utf-8")))
        return result
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        item_count = len(value)
        limit = item_count if depth == 0 else min(item_count, budget.max_children)
        for raw_key, child in islice(value.items(), limit):
            key = _truncate_utf8(str(raw_key), min(256, budget.max_text_bytes))
            output[key] = _bounded_copy(child, budget, depth=depth + 1)
        if item_count > limit:
            output["[truncated]"] = f"{item_count - limit} fields omitted"
        return output
    if isinstance(value, (list, tuple)):
        item_count = len(value)
        output = [
            _bounded_copy(child, budget, depth=depth + 1)
            for child in islice(value, budget.max_children)
        ]
        if item_count > budget.max_children:
            output.append(f"[truncated: {item_count - budget.max_children} items omitted]")
        return output
    return _bounded_copy(str(value), budget, depth=depth)


def truncate_utf8(value: str, max_bytes: int) -> str:
    """Return ``value`` truncated at a valid UTF-8 boundary."""

    return _truncate_utf8(value, max_bytes)


def bounded_copy(
    value: Any,
    *,
    max_total_bytes: int = 65_536,
    max_text_bytes: int = 4_096,
    max_children: int = 32,
    max_depth: int = 8,
) -> Any:
    """Bound an arbitrary record without splitting UTF-8 text or mutating it."""

    budget = _CopyBudget(
        remaining_bytes=max_total_bytes,
        remaining_nodes=4_096,
        max_text_bytes=max_text_bytes,
        max_children=max_children,
        max_depth=max_depth,
    )
    return _bounded_copy(value, budget)


__all__ = ["bounded_copy", "truncate_utf8"]
