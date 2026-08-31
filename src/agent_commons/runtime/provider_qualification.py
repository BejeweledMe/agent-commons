"""Operational, source-bound qualification for allowlisted provider profiles.

Qualification is deliberately not canonical project truth.  It is a private
host receipt proving that one exact profile/runtime/executable bundle passed
the three distinct compatibility gates required before a paid launch: static
preflight, provider initialization, and a scoped terminal-MCP behavior canary.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import loads_json_strict
from agent_commons.errors import ConfigurationError, IntegrityError, ValidationError
from agent_commons.storage.atomic import atomic_write_replace
from agent_commons.storage.opstate import (
    PROVIDER_QUALIFICATION_STORAGE,
    ensure_private_directory,
    exclusive_lock,
    strict_state_bytes,
)

from .adapters import AdapterRegistry, default_adapter_registry
from .capabilities import ProviderRefusalCode, TypedRefusal
from .model import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    ExecutableRole,
    RunnerProfile,
    resolve_trusted_executable,
    validate_profile_launch_boundary,
)
from .source_contract import agent_commons_source_sha256

QUALIFICATION_SCHEMA = "agent_commons.provider_qualification.v1"
_QUALIFICATION_DIRECTORY = "provider-qualification"
_SHA256_LENGTH = 64
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "profile_id",
        "provider",
        "fingerprint",
        "qualified",
        "probes",
        "checked_at",
        "provider_version",
    }
)
_PROBE_KEYS = frozenset(
    {
        "static_preflight",
        "initialization_probe",
        "behavioral_canary",
    }
)


def _timestamp() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _canonical_utc_timestamp(value: object) -> str:
    """Own exactly the canonical UTC spelling emitted by :func:`_timestamp`."""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise IntegrityError("provider qualification timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise IntegrityError("provider qualification timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise IntegrityError("provider qualification timestamp is invalid")
    canonical = parsed.isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise IntegrityError("provider qualification timestamp is not canonical UTC")
    return value


def _launch_policy(profile: RunnerProfile) -> dict[str, object]:
    """Return every fixed safe-launch policy field qualified by the receipt."""

    validate_profile_launch_boundary(profile)
    if isinstance(profile, CodexRunnerProfile):
        return {
            "approval_policy": profile.approval_policy.value,
            "sandbox": profile.sandbox.value,
            "trusted_workspace": profile.trusted_workspace,
        }
    if isinstance(profile, ClaudeRunnerProfile):
        return {
            "max_budget_microusd": profile.max_budget_microusd,
            "permission_mode": profile.permission_mode.value,
            "trusted_workspace": profile.trusted_workspace,
        }
    raise ConfigurationError("provider profile has no fixed launch policy")


def _executable_identity(
    executable_value: str,
    *,
    workspace_root: Path,
    role: ExecutableRole,
) -> str:
    """Hash stable file metadata without retaining an executable path."""

    executable = resolve_trusted_executable(
        executable_value,
        workspace_root=workspace_root,
        role=role,
    )
    descriptor = -1
    try:
        descriptor = os.open(executable, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_EXECUTABLE_BYTES:
            raise ConfigurationError("provider executable cannot be fingerprinted safely")
        digest = hashlib.sha256()
        digest.update(f"mode:{metadata.st_mode & 0o7777}\0".encode("ascii"))
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ConfigurationError("provider executable cannot be fingerprinted safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def qualification_fingerprint(
    profile: RunnerProfile,
    *,
    workspace_root: str | Path,
    adapters: AdapterRegistry | None = None,
) -> str:
    """Fingerprint the exact safe runtime bundle a receipt qualifies."""

    root = Path(workspace_root).expanduser().resolve()
    registry = adapters or default_adapter_registry()
    adapter = registry.for_profile(profile)
    if isinstance(adapter, TypedRefusal):
        raise ConfigurationError("provider profile has no allowlisted adapter")
    descriptor = adapter.describe(profile)
    capabilities = adapter.capabilities(profile)
    body = {
        "profile_id": profile.profile_id.value,
        "provider": profile.provider.value,
        "model": getattr(profile, "model", None),
        "adapter_version": descriptor.adapter_version,
        "capabilities": capabilities.as_dict(),
        "launch_policy": _launch_policy(profile),
        "agent_commons_source_sha256": agent_commons_source_sha256(),
        "executable_identities": {
            "provider": _executable_identity(
                str(getattr(profile, "executable", "")),
                workspace_root=root,
                role=ExecutableRole.PROVIDER,
            ),
            "mcp": _executable_identity(
                str(getattr(profile, "mcp_executable", "")),
                workspace_root=root,
                role=ExecutableRole.MCP,
            ),
            "git": _executable_identity(
                str(getattr(profile, "git_executable", "")),
                workspace_root=root,
                role=ExecutableRole.GIT,
            ),
        },
    }
    return hashlib.sha256(strict_state_bytes(body)).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderQualification:
    profile_id: BuiltinProfileId
    fingerprint: str
    qualified: bool
    static_preflight: bool
    initialization_probe: bool
    behavioral_canary: bool
    checked_at: str
    provider_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", BuiltinProfileId(self.profile_id))
        if len(self.fingerprint) != _SHA256_LENGTH or any(
            character not in "0123456789abcdef" for character in self.fingerprint
        ):
            raise IntegrityError("provider qualification fingerprint is invalid")
        if self.qualified != (
            self.static_preflight and self.initialization_probe and self.behavioral_canary
        ):
            raise IntegrityError("provider qualification verdict is inconsistent")
        object.__setattr__(self, "checked_at", _canonical_utc_timestamp(self.checked_at))
        if self.provider_version is not None and (
            not isinstance(self.provider_version, str)
            or len(self.provider_version.encode("utf-8")) > 128
        ):
            raise IntegrityError("provider qualification version is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": QUALIFICATION_SCHEMA,
            "profile_id": self.profile_id.value,
            "provider": self.profile_id.provider.value,
            "fingerprint": self.fingerprint,
            "qualified": self.qualified,
            "probes": {
                "static_preflight": self.static_preflight,
                "initialization_probe": self.initialization_probe,
                "behavioral_canary": self.behavioral_canary,
            },
            "checked_at": self.checked_at,
            "provider_version": self.provider_version,
        }

    def refusal(self) -> TypedRefusal | None:
        if self.qualified:
            return None
        return TypedRefusal.create(
            ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED,
            provider=self.profile_id.provider,
            profile_id=self.profile_id,
        )


class ProviderQualificationStore:
    """One replaceable derived receipt per fixed built-in profile."""

    def __init__(self, state_root: str | Path, *, read_only: bool = False) -> None:
        self.root = Path(state_root).expanduser().resolve() / "runtime" / _QUALIFICATION_DIRECTORY
        self.read_only = read_only

    def _path(self, profile_id: BuiltinProfileId) -> Path:
        return self.root / f"{profile_id.value}.json"

    def record(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: str | Path,
        static_preflight: bool,
        initialization_probe: bool,
        behavioral_canary: bool,
        provider_version: str | None,
    ) -> ProviderQualification:
        if self.read_only:
            raise ConfigurationError("provider qualification store is read-only")
        fingerprint = qualification_fingerprint(profile, workspace_root=workspace_root)
        receipt = ProviderQualification(
            profile_id=profile.profile_id,
            fingerprint=fingerprint,
            qualified=static_preflight and initialization_probe and behavioral_canary,
            static_preflight=static_preflight,
            initialization_probe=initialization_probe,
            behavioral_canary=behavioral_canary,
            checked_at=_timestamp(),
            provider_version=provider_version,
        )
        path = self._path(profile.profile_id)
        ensure_private_directory(path.parent, policy=PROVIDER_QUALIFICATION_STORAGE)
        with exclusive_lock(path.with_suffix(".lock"), policy=PROVIDER_QUALIFICATION_STORAGE):
            atomic_write_replace(path, strict_state_bytes(receipt.as_dict()), mode=0o600)
        return receipt

    def read(self, profile_id: str | BuiltinProfileId) -> ProviderQualification | None:
        normalized = BuiltinProfileId(profile_id)
        path = self._path(normalized)
        if self.root.is_symlink() or path.is_symlink():
            raise IntegrityError("provider qualification receipt must not be a symlink")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_RECEIPT_BYTES:
                raise IntegrityError("provider qualification receipt is unsafe")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(_MAX_RECEIPT_BYTES + 1)
            descriptor = -1
            value = loads_json_strict(raw)
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError) as exc:
            raise IntegrityError("provider qualification receipt is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not isinstance(value, Mapping)
            or frozenset(value) != _RECEIPT_KEYS
            or value.get("schema") != QUALIFICATION_SCHEMA
        ):
            raise IntegrityError("provider qualification receipt has an invalid envelope")
        if raw != strict_state_bytes(value):
            raise IntegrityError("provider qualification receipt is not canonical JSON")
        probes = value.get("probes")
        if (
            not isinstance(probes, Mapping)
            or frozenset(probes) != _PROBE_KEYS
            or any(type(probes[key]) is not bool for key in _PROBE_KEYS)
        ):
            raise IntegrityError("provider qualification probes are invalid")
        if value.get("profile_id") != normalized.value:
            raise IntegrityError("provider qualification receipt profile does not match its path")
        if value.get("provider") != normalized.provider.value:
            raise IntegrityError("provider qualification provider/profile mismatch")
        if type(value.get("qualified")) is not bool:
            raise IntegrityError("provider qualification verdict is invalid")
        if not isinstance(value.get("fingerprint"), str):
            raise IntegrityError("provider qualification fingerprint is invalid")
        checked_at = _canonical_utc_timestamp(value.get("checked_at"))
        if value.get("provider_version") is not None and not isinstance(
            value.get("provider_version"), str
        ):
            raise IntegrityError("provider qualification version is invalid")
        return ProviderQualification(
            profile_id=normalized,
            fingerprint=str(value.get("fingerprint")),
            qualified=value["qualified"],
            static_preflight=probes["static_preflight"],
            initialization_probe=probes["initialization_probe"],
            behavioral_canary=probes["behavioral_canary"],
            checked_at=checked_at,
            provider_version=(
                str(value["provider_version"])
                if value.get("provider_version") is not None
                else None
            ),
        )

    def status(
        self,
        profile: RunnerProfile,
        *,
        workspace_root: str | Path,
    ) -> ProviderQualification | TypedRefusal:
        try:
            receipt = self.read(profile.profile_id)
            expected = qualification_fingerprint(profile, workspace_root=workspace_root)
        except (ConfigurationError, IntegrityError):
            return TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED,
                provider=profile.provider,
                profile_id=profile.profile_id,
            )
        if receipt is None:
            return TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_QUALIFICATION_REQUIRED,
                provider=profile.provider,
                profile_id=profile.profile_id,
            )
        if receipt.fingerprint != expected or not receipt.qualified:
            return TypedRefusal.create(
                ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED,
                provider=profile.provider,
                profile_id=profile.profile_id,
            )
        return receipt
