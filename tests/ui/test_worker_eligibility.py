"""Worker-eligibility reads and fail-closed hiring behaviour."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest

from agent_commons.library import LibraryStore
from agent_commons.runtime.provider_qualification import ProviderQualificationStore
from agent_commons.services.delegation_runtime import load_runtime_configuration
from agent_commons.ui.context import UIContext, ledger_fingerprint
from tests.ui.conftest import authorized
from tests.ui.test_launch import _client, _launch_workspace


def _role_ref(role_id: str = "agent-engineer") -> dict[str, str]:
    return next(
        item["ref"] for item in LibraryStore().catalog()["roles"] if item["ref"]["id"] == role_id
    )


def _specialization(ref: dict[str, str]) -> str:
    return quote("/".join((ref["source"], ref["id"], ref["version"])))


def _ready_fixture(workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fixture = _launch_workspace(workspace)
    context = fixture["context"]
    config = load_runtime_configuration(context._profile_config, workspace_root=context.repo)
    store = ProviderQualificationStore(context.manager().paths.state_root)
    for profile_id in config.profiles.profile_ids:
        store.record(
            config.profiles.get(profile_id),
            workspace_root=context.repo,
            static_preflight=True,
            initialization_probe=True,
            behavioral_canary=True,
            provider_version="test-provider",
        )
    monkeypatch.setattr(
        context._launch_coordinator,
        "provider_auth_status",
        lambda _profile_id: {"state": "ready", "freshness": "fresh"},
    )
    return fixture


def _payload(client: Any, ref: dict[str, str]) -> dict[str, Any]:
    response = client.get(
        f"/api/workers/eligibility?specialization={_specialization(ref)}",
        headers=authorized(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _worker(payload: dict[str, Any], profile_id: str) -> dict[str, Any]:
    return next(item for item in payload["workers"] if item["profile_id"] == profile_id)


def test_eligible_builder_has_current_observations_and_launchable_profile(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    with _client(fixture["context"]) as client:
        worker = _worker(_payload(client, _role_ref()), "claude-builder")
    assert worker["eligibility"] == "eligible"
    assert worker["refusal"] is None


def test_missing_or_stale_qualification_is_unknown(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _launch_workspace(workspace)
    monkeypatch.setattr(
        fixture["context"]._launch_coordinator,
        "provider_auth_status",
        lambda _profile_id: {"state": "ready", "freshness": "fresh"},
    )
    with _client(fixture["context"]) as client:
        worker = _worker(_payload(client, _role_ref()), "claude-builder")
    assert worker["eligibility"] == "unknown"
    assert worker["refusal"]["code"] == "worker_observation_missing"


def test_missing_or_stale_authentication_is_unknown(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    monkeypatch.setattr(
        fixture["context"]._launch_coordinator,
        "provider_auth_status",
        lambda _profile_id: {"state": "ready", "freshness": "stale"},
    )
    with _client(fixture["context"]) as client:
        worker = _worker(_payload(client, _role_ref()), "claude-builder")
    assert worker["eligibility"] == "unknown"
    assert worker["refusal"]["code"] == "worker_observation_missing"


def test_unresolvable_specialization_makes_every_worker_unknown(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    bad_ref = {"kind": "role", "source": "builtin", "id": "gone", "version": "f" * 64}
    with _client(fixture["context"]) as client:
        payload = _payload(client, bad_ref)
    assert payload["specialization"] is None
    assert {worker["eligibility"] for worker in payload["workers"]} == {"unknown"}
    assert {worker["refusal"]["code"] for worker in payload["workers"]} == {
        "specialization_unavailable"
    }


def test_reviewer_profile_is_only_eligible_for_reviewer_specializations(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    with _client(fixture["context"]) as client:
        builder = _worker(_payload(client, _role_ref()), "claude-independent-reviewer")
        reviewer = _worker(
            _payload(client, _role_ref("security-reviewer")), "claude-independent-reviewer"
        )
    assert builder["eligibility"] == "ineligible"
    assert builder["refusal"]["code"] == "review_profile_incompatible"
    assert reviewer["eligibility"] == "eligible"


def test_unlaunchable_provider_keeps_its_availability_refusal(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    monkeypatch.setattr(
        fixture["context"]._launch_coordinator,
        "provider_auth_status",
        lambda _profile_id: {"state": "authentication_required", "freshness": "fresh"},
    )
    with _client(fixture["context"]) as client:
        worker = _worker(_payload(client, _role_ref()), "claude-builder")
    assert worker["eligibility"] == "ineligible"
    assert worker["refusal"]["code"] == "provider_authentication_required"


def test_ineligible_hire_returns_typed_refusal_without_a_ledger_write(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    context = fixture["context"]
    ref = _role_ref()
    before_events = context.manager().snapshot().known_event_ids
    before_fingerprint = ledger_fingerprint(context.manager().paths)
    with _client(context) as client:
        response = client.post(
            "/api/agents",
            json={
                "name": "Unavailable worker",
                "profile_id": "claude-independent-reviewer",
                "rationale": "exercise the refusal",
                "specialization_ref": ref,
            },
            headers=authorized(),
        )
    assert response.status_code == 409
    assert response.json() == {
        "code": "worker_ineligible",
        "refusal": {
            "code": "review_profile_incompatible",
            "remediation": ["choose_builder_profile"],
        },
    }
    assert context.manager().snapshot().known_event_ids == before_events
    assert ledger_fingerprint(context.manager().paths) == before_fingerprint


def test_eligibility_response_is_closed(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    with _client(fixture["context"]) as client:
        payload = _payload(client, _role_ref())
    assert set(payload) == {"schema", "specialization", "workers"}
    assert payload["schema"] == "agent_commons.worker-eligibility.v1"
    assert all(
        set(worker)
        == {
            "profile_id",
            "provider",
            "model",
            "eligibility",
            "refusal",
            "capabilities",
            "observed_revision",
        }
        for worker in payload["workers"]
    )


def test_read_only_client_can_read_eligibility_but_cannot_hire(
    workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _ready_fixture(workspace, monkeypatch)
    configured = fixture["context"]
    context = UIContext(
        configured.repo,
        state_root=configured.manager().paths.state_root,
        profile_config=configured._profile_config,
        runtime_factory=configured._runtime_factory,
    )
    monkeypatch.setattr(
        context._launch_coordinator,
        "provider_auth_status",
        lambda _profile_id: {"state": "ready", "freshness": "fresh"},
    )
    ref = _role_ref()
    with _client(context) as client:
        response = client.get(
            f"/api/workers/eligibility?specialization={_specialization(ref)}",
            headers=authorized(),
        )
        refused = client.post(
            "/api/agents",
            json={
                "name": "Read-only hire",
                "profile_id": "claude-builder",
                "rationale": "must not write",
                "specialization_ref": ref,
            },
            headers=authorized(),
        )
    assert response.status_code == 200, response.text
    assert refused.status_code == 404
