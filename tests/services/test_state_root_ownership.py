from __future__ import annotations

from pathlib import Path

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.services import CommonsManager


def test_foreign_ambient_exact_root_cannot_authorize_a_canonical_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owning_repo = tmp_path / "owning-repo"
    target_repo = tmp_path / "target-repo"
    owning_repo.mkdir()
    target_repo.mkdir()
    CommonsManager.initialize(owning_repo, integrations=(), workspace_name="owner")
    CommonsManager.initialize(target_repo, integrations=(), workspace_name="target")

    shared_exact_root = tmp_path / "shared-exact-state"
    owner = CommonsManager(owning_repo, state_root=shared_exact_root)
    foreign_session = owner.start_session(
        stable_instance_id="foreign-exact-root-writer",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
        capabilities=("task:write",),
    )
    target_events = target_repo / ".agent-commons" / "events"
    before = tuple(target_events.rglob("*.json"))
    monkeypatch.setenv("AGENT_COMMONS_STATE_ROOT", str(shared_exact_root))

    with pytest.raises(ConfigurationError, match="belongs to a different") as captured:
        intruder = CommonsManager(
            target_repo,
            session_id=foreign_session["session_id"],
            state_root=shared_exact_root,
        )
        intruder.create_task(
            title="Unauthorized cross-workspace write",
            description="A foreign operational session must not authorize this event.",
            acceptance_criteria=("No canonical event is written.",),
            idempotency_key="foreign-exact-root-canonical-write",
        )

    assert captured.value.code == "state_owner_mismatch"
    assert tuple(target_events.rglob("*.json")) == before
