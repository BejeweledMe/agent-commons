from __future__ import annotations

import json
import stat
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError, IntegrityError
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ProviderQualification,
    ProviderQualificationStore,
    ProviderRefusalCode,
    TypedRefusal,
    default_profile_registry,
    qualification_fingerprint,
)
from agent_commons.storage.opstate import strict_state_bytes


def _executable(path: Path, body: str = "pass\n") -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _profile(tmp_path: Path, profile_id: BuiltinProfileId = BuiltinProfileId.CODEX_BUILDER):
    (tmp_path / "workspace").mkdir(exist_ok=True)
    provider = _executable(tmp_path / "provider")
    profiles = default_profile_registry(
        codex_executable=str(provider),
        claude_executable=str(provider),
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/git",
        trusted_workspace=True,
    )
    return provider, profiles.get(profile_id)


def test_missing_failed_and_current_qualification_are_distinct(tmp_path: Path) -> None:
    provider, profile = _profile(tmp_path)
    store = ProviderQualificationStore(tmp_path / "state")

    workspace = tmp_path / "workspace"
    missing = store.status(profile, workspace_root=workspace)
    assert isinstance(missing, TypedRefusal)
    assert missing.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_REQUIRED

    failed = store.record(
        profile,
        workspace_root=workspace,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=False,
        provider_version="codex-cli 1.2.3",
    )
    assert failed.qualified is False
    refused = store.status(profile, workspace_root=workspace)
    assert isinstance(refused, TypedRefusal)
    assert refused.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED

    passed = store.record(
        profile,
        workspace_root=workspace,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version="codex-cli 1.2.3",
    )
    current = store.status(profile, workspace_root=workspace)
    assert isinstance(current, ProviderQualification)
    assert current == passed
    assert current.qualified is True

    provider.write_text(f"#!{sys.executable}\nprint('changed')\n", encoding="utf-8")
    stale = store.status(profile, workspace_root=workspace)
    assert isinstance(stale, TypedRefusal)
    assert stale.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED


def test_qualification_receipt_contains_no_paths_or_provider_output(tmp_path: Path) -> None:
    _provider, profile = _profile(tmp_path)
    store = ProviderQualificationStore(tmp_path / "state")
    receipt = store.record(
        profile,
        workspace_root=tmp_path / "workspace",
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    path = store.root / f"{profile.profile_id.value}.json"
    serialized = path.read_text(encoding="utf-8")

    assert json.loads(serialized)["fingerprint"] == receipt.fingerprint
    assert str(tmp_path) not in serialized
    assert "argv" not in serialized
    assert "stderr" not in serialized
    assert "instruction" not in serialized


def test_qualification_fingerprint_binds_profile_model_and_executable(tmp_path: Path) -> None:
    provider, codex = _profile(tmp_path)
    _other, claude = _profile(tmp_path, BuiltinProfileId.CLAUDE_BUILDER)

    workspace = tmp_path / "workspace"
    codex_fingerprint = qualification_fingerprint(codex, workspace_root=workspace)
    claude_fingerprint = qualification_fingerprint(claude, workspace_root=workspace)
    assert codex_fingerprint != claude_fingerprint

    provider.write_text(f"#!{sys.executable}\nprint('new runtime')\n", encoding="utf-8")
    assert qualification_fingerprint(codex, workspace_root=workspace) != codex_fingerprint


def test_qualification_fingerprint_binds_safe_launch_policy(tmp_path: Path) -> None:
    _provider, claude = _profile(tmp_path, BuiltinProfileId.CLAUDE_BUILDER)
    _other, codex = _profile(tmp_path)
    workspace = tmp_path / "workspace"

    before = qualification_fingerprint(claude, workspace_root=workspace)
    changed_permission = replace(claude, permission_mode=ClaudePermissionMode.DONT_ASK)
    assert qualification_fingerprint(changed_permission, workspace_root=workspace) != before

    untrusted_builder = replace(codex, trusted_workspace=False)
    with pytest.raises(ConfigurationError, match="trusted_workspace"):
        qualification_fingerprint(untrusted_builder, workspace_root=workspace)


@pytest.mark.parametrize("component", ("provider", "mcp", "git"))
def test_qualification_fingerprint_binds_every_allowlisted_executable(
    tmp_path: Path,
    component: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executables = {name: _executable(tmp_path / name) for name in ("provider", "mcp", "git")}
    profile = default_profile_registry(
        codex_executable=str(executables["provider"]),
        claude_executable=str(executables["provider"]),
        mcp_executable=str(executables["mcp"]),
        git_executable=str(executables["git"]),
        trusted_workspace=True,
    ).get(BuiltinProfileId.CODEX_BUILDER)
    before = qualification_fingerprint(profile, workspace_root=workspace)

    executables[component].write_text(
        f"#!{sys.executable}\nprint('replacement')\n",
        encoding="utf-8",
    )

    assert qualification_fingerprint(profile, workspace_root=workspace) != before


def test_store_is_read_only_and_rejects_symlinked_receipt(tmp_path: Path) -> None:
    _provider, profile = _profile(tmp_path)
    state = tmp_path / "state"
    read_only = ProviderQualificationStore(state, read_only=True)
    with pytest.raises(ConfigurationError, match="read-only"):
        read_only.record(
            profile,
            workspace_root=tmp_path / "workspace",
            static_preflight=True,
            initialization_probe=True,
            behavioral_canary=True,
            provider_version=None,
        )

    store = ProviderQualificationStore(state)
    store.root.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (store.root / f"{profile.profile_id.value}.json").symlink_to(outside)
    with pytest.raises(IntegrityError, match="symlink"):
        store.read(profile.profile_id)


def test_same_provider_profile_swap_fails_closed(tmp_path: Path) -> None:
    _provider, profile = _profile(tmp_path)
    workspace = tmp_path / "workspace"
    store = ProviderQualificationStore(tmp_path / "state")
    store.record(
        profile,
        workspace_root=workspace,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version="codex-cli 1.2.3",
    )
    path = store.root / f"{profile.profile_id.value}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profile_id"] = BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER.value
    path.write_bytes(strict_state_bytes(document))

    with pytest.raises(IntegrityError, match="profile does not match"):
        store.read(profile.profile_id)
    status = store.status(profile, workspace_root=workspace)
    assert isinstance(status, TypedRefusal)
    assert status.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"unexpected": True}), "invalid envelope"),
        (lambda value: value["probes"].pop("behavioral_canary"), "probes are invalid"),
        (lambda value: value["probes"].update({"static_preflight": 1}), "probes are invalid"),
        (lambda value: value.update({"qualified": 1}), "verdict is invalid"),
    ),
)
def test_receipt_envelope_is_closed_and_strictly_typed(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    _provider, profile = _profile(tmp_path)
    workspace = tmp_path / "workspace"
    store = ProviderQualificationStore(tmp_path / "state")
    store.record(
        profile,
        workspace_root=workspace,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    path = store.root / f"{profile.profile_id.value}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_bytes(strict_state_bytes(document))

    with pytest.raises(IntegrityError, match=message):
        store.read(profile.profile_id)


@pytest.mark.parametrize(
    "checked_at",
    (
        "2026-02-30T12:00:00Z",
        "2026-08-31T12:00:00+00:00",
        "2026-08-31T12:00:00.000000Z",
        "2026-08-31T12:00:00.1Z",
    ),
)
def test_receipt_rejects_impossible_or_noncanonical_utc_timestamp(
    tmp_path: Path,
    checked_at: str,
) -> None:
    _provider, profile = _profile(tmp_path)
    workspace = tmp_path / "workspace"
    store = ProviderQualificationStore(tmp_path / "state")
    store.record(
        profile,
        workspace_root=workspace,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    path = store.root / f"{profile.profile_id.value}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["checked_at"] = checked_at
    path.write_bytes(strict_state_bytes(document))

    with pytest.raises(IntegrityError, match="timestamp"):
        store.read(profile.profile_id)
    status = store.status(profile, workspace_root=workspace)
    assert isinstance(status, TypedRefusal)
    assert status.code is ProviderRefusalCode.PROVIDER_QUALIFICATION_FAILED
