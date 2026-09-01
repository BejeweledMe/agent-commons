from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_commons.core.ids import stable_id
from agent_commons.runtime.execution_host import (
    MAX_EXECUTION_HOST_REQUEST_BYTES,
    ExecutionHostAdmissionStore,
    ExecutionHostBinding,
    ExecutionHostRefusal,
    ExecutionHostRefusalCode,
    ExecutionHostRequest,
    encode_execution_host_request,
    open_owner_only_unix_listener,
    path_identity_sha256,
    read_execution_host_request,
    require_operator_peer,
)
from agent_commons.runtime.model import BuiltinProfileId


def _id(prefix: str, name: str) -> str:
    return stable_id(prefix, name)


@pytest.fixture
def binding() -> ExecutionHostBinding:
    return ExecutionHostBinding(
        workspace_sha256="1" * 64,
        state_root_sha256="2" * 64,
        delegation_id=_id("delegation", "host-delegation"),
        delegation_revision=_id("evt", "host-revision"),
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        broker_instance_id=_id("broker_instance", "host-broker"),
        launch_plan_sha256="3" * 64,
    )


@pytest.fixture
def host_request(binding: ExecutionHostBinding) -> ExecutionHostRequest:
    return ExecutionHostRequest.from_binding(_id("request", "host-request"), binding)


def _assert_refusal(code: ExecutionHostRefusalCode, action: object) -> None:
    with pytest.raises(ExecutionHostRefusal) as raised:
        action()  # type: ignore[operator]
    assert raised.value.code is code
    assert str(raised.value) == code.value


def test_request_wire_schema_is_closed_and_contains_no_private_material(
    host_request: ExecutionHostRequest,
) -> None:
    wire = host_request.to_wire()
    assert ExecutionHostRequest.from_wire(wire) == host_request
    assert set(wire) == {
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
    serialized = encode_execution_host_request(host_request)
    for forbidden in (b"argv", b"environment", b"prompt", b"output", b"credential"):
        assert forbidden not in serialized.lower()

    for forbidden in ("argv", "env", "prompt", "output"):
        invalid = {**wire, forbidden: []}
        _assert_refusal(
            ExecutionHostRefusalCode.INVALID_REQUEST,
            lambda invalid=invalid: ExecutionHostRequest.from_wire(invalid),
        )


def test_request_wire_rejects_non_string_values(host_request: ExecutionHostRequest) -> None:
    invalid = {**host_request.to_wire(), "request_id": 7}
    _assert_refusal(
        ExecutionHostRefusalCode.INVALID_REQUEST,
        lambda: ExecutionHostRequest.from_wire(invalid),
    )


def test_bounded_request_reader_round_trips(host_request: ExecutionHostRequest) -> None:
    reader, writer = socket.socketpair()
    try:
        writer.sendall(encode_execution_host_request(host_request))
        assert read_execution_host_request(reader) == host_request
    finally:
        reader.close()
        writer.close()


def test_bounded_request_reader_rejects_truncated_frame() -> None:
    reader, writer = socket.socketpair()
    try:
        writer.sendall(b'{"schema":"incomplete"}')
        writer.shutdown(socket.SHUT_WR)
        _assert_refusal(
            ExecutionHostRefusalCode.REQUEST_TRUNCATED,
            lambda: read_execution_host_request(reader),
        )
    finally:
        reader.close()
        writer.close()


def test_bounded_request_reader_rejects_oversized_frame() -> None:
    reader, writer = socket.socketpair()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            sent = executor.submit(writer.sendall, b"x" * (MAX_EXECUTION_HOST_REQUEST_BYTES + 1))
            _assert_refusal(
                ExecutionHostRefusalCode.REQUEST_OVERSIZED,
                lambda: read_execution_host_request(reader),
            )
            sent.result()
    finally:
        reader.close()
        writer.close()


def test_bounded_request_reader_rejects_oversized_complete_frame() -> None:
    reader, writer = socket.socketpair()
    try:
        oversized = b" " * MAX_EXECUTION_HOST_REQUEST_BYTES + b"\n"
        with ThreadPoolExecutor(max_workers=1) as executor:
            sent = executor.submit(writer.sendall, oversized)
            _assert_refusal(
                ExecutionHostRefusalCode.REQUEST_OVERSIZED,
                lambda: read_execution_host_request(reader),
            )
            sent.result()
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("workspace_sha256", "a" * 64, ExecutionHostRefusalCode.FOREIGN_WORKSPACE),
        ("state_root_sha256", "b" * 64, ExecutionHostRefusalCode.FOREIGN_STATE_ROOT),
        (
            "broker_instance_id",
            _id("broker_instance", "other-broker"),
            ExecutionHostRefusalCode.WRONG_BROKER_INSTANCE,
        ),
        (
            "delegation_id",
            _id("delegation", "other-delegation"),
            ExecutionHostRefusalCode.STALE_DELEGATION,
        ),
        (
            "delegation_revision",
            _id("evt", "other-revision"),
            ExecutionHostRefusalCode.STALE_DELEGATION,
        ),
        (
            "profile_id",
            BuiltinProfileId.CODEX_BUILDER,
            ExecutionHostRefusalCode.PROFILE_MISMATCH,
        ),
        ("launch_plan_sha256", "c" * 64, ExecutionHostRefusalCode.PLAN_MISMATCH),
    ],
)
def test_request_mismatch_matrix_is_typed(
    binding: ExecutionHostBinding,
    host_request: ExecutionHostRequest,
    field: str,
    replacement: object,
    code: ExecutionHostRefusalCode,
) -> None:
    mismatched = replace(host_request, **{field: replacement})
    _assert_refusal(code, lambda: mismatched.validate_against(binding))


def test_binding_from_validated_plan_keeps_only_fingerprints(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "state"
    workspace.mkdir()
    state_root.mkdir()
    plan = SimpleNamespace(
        plan=SimpleNamespace(profile_id=BuiltinProfileId.CLAUDE_BUILDER),
        invocation_fingerprint="f" * 64,
        invocation=SimpleNamespace(argv=("secret-prompt",), env={"TOKEN": "secret"}),
    )
    binding = ExecutionHostBinding.from_validated_plan(
        workspace_root=workspace,
        state_root=state_root,
        delegation_id=_id("delegation", "bound-delegation"),
        delegation_revision=_id("evt", "bound-revision"),
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        broker_instance_id=_id("broker_instance", "bound-broker"),
        validated_launch_plan=plan,  # type: ignore[arg-type]
    )
    request = ExecutionHostRequest.from_binding(_id("request", "bound-request"), binding)
    serialized = json.dumps(request.to_wire(), sort_keys=True)
    assert binding.workspace_sha256 == path_identity_sha256(workspace)
    assert binding.state_root_sha256 == path_identity_sha256(state_root)
    assert binding.launch_plan_sha256 == "f" * 64
    assert "secret-prompt" not in serialized
    assert "TOKEN" not in serialized


def test_admission_is_durable_exactly_once_and_private_data_free(
    tmp_path: Path,
    binding: ExecutionHostBinding,
    host_request: ExecutionHostRequest,
) -> None:
    root = tmp_path / "execution-host"
    store = ExecutionHostAdmissionStore(root)
    first = store.admit(host_request, binding, peer_uid=os.getuid())
    duplicate = store.admit(host_request, binding, peer_uid=os.getuid())
    recovered = ExecutionHostAdmissionStore(root).admit(host_request, binding, peer_uid=os.getuid())

    assert first.created is True
    assert duplicate.created is False
    assert recovered.created is False
    assert first.admission == duplicate.admission == recovered.admission
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    receipt = next((root / "requests").glob("*/*.json"))
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    raw = receipt.read_text(encoding="utf-8")
    for forbidden in ("argv", "environment", "prompt", "output", "credential"):
        assert forbidden not in raw.lower()


def test_admission_is_exactly_once_under_concurrency(
    tmp_path: Path,
    binding: ExecutionHostBinding,
    host_request: ExecutionHostRequest,
) -> None:
    store = ExecutionHostAdmissionStore(tmp_path / "concurrent-host")

    def admit() -> object:
        return store.admit(host_request, binding, peer_uid=os.getuid())

    with ThreadPoolExecutor(max_workers=16) as executor:
        decisions = list(executor.map(lambda _: admit(), range(32)))
    assert sum(decision.created for decision in decisions) == 1
    assert len({decision.admission for decision in decisions}) == 1


def test_reused_request_id_with_different_content_is_rejected(
    tmp_path: Path,
    binding: ExecutionHostBinding,
    host_request: ExecutionHostRequest,
) -> None:
    store = ExecutionHostAdmissionStore(tmp_path / "conflict-host")
    store.admit(host_request, binding, peer_uid=os.getuid())
    changed_binding = replace(binding, launch_plan_sha256="d" * 64)
    changed_request = replace(host_request, launch_plan_sha256="d" * 64)
    _assert_refusal(
        ExecutionHostRefusalCode.REQUEST_CONFLICT,
        lambda: store.admit(changed_request, changed_binding, peer_uid=os.getuid()),
    )


def test_foreign_peer_and_unsafe_store_root_are_rejected(
    tmp_path: Path,
    binding: ExecutionHostBinding,
    host_request: ExecutionHostRequest,
) -> None:
    store = ExecutionHostAdmissionStore(tmp_path / "foreign-host")
    _assert_refusal(
        ExecutionHostRefusalCode.UNAUTHENTICATED_PEER,
        lambda: store.admit(host_request, binding, peer_uid=os.getuid() + 1),
    )

    unsafe_root = tmp_path / "unsafe-host"
    unsafe_root.mkdir(mode=0o755)
    unsafe_root.chmod(0o755)
    _assert_refusal(
        ExecutionHostRefusalCode.UNSAFE_ENDPOINT,
        lambda: ExecutionHostAdmissionStore(unsafe_root).admit(
            host_request, binding, peer_uid=os.getuid()
        ),
    )
    _assert_refusal(
        ExecutionHostRefusalCode.UNSAFE_ENDPOINT,
        lambda: ExecutionHostAdmissionStore(Path("relative-host")),
    )


def test_unix_listener_rejects_relative_endpoint() -> None:
    _assert_refusal(
        ExecutionHostRefusalCode.UNSAFE_ENDPOINT,
        lambda: open_owner_only_unix_listener(Path("relative.sock")),
    )


@pytest.mark.skipif(os.name != "posix", reason="Unix peer credentials require POSIX")
def test_owner_only_unix_listener_authenticates_local_peer() -> None:
    with tempfile.TemporaryDirectory(prefix="ac-host-", dir="/tmp") as directory:
        socket_path = Path(directory) / "host.sock"
        listener = open_owner_only_unix_listener(socket_path)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        accepted: socket.socket | None = None
        try:
            client.connect(os.fspath(socket_path))
            accepted, _ = listener.accept()
            assert require_operator_peer(accepted) == os.getuid()
            assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        finally:
            if accepted is not None:
                accepted.close()
            client.close()
            listener.close()
            socket_path.unlink(missing_ok=True)


def test_path_identity_uses_resolved_directory_identity(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    alias = tmp_path / "alias"
    first.mkdir()
    second.mkdir()
    alias.symlink_to(first, target_is_directory=True)
    assert path_identity_sha256(first) == path_identity_sha256(alias)
    assert path_identity_sha256(first) != path_identity_sha256(second)
