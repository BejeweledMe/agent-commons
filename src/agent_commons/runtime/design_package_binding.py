"""Immutable, provider-neutral binding of one exact Design Package revision.

The runtime needs to know that an operator selected a current Design Package
revision for a launch, but provider prompts and run DTOs must not receive
design source bytes, preview paths, credentials, transcripts, or raw provider
output.  This module therefore persists only exact provenance metadata beside
the delegation's operational launch key.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_commons.core.canonical import loads_json_strict, sha256_bytes
from agent_commons.core.ids import is_typed_id
from agent_commons.domain.design_packages import DesignPackageRecord
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy
from agent_commons.storage.atomic import atomic_write_immutable
from agent_commons.storage.opstate import (
    CONTEXT_BINDING_STORAGE,
    ensure_private_directory,
    exclusive_lock,
    strict_state_bytes,
)

ExactDesignPackageLookup = Callable[[str, str], DesignPackageRecord | None]
ExactDesignPackageAuthorizer = Callable[[DesignPackageRecord], bool]

_SAFE_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
DESIGN_PACKAGE_BINDING_STATE_SCHEMA = "agent_commons.runtime_design_package_binding.v1"


class DesignPackageBindingRefusalCode(StrEnum):
    """Stable, bounded failures emitted before launch-side effects."""

    MISSING = "design_package_missing"
    STALE = "design_package_stale"
    UNAUTHORIZED = "design_package_unauthorized"
    UNAVAILABLE = "design_package_unavailable"


_REFUSAL_COPY: dict[DesignPackageBindingRefusalCode, tuple[str, str]] = {
    DesignPackageBindingRefusalCode.MISSING: (
        "The selected Design Package revision is unavailable.",
        "Select an existing exact current Design Package revision.",
    ),
    DesignPackageBindingRefusalCode.STALE: (
        "The selected Design Package revision is stale or superseded.",
        "Refresh the Work launch options and retry with the current exact revision.",
    ),
    DesignPackageBindingRefusalCode.UNAUTHORIZED: (
        "The selected Design Package revision is not authorized for this launch.",
        "Select a Design Package revision authorized for the current workspace.",
    ),
    DesignPackageBindingRefusalCode.UNAVAILABLE: (
        "The selected Design Package revision cannot be bound safely.",
        "Inspect the Design Package and select a valid exact revision.",
    ),
}


@dataclass(frozen=True, slots=True)
class DesignPackageBindingRefusal:
    """A privacy-safe refusal value; it never echoes package content."""

    code: DesignPackageBindingRefusalCode
    message: str
    remediation: str

    @classmethod
    def create(cls, code: DesignPackageBindingRefusalCode) -> DesignPackageBindingRefusal:
        message, remediation = _REFUSAL_COPY[code]
        return cls(code=code, message=message, remediation=remediation)

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class DesignPackageBindingRequest:
    """Pure Design Package selection supplied to the pre-launch resolver."""

    design_package_id: str
    design_package_revision: str

    def __post_init__(self) -> None:
        if (
            type(self.design_package_id) is not str
            or type(self.design_package_revision) is not str
            or not is_typed_id(self.design_package_id, "design_package")
            or not is_typed_id(self.design_package_revision, "evt")
        ):
            raise ValueError("Design Package binding requires typed exact identifiers")


@dataclass(frozen=True, slots=True)
class DesignPackageScreenMetadata:
    """Safe preview provenance for one package screen."""

    screen_id: str
    artifact_id: str
    artifact_revision: str
    artifact_content_revision: str
    producer_task_id: str
    producer_task_revision: str
    classification: str
    media_type: str

    def as_dict(self) -> dict[str, object]:
        return {
            "screen_id": self.screen_id,
            "artifact_id": self.artifact_id,
            "artifact_revision": self.artifact_revision,
            "artifact_content_revision": self.artifact_content_revision,
            "producer_task_id": self.producer_task_id,
            "producer_task_revision": self.producer_task_revision,
            "classification": self.classification,
            "media_type": self.media_type,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DesignPackageScreenMetadata:
        expected = {
            "screen_id",
            "artifact_id",
            "artifact_revision",
            "artifact_content_revision",
            "producer_task_id",
            "producer_task_revision",
            "classification",
            "media_type",
        }
        if set(value) != expected:
            raise IntegrityError("runtime Design Package screen metadata has an invalid shape")
        try:
            screen = cls(
                screen_id=value["screen_id"],  # type: ignore[arg-type]
                artifact_id=value["artifact_id"],  # type: ignore[arg-type]
                artifact_revision=value["artifact_revision"],  # type: ignore[arg-type]
                artifact_content_revision=value["artifact_content_revision"],  # type: ignore[arg-type]
                producer_task_id=value["producer_task_id"],  # type: ignore[arg-type]
                producer_task_revision=value["producer_task_revision"],  # type: ignore[arg-type]
                classification=value["classification"],  # type: ignore[arg-type]
                media_type=value["media_type"],  # type: ignore[arg-type]
            )
        except TypeError as exc:
            raise IntegrityError("runtime Design Package screen metadata is invalid") from exc
        screen._validate()
        return screen

    def _validate(self) -> None:
        if (
            type(self.screen_id) is not str
            or type(self.artifact_id) is not str
            or type(self.artifact_revision) is not str
            or type(self.artifact_content_revision) is not str
            or type(self.producer_task_id) is not str
            or type(self.producer_task_revision) is not str
            or type(self.classification) is not str
            or type(self.media_type) is not str
            or not is_typed_id(self.screen_id, "screen")
            or not is_typed_id(self.artifact_id, "artifact")
            or not is_typed_id(self.artifact_revision, "evt")
            or not is_typed_id(self.producer_task_id, "task")
            or not is_typed_id(self.producer_task_revision, "evt")
            or not self.artifact_content_revision.startswith("sha256:")
            or self.classification not in {"public", "internal"}
            or self.media_type not in {"image/png", "image/jpeg"}
        ):
            raise IntegrityError("runtime Design Package screen metadata is invalid")


@dataclass(frozen=True, slots=True)
class DesignPackageBindingMetadata:
    """Exact Design Package provenance safe to persist beside a launch."""

    design_package_id: str
    design_package_revision: str
    source_event_id: str
    producer_session_id: str
    screen_count: int
    screens: tuple[DesignPackageScreenMetadata, ...]

    @classmethod
    def from_record(cls, record: DesignPackageRecord) -> DesignPackageBindingMetadata:
        screens = tuple(
            DesignPackageScreenMetadata(
                screen_id=screen.screen_id,
                artifact_id=screen.artifact_binding.identifier,
                artifact_revision=screen.artifact_binding.revision,
                artifact_content_revision=screen.artifact_content_revision,
                producer_task_id=screen.producer_task_binding.identifier,
                producer_task_revision=screen.producer_task_binding.revision,
                classification=screen.classification,
                media_type=screen.media_type,
            )
            for screen in record.draft.screens
        )
        metadata = cls(
            design_package_id=record.design_package_id,
            design_package_revision=record.revision,
            source_event_id=record.source_event_id,
            producer_session_id=record.producer_session_id,
            screen_count=len(screens),
            screens=screens,
        )
        metadata._validate()
        return metadata

    def as_dict(self) -> dict[str, object]:
        return {
            "design_package_id": self.design_package_id,
            "design_package_revision": self.design_package_revision,
            "source_event_id": self.source_event_id,
            "producer_session_id": self.producer_session_id,
            "screen_count": self.screen_count,
            "screens": [screen.as_dict() for screen in self.screens],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DesignPackageBindingMetadata:
        expected = {
            "design_package_id",
            "design_package_revision",
            "source_event_id",
            "producer_session_id",
            "screen_count",
            "screens",
        }
        if set(value) != expected or not isinstance(value.get("screens"), list):
            raise IntegrityError("runtime Design Package binding metadata has an invalid shape")
        try:
            metadata = cls(
                design_package_id=value["design_package_id"],  # type: ignore[arg-type]
                design_package_revision=value["design_package_revision"],  # type: ignore[arg-type]
                source_event_id=value["source_event_id"],  # type: ignore[arg-type]
                producer_session_id=value["producer_session_id"],  # type: ignore[arg-type]
                screen_count=value["screen_count"],  # type: ignore[arg-type]
                screens=tuple(
                    DesignPackageScreenMetadata.from_mapping(item)
                    for item in value["screens"]  # type: ignore[union-attr]
                    if isinstance(item, Mapping)
                ),
            )
        except TypeError as exc:
            raise IntegrityError("runtime Design Package binding metadata is invalid") from exc
        metadata._validate()
        return metadata

    def _validate(self) -> None:
        if (
            type(self.design_package_id) is not str
            or type(self.design_package_revision) is not str
            or type(self.source_event_id) is not str
            or type(self.producer_session_id) is not str
            or type(self.screen_count) is not int
            or not is_typed_id(self.design_package_id, "design_package")
            or not is_typed_id(self.design_package_revision, "evt")
            or not is_typed_id(self.source_event_id, "evt")
            or not self.producer_session_id
            or self.screen_count != len(self.screens)
            or not 1 <= self.screen_count <= 64
        ):
            raise IntegrityError("runtime Design Package binding metadata is invalid")


@dataclass(frozen=True, slots=True)
class StoredDesignPackageBinding:
    delegation_id: str
    launch_key_sha256: str
    metadata: DesignPackageBindingMetadata


class DesignPackageBindingStore:
    """Crash-safe immutable Design Package selection, separate from canonical truth."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        security_policy: SecurityPolicy | None = None,
        read_only: bool = False,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.root = self.state_root / "runtime" / "design-package-bindings"
        self.lock_path = self.state_root / "runtime" / "design-package-bindings.lock"
        self.security_policy = security_policy or SecurityPolicy()
        self.read_only = read_only
        if not read_only:
            ensure_private_directory(self.state_root, policy=CONTEXT_BINDING_STORAGE)
            ensure_private_directory(self.state_root / "runtime", policy=CONTEXT_BINDING_STORAGE)
            ensure_private_directory(self.root, policy=CONTEXT_BINDING_STORAGE)

    @staticmethod
    def _digest(delegation_id: str) -> str:
        if not is_typed_id(delegation_id, "delegation"):
            raise ValueError("Design Package binding requires a typed delegation id")
        return sha256_bytes(delegation_id.encode("utf-8"))

    def _path(self, delegation_id: str) -> Path:
        return self.root / f"{self._digest(delegation_id)}.json"

    def get(self, delegation_id: str) -> StoredDesignPackageBinding | None:
        path = self._path(delegation_id)
        if path.is_symlink():
            raise IntegrityError("runtime Design Package binding document must not be a symlink")
        if not path.exists():
            return None
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("runtime Design Package binding document is not regular")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(64 * 1024 + 1)
            descriptor = -1
            if len(raw) > 64 * 1024:
                raise IntegrityError("runtime Design Package binding document exceeds byte bound")
            value = loads_json_strict(raw)
        except (OSError, ValidationError, ValueError) as exc:
            raise IntegrityError("runtime Design Package binding document is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "delegation_id", "launch_key_sha256", "binding"}
            or value.get("schema") != DESIGN_PACKAGE_BINDING_STATE_SCHEMA
            or raw != strict_state_bytes(value)
            or value.get("delegation_id") != delegation_id
            or _SAFE_FINGERPRINT.fullmatch(str(value.get("launch_key_sha256", ""))) is None
            or not isinstance(value.get("binding"), dict)
        ):
            raise IntegrityError("runtime Design Package binding document has an invalid envelope")
        self.security_policy.assert_safe(value, context="runtime Design Package binding")
        return StoredDesignPackageBinding(
            delegation_id=delegation_id,
            launch_key_sha256=str(value["launch_key_sha256"]),
            metadata=DesignPackageBindingMetadata.from_mapping(value["binding"]),
        )

    def bind(
        self,
        delegation_id: str,
        launch_key_sha256: str,
        metadata: DesignPackageBindingMetadata,
    ) -> StoredDesignPackageBinding:
        if self.read_only:
            raise LifecycleConflictError(
                "runtime Design Package binding store was opened read-only"
            )
        if _SAFE_FINGERPRINT.fullmatch(launch_key_sha256) is None:
            raise ValueError("runtime launch key digest is invalid")
        body: dict[str, Any] = {
            "schema": DESIGN_PACKAGE_BINDING_STATE_SCHEMA,
            "delegation_id": delegation_id,
            "launch_key_sha256": launch_key_sha256,
            "binding": metadata.as_dict(),
        }
        self.security_policy.assert_safe(body, context="runtime Design Package binding")
        with exclusive_lock(self.lock_path, policy=CONTEXT_BINDING_STORAGE):
            existing = self.get(delegation_id)
            if existing is not None:
                if existing.launch_key_sha256 != launch_key_sha256:
                    raise IdempotencyConflictError(
                        "delegation Design Package binding belongs to a different launch key"
                    )
                if existing.metadata != metadata:
                    raise IdempotencyConflictError(
                        "runtime launch key is already bound to different Design Package metadata"
                    )
                return existing
            atomic_write_immutable(self._path(delegation_id), strict_state_bytes(body), mode=0o600)
            return StoredDesignPackageBinding(delegation_id, launch_key_sha256, metadata)


class DesignPackageBindingResolver:
    """Resolve and authorize one exact package without launch side effects."""

    def resolve(
        self,
        request: DesignPackageBindingRequest,
        *,
        load_exact: ExactDesignPackageLookup,
        authorize_exact: ExactDesignPackageAuthorizer,
    ) -> DesignPackageBindingMetadata | DesignPackageBindingRefusal:
        package_id = request.design_package_id
        revision = request.design_package_revision
        try:
            record = load_exact(package_id, revision)
        except Exception:
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.UNAVAILABLE)
        if record is None:
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.MISSING)
        if not isinstance(record, DesignPackageRecord):
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.STALE)
        try:
            if (
                record.design_package_id != package_id
                or record.revision != revision
                or record.effective_revision != revision
                or record.state != "published"
            ):
                return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.STALE)
        except Exception:
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.UNAVAILABLE)
        try:
            authorized = authorize_exact(record)
        except Exception:
            authorized = False
        if authorized is not True:
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.UNAUTHORIZED)
        try:
            return DesignPackageBindingMetadata.from_record(record)
        except Exception:
            return DesignPackageBindingRefusal.create(DesignPackageBindingRefusalCode.UNAVAILABLE)
