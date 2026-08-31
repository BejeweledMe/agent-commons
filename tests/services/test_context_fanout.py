from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_commons.runtime import (
    ContextBindingRequest,
    ProcessResult,
    Provider,
    ProviderAuthOperation,
    ProviderAuthState,
    ProviderAuthStatus,
    ProviderInitializationState,
    ProviderInitializationStatus,
    RunOutcome,
    RunReason,
    default_profile_registry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService


class _ReadyAuth:
    def status(self, profile: Any, **values: Any) -> ProviderAuthStatus:
        del values
        return ProviderAuthStatus.create(
            provider=Provider(profile.provider),
            operation=ProviderAuthOperation.STATUS,
            state=ProviderAuthState.READY,
        )


class _ReadyInitialization:
    def probe(self, profile: Any, **values: Any) -> ProviderInitializationStatus:
        del values
        return ProviderInitializationStatus(
            provider=Provider(profile.provider),
            state=ProviderInitializationState.READY,
        )


class _BoundedExitWithoutTerminalResult:
    def __init__(self) -> None:
        self.invocations: list[Any] = []

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        self.invocations.append(invocation)
        values["on_started"](47000 + len(self.invocations))
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=47000 + len(self.invocations),
            duration_seconds=0.01,
            stdout=b"provider output is not canonical context",
            stderr=b"",
            stdout_bytes_seen=40,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def _limits() -> dict[str, object]:
    return {
        "max_depth": 0,
        "wall_time_seconds": 30,
        "max_attempts": 1,
        "max_concurrency": 1,
        "budget": {"unit": "provider_units", "limit": 1},
    }


def test_two_heterogeneous_runs_share_only_the_frozen_compiled_baseline(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="c3-context-fanout")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="c3-context-fanout-parent",
        principal="operator",
        client="codex",
        software="codex-desktop",
        role="coordinator",
        ttl_seconds=3600,
    )
    manager.session_id = str(parent["session_id"])
    source = repo / "frozen-context-source.txt"
    source.write_text("exact shared source", encoding="utf-8")
    artifact = manager.register_artifact(
        source,
        media_type="text/plain",
        classification="internal",
        idempotency_key="c3-source",
    )
    draft = {
        "summary": "Frozen fan-out baseline",
        "facts": [
            {
                "statement": "Both roles receive the exact source revision.",
                "source_refs": [{"ref": artifact["entity_ref"], "revision": artifact["revision"]}],
            }
        ],
        "decision_refs": [],
        "open_questions": ["Which role-specific result is needed?"],
    }
    pack = manager.context_packs.publish(draft, idempotency_key="c3-pack")
    compiled = manager.context_packs.compile(pack.context_pack_id, pack.revision)
    selection = ContextBindingRequest.accumulated(
        context_pack_id=pack.context_pack_id,
        context_pack_revision=pack.revision,
    )

    roles = (
        manager.create_agent(
            name="Backend role",
            profile_id="claude-builder",
            rationale="implements the server-specific result",
            context_mode="accumulated",
            idempotency_key="c3-backend-role",
        ),
        manager.create_agent(
            name="Frontend role",
            profile_id="codex-builder",
            rationale="implements the client-specific result",
            context_mode="accumulated",
            idempotency_key="c3-frontend-role",
        ),
    )
    tasks = (
        manager.create_task(
            title="Implement the server result",
            description="Produce the bounded backend behavior.",
            acceptance_criteria=("Backend behavior is verified.",),
            idempotency_key="c3-backend-task",
        ),
        manager.create_task(
            title="Implement the client result",
            description="Produce the accessible frontend behavior.",
            acceptance_criteria=("Frontend behavior is verified.",),
            idempotency_key="c3-frontend-task",
        ),
    )
    profiles = ("claude-builder", "codex-builder")
    delegations = tuple(
        manager.create_delegation(
            target_ref=task["entity_ref"],
            target_revision=str(task["revision"]),
            target_profile=profile_id,
            purpose="implementation",
            limits=_limits(),
            on_behalf_of_agent_id=str(role["entity_ref"]["id"]),
            idempotency_key=f"c3-delegation-{index}",
        )
        for index, (role, task, profile_id) in enumerate(
            zip(roles, tasks, profiles, strict=True), start=1
        )
    )
    runner = _BoundedExitWithoutTerminalResult()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        provider_auth=_ReadyAuth(),  # type: ignore[arg-type]
        initialization_probe=_ReadyInitialization(),
        profiles=default_profile_registry(
            codex_executable="/bin/echo",
            claude_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=True,
        ),
    )

    results = tuple(
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key=f"c3-launch-{index}",
            context=selection,
        )
        for index, delegation in enumerate(delegations, start=1)
    )

    baseline = compiled.text.encode("utf-8")
    assert len(runner.invocations) == 2
    assert all(invocation.stdin.endswith(baseline) for invocation in runner.invocations)
    assert (
        runner.invocations[0].stdin[: -len(baseline)]
        != (runner.invocations[1].stdin[: -len(baseline)])
    )
    assert {result["delegation"]["state"] for result in results} == {"needs_operator"}
    attempts = service.attempts.list_attempts()
    assert len({attempt.attempt_id for attempt in attempts}) == 2
    child_ids = {attempt.correlation.child_session_id for attempt in attempts}
    assert len(child_ids) == 2
    assert all(manager.show_session(child_id)["status"] == "closed" for child_id in child_ids)

    stored = tuple(
        service.context_bindings.get(str(delegation["entity_ref"]["id"]))
        for delegation in delegations
    )
    assert all(item is not None for item in stored)
    assert {
        (
            item.metadata.context_pack_id,
            item.metadata.context_pack_revision,
            item.metadata.compiled_context_fingerprint,
        )
        for item in stored
        if item is not None
    } == {(pack.context_pack_id, pack.revision, compiled.compiled_context_fingerprint)}
    binding_documents = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "state" / "runtime" / "context-bindings").glob("*.json")
    )
    assert "Frozen fan-out baseline" not in binding_documents
    assert "provider output" not in binding_documents

    newer = manager.context_packs.revise(
        pack.context_pack_id,
        pack.revision,
        {**draft, "summary": "A later baseline"},
        idempotency_key="c3-pack-v2",
    )
    assert newer.revision != pack.revision
    assert all(invocation.stdin.endswith(baseline) for invocation in runner.invocations)
    assert all(
        item is not None and item.metadata.context_pack_revision == pack.revision for item in stored
    )
