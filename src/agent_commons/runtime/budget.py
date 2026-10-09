"""Content-free observation of reserved provider launch units, never billed spend."""

from __future__ import annotations

from dataclasses import dataclass

from .model import BuiltinProfileId, Provider
from .policy import PolicyViolationError


@dataclass(frozen=True, slots=True)
class ProviderUnitBudget:
    provider: Provider
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def to_wire(self, profile_id: BuiltinProfileId) -> dict[str, object]:
        if profile_id.provider is not self.provider:
            raise ValueError("budget profile/provider mismatch")
        return {
            "schema": "agent_commons.launch-budget.v1",
            "profile_id": profile_id.value,
            "provider": self.provider.value,
            "unit": "provider_units",
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "exhausted": self.exhausted,
        }


class OperatorBudgetExhaustedError(PolicyViolationError):
    """A fixed public refusal; provider output never contributes to this error."""

    code = "operator_budget_exhausted"
    canonical_reason_code = "budget_exhausted"
    safe_next_actions = ("review_operator_budget",)

    def __init__(self, observation: ProviderUnitBudget, profile_id: BuiltinProfileId) -> None:
        super().__init__("operator provider_units budget is exhausted")
        self.budget = observation.to_wire(profile_id)
