"""Monotonic delegation limits for local agent execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agent_commons.errors import ValidationError


class PolicyViolationError(ValidationError):
    """A delegation exceeds authority inherited from its parent."""


def _positive(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyViolationError(f"{name} must be a positive integer")
    return value


def _nonnegative(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyViolationError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeUsage:
    active_fanout: int = 0
    attempts_started: int = 0
    active_concurrency: int = 0

    def __post_init__(self) -> None:
        _nonnegative("active_fanout", self.active_fanout)
        _nonnegative("attempts_started", self.attempts_started)
        _nonnegative("active_concurrency", self.active_concurrency)


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    """Limits carried down a delegation tree without authority amplification.

    ``remaining_depth`` belongs to the holder of this policy.  Creating a child
    consumes one level; all other child limits must remain equal or decrease.
    ``max_budget_microusd`` is a monetary ceiling when the selected provider can
    enforce one.  A bounded parent can never produce an unbounded child.
    """

    remaining_depth: int = 1
    max_fanout: int = 1
    max_attempts: int = 1
    max_concurrency: int = 1
    timeout_seconds: int = 1_800
    max_output_bytes: int = 1_048_576
    max_budget_microusd: int | None = None

    def __post_init__(self) -> None:
        _nonnegative("remaining_depth", self.remaining_depth)
        _positive("max_fanout", self.max_fanout)
        _positive("max_attempts", self.max_attempts)
        _positive("max_concurrency", self.max_concurrency)
        _positive("timeout_seconds", self.timeout_seconds)
        _positive("max_output_bytes", self.max_output_bytes)
        if self.max_budget_microusd is not None:
            _positive("max_budget_microusd", self.max_budget_microusd)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RuntimePolicy:
        allowed = {
            "remaining_depth",
            "max_fanout",
            "max_attempts",
            "max_concurrency",
            "timeout_seconds",
            "max_output_bytes",
            "max_budget_microusd",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise PolicyViolationError(
                "runtime policy has unsupported fields: " + ", ".join(unknown)
            )
        return cls(**value)

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)

    def derive_child(self, **reductions: int | None) -> RuntimePolicy:
        allowed = set(self.as_dict())
        unknown = sorted(set(reductions) - allowed)
        if unknown:
            raise PolicyViolationError("child policy has unsupported fields: " + ", ".join(unknown))
        if self.remaining_depth < 1:
            raise PolicyViolationError("delegation depth is exhausted")
        values = self.as_dict()
        values["remaining_depth"] = self.remaining_depth - 1
        values.update(reductions)
        child = RuntimePolicy(**values)
        child.assert_reduction_of(self)
        return child

    def assert_reduction_of(self, parent: RuntimePolicy) -> None:
        if self.remaining_depth >= parent.remaining_depth:
            raise PolicyViolationError("child remaining_depth must consume one parent level")
        for name in (
            "max_fanout",
            "max_attempts",
            "max_concurrency",
            "timeout_seconds",
            "max_output_bytes",
        ):
            if getattr(self, name) > getattr(parent, name):
                raise PolicyViolationError(f"child {name} exceeds the parent limit")
        if parent.max_budget_microusd is not None and (
            self.max_budget_microusd is None
            or self.max_budget_microusd > parent.max_budget_microusd
        ):
            raise PolicyViolationError("child monetary budget exceeds the parent limit")

    def assert_launch_allowed(self, usage: RuntimeUsage) -> None:
        if self.remaining_depth < 1:
            raise PolicyViolationError("delegation depth is exhausted")
        if usage.active_fanout >= self.max_fanout:
            raise PolicyViolationError("delegation fanout limit is exhausted")
        if usage.attempts_started >= self.max_attempts:
            raise PolicyViolationError("delegation attempt limit is exhausted")
        if usage.active_concurrency >= self.max_concurrency:
            raise PolicyViolationError("runtime concurrency limit is exhausted")
