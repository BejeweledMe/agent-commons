from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import LifecycleConflictError
from agent_commons.services import CommonsManager

_SESSION_KEYS_WITH_NONCE = [
    "schema",
    "session_id",
    "stable_instance_id",
    "principal",
    "client",
    "software",
    "model_family",
    "model",
    "role",
    "capabilities",
    "source_producer",
    "nonce",
    "opened_at",
    "last_seen_at",
    "expires_at",
    "status",
    "closed_at",
    "effective_status",
]
_CLAIM_KEYS_WITH_NONCE = [
    "schema",
    "claim_id",
    "resources",
    "owner_session_id",
    "mode",
    "nonce",
    "acquired_at",
    "renewed_at",
    "expires_at",
    "description",
    "status",
    "ended_at",
    "ended_by_session_id",
    "end_reason",
]


def _manager(tmp_path: Path) -> CommonsManager:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="public-views-tests")
    return CommonsManager(repo, state_root=tmp_path / "state")


def _start_session(manager: CommonsManager) -> dict[str, object]:
    session = manager.start_session(
        stable_instance_id="public-views-builder-session-12345678",
        principal="operator",
        client="codex",
        software="codex-desktop",
        role="builder",
        capabilities=("claim:write", "task:write"),
        model_family="openai",
        model="gpt-5.6",
        source_producer={
            "client": "claude",
            "software": "claude-code",
            "model_family": "anthropic",
            "model": "claude-sonnet",
            "principal": "external-operator",
            "external_session_id": "external-session-12345678",
        },
    )
    manager.session_id = str(session["session_id"])
    return session


def test_session_and_claim_views_preserve_wire_shape_key_order_and_nonce_redaction(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    opened = _start_session(manager)

    assert list(opened) == _SESSION_KEYS_WITH_NONCE
    assert opened["capabilities"] == ["claim:write", "task:write"]
    assert opened["source_producer"] == {
        "client": "claude",
        "software": "claude-code",
        "model_family": "anthropic",
        "model": "claude-sonnet",
        "principal": "external-operator",
        "external_session_id": "external-session-12345678",
    }
    shown = manager.show_session()
    assert isinstance(shown, dict)
    assert list(shown) == [key for key in _SESSION_KEYS_WITH_NONCE if key != "nonce"]
    assert "nonce" not in shown
    assert shown == {key: value for key, value in opened.items() if key != "nonce"}

    acquired = manager.acquire_claim(
        ("path:src/agent_commons/services/public_views.py",),
        description="Pin the public view shape.",
        idempotency_key="public-view-shape-claim",
    )
    assert list(acquired) == _CLAIM_KEYS_WITH_NONCE
    assert acquired["resources"] == ["path:src/agent_commons/services/public_views.py"]
    listed = manager.list_claims()
    assert list(listed[0]) == [key for key in _CLAIM_KEYS_WITH_NONCE if key != "nonce"]
    assert "nonce" not in listed[0]
    assert listed[0] == {key: value for key, value in acquired.items() if key != "nonce"}


def test_end_session_keeps_the_live_delegation_guard_in_the_manager_core(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _start_session(manager)
    task = manager.create_task(
        title="Protect a live delegation",
        description="The session must remain open while its work is non-terminal.",
        acceptance_criteria=("The guard rejects closure.",),
        idempotency_key="public-view-session-guard-task",
    )
    requested = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=str(task["revision"]),
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 900,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "tokens", "limit": 10_000},
        },
        idempotency_key="public-view-session-guard-delegation",
    )

    with pytest.raises(LifecycleConflictError, match="non-terminal delegations"):
        manager.end_session(nonce=str(session["nonce"]))

    active = manager.show_session(str(session["session_id"]))
    assert isinstance(active, dict)
    assert active["status"] == "active"
    assert manager.get_delegation(requested["entity_ref"]["id"])["state"] == "requested"
