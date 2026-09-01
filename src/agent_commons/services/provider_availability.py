"""Unified, secret-free provider availability read model.

This service joins allowlisted adapter capabilities with operational
qualification and authentication observations.  It is derived state only: it
never writes canonical events, launches work, or retains provider output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from agent_commons.errors import ConfigurationError
from agent_commons.runtime.adapters import AdapterRegistry, default_adapter_registry
from agent_commons.runtime.capabilities import ProviderRefusalCode, TypedRefusal
from agent_commons.runtime.model import (
    BuiltinProfileId,
    ExecutableRole,
    ProfileRegistry,
    Provider,
    resolve_trusted_executable,
    validate_profile_launch_boundary,
)
from agent_commons.runtime.policy import OperatorLimits
from agent_commons.runtime.provider_qualification import (
    ProviderQualification,
    ProviderQualificationStore,
    qualification_fingerprint,
)


class AvailabilityRefusalCode(StrEnum):
    INSTALLATION_UNAVAILABLE = "provider_installation_unavailable"
    INITIALIZATION_FAILED = "provider_initialization_failed"
    QUALIFICATION_REQUIRED = "provider_qualification_required"
    QUALIFICATION_FAILED = "provider_qualification_failed"
    AUTHENTICATION_REQUIRED = "provider_authentication_required"
    AUTHENTICATION_UNCONFIRMED = "provider_authentication_unconfirmed"


class CapabilityRefusalCode(StrEnum):
    RESUME_UNAVAILABLE = "provider_resume_unavailable"
    SKILL_PROJECTION_UNAVAILABLE = "provider_skill_projection_unavailable"
    MONETARY_BUDGET_UNAVAILABLE = "provider_monetary_budget_unavailable"


_REMEDIATION: dict[AvailabilityRefusalCode, tuple[str, ...]] = {
    AvailabilityRefusalCode.INSTALLATION_UNAVAILABLE: ("verify_provider_installation",),
    AvailabilityRefusalCode.INITIALIZATION_FAILED: (
        "repair_provider_initialization",
        "rerun_provider_canary",
    ),
    AvailabilityRefusalCode.QUALIFICATION_REQUIRED: ("run_provider_canary",),
    AvailabilityRefusalCode.QUALIFICATION_FAILED: (
        "inspect_failed_provider_probe",
        "rerun_provider_canary",
    ),
    AvailabilityRefusalCode.AUTHENTICATION_REQUIRED: ("authenticate_provider",),
    AvailabilityRefusalCode.AUTHENTICATION_UNCONFIRMED: ("check_provider_authentication",),
}

_CAPABILITY_REMEDIATION: dict[CapabilityRefusalCode, tuple[str, ...]] = {
    CapabilityRefusalCode.RESUME_UNAVAILABLE: ("start_new_run",),
    CapabilityRefusalCode.SKILL_PROJECTION_UNAVAILABLE: (
        "remove_skill_requirement",
        "use_manual_workflow",
    ),
    CapabilityRefusalCode.MONETARY_BUDGET_UNAVAILABLE: (
        "use_provider_unit_budget",
        "choose_monetary_budget_profile",
    ),
}

_AUTH_STATES = frozenset(
    {
        "ready",
        "authentication_required",
        "authenticating",
        "timed_out",
        "cancelled",
        "failed",
        "unsupported",
        "credential_store_unavailable",
        "not_checked",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderAvailabilityRefusal:
    code: AvailabilityRefusalCode
    remediation: tuple[str, ...]

    @classmethod
    def create(cls, code: AvailabilityRefusalCode) -> ProviderAvailabilityRefusal:
        return cls(code=code, remediation=_REMEDIATION[code])

    def to_wire(self) -> dict[str, object]:
        return {"code": self.code.value, "remediation": list(self.remediation)}


@dataclass(frozen=True, slots=True)
class ProviderCapabilityRefusal:
    code: CapabilityRefusalCode
    remediation: tuple[str, ...]

    @classmethod
    def create(cls, code: CapabilityRefusalCode) -> ProviderCapabilityRefusal:
        return cls(code=code, remediation=_CAPABILITY_REMEDIATION[code])

    def to_wire(self) -> dict[str, object]:
        return {"code": self.code.value, "remediation": list(self.remediation)}


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    profile_id: BuiltinProfileId
    provider: Provider
    model: str | None
    capabilities: Mapping[str, object]
    capability_refusals: tuple[ProviderCapabilityRefusal, ...]
    installation_state: Literal["installed", "unavailable"]
    initialization_state: Literal["ready", "failed", "passed_unqualified", "not_checked"]
    qualification_state: Literal["qualified", "required", "failed"]
    qualification_freshness: Literal["current", "missing", "invalid"]
    qualification_fingerprint: str | None
    qualification_checked_at: str | None
    auth_state: str
    auth_freshness: Literal["fresh", "stale", "unknown"]
    launchable: bool
    refusal: ProviderAvailabilityRefusal | None
    operator_limits: Mapping[str, int | float]

    def to_wire(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id.value,
            "provider": self.provider.value,
            "model": self.model,
            "capabilities": dict(self.capabilities),
            "capability_refusals": [item.to_wire() for item in self.capability_refusals],
            "installation_state": self.installation_state,
            "initialization_state": self.initialization_state,
            "qualification": {
                "state": self.qualification_state,
                "freshness": self.qualification_freshness,
                "fingerprint": self.qualification_fingerprint,
                "checked_at": self.qualification_checked_at,
            },
            "authentication": {
                "state": self.auth_state,
                "freshness": self.auth_freshness,
            },
            "launchable": self.launchable,
            "refusal": self.refusal.to_wire() if self.refusal is not None else None,
            "operator_limits": dict(self.operator_limits),
        }


class ProviderAvailabilityService:
    """Compose safe availability for a fixed allowlisted profile registry."""

    def __init__(
        self,
        profiles: ProfileRegistry,
        *,
        workspace_root: str | Path,
        qualifications: ProviderQualificationStore,
        limits: OperatorLimits | None = None,
        adapters: AdapterRegistry | None = None,
    ) -> None:
        self.profiles = profiles
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.qualifications = qualifications
        self.limits = limits or OperatorLimits()
        self.adapters = adapters or default_adapter_registry()

    @staticmethod
    def _auth(
        value: Mapping[str, Any] | None,
    ) -> tuple[str, Literal["fresh", "stale", "unknown"]]:
        if value is None:
            return "not_checked", "unknown"
        state = value.get("state")
        freshness = value.get("freshness")
        if (
            not isinstance(state, str)
            or state not in _AUTH_STATES - {"not_checked"}
            or freshness not in {"fresh", "stale"}
        ):
            return "not_checked", "unknown"
        return state, freshness

    @staticmethod
    def _capability_refusals(
        *, provider: Provider, skills: tuple[str, ...]
    ) -> tuple[ProviderCapabilityRefusal, ...]:
        codes = [CapabilityRefusalCode.RESUME_UNAVAILABLE]
        if not skills:
            codes.append(CapabilityRefusalCode.SKILL_PROJECTION_UNAVAILABLE)
        if provider in {Provider.CODEX, Provider.GROK}:
            codes.append(CapabilityRefusalCode.MONETARY_BUDGET_UNAVAILABLE)
        return tuple(ProviderCapabilityRefusal.create(code) for code in codes)

    def _executables_available(self, profile: object) -> bool:
        """Resolve every allowlisted executable now without exposing its path."""

        values = (
            (getattr(profile, "executable", ""), ExecutableRole.PROVIDER),
            (getattr(profile, "mcp_executable", ""), ExecutableRole.MCP),
            (getattr(profile, "git_executable", ""), ExecutableRole.GIT),
        )
        try:
            for executable, role in values:
                resolve_trusted_executable(
                    str(executable),
                    workspace_root=self.workspace_root,
                    role=role,
                )
        except Exception:  # noqa: BLE001 - executable detail never crosses this seam
            return False
        return True

    @staticmethod
    def _launch_boundary_available(profile: object) -> bool:
        """Apply the same fixed host-isolation gate used by launch planning."""

        try:
            validate_profile_launch_boundary(profile)  # type: ignore[arg-type]
        except ConfigurationError:
            return False
        return True

    def describe(
        self,
        profile_id: str | BuiltinProfileId,
        *,
        auth: Mapping[str, Any] | None = None,
    ) -> ProviderAvailability:
        normalized = BuiltinProfileId(profile_id)
        profile = self.profiles.get(normalized)
        adapter = self.adapters.for_profile(profile)
        if isinstance(adapter, TypedRefusal):
            raise ValueError("configured provider profile has no allowlisted adapter")
        descriptor = adapter.describe(profile)
        capability_set = adapter.capabilities(profile)
        capabilities: dict[str, object] = {
            "mcp": capability_set.mcp,
            "skills": bool(capability_set.skills),
            "resume": "unavailable",
            "cancellation": capability_set.cancellation_mode.value,
            "usage_reporting": capability_set.usage_reporting.value,
            "sandbox_boundary": capability_set.sandbox_boundary.value,
            "budget_units": [unit.value for unit in capability_set.budget_units],
            "context_modes": ["fresh", "accumulated"],
        }
        capability_refusals = self._capability_refusals(
            provider=profile.provider,
            skills=capability_set.skills,
        )
        installation_state: Literal["installed", "unavailable"] = "installed"
        initialization_state: Literal["ready", "failed", "passed_unqualified", "not_checked"] = (
            "not_checked"
        )
        qualification_state: Literal["qualified", "required", "failed"] = "required"
        qualification_freshness: Literal["current", "missing", "invalid"] = "missing"
        fingerprint: str | None = None
        checked_at: str | None = None
        refusal: ProviderAvailabilityRefusal | None = None
        try:
            qualification = self.qualifications.status(
                profile,
                workspace_root=self.workspace_root,
            )
        except Exception:  # noqa: BLE001 - strict secrecy boundary over host details
            qualification: ProviderQualification | TypedRefusal = TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED,
                provider=profile.provider,
                profile_id=profile.profile_id,
            )
        # Qualification is historical evidence.  Resolve every executable
        # again after reading it so disappearance cannot inherit a green view.
        if not self._launch_boundary_available(profile):
            qualification_state = "failed"
            qualification_freshness = "invalid"
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.QUALIFICATION_FAILED
            )
        elif not self._executables_available(profile):
            installation_state = "unavailable"
            qualification_state = "failed"
            qualification_freshness = "invalid"
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.INSTALLATION_UNAVAILABLE
            )
        elif isinstance(qualification, ProviderQualification):
            initialization_state = "ready"
            qualification_state = "qualified"
            qualification_freshness = "current"
            fingerprint = qualification.fingerprint
            checked_at = qualification.checked_at
        elif qualification.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_REQUIRED:
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.QUALIFICATION_REQUIRED
            )
        else:
            qualification_state = "failed"
            qualification_freshness = "invalid"
            receipt = None
            try:
                receipt = self.qualifications.read(normalized)
                current_fingerprint = qualification_fingerprint(
                    profile,
                    workspace_root=self.workspace_root,
                )
            except Exception:  # noqa: BLE001 - corrupt receipt is invalid, never echoed
                receipt = None
            if receipt is not None and receipt.fingerprint == current_fingerprint:
                qualification_freshness = "current"
                checked_at = receipt.checked_at
                initialization_state = (
                    "passed_unqualified" if receipt.initialization_probe else "failed"
                )
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.INITIALIZATION_FAILED
                if initialization_state == "failed"
                else AvailabilityRefusalCode.QUALIFICATION_FAILED
            )

        auth_state, auth_freshness = self._auth(auth)
        if refusal is None and auth_freshness != "fresh":
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.AUTHENTICATION_UNCONFIRMED
            )
        elif refusal is None and auth_state == "authentication_required":
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.AUTHENTICATION_REQUIRED
            )
        elif refusal is None and auth_state not in {"ready", "unsupported"}:
            refusal = ProviderAvailabilityRefusal.create(
                AvailabilityRefusalCode.AUTHENTICATION_UNCONFIRMED
            )
        launchable = (
            installation_state == "installed"
            and initialization_state == "ready"
            and qualification_state == "qualified"
            and auth_freshness == "fresh"
            and auth_state in {"ready", "unsupported"}
            and refusal is None
        )
        return ProviderAvailability(
            profile_id=normalized,
            provider=profile.provider,
            model=descriptor.model,
            capabilities=capabilities,
            capability_refusals=capability_refusals,
            installation_state=installation_state,
            initialization_state=initialization_state,
            qualification_state=qualification_state,
            qualification_freshness=qualification_freshness,
            qualification_fingerprint=fingerprint,
            qualification_checked_at=checked_at,
            auth_state=auth_state,
            auth_freshness=auth_freshness,
            launchable=launchable,
            refusal=refusal,
            operator_limits={
                "global_concurrency": self.limits.global_concurrency,
                "provider_concurrency": self.limits.provider_concurrency_cap(
                    profile.provider.value
                ),
                "profile_concurrency": self.limits.profile_concurrency_cap(normalized.value),
                "parent_provider_units": self.limits.provider_units_cap(profile.provider.value),
                "queue_capacity": self.limits.queue_capacity,
                "queue_wait_seconds": self.limits.queue_wait_seconds,
            },
        )

    def list(
        self,
        *,
        auth_by_profile: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, object]]:
        auth_values = auth_by_profile or {}
        return [
            self.describe(profile_id, auth=auth_values.get(profile_id.value)).to_wire()
            for profile_id in self.profiles.profile_ids
        ]
