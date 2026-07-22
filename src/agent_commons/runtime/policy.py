"""Monotonic delegation limits for local agent execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
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


_PROVIDERS = frozenset({"codex", "claude"})
_PROFILES = frozenset(
    {
        "codex-builder",
        "codex-independent-reviewer",
        "claude-builder",
        "claude-independent-reviewer",
    }
)


def _validated_limits(
    name: str,
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
) -> Mapping[str, int]:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PolicyViolationError(f"{name} has unsupported keys: {', '.join(unknown)}")
    return MappingProxyType(
        {str(key): _positive(f"{name}.{key}", raw) for key, raw in value.items()}
    )


@dataclass(frozen=True, slots=True)
class OperatorLimits:
    """Operator-owned caps shared by every broker using one state root."""

    global_concurrency: int = 2
    queue_capacity: int = 8
    queue_wait_seconds: int = 30
    parent_provider_units: int = 4
    parent_budget_microusd: int = 10_000_000
    provider_concurrency: Mapping[str, int] = field(
        default_factory=lambda: {"codex": 2, "claude": 2}
    )
    profile_concurrency: Mapping[str, int] = field(
        default_factory=lambda: {
            "codex-builder": 1,
            "codex-independent-reviewer": 1,
            "claude-builder": 1,
            "claude-independent-reviewer": 1,
        }
    )
    provider_parent_provider_units: Mapping[str, int] = field(default_factory=dict)
    provider_parent_budget_microusd: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive("global_concurrency", self.global_concurrency)
        _nonnegative("queue_capacity", self.queue_capacity)
        _positive("queue_wait_seconds", self.queue_wait_seconds)
        _positive("parent_provider_units", self.parent_provider_units)
        _positive("parent_budget_microusd", self.parent_budget_microusd)
        object.__setattr__(
            self,
            "provider_concurrency",
            _validated_limits(
                "provider_concurrency", self.provider_concurrency, allowed=_PROVIDERS
            ),
        )
        object.__setattr__(
            self,
            "profile_concurrency",
            _validated_limits("profile_concurrency", self.profile_concurrency, allowed=_PROFILES),
        )
        object.__setattr__(
            self,
            "provider_parent_provider_units",
            _validated_limits(
                "provider_parent_provider_units",
                self.provider_parent_provider_units,
                allowed=_PROVIDERS,
            ),
        )
        object.__setattr__(
            self,
            "provider_parent_budget_microusd",
            _validated_limits(
                "provider_parent_budget_microusd",
                self.provider_parent_budget_microusd,
                allowed=_PROVIDERS,
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> OperatorLimits:
        if value is None:
            return cls()
        defaults = cls()
        allowed = {
            "global_concurrency",
            "queue_capacity",
            "queue_wait_seconds",
            "parent_provider_units",
            "parent_budget_microusd",
            "provider_concurrency",
            "profile_concurrency",
            "provider_parent_provider_units",
            "provider_parent_budget_microusd",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise PolicyViolationError(
                "operator limits have unsupported fields: " + ", ".join(unknown)
            )
        mappings = {
            "provider_concurrency",
            "profile_concurrency",
            "provider_parent_provider_units",
            "provider_parent_budget_microusd",
        }
        normalized: dict[str, Any] = {}
        for key, raw in value.items():
            if key in mappings:
                if not isinstance(raw, Mapping):
                    raise PolicyViolationError(f"operator limit {key} must be a mapping")
                normalized[key] = {**dict(getattr(defaults, key)), **dict(raw)}
            else:
                normalized[key] = raw
        return cls(**normalized)

    def provider_concurrency_cap(self, provider: str) -> int:
        return min(self.global_concurrency, self.provider_concurrency.get(provider, 1))

    def profile_concurrency_cap(self, profile_id: str) -> int:
        provider = profile_id.partition("-")[0]
        return min(
            self.global_concurrency,
            self.provider_concurrency_cap(provider),
            self.profile_concurrency.get(profile_id, 1),
        )

    def provider_units_cap(self, provider: str) -> int:
        return min(
            self.parent_provider_units,
            self.provider_parent_provider_units.get(provider, self.parent_provider_units),
        )

    def budget_microusd_cap(self, provider: str) -> int:
        return min(
            self.parent_budget_microusd,
            self.provider_parent_budget_microusd.get(provider, self.parent_budget_microusd),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "global_concurrency": self.global_concurrency,
            "queue_capacity": self.queue_capacity,
            "queue_wait_seconds": self.queue_wait_seconds,
            "parent_provider_units": self.parent_provider_units,
            "parent_budget_microusd": self.parent_budget_microusd,
            "provider_concurrency": dict(self.provider_concurrency),
            "profile_concurrency": dict(self.profile_concurrency),
            "provider_parent_provider_units": dict(self.provider_parent_provider_units),
            "provider_parent_budget_microusd": dict(self.provider_parent_budget_microusd),
        }
