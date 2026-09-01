from __future__ import annotations

import json
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent_commons.runtime import (
    BuiltinProfileId,
    ProfileRegistry,
    ProviderQualificationStore,
    default_profile_registry,
)
from agent_commons.services.provider_availability import ProviderAvailabilityService
from agent_commons.storage.opstate import strict_state_bytes


def _executable(path: Path, marker: str) -> Path:
    path.write_text(f"#!{sys.executable}\n# {marker}\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _service(tmp_path: Path) -> tuple[ProviderAvailabilityService, ProviderQualificationStore]:
    tool_root = tmp_path.parent / f"{tmp_path.name}-tools"
    tool_root.mkdir()
    provider = _executable(tool_root / "provider", "provider")
    mcp = _executable(tool_root / "mcp", "mcp")
    git = _executable(tool_root / "git", "git")
    profiles = default_profile_registry(
        codex_executable=str(provider),
        claude_executable=str(provider),
        grok_executable=str(provider),
        mcp_executable=str(mcp),
        git_executable=str(git),
        trusted_workspace=True,
    )
    store = ProviderQualificationStore(tmp_path / "state")
    return (
        ProviderAvailabilityService(
            profiles,
            workspace_root=tmp_path,
            qualifications=store,
        ),
        store,
    )


def test_unified_availability_is_closed_honest_and_codex_has_no_money_claim(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("codex-builder")
    receipt = store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version="test-provider-v1",
    )

    available = service.describe(
        "codex-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()

    assert available["launchable"] is True
    assert available["qualification"] == {
        "state": "qualified",
        "freshness": "current",
        "fingerprint": receipt.fingerprint,
        "checked_at": receipt.checked_at,
    }
    assert available["initialization_state"] == "ready"
    assert available["authentication"] == {"state": "ready", "freshness": "fresh"}
    assert available["capabilities"]["budget_units"] == ["provider_units"]  # type: ignore[index]
    refusal_codes = {item["code"] for item in available["capability_refusals"]}  # type: ignore[index]
    assert "provider_resume_unavailable" in refusal_codes
    assert available["capabilities"]["skills"] is True  # type: ignore[index]
    assert "provider_skill_projection_unavailable" not in refusal_codes
    assert "provider_monetary_budget_unavailable" in refusal_codes
    rendered = str(available).lower()
    assert set(available) == {
        "profile_id",
        "provider",
        "model",
        "capabilities",
        "capability_refusals",
        "installation_state",
        "initialization_state",
        "qualification",
        "authentication",
        "launchable",
        "refusal",
    }
    for forbidden in (
        "argv",
        "executable",
        "stderr",
        "environment",
        "mcp_json",
        "/bin/echo",
        "operator_limits",
        "global_concurrency",
        "provider_concurrency",
        "profile_concurrency",
        "parent_provider_units",
        "queue_capacity",
        "queue_wait_seconds",
    ):
        assert forbidden not in rendered


def test_missing_qualification_and_failed_initialization_are_distinct_typed_refusals(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    missing = service.describe(
        "claude-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()
    assert missing["launchable"] is False
    assert missing["initialization_state"] == "not_checked"
    assert missing["qualification"]["state"] == "required"  # type: ignore[index]
    assert missing["refusal"] == {
        "code": "provider_qualification_required",
        "remediation": ["run_provider_canary"],
    }

    profile = service.profiles.get("claude-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=False,
        behavioral_canary=False,
        provider_version=None,
    )
    failed = service.describe(
        "claude-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()
    assert failed["initialization_state"] == "failed"
    assert failed["qualification"]["freshness"] == "current"  # type: ignore[index]
    assert failed["refusal"] == {
        "code": "provider_initialization_failed",
        "remediation": ["repair_provider_initialization", "rerun_provider_canary"],
    }


def test_current_behavioral_failure_does_not_relabel_initialization_as_failed(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("claude-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=False,
        provider_version=None,
    )

    failed = service.describe(
        "claude-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()

    assert failed["initialization_state"] == "passed_unqualified"
    assert failed["qualification"] == {
        "state": "failed",
        "freshness": "current",
        "fingerprint": None,
        "checked_at": failed["qualification"]["checked_at"],  # type: ignore[index]
    }
    assert failed["qualification"]["checked_at"] is not None  # type: ignore[index]
    assert failed["refusal"] == {
        "code": "provider_qualification_failed",
        "remediation": ["inspect_failed_provider_probe", "rerun_provider_canary"],
    }


def test_authentication_state_changes_only_the_operational_availability_view(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("claude-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )

    required = service.describe(
        "claude-builder",
        auth={"state": "authentication_required", "freshness": "fresh"},
    ).to_wire()

    assert required["launchable"] is False
    assert required["refusal"] == {
        "code": "provider_authentication_required",
        "remediation": ["authenticate_provider"],
    }
    assert required["qualification"]["state"] == "qualified"  # type: ignore[index]


@pytest.mark.parametrize(
    ("auth", "expected_auth"),
    (
        (
            {"state": "ready", "freshness": "stale"},
            {"state": "ready", "freshness": "stale"},
        ),
        (
            {"state": "authentication_required", "freshness": "stale"},
            {"state": "authentication_required", "freshness": "stale"},
        ),
        (
            {"state": "ready", "freshness": "unknown"},
            {"state": "not_checked", "freshness": "unknown"},
        ),
        (
            {"state": "bogus", "freshness": "fresh"},
            {"state": "not_checked", "freshness": "unknown"},
        ),
    ),
)
def test_stale_or_malformed_auth_never_becomes_launchable(
    tmp_path: Path,
    auth: dict[str, str],
    expected_auth: dict[str, str],
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("claude-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )

    unavailable = service.describe("claude-builder", auth=auth).to_wire()

    assert unavailable["authentication"] == expected_auth
    assert unavailable["launchable"] is False
    assert unavailable["refusal"] == {
        "code": "provider_authentication_unconfirmed",
        "remediation": ["check_provider_authentication"],
    }


def test_fresh_explicitly_unsupported_auth_is_nonblocking(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("codex-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )

    available = service.describe(
        "codex-builder",
        auth={"state": "unsupported", "freshness": "fresh"},
    ).to_wire()

    assert available["authentication"] == {"state": "unsupported", "freshness": "fresh"}
    assert available["launchable"] is True
    assert available["refusal"] is None


def test_grok_uses_the_existing_closed_provider_projection(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("grok-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )

    available = service.describe(
        "grok-builder",
        auth={"state": "unsupported", "freshness": "fresh"},
    ).to_wire()

    assert available["profile_id"] == "grok-builder"
    assert available["provider"] == "grok"
    assert available["launchable"] is True
    assert available["capabilities"]["budget_units"] == ["provider_units"]  # type: ignore[index]
    assert available["authentication"] == {"state": "unsupported", "freshness": "fresh"}
    assert available["refusal"] is None


@pytest.mark.parametrize(
    "profile_id",
    (BuiltinProfileId.CODEX_BUILDER, BuiltinProfileId.CLAUDE_BUILDER),
)
def test_untrusted_builder_fails_same_launch_boundary_as_runtime(
    tmp_path: Path,
    profile_id: BuiltinProfileId,
) -> None:
    service, store = _service(tmp_path)
    trusted = service.profiles.get(profile_id)
    store.record(
        trusted,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    profiles = ProfileRegistry(
        {
            configured_id: replace(profile, trusted_workspace=False)
            if configured_id is profile_id
            else profile
            for configured_id in service.profiles.profile_ids
            for profile in (service.profiles.get(configured_id),)
        }
    )
    untrusted = (
        ProviderAvailabilityService(
            profiles,
            workspace_root=tmp_path,
            qualifications=store,
        )
        .describe(
            profile_id,
            auth={"state": "ready", "freshness": "fresh"},
        )
        .to_wire()
    )

    assert untrusted["installation_state"] == "installed"
    assert untrusted["initialization_state"] == "not_checked"
    assert untrusted["qualification"] == {
        "state": "failed",
        "freshness": "invalid",
        "fingerprint": None,
        "checked_at": None,
    }
    assert untrusted["launchable"] is False
    assert untrusted["refusal"] == {
        "code": "provider_qualification_failed",
        "remediation": ["inspect_failed_provider_probe", "rerun_provider_canary"],
    }


def test_missing_provider_installation_has_its_own_typed_refusal(tmp_path: Path) -> None:
    profiles = default_profile_registry(
        codex_executable="provider-missing-for-r0-test",
        claude_executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/true",
        trusted_workspace=True,
    )
    service = ProviderAvailabilityService(
        profiles,
        workspace_root=tmp_path,
        qualifications=ProviderQualificationStore(tmp_path / "state"),
    )

    missing = service.describe("codex-builder").to_wire()

    assert missing["installation_state"] == "unavailable"
    assert missing["launchable"] is False
    assert missing["refusal"] == {
        "code": "provider_installation_unavailable",
        "remediation": ["verify_provider_installation"],
    }


@pytest.mark.parametrize("attribute", ("executable", "mcp_executable", "git_executable"))
def test_disappeared_allowlisted_executable_fails_closed_after_qualification(
    tmp_path: Path,
    attribute: str,
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("codex-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    Path(str(getattr(profile, attribute))).unlink()

    unavailable = service.describe(
        "codex-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()

    assert unavailable["installation_state"] == "unavailable"
    assert unavailable["initialization_state"] == "not_checked"
    assert unavailable["launchable"] is False
    assert unavailable["refusal"]["code"] == "provider_installation_unavailable"  # type: ignore[index]


@pytest.mark.parametrize("attribute", ("executable", "mcp_executable", "git_executable"))
def test_replaced_allowlisted_executable_invalidates_qualification_without_init_ready(
    tmp_path: Path,
    attribute: str,
) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("codex-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    _executable(Path(str(getattr(profile, attribute))), "replacement")

    stale = service.describe(
        "codex-builder",
        auth={"state": "ready", "freshness": "fresh"},
    ).to_wire()

    assert stale["installation_state"] == "installed"
    assert stale["qualification"]["freshness"] == "invalid"  # type: ignore[index]
    assert stale["initialization_state"] == "not_checked"
    assert stale["launchable"] is False
    assert stale["refusal"]["code"] == "provider_qualification_failed"  # type: ignore[index]


def test_corrupt_or_profile_mismatched_receipt_never_implies_init_ready(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    profile = service.profiles.get("codex-builder")
    store.record(
        profile,
        workspace_root=tmp_path,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version=None,
    )
    path = store.root / f"{profile.profile_id.value}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profile_id"] = "codex-independent-reviewer"
    path.write_bytes(strict_state_bytes(document))

    invalid = service.describe("codex-builder").to_wire()

    assert invalid["qualification"]["freshness"] == "invalid"  # type: ignore[index]
    assert invalid["initialization_state"] == "not_checked"
    assert invalid["launchable"] is False
