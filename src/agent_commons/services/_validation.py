"""Shared normalization helpers for service command inputs."""

from __future__ import annotations

from collections.abc import Sequence

from agent_commons.errors import ValidationError


def _optional_list(values: Sequence[str], label: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if any(not value for value in result):
        raise ValidationError(f"{label} must contain non-empty values")
    return result


def _nonempty_list(values: Sequence[str], label: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if not result or any(not value for value in result):
        raise ValidationError(f"{label} must contain non-empty values")
    return result
