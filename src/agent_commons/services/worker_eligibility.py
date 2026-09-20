"""Closed, fail-closed worker choices for specialization hiring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from agent_commons.errors import ValidationError
from agent_commons.library import LibraryStore
from agent_commons.runtime.model import ProfileRegistry
from agent_commons.services.provider_availability import ProviderAvailabilityService


class WorkerEligibilityRefusalCode(StrEnum):
    OBSERVATION_MISSING = "worker_observation_missing"
    SPECIALIZATION_UNAVAILABLE = "specialization_unavailable"
    REVIEW_PROFILE_INCOMPATIBLE = "review_profile_incompatible"


_REMEDIATION: dict[WorkerEligibilityRefusalCode, tuple[str, ...]] = {
    WorkerEligibilityRefusalCode.OBSERVATION_MISSING: ("refresh_worker_availability",),
    WorkerEligibilityRefusalCode.SPECIALIZATION_UNAVAILABLE: ("choose_available_specialization",),
    WorkerEligibilityRefusalCode.REVIEW_PROFILE_INCOMPATIBLE: ("choose_builder_profile",),
}


@dataclass(frozen=True, slots=True)
class WorkerIneligibleError(ValidationError):
    """Typed refusal that a hire route can return without a canonical write."""

    refusal: dict[str, object]
    code: str = "worker_ineligible"

    def __str__(self) -> str:
        return "the selected worker is not eligible for this specialization"


def _refusal(code: str, remediation: tuple[str, ...] | list[str]) -> dict[str, object]:
    return {"code": code, "remediation": list(remediation)}


class WorkerEligibilityService:
    """Compose eligibility from the launch truth, profile purpose, and library."""

    schema = "agent_commons.worker-eligibility.v1"

    def __init__(
        self,
        profiles: ProfileRegistry,
        *,
        availability: ProviderAvailabilityService,
        library: LibraryStore,
    ) -> None:
        self.profiles = profiles
        self.availability = availability
        self.library = library

    def _specialization(self, value: object) -> tuple[dict[str, str] | None, bool]:
        try:
            snapshot = self.library.resolve(value)
            ref = snapshot["ref"]
            if not isinstance(ref, dict):  # defensive: LibraryStore normally proves this
                return None, False
            role_ref = {key: str(ref[key]) for key in ("kind", "source", "id", "version")}
            # A reviewer specialization is deliberately explicit in its stable identity.
            return role_ref, role_ref["id"].endswith("-reviewer")
        except Exception:  # no library detail crosses this read model
            return None, False

    @staticmethod
    def _capabilities(profile: object, availability: Mapping[str, object]) -> list[str]:
        values: list[str] = []
        raw = availability.get("capabilities")
        if isinstance(raw, Mapping) and raw.get("mcp") is True:
            values.append("tools")
        if isinstance(raw, Mapping) and raw.get("skills") is True:
            values.append("skills_projection")
        if getattr(profile, "trusted_workspace", False):
            values.append("trusted_workspace")
        if getattr(getattr(profile, "profile_id", None), "independent_reviewer", False):
            values.append("review_only")
        return values

    def payload(
        self,
        specialization: object,
        *,
        auth_by_profile: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, object]:
        role_ref, is_review_role = self._specialization(specialization)
        workers: list[dict[str, object]] = []
        for profile_id in self.profiles.profile_ids:
            profile = self.profiles.get(profile_id)
            availability = self.availability.describe(
                profile_id, auth=auth_by_profile.get(profile_id.value)
            ).to_wire()
            qualification = availability["qualification"]
            authentication = availability["authentication"]
            observed_revision = qualification["fingerprint"]
            refusal: dict[str, object] | None = None
            eligibility: Literal["eligible", "ineligible", "unknown"]
            if role_ref is None:
                eligibility = "unknown"
                refusal = _refusal(
                    WorkerEligibilityRefusalCode.SPECIALIZATION_UNAVAILABLE.value,
                    _REMEDIATION[WorkerEligibilityRefusalCode.SPECIALIZATION_UNAVAILABLE],
                )
            elif qualification["freshness"] != "current" or authentication["freshness"] != "fresh":
                eligibility = "unknown"
                refusal = _refusal(
                    WorkerEligibilityRefusalCode.OBSERVATION_MISSING.value,
                    _REMEDIATION[WorkerEligibilityRefusalCode.OBSERVATION_MISSING],
                )
            elif profile_id.independent_reviewer and not is_review_role:
                eligibility = "ineligible"
                refusal = _refusal(
                    WorkerEligibilityRefusalCode.REVIEW_PROFILE_INCOMPATIBLE.value,
                    _REMEDIATION[WorkerEligibilityRefusalCode.REVIEW_PROFILE_INCOMPATIBLE],
                )
            elif not availability["launchable"]:
                eligibility = "ineligible"
                refusal = availability["refusal"]  # type: ignore[assignment]
                if refusal is None:  # an unexpected launch refusal must not become eligible
                    refusal = _refusal(
                        WorkerEligibilityRefusalCode.OBSERVATION_MISSING.value,
                        _REMEDIATION[WorkerEligibilityRefusalCode.OBSERVATION_MISSING],
                    )
            else:
                eligibility = "eligible"
            workers.append(
                {
                    "profile_id": profile_id.value,
                    "provider": profile.provider.value,
                    "model": availability["model"],
                    "eligibility": eligibility,
                    "refusal": refusal,
                    "capabilities": self._capabilities(profile, availability),
                    "observed_revision": observed_revision,
                }
            )
        return {"schema": self.schema, "specialization": role_ref, "workers": workers}
