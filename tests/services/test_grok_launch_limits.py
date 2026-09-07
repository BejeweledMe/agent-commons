"""Oversized synthetic instructions never reach a provider or child session."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    ContextBinding,
    ProviderInitializationState,
    default_profile_registry,
)
from agent_commons.runtime.model import GROK_PROMPT_ARGUMENT_MAX_BYTES
from agent_commons.services.delegation_runtime import DelegationRuntimeService
from tests.runtime.test_grok_instruction_limits import sized_instruction
from tests.runtime.test_launch_plan import _context_binding
from tests.services.test_delegation_launch_plan import (
    _Initialization,
    _ReadyAuth,
    _SuccessWithoutTerminalMcp,
    _workspace,
)


@pytest.mark.parametrize("refusal_stage", ["static", "early-context", "post-probe-context"])
def test_oversized_grok_launch_refuses_before_child_reservation_or_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal_stage: str
) -> None:
    manager, original = _workspace(tmp_path)
    original_record = manager.get_delegation(str(original["entity_ref"]["id"]))
    delegation = manager.create_delegation(
        target_ref=original_record["target_ref"],
        target_revision=original_record["target_revision"],
        target_profile="grok-builder",
        purpose="implementation",
        limits=original_record["limits"],
        idempotency_key="synthetic-grok-limit-delegation",
    )
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        provider_auth=_ReadyAuth(),  # type: ignore[arg-type]
        initialization_probe=initialization,
        profiles=default_profile_registry(
            grok_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=True,
        ),
    )
    marker = "SYNTHETIC_OVERSIZED_INSTRUCTION"
    size = GROK_PROMPT_ARGUMENT_MAX_BYTES + (refusal_stage == "static")
    instruction = marker + sized_instruction(size - len(marker), "界")
    monkeypatch.setattr(service, "_instruction", lambda *args, **kwargs: instruction)
    resolutions: list[None] = []

    def bind_context(**kwargs: Any) -> ContextBinding:
        del kwargs
        resolutions.append(None)
        if refusal_stage == "static":
            pytest.fail("static refusal must precede context binding")
        if refusal_stage == "post-probe-context" and len(resolutions) == 1:
            return ContextBinding.fresh()
        return _context_binding()

    monkeypatch.setattr(service, "_bind_context_for_launch", bind_context)
    sessions_before = len(manager.sessions.list_sessions())
    delegation_id = str(delegation["entity_ref"]["id"])
    with pytest.raises(ConfigurationError) as caught:
        service.run(
            delegation_id,
            str(delegation["revision"]),
            idempotency_key="synthetic-grok-oversize",
        )
    assert caught.value.code == "instruction_too_large"
    assert marker not in str(caught.value)
    assert marker not in str(caught.value.safe_next_actions)
    assert initialization.calls == (1 if refusal_stage == "post-probe-context" else 0)
    assert (
        len(resolutions)
        == {"static": 0, "early-context": 1, "post-probe-context": 2}[refusal_stage]
    )
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    assert manager.get_delegation(delegation_id)["state"] == "requested"
