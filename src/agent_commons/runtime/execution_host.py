"""Owner-only local IPC admission for an external provider execution host.

This module is operational state, not canonical history.  It deliberately
admits only immutable launch identities and never transports an invocation,
environment, prompt, credentials, provider output, or lifecycle verdict.
"""

from __future__ import annotations

import hashlib
import os
import socket
import stat
import struct
import sys
from collections.abc import Mapping
from ctypes import CDLL, POINTER, byref, c_int, c_uint, get_errno
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_commons.core.canonical import (
    canonical_json_bytes,
    canonical_json_file_bytes,
    loads_json_strict,
    sha256_bytes,
)
from agent_commons.core.ids import is_typed_id
from agent_commons.errors import ConfigurationError, ImmutableCollisionError, ValidationError
from agent_commons.storage.atomic import atomic_write_immutable

from .launch import ValidatedLaunchPlan
from .model import BuiltinProfileId

MAX_EXECUTION_HOST_REQUEST_BYTES = 8192
_SHA256_LENGTH = 64
_REQUEST_SCHEMA = "agent_commons.execution_host_request.v1"
_ADMISSION_SCHEMA = "agent_commons.execution_host_admission.v1"


class ExecutionHostRefusalCode(StrEnum):
    """Closed operator-safe refusal vocabulary for P3 host admission."""

    UNSUPPORTED_HOST = "execution_host_unsupported"
    UNSAFE_ENDPOINT = "execution_host_unsafe_endpoint"
    UNAUTHENTICATED_PEER = "execution_host_unauthenticated_peer"
    INVALID_REQUEST = "execution_host_invalid_request"
    REQUEST_OVERSIZED = "execution_host_request_oversized"
    REQUEST_TRUNCATED = "execution_host_request_truncated"
    FOREIGN_WORKSPACE = "execution_host_foreign_workspace"
    FOREIGN_STATE_ROOT = "execution_host_foreign_state_root"
    WRONG_BROKER_INSTANCE = "execution_host_wrong_broker_instance"
    STALE_DELEGATION = "execution_host_stale_delegation"
    PROFILE_MISMATCH = "execution_host_profile_mismatch"
    PLAN_MISMATCH = "execution_host_plan_mismatch"
    REQUEST_CONFLICT = "execution_host_request_conflict"


class ExecutionHostRefusal(ConfigurationError):
    """A typed refusal that contains no request-controlled diagnostic text."""

    def __init__(self, code: ExecutionHostRefusalCode) -> None:
        super().__init__(code.value)
        self.code = code


def _refuse(code: ExecutionHostRefusalCode) -> None:
    raise ExecutionHostRefusal(code)


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _typed_id(value: object, prefix: str, field: str) -> str:
    if not is_typed_id(value, prefix):
        raise ValidationError(f"{field} must be a {prefix}.<ULID> identifier")
    return str(value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    return value


def path_identity_sha256(path: str | Path) -> str:
    """Hash one exact existing directory identity without exposing its path."""

    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError("execution host identity path must resolve") from exc
    if not resolved.is_dir():
        raise ValidationError("execution host identity path must be a directory")
    return hashlib.sha256(os.fsencode(resolved)).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionHostBinding:
    """The exact operator-side identity authorized for one host request."""

    workspace_sha256: str
    state_root_sha256: str
    delegation_id: str
    delegation_revision: str
    profile_id: BuiltinProfileId
    broker_instance_id: str
    launch_plan_sha256: str

    def __post_init__(self) -> None:
        _sha256(self.workspace_sha256, "workspace_sha256")
        _sha256(self.state_root_sha256, "state_root_sha256")
        _typed_id(self.delegation_id, "delegation", "delegation_id")
        _typed_id(self.delegation_revision, "evt", "delegation_revision")
        if type(self.profile_id) is not BuiltinProfileId:
            raise ValidationError("profile_id must be a built-in profile")
        _typed_id(self.broker_instance_id, "broker_instance", "broker_instance_id")
        _sha256(self.launch_plan_sha256, "launch_plan_sha256")

    def as_request_fields(self) -> dict[str, object]:
        return {
            "workspace_sha256": self.workspace_sha256,
            "state_root_sha256": self.state_root_sha256,
            "delegation_id": self.delegation_id,
            "delegation_revision": self.delegation_revision,
            "profile_id": self.profile_id,
            "broker_instance_id": self.broker_instance_id,
            "launch_plan_sha256": self.launch_plan_sha256,
        }

    @classmethod
    def from_validated_plan(
        cls,
        *,
        workspace_root: str | Path,
        state_root: str | Path,
        delegation_id: str,
        delegation_revision: str,
        profile_id: str | BuiltinProfileId,
        broker_instance_id: str,
        validated_launch_plan: ValidatedLaunchPlan,
    ) -> ExecutionHostBinding:
        """Bind P2's immutable plan without copying its invocation material."""

        try:
            profile = BuiltinProfileId(profile_id)
        except ValueError as exc:
            raise ValidationError("profile_id must be a built-in profile") from exc
        if validated_launch_plan.plan.profile_id is not profile:
            _refuse(ExecutionHostRefusalCode.PROFILE_MISMATCH)
        return cls(
            workspace_sha256=path_identity_sha256(workspace_root),
            state_root_sha256=path_identity_sha256(state_root),
            delegation_id=delegation_id,
            delegation_revision=delegation_revision,
            profile_id=profile,
            broker_instance_id=broker_instance_id,
            launch_plan_sha256=validated_launch_plan.invocation_fingerprint,
        )


@dataclass(frozen=True, slots=True)
class ExecutionHostRequest:
    """One bounded IPC request containing identities and digests only."""

    request_id: str
    workspace_sha256: str
    state_root_sha256: str
    delegation_id: str
    delegation_revision: str
    profile_id: BuiltinProfileId
    broker_instance_id: str
    launch_plan_sha256: str

    def __post_init__(self) -> None:
        _typed_id(self.request_id, "request", "request_id")
        ExecutionHostBinding(
            workspace_sha256=self.workspace_sha256,
            state_root_sha256=self.state_root_sha256,
            delegation_id=self.delegation_id,
            delegation_revision=self.delegation_revision,
            profile_id=self.profile_id,
            broker_instance_id=self.broker_instance_id,
            launch_plan_sha256=self.launch_plan_sha256,
        )

    @classmethod
    def from_binding(cls, request_id: str, binding: ExecutionHostBinding) -> ExecutionHostRequest:
        return cls(request_id=request_id, **binding.as_request_fields())

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> ExecutionHostRequest:
        expected = {
            "schema",
            "request_id",
            "workspace_sha256",
            "state_root_sha256",
            "delegation_id",
            "delegation_revision",
            "profile_id",
            "broker_instance_id",
            "launch_plan_sha256",
        }
        if set(value) != expected or value.get("schema") != _REQUEST_SCHEMA:
            _refuse(ExecutionHostRefusalCode.INVALID_REQUEST)
        try:
            profile = BuiltinProfileId(_string(value["profile_id"], "profile_id"))
            return cls(
                request_id=_string(value["request_id"], "request_id"),
                workspace_sha256=_string(value["workspace_sha256"], "workspace_sha256"),
                state_root_sha256=_string(value["state_root_sha256"], "state_root_sha256"),
                delegation_id=_string(value["delegation_id"], "delegation_id"),
                delegation_revision=_string(value["delegation_revision"], "delegation_revision"),
                profile_id=profile,
                broker_instance_id=_string(value["broker_instance_id"], "broker_instance_id"),
                launch_plan_sha256=_string(value["launch_plan_sha256"], "launch_plan_sha256"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.INVALID_REQUEST) from exc

    def to_wire(self) -> dict[str, str]:
        return {
            "schema": _REQUEST_SCHEMA,
            "request_id": self.request_id,
            "workspace_sha256": self.workspace_sha256,
            "state_root_sha256": self.state_root_sha256,
            "delegation_id": self.delegation_id,
            "delegation_revision": self.delegation_revision,
            "profile_id": self.profile_id.value,
            "broker_instance_id": self.broker_instance_id,
            "launch_plan_sha256": self.launch_plan_sha256,
        }

    @property
    def semantic_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_wire()))

    def validate_against(self, binding: ExecutionHostBinding) -> None:
        comparisons = (
            (
                self.workspace_sha256,
                binding.workspace_sha256,
                ExecutionHostRefusalCode.FOREIGN_WORKSPACE,
            ),
            (
                self.state_root_sha256,
                binding.state_root_sha256,
                ExecutionHostRefusalCode.FOREIGN_STATE_ROOT,
            ),
            (
                self.broker_instance_id,
                binding.broker_instance_id,
                ExecutionHostRefusalCode.WRONG_BROKER_INSTANCE,
            ),
            (
                (self.delegation_id, self.delegation_revision),
                (binding.delegation_id, binding.delegation_revision),
                ExecutionHostRefusalCode.STALE_DELEGATION,
            ),
            (self.profile_id, binding.profile_id, ExecutionHostRefusalCode.PROFILE_MISMATCH),
            (
                self.launch_plan_sha256,
                binding.launch_plan_sha256,
                ExecutionHostRefusalCode.PLAN_MISMATCH,
            ),
        )
        for actual, expected, code in comparisons:
            if actual != expected:
                _refuse(code)


@dataclass(frozen=True, slots=True)
class ExecutionHostAdmission:
    """Immutable operational receipt; it is never a lifecycle verdict."""

    request_id: str
    request_sha256: str
    workspace_sha256: str
    state_root_sha256: str
    delegation_id: str
    delegation_revision: str
    profile_id: BuiltinProfileId
    broker_instance_id: str
    launch_plan_sha256: str
    state: str = "admitted"

    @classmethod
    def from_request(cls, request: ExecutionHostRequest) -> ExecutionHostAdmission:
        return cls(
            request_id=request.request_id,
            request_sha256=request.semantic_sha256,
            workspace_sha256=request.workspace_sha256,
            state_root_sha256=request.state_root_sha256,
            delegation_id=request.delegation_id,
            delegation_revision=request.delegation_revision,
            profile_id=request.profile_id,
            broker_instance_id=request.broker_instance_id,
            launch_plan_sha256=request.launch_plan_sha256,
        )

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> ExecutionHostAdmission:
        expected = {
            "schema",
            "request_id",
            "request_sha256",
            "workspace_sha256",
            "state_root_sha256",
            "delegation_id",
            "delegation_revision",
            "profile_id",
            "broker_instance_id",
            "launch_plan_sha256",
            "state",
        }
        if (
            set(value) != expected
            or value.get("schema") != _ADMISSION_SCHEMA
            or value.get("state") != "admitted"
        ):
            _refuse(ExecutionHostRefusalCode.REQUEST_CONFLICT)
        try:
            return cls(
                request_id=_typed_id(value.get("request_id"), "request", "request_id"),
                request_sha256=_sha256(value.get("request_sha256"), "request_sha256"),
                workspace_sha256=_sha256(value.get("workspace_sha256"), "workspace_sha256"),
                state_root_sha256=_sha256(value.get("state_root_sha256"), "state_root_sha256"),
                delegation_id=_typed_id(value.get("delegation_id"), "delegation", "delegation_id"),
                delegation_revision=_typed_id(
                    value.get("delegation_revision"), "evt", "delegation_revision"
                ),
                profile_id=BuiltinProfileId(_string(value.get("profile_id"), "profile_id")),
                broker_instance_id=_typed_id(
                    value.get("broker_instance_id"),
                    "broker_instance",
                    "broker_instance_id",
                ),
                launch_plan_sha256=_sha256(value.get("launch_plan_sha256"), "launch_plan_sha256"),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.REQUEST_CONFLICT) from exc

    def to_wire(self) -> dict[str, str]:
        return {
            "schema": _ADMISSION_SCHEMA,
            "request_id": self.request_id,
            "request_sha256": self.request_sha256,
            "workspace_sha256": self.workspace_sha256,
            "state_root_sha256": self.state_root_sha256,
            "delegation_id": self.delegation_id,
            "delegation_revision": self.delegation_revision,
            "profile_id": self.profile_id.value,
            "broker_instance_id": self.broker_instance_id,
            "launch_plan_sha256": self.launch_plan_sha256,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class ExecutionHostAdmissionDecision:
    admission: ExecutionHostAdmission
    created: bool


class ExecutionHostAdmissionStore:
    """Crash-safe exactly-once request receipts below an operator-owned root."""

    def __init__(self, root: str | Path, *, owner_uid: int | None = None) -> None:
        self.root = Path(root)
        if not self.root.is_absolute():
            _refuse(ExecutionHostRefusalCode.UNSAFE_ENDPOINT)
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid

    def _prepare_root(self) -> None:
        _ensure_owner_directory(self.root, self.owner_uid)

    def _path(self, request_id: str) -> Path:
        digest = sha256_bytes(request_id.encode("ascii"))
        return self.root / "requests" / digest[:2] / f"{digest}.json"

    def admit(
        self,
        request: ExecutionHostRequest,
        binding: ExecutionHostBinding,
        *,
        peer_uid: int,
    ) -> ExecutionHostAdmissionDecision:
        if peer_uid != self.owner_uid:
            _refuse(ExecutionHostRefusalCode.UNAUTHENTICATED_PEER)
        request.validate_against(binding)
        self._prepare_root()
        path = self._path(request.request_id)
        _ensure_owner_directory(self.root / "requests", self.owner_uid)
        _ensure_owner_directory(path.parent, self.owner_uid)
        admission = ExecutionHostAdmission.from_request(request)
        data = canonical_json_file_bytes(admission.to_wire())
        try:
            result = atomic_write_immutable(path, data, mode=0o600)
        except ImmutableCollisionError as exc:
            existing = self._load(path)
            if existing != admission:
                raise ExecutionHostRefusal(ExecutionHostRefusalCode.REQUEST_CONFLICT) from exc
            return ExecutionHostAdmissionDecision(existing, created=False)
        except OSError as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.UNSAFE_ENDPOINT) from exc
        loaded = self._load(path)
        if loaded != admission:
            _refuse(ExecutionHostRefusalCode.REQUEST_CONFLICT)
        return ExecutionHostAdmissionDecision(loaded, created=result.created)

    def _load(self, path: Path) -> ExecutionHostAdmission:
        try:
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                _refuse(ExecutionHostRefusalCode.REQUEST_CONFLICT)
            raw = path.read_bytes()
            value = loads_json_strict(raw)
            if not isinstance(value, dict):
                _refuse(ExecutionHostRefusalCode.REQUEST_CONFLICT)
            return ExecutionHostAdmission.from_wire(value)
        except ExecutionHostRefusal:
            raise
        except (OSError, ValidationError) as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.REQUEST_CONFLICT) from exc


def encode_execution_host_request(request: ExecutionHostRequest) -> bytes:
    data = canonical_json_bytes(request.to_wire()) + b"\n"
    if len(data) > MAX_EXECUTION_HOST_REQUEST_BYTES:
        _refuse(ExecutionHostRefusalCode.REQUEST_OVERSIZED)
    return data


def read_execution_host_request(connection: socket.socket) -> ExecutionHostRequest:
    """Read one bounded newline-delimited request from a local socket."""

    data = bytearray()
    while len(data) <= MAX_EXECUTION_HOST_REQUEST_BYTES:
        chunk = connection.recv(min(4096, MAX_EXECUTION_HOST_REQUEST_BYTES + 1 - len(data)))
        if not chunk:
            _refuse(ExecutionHostRefusalCode.REQUEST_TRUNCATED)
        data.extend(chunk)
        if len(data) > MAX_EXECUTION_HOST_REQUEST_BYTES:
            _refuse(ExecutionHostRefusalCode.REQUEST_OVERSIZED)
        newline = data.find(b"\n")
        if newline >= 0:
            if newline != len(data) - 1:
                _refuse(ExecutionHostRefusalCode.INVALID_REQUEST)
            try:
                value = loads_json_strict(bytes(data[:newline]))
            except ValidationError as exc:
                raise ExecutionHostRefusal(ExecutionHostRefusalCode.INVALID_REQUEST) from exc
            if not isinstance(value, dict):
                _refuse(ExecutionHostRefusalCode.INVALID_REQUEST)
            return ExecutionHostRequest.from_wire(value)
    _refuse(ExecutionHostRefusalCode.REQUEST_OVERSIZED)


def open_owner_only_unix_listener(
    socket_path: str | Path,
    *,
    owner_uid: int | None = None,
    backlog: int = 8,
) -> socket.socket:
    """Bind one 0600 Unix listener below an owner-only directory."""

    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        _refuse(ExecutionHostRefusalCode.UNSUPPORTED_HOST)
    uid = os.getuid() if owner_uid is None else owner_uid
    path = Path(socket_path)
    if not path.is_absolute() or len(os.fsencode(path)) > 100 or path.exists() or path.is_symlink():
        _refuse(ExecutionHostRefusalCode.UNSAFE_ENDPOINT)
    _ensure_owner_directory(path.parent, uid)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    previous_umask = os.umask(0o177)
    try:
        listener.bind(os.fspath(path))
        bound = True
    except OSError as exc:
        listener.close()
        raise ExecutionHostRefusal(ExecutionHostRefusalCode.UNSAFE_ENDPOINT) from exc
    finally:
        os.umask(previous_umask)
    try:
        os.chmod(path, 0o600, follow_symlinks=False)
        metadata = path.lstat()
        if (
            not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _refuse(ExecutionHostRefusalCode.UNSAFE_ENDPOINT)
        listener.listen(backlog)
        return listener
    except Exception:
        listener.close()
        if bound:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def unix_peer_uid(connection: socket.socket) -> int:
    """Return the authenticated peer uid on supported macOS/Linux hosts."""

    getter = getattr(connection, "getpeereid", None)
    if callable(getter):
        uid, _ = getter()
        return int(uid)
    peer_credential = getattr(socket, "SO_PEERCRED", None)
    if peer_credential is not None:
        try:
            raw = connection.getsockopt(socket.SOL_SOCKET, peer_credential, struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", raw)
            return int(uid)
        except OSError as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.UNSUPPORTED_HOST) from exc
    if sys.platform == "darwin":
        try:
            libc = CDLL(None, use_errno=True)
            getpeereid = libc.getpeereid
            getpeereid.argtypes = [c_int, POINTER(c_uint), POINTER(c_uint)]
            getpeereid.restype = c_int
            uid = c_uint()
            gid = c_uint()
            if getpeereid(connection.fileno(), byref(uid), byref(gid)) != 0:
                raise OSError(get_errno(), "getpeereid failed")
            return int(uid.value)
        except (AttributeError, OSError) as exc:
            raise ExecutionHostRefusal(ExecutionHostRefusalCode.UNSUPPORTED_HOST) from exc
    _refuse(ExecutionHostRefusalCode.UNSUPPORTED_HOST)


def require_operator_peer(connection: socket.socket, *, owner_uid: int | None = None) -> int:
    uid = unix_peer_uid(connection)
    expected = os.getuid() if owner_uid is None else owner_uid
    if uid != expected:
        _refuse(ExecutionHostRefusalCode.UNAUTHENTICATED_PEER)
    return uid


def _ensure_owner_directory(path: Path, owner_uid: int) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise ExecutionHostRefusal(ExecutionHostRefusalCode.UNSAFE_ENDPOINT) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _refuse(ExecutionHostRefusalCode.UNSAFE_ENDPOINT)
