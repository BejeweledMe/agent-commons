"""Immutable, provider-neutral binding of one exact Context Pack revision.

This module is the narrow C2/P2 seam.  It resolves and compiles context before
any child/session/request/attempt side effect.  The caller retains ownership of
lookup, authorization, role instruction, and launch construction.
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
from agent_commons.domain.context_pack import (
    ContextPackBinding,
    ContextPackRecord,
    ContextPackRefusal,
    ContextPackRefusalCode,
)
from agent_commons.errors import (
    IdempotencyConflictError,
    IntegrityError,
    LifecycleConflictError,
    ValidationError,
)
from agent_commons.security import SecurityPolicy
from agent_commons.services.context_compiler import (
    MAX_COMPILED_CONTEXT_BYTES,
    CompiledContext,
    ContextCompiler,
)
from agent_commons.storage.atomic import atomic_write_immutable
from agent_commons.storage.opstate import (
    CONTEXT_BINDING_STORAGE,
    ensure_private_directory,
    exclusive_lock,
    strict_state_bytes,
)

ExactContextPackLookup = Callable[[str, str], ContextPackRecord | None]
ExactContextPackAuthorizer = Callable[[ContextPackRecord], bool]

_SAFE_COMPILER_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
CONTEXT_BINDING_STATE_SCHEMA = "agent_commons.runtime_context_binding.v1"


class ContextBindingMode(StrEnum):
    """Closed context modes supported by the runtime."""

    FRESH = "fresh"
    ACCUMULATED = "accumulated"


class ContextBindingRefusalCode(StrEnum):
    """Stable, bounded failures emitted before launch-side effects."""

    MISSING = "context_binding_missing"
    STALE = "context_binding_stale"
    UNAUTHORIZED = "context_binding_unauthorized"
    OVERSIZED = "context_binding_oversized"
    UNAVAILABLE = "context_binding_unavailable"
    ROLE_CONTEXT_MISMATCH = "context_binding_role_mismatch"


_REFUSAL_COPY: dict[ContextBindingRefusalCode, tuple[str, str]] = {
    ContextBindingRefusalCode.MISSING: (
        "The selected Context Pack revision is unavailable.",
        "Select an existing exact Context Pack revision.",
    ),
    ContextBindingRefusalCode.STALE: (
        "The resolved Context Pack does not match the selected exact revision.",
        "Refresh the selection and retry with one exact revision.",
    ),
    ContextBindingRefusalCode.UNAUTHORIZED: (
        "The selected Context Pack revision is not authorized for this launch.",
        "Select a Context Pack revision authorized for the current workspace and task.",
    ),
    ContextBindingRefusalCode.OVERSIZED: (
        "The compiled Context Pack exceeds the launch byte limit.",
        "Publish a smaller Context Pack revision and select that exact revision.",
    ),
    ContextBindingRefusalCode.UNAVAILABLE: (
        "The selected Context Pack revision cannot be compiled safely.",
        "Inspect the Context Pack and select a valid exact revision.",
    ),
    ContextBindingRefusalCode.ROLE_CONTEXT_MISMATCH: (
        "The requested context does not match the role's configured context mode.",
        "Launch a fresh role without a Context Pack, or select one exact Context Pack "
        "revision for an accumulated role.",
    ),
}


@dataclass(frozen=True, slots=True)
class ContextBindingRefusal:
    """A privacy-safe refusal value; it never echoes pack content."""

    code: ContextBindingRefusalCode
    message: str
    remediation: str

    @classmethod
    def create(cls, code: ContextBindingRefusalCode) -> ContextBindingRefusal:
        message, remediation = _REFUSAL_COPY[code]
        return cls(code=code, message=message, remediation=remediation)

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ContextBindingRequest:
    """Pure context selection supplied to the pre-launch resolver."""

    mode: ContextBindingMode
    context_pack_id: str | None = None
    context_pack_revision: str | None = None

    def __post_init__(self) -> None:
        mode = ContextBindingMode(self.mode)
        has_pack = self.context_pack_id is not None or self.context_pack_revision is not None
        if mode is ContextBindingMode.FRESH and has_pack:
            raise ValueError("fresh context cannot select a Context Pack")
        if mode is ContextBindingMode.ACCUMULATED and (
            not self.context_pack_id or not self.context_pack_revision
        ):
            raise ValueError("accumulated context requires one exact Context Pack revision")
        if mode is ContextBindingMode.ACCUMULATED and (
            type(self.context_pack_id) is not str
            or type(self.context_pack_revision) is not str
            or not is_typed_id(self.context_pack_id, "context_pack")
            or not is_typed_id(self.context_pack_revision, "evt")
        ):
            raise ValueError("accumulated context requires typed pack and event identifiers")
        object.__setattr__(self, "mode", mode)

    @classmethod
    def fresh(cls) -> ContextBindingRequest:
        return cls(mode=ContextBindingMode.FRESH)

    @classmethod
    def accumulated(
        cls, *, context_pack_id: str, context_pack_revision: str
    ) -> ContextBindingRequest:
        return cls(
            mode=ContextBindingMode.ACCUMULATED,
            context_pack_id=context_pack_id,
            context_pack_revision=context_pack_revision,
        )


@dataclass(frozen=True, slots=True)
class ContextBinding:
    """One immutable compiled baseline, or the explicit fresh-context sentinel.

    No role instruction, transcript, reasoning, authority, provider state, or
    mutable store is retained here.  ``compiled_context_bytes`` is deliberately
    not serializable through a public ``as_dict`` method.
    """

    binding: ContextPackBinding | None
    compiled_context_bytes: bytes | None

    def __post_init__(self) -> None:
        if self.binding is None:
            if self.compiled_context_bytes is not None:
                raise ValueError("fresh context cannot contain hidden compiled bytes")
            return
        if self.compiled_context_bytes is None:
            raise ValueError("accumulated context requires compiled bytes")
        if type(self.compiled_context_bytes) is not bytes:
            raise ValueError("compiled context must use owned plain bytes")
        compiled = self.compiled_context_bytes
        if not compiled or len(compiled) > MAX_COMPILED_CONTEXT_BYTES:
            raise ValueError("compiled context bytes are outside the safe bound")
        try:
            compiled.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("compiled context bytes must be UTF-8") from exc
        binding = _own_and_validate_binding(self.binding, compiled)
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "compiled_context_bytes", compiled)

    @classmethod
    def fresh(cls) -> ContextBinding:
        return cls(binding=None, compiled_context_bytes=None)

    @property
    def mode(self) -> ContextBindingMode:
        return ContextBindingMode.FRESH if self.binding is None else ContextBindingMode.ACCUMULATED

    @property
    def compiled_context_fingerprint(self) -> str | None:
        if self.binding is None:
            return None
        return self.binding.compiled_context_fingerprint


@dataclass(frozen=True, slots=True)
class ContextBindingMetadata:
    """Secret-free operational identity of one compiled launch baseline."""

    mode: ContextBindingMode
    context_pack_id: str | None
    context_pack_revision: str | None
    compiler_version: str | None
    compiled_context_fingerprint: str | None
    compiled_context_size_bytes: int

    def __post_init__(self) -> None:
        mode = ContextBindingMode(self.mode)
        if (
            type(self.compiled_context_size_bytes) is not int
            or self.compiled_context_size_bytes < 0
        ):
            raise ValueError("compiled context byte size must be a non-negative integer")
        if mode is ContextBindingMode.FRESH:
            if (
                any(
                    value is not None
                    for value in (
                        self.context_pack_id,
                        self.context_pack_revision,
                        self.compiler_version,
                        self.compiled_context_fingerprint,
                    )
                )
                or self.compiled_context_size_bytes != 0
            ):
                raise ValueError("fresh context metadata cannot retain a pack binding")
        else:
            if (
                type(self.context_pack_id) is not str
                or type(self.context_pack_revision) is not str
                or type(self.compiler_version) is not str
                or type(self.compiled_context_fingerprint) is not str
                or not is_typed_id(self.context_pack_id, "context_pack")
                or not is_typed_id(self.context_pack_revision, "evt")
                or _SAFE_COMPILER_VERSION.fullmatch(self.compiler_version) is None
                or _SAFE_FINGERPRINT.fullmatch(self.compiled_context_fingerprint) is None
                or not 1 <= self.compiled_context_size_bytes <= MAX_COMPILED_CONTEXT_BYTES
            ):
                raise ValueError("accumulated context metadata is invalid")
        object.__setattr__(self, "mode", mode)

    @classmethod
    def from_binding(cls, binding: ContextBinding) -> ContextBindingMetadata:
        if binding.binding is None:
            return cls(ContextBindingMode.FRESH, None, None, None, None, 0)
        compiled = binding.compiled_context_bytes
        assert compiled is not None
        return cls(
            ContextBindingMode.ACCUMULATED,
            binding.binding.context_pack_id,
            binding.binding.context_pack_revision,
            binding.binding.compiler_version,
            binding.binding.compiled_context_fingerprint,
            len(compiled),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "context_pack_id": self.context_pack_id,
            "context_pack_revision": self.context_pack_revision,
            "compiler_version": self.compiler_version,
            "compiled_context_fingerprint": self.compiled_context_fingerprint,
            "compiled_context_size_bytes": self.compiled_context_size_bytes,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ContextBindingMetadata:
        expected = {
            "mode",
            "context_pack_id",
            "context_pack_revision",
            "compiler_version",
            "compiled_context_fingerprint",
            "compiled_context_size_bytes",
        }
        if set(value) != expected:
            raise IntegrityError("runtime context binding metadata has an invalid shape")
        try:
            return cls(
                mode=ContextBindingMode(str(value["mode"])),
                context_pack_id=value["context_pack_id"],  # type: ignore[arg-type]
                context_pack_revision=value["context_pack_revision"],  # type: ignore[arg-type]
                compiler_version=value["compiler_version"],  # type: ignore[arg-type]
                compiled_context_fingerprint=value["compiled_context_fingerprint"],  # type: ignore[arg-type]
                compiled_context_size_bytes=value["compiled_context_size_bytes"],  # type: ignore[arg-type]
            )
        except (TypeError, ValueError) as exc:
            raise IntegrityError("runtime context binding metadata is invalid") from exc


@dataclass(frozen=True, slots=True)
class StoredContextBinding:
    delegation_id: str
    launch_key_sha256: str
    metadata: ContextBindingMetadata


class ContextBindingStore:
    """Crash-safe immutable binding selection, separate from canonical truth."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        security_policy: SecurityPolicy | None = None,
        read_only: bool = False,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.root = self.state_root / "runtime" / "context-bindings"
        self.lock_path = self.state_root / "runtime" / "context-bindings.lock"
        self.security_policy = security_policy or SecurityPolicy()
        self.read_only = read_only
        if not read_only:
            ensure_private_directory(self.state_root, policy=CONTEXT_BINDING_STORAGE)
            ensure_private_directory(self.state_root / "runtime", policy=CONTEXT_BINDING_STORAGE)
            ensure_private_directory(self.root, policy=CONTEXT_BINDING_STORAGE)

    @staticmethod
    def _digest(delegation_id: str) -> str:
        if not is_typed_id(delegation_id, "delegation"):
            raise ValueError("context binding requires a typed delegation id")
        return sha256_bytes(delegation_id.encode("utf-8"))

    def _path(self, delegation_id: str) -> Path:
        return self.root / f"{self._digest(delegation_id)}.json"

    def get(self, delegation_id: str) -> StoredContextBinding | None:
        path = self._path(delegation_id)
        if path.is_symlink():
            raise IntegrityError("runtime context binding document must not be a symlink")
        if not path.exists():
            return None
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("runtime context binding document is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(16 * 1024 + 1)
            descriptor = -1
            if len(raw) > 16 * 1024:
                raise IntegrityError("runtime context binding document exceeds its byte bound")
            value = loads_json_strict(raw)
        except (OSError, ValidationError, ValueError) as exc:
            raise IntegrityError("runtime context binding document is unreadable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "delegation_id",
                "launch_key_sha256",
                "binding",
            }
            or value.get("schema") != CONTEXT_BINDING_STATE_SCHEMA
            or raw != strict_state_bytes(value)
            or value.get("delegation_id") != delegation_id
            or _SAFE_FINGERPRINT.fullmatch(str(value.get("launch_key_sha256", ""))) is None
            or not isinstance(value.get("binding"), dict)
        ):
            raise IntegrityError("runtime context binding document has an invalid envelope")
        self.security_policy.assert_safe(value, context="runtime context binding")
        return StoredContextBinding(
            delegation_id=delegation_id,
            launch_key_sha256=str(value["launch_key_sha256"]),
            metadata=ContextBindingMetadata.from_mapping(value["binding"]),
        )

    def bind(
        self,
        delegation_id: str,
        launch_key_sha256: str,
        metadata: ContextBindingMetadata,
    ) -> StoredContextBinding:
        if self.read_only:
            raise LifecycleConflictError("runtime context binding store was opened read-only")
        if _SAFE_FINGERPRINT.fullmatch(launch_key_sha256) is None:
            raise ValueError("runtime launch key digest is invalid")
        body: dict[str, Any] = {
            "schema": CONTEXT_BINDING_STATE_SCHEMA,
            "delegation_id": delegation_id,
            "launch_key_sha256": launch_key_sha256,
            "binding": metadata.as_dict(),
        }
        self.security_policy.assert_safe(body, context="runtime context binding")
        with exclusive_lock(self.lock_path, policy=CONTEXT_BINDING_STORAGE):
            existing = self.get(delegation_id)
            if existing is not None:
                if existing.launch_key_sha256 != launch_key_sha256:
                    raise IdempotencyConflictError(
                        "delegation context binding belongs to a different runtime launch key"
                    )
                if existing.metadata != metadata:
                    raise IdempotencyConflictError(
                        "runtime launch key is already bound to different context metadata"
                    )
                return existing
            atomic_write_immutable(self._path(delegation_id), strict_state_bytes(body), mode=0o600)
            return StoredContextBinding(delegation_id, launch_key_sha256, metadata)


class ContextBindingResolver:
    """Resolve, authorize, and compile exactly once without launch side effects."""

    def __init__(self, *, compiler: ContextCompiler | None = None) -> None:
        self._compiler = compiler or ContextCompiler()

    def resolve(
        self,
        request: ContextBindingRequest,
        *,
        load_exact: ExactContextPackLookup,
        authorize_exact: ExactContextPackAuthorizer,
    ) -> ContextBinding | ContextBindingRefusal:
        if request.mode is ContextBindingMode.FRESH:
            return ContextBinding.fresh()

        pack_id = request.context_pack_id
        revision = request.context_pack_revision
        assert pack_id is not None and revision is not None
        try:
            record = load_exact(pack_id, revision)
        except Exception:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
        if record is None:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.MISSING)
        if not isinstance(record, ContextPackRecord):
            return ContextBindingRefusal.create(ContextBindingRefusalCode.STALE)
        try:
            record_pack_id = record.context_pack_id
            record_revision = record.revision
            record_effective_revision = record.effective_revision
            if (
                type(record_pack_id) is not str
                or type(record_revision) is not str
                or type(record_effective_revision) is not str
            ):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
        except Exception:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
        if (
            record_pack_id != pack_id
            or record_revision != revision
            or record_effective_revision != revision
        ):
            return ContextBindingRefusal.create(ContextBindingRefusalCode.STALE)
        try:
            authorized = authorize_exact(record)
        except Exception:
            authorized = False
        if authorized is not True:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAUTHORIZED)

        try:
            compiled = self._compiler.compile(record)
        except ContextPackRefusal as refusal:
            try:
                refusal_code = refusal.code
            except Exception:
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            code = (
                ContextBindingRefusalCode.OVERSIZED
                if refusal_code is ContextPackRefusalCode.OVERSIZED
                else ContextBindingRefusalCode.UNAVAILABLE
            )
            return ContextBindingRefusal.create(code)
        except Exception:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
        try:
            if not isinstance(compiled, CompiledContext):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            text = compiled.text
            binding = compiled.binding
            size_bytes = compiled.size_bytes
            if (
                type(text) is not str
                or not isinstance(binding, ContextPackBinding)
                or type(size_bytes) is not int
            ):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            if not text:
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            if len(text) > MAX_COMPILED_CONTEXT_BYTES:
                return ContextBindingRefusal.create(ContextBindingRefusalCode.OVERSIZED)
            if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            compiled_bytes = text.encode("utf-8")
            if size_bytes != len(compiled_bytes):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            if len(compiled_bytes) > MAX_COMPILED_CONTEXT_BYTES:
                return ContextBindingRefusal.create(ContextBindingRefusalCode.OVERSIZED)
            resolved = ContextBinding(
                binding=binding,
                compiled_context_bytes=compiled_bytes,
            )
            owned_binding = resolved.binding
            if owned_binding is None:
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            if (
                owned_binding.context_pack_id != pack_id
                or owned_binding.context_pack_revision != revision
            ):
                return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)
            return resolved
        except Exception:
            return ContextBindingRefusal.create(ContextBindingRefusalCode.UNAVAILABLE)


def _own_and_validate_binding(
    binding: ContextPackBinding,
    compiled: bytes,
) -> ContextPackBinding:
    """Copy compiler metadata into exact primitives and verify its byte binding."""

    try:
        context_pack_id = binding.context_pack_id
        context_pack_revision = binding.context_pack_revision
        compiler_version = binding.compiler_version
        fingerprint = binding.compiled_context_fingerprint
    except Exception:
        raise ValueError("compiled context binding is unavailable") from None
    if (
        type(context_pack_id) is not str
        or type(context_pack_revision) is not str
        or type(compiler_version) is not str
        or type(fingerprint) is not str
    ):
        raise ValueError("compiled context binding must contain plain strings")
    if (
        not is_typed_id(context_pack_id, "context_pack")
        or not is_typed_id(context_pack_revision, "evt")
        or _SAFE_COMPILER_VERSION.fullmatch(compiler_version) is None
        or _SAFE_FINGERPRINT.fullmatch(fingerprint) is None
    ):
        raise ValueError("compiled context binding has an invalid shape")
    expected_fingerprint = sha256_bytes(compiler_version.encode("ascii") + b"\0" + compiled)
    if expected_fingerprint != fingerprint:
        raise ValueError("compiled context fingerprint does not match its bytes")
    return ContextPackBinding(
        context_pack_id=context_pack_id,
        context_pack_revision=context_pack_revision,
        compiler_version=compiler_version,
        compiled_context_fingerprint=fingerprint,
    )
