from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import agent_commons.runtime.skill_projection as projection_module
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    AttemptState,
    AttemptStore,
    ContextBindingRequest,
    ContextBindingResolver,
    LaunchPlanner,
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
from agent_commons.services.context_compiler import ContextCompiler
from agent_commons.services.delegation_runtime import DelegationRuntimeService, _RoleScope


class _ReadyAuth:
    def status(self, profile: Any, **values: Any) -> ProviderAuthStatus:
        del values
        return ProviderAuthStatus.create(
            provider=Provider(profile.provider),
            operation=ProviderAuthOperation.STATUS,
            state=ProviderAuthState.READY,
        )


class _Initialization:
    def __init__(self, state: ProviderInitializationState) -> None:
        self.state = state
        self.calls = 0

    def probe(self, profile: Any, **values: Any) -> ProviderInitializationStatus:
        del values
        self.calls += 1
        return ProviderInitializationStatus(
            provider=Provider(profile.provider),
            state=self.state,
        )


class _SuccessWithoutTerminalMcp:
    def __init__(self) -> None:
        self.invocations: list[Any] = []

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        self.invocations.append(invocation)
        values["on_started"](values.pop("test_pid", 43210))
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=43210,
            duration_seconds=0.01,
            stdout=b"raw provider secret",
            stderr=b"",
            stdout_bytes_seen=19,
            stderr_bytes_seen=0,
            output_truncated=False,
        )


def _workspace(tmp_path: Path) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="p2-launch-plan")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="p2-parent-session-00000001",
        principal="operator",
        client="codex",
        software="codex-desktop",
        role="coordinator",
        ttl_seconds=3600,
    )
    manager.session_id = str(parent["session_id"])
    task = manager.create_task(
        title="Exercise immutable launch",
        description="Run one exact provider launch.",
        acceptance_criteria=("runtime result remains honest",),
        idempotency_key="p2-target-task",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=str(task["revision"]),
        target_profile="claude-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 30,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key="p2-delegation",
    )
    return manager, delegation


def _service(
    manager: CommonsManager,
    *,
    runner: Any,
    initialization: _Initialization,
) -> DelegationRuntimeService:
    return DelegationRuntimeService(
        manager,
        runner=runner,
        provider_auth=_ReadyAuth(),  # type: ignore[arg-type]
        initialization_probe=initialization,
        profiles=default_profile_registry(
            codex_executable="/bin/echo",
            claude_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=True,
        ),
    )


def _delegation_for_role(
    manager: CommonsManager,
    original: dict[str, Any],
    *,
    context_mode: str,
    suffix: str,
) -> dict[str, Any]:
    original = manager.get_delegation(str(original["entity_ref"]["id"]))
    role = manager.create_agent(
        name=f"{context_mode.title()} launch role",
        profile_id="claude-builder",
        rationale="Exercise the canonical role context contract.",
        context_mode=context_mode,
        idempotency_key=f"role-context-{suffix}",
    )
    return manager.create_delegation(
        target_ref=original["target_ref"],
        target_revision=str(original["target_revision"]),
        target_profile="claude-builder",
        purpose="implementation",
        limits=original["limits"],
        on_behalf_of_agent_id=str(role["entity_ref"]["id"]),
        idempotency_key=f"role-context-delegation-{suffix}",
    )


@pytest.mark.parametrize("context_mode", ("accumulated", "fresh"))
def test_role_context_contract_refuses_before_probe_child_and_attempt(
    tmp_path: Path,
    context_mode: str,
) -> None:
    manager, original = _workspace(tmp_path)
    delegation = _delegation_for_role(
        manager,
        original,
        context_mode=context_mode,
        suffix=context_mode,
    )
    pack = manager.context_packs.publish(
        _pack_draft(manager, "Role context baseline"),
        idempotency_key=f"role-context-pack-{context_mode}",
    )
    context = (
        None
        if context_mode == "accumulated"
        else ContextBindingRequest.accumulated(
            context_pack_id=pack.context_pack_id,
            context_pack_revision=pack.revision,
        )
    )
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as refused:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key=f"role-context-launch-{context_mode}",
            context=context,
        )

    assert refused.value.code == "context_binding_role_mismatch"
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert service.context_bindings.get(str(delegation["entity_ref"]["id"])) is None
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_persistent_initialization_refusal_is_pure_and_not_reprobed(tmp_path: Path) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.HOST_SANDBOX_REFUSED)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    delegation_id = str(delegation["entity_ref"]["id"])
    revision = str(delegation["revision"])
    sessions_before = len(manager.sessions.list_sessions())

    for _ in range(2):
        with pytest.raises(ConfigurationError) as caught:
            service.run(
                delegation_id,
                revision,
                idempotency_key="p2-refusal-key",
            )
        assert caught.value.code == "host_sandbox_refused"

    assert initialization.calls == 1
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    assert manager.get_delegation(delegation_id)["state"] == "requested"


def test_missing_profile_qualification_refuses_before_probe_child_and_attempt(
    tmp_path: Path,
) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        provider_auth=_ReadyAuth(),  # type: ignore[arg-type]
        initialization_probe=initialization,
        qualification_required=True,
        profiles=default_profile_registry(
            codex_executable="/bin/echo",
            claude_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=True,
        ),
    )
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as caught:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="p3-unqualified-refusal",
        )

    assert caught.value.code == "provider_qualification_required"
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_init_green_does_not_turn_process_exit_into_terminal_mcp_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    original_build = LaunchPlanner.build
    built: list[Any] = []

    def recording_build(*args: Any, **kwargs: Any) -> Any:
        result = original_build(*args, **kwargs)
        built.append(result)
        return result

    monkeypatch.setattr(LaunchPlanner, "build", staticmethod(recording_build))

    result = service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="p2-green-no-terminal",
    )

    assert initialization.calls == 1
    assert len(built) == 1
    assert len(runner.invocations) == 1
    assert runner.invocations[0] is built[0].invocation
    assert result["delegation"]["state"] == "needs_operator"
    assert result["delegation"]["reason_code"] == "invalid_result"
    assert result["attempt"]["launch_plan_sha256"]
    serialized = str(result)
    assert "raw provider secret" not in serialized


def test_static_refusal_precedes_auth_init_child_and_attempt(tmp_path: Path) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    # Codex cannot enforce micro_usd.  Mutate only the in-memory read
    # projection to exercise the pure planner boundary without creating a
    # second canonical fixture shape.
    original = service.manager.get_delegation

    def incompatible(entity_id: str) -> dict[str, Any]:
        value = dict(original(entity_id))
        value["target_profile"] = "codex-builder"
        limits = dict(value["limits"])
        limits["budget"] = {"unit": "micro_usd", "limit": 1}
        value["limits"] = limits
        return value

    service.manager.get_delegation = incompatible  # type: ignore[method-assign]
    sessions_before = len(manager.sessions.list_sessions())
    with pytest.raises(ConfigurationError):
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="p2-static-refusal",
        )
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_unavailable_skill_projection_precedes_init_child_and_attempt(tmp_path: Path) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    service._role_scope = lambda _delegation: _RoleScope(  # type: ignore[method-assign]
        skills=("private-workspace-skill",)
    )
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as caught:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="p4-skill-refusal",
        )

    assert caught.value.code == "skill_projection_unavailable"
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_skill_source_drift_after_static_validation_has_zero_launch_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    service._role_scope = lambda _delegation: _RoleScope(  # type: ignore[method-assign]
        skills=("commons-start",)
    )

    class CountingAuth(_ReadyAuth):
        calls = 0

        def status(self, profile: Any, **values: Any) -> ProviderAuthStatus:
            self.calls += 1
            return super().status(profile, **values)

    auth = CountingAuth()
    service.provider_auth = auth  # type: ignore[assignment]
    original_validate = LaunchPlanner.validate_static
    original_source = projection_module._source

    def validate_then_change_source(planner: LaunchPlanner, *args: Any, **kwargs: Any) -> Any:
        result = original_validate(planner, *args, **kwargs)

        def changed_source(skill_id: str) -> bytes:
            return original_source(skill_id) + b"\nchanged-after-static-projection"

        monkeypatch.setattr(projection_module, "_source", changed_source)
        return result

    monkeypatch.setattr(LaunchPlanner, "validate_static", validate_then_change_source)
    sessions_before = len(manager.sessions.list_sessions())
    delegation_id = str(delegation["entity_ref"]["id"])

    with pytest.raises(ConfigurationError) as caught:
        service.run(
            delegation_id,
            str(delegation["revision"]),
            idempotency_key="p4-stale-projection",
        )

    assert caught.value.code == "skill_projection_unavailable"
    assert initialization.calls == 0
    assert auth.calls == 0
    assert runner.invocations == []
    assert service.context_bindings.get(delegation_id) is None
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_untrusted_profile_refuses_before_auth_init_child_and_attempt(tmp_path: Path) -> None:
    manager, delegation = _workspace(tmp_path)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = DelegationRuntimeService(
        manager,
        runner=runner,  # type: ignore[arg-type]
        provider_auth=_ReadyAuth(),  # type: ignore[arg-type]
        initialization_probe=initialization,
        profiles=default_profile_registry(
            codex_executable="/bin/echo",
            claude_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=False,
        ),
    )
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as caught:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="p2-untrusted-static-refusal",
        )

    assert caught.value.code == "trusted_workspace_required"
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before


def test_initialization_precedes_auth_immediately_before_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    events: list[str] = []

    class OrderedInitialization(_Initialization):
        def probe(self, profile: Any, **values: Any) -> ProviderInitializationStatus:
            events.append("initialization")
            return super().probe(profile, **values)

    class OrderedAuth(_ReadyAuth):
        def status(self, profile: Any, **values: Any) -> ProviderAuthStatus:
            events.append("auth")
            return super().status(profile, **values)

    service = DelegationRuntimeService(
        manager,
        runner=_SuccessWithoutTerminalMcp(),  # type: ignore[arg-type]
        provider_auth=OrderedAuth(),  # type: ignore[arg-type]
        initialization_probe=OrderedInitialization(ProviderInitializationState.READY),
        profiles=default_profile_registry(
            codex_executable="/bin/echo",
            claude_executable="/bin/echo",
            mcp_executable="/bin/echo",
            git_executable="/usr/bin/true",
            trusted_workspace=True,
        ),
    )
    open_child = service._open_child_session

    def recording_open_child(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("child")
        return open_child(*args, **kwargs)

    monkeypatch.setattr(service, "_open_child_session", recording_open_child)

    service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="p2-probe-order",
    )

    assert events == ["initialization", "auth", "child"]


def test_crash_after_reserve_reconciles_without_second_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(
        manager,
        runner=runner,
        initialization=_Initialization(ProviderInitializationState.READY),
    )
    original_transition = AttemptStore.transition
    crashed = False

    def crash_once(store: AttemptStore, attempt_id: str, state: AttemptState, **values: Any):
        nonlocal crashed
        if state is AttemptState.LAUNCHING and not crashed:
            crashed = True
            raise RuntimeError("crash after reserve")
        return original_transition(store, attempt_id, state, **values)

    monkeypatch.setattr(AttemptStore, "transition", crash_once)
    delegation_id = str(delegation["entity_ref"]["id"])
    with pytest.raises(RuntimeError, match="after reserve"):
        service.run(
            delegation_id,
            str(delegation["revision"]),
            idempotency_key="p2-crash-reserve",
        )
    assert len(service.attempts.list_attempts()) == 1

    recovered = service.run(
        delegation_id,
        str(delegation["revision"]),
        idempotency_key="p2-crash-reserve",
    )
    assert recovered["delegation"]["state"] == "needs_operator"
    assert len(service.attempts.list_attempts()) == 1
    assert runner.invocations == []


def test_legacy_v4_attempt_without_binding_reconciles_but_cannot_relaunch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(
        manager,
        runner=runner,
        initialization=_Initialization(ProviderInitializationState.READY),
    )
    original_transition = AttemptStore.transition
    crashed = False

    def crash_once(store: AttemptStore, attempt_id: str, state: AttemptState, **values: Any):
        nonlocal crashed
        if state is AttemptState.LAUNCHING and not crashed:
            crashed = True
            raise RuntimeError("legacy crash after reserve")
        return original_transition(store, attempt_id, state, **values)

    monkeypatch.setattr(AttemptStore, "transition", crash_once)
    delegation_id = str(delegation["entity_ref"]["id"])
    revision = str(delegation["revision"])
    with pytest.raises(RuntimeError, match="legacy crash"):
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-legacy-recovery",
        )

    binding_path = next((tmp_path / "state" / "runtime" / "context-bindings").glob("*.json"))
    binding_path.unlink()
    request_path = next((tmp_path / "state" / "runtime" / "requests").glob("*.json"))
    document = json.loads(request_path.read_text(encoding="utf-8"))
    document["schema"] = "agent_commons.runtime_request.v4"
    for attempt in document["attempts"]:
        attempt["schema"] = "agent_commons.runtime_attempt.v4"
        attempt.pop("stderr_diagnostic_tail")
        attempt.pop("stderr_diagnostic_tail_truncated")
        attempt.pop("stderr_diagnostic_tail_redacted")
    request_path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as retry_refusal:
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-legacy-recovery",
            retry=True,
        )
    assert retry_refusal.value.code == "context_binding_unavailable"
    assert runner.invocations == []

    recovered = service.run(
        delegation_id,
        revision,
        idempotency_key="c2-legacy-recovery",
    )
    assert recovered["delegation"]["state"] == "needs_operator"
    assert runner.invocations == []
    assert service.context_bindings.get(delegation_id) is None


def test_crash_after_start_reconciles_dead_process_without_relaunch(tmp_path: Path) -> None:
    class CrashAfterStart:
        calls = 0

        def run(self, invocation: Any, **values: Any) -> ProcessResult:
            del invocation
            self.calls += 1
            process = subprocess.Popen(["/usr/bin/true"])
            values["on_started"](process.pid)
            process.wait(timeout=5)
            raise RuntimeError("crash after start")

    manager, delegation = _workspace(tmp_path)
    runner = CrashAfterStart()
    service = _service(
        manager,
        runner=runner,
        initialization=_Initialization(ProviderInitializationState.READY),
    )
    delegation_id = str(delegation["entity_ref"]["id"])
    with pytest.raises(RuntimeError, match="after start"):
        service.run(
            delegation_id,
            str(delegation["revision"]),
            idempotency_key="p2-crash-start",
        )

    recovered = service.run(
        delegation_id,
        str(delegation["revision"]),
        idempotency_key="p2-crash-start",
    )
    assert recovered["delegation"]["state"] == "needs_operator"
    assert runner.calls == 1
    assert len(service.attempts.list_attempts()) == 1


def test_crash_before_canonical_finalization_replays_terminal_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(
        manager,
        runner=runner,
        initialization=_Initialization(ProviderInitializationState.READY),
    )
    original_finalize = service._finalize_attempt
    crashed = False

    def crash_once(attempt: Any):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before canonical finalization")
        return original_finalize(attempt)

    monkeypatch.setattr(service, "_finalize_attempt", crash_once)
    delegation_id = str(delegation["entity_ref"]["id"])
    with pytest.raises(RuntimeError, match="before canonical finalization"):
        service.run(
            delegation_id,
            str(delegation["revision"]),
            idempotency_key="p2-crash-finalize",
        )

    recovered = service.run(
        delegation_id,
        str(delegation["revision"]),
        idempotency_key="p2-crash-finalize",
    )
    assert recovered["delegation"]["state"] == "needs_operator"
    assert len(runner.invocations) == 1
    assert len(service.attempts.list_attempts()) == 1


def _pack_draft(manager: CommonsManager, summary: str) -> dict[str, Any]:
    source = manager.repo_root / "context-source.txt"
    source.write_text("verified source", encoding="utf-8")
    artifact = manager.register_artifact(
        source,
        media_type="text/plain",
        classification="internal",
        idempotency_key="c2-context-source",
    )
    return {
        "summary": summary,
        "facts": [
            {
                "statement": "The artifact is the exact verified input.",
                "source_refs": [{"ref": artifact["entity_ref"], "revision": artifact["revision"]}],
            }
        ],
        "decision_refs": [],
        "open_questions": ["What remains open?"],
    }


def test_accumulated_launch_binds_exact_revision_and_later_revision_cannot_alter_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    pack = manager.context_packs.publish(
        _pack_draft(manager, "C2 baseline"), idempotency_key="c2-pack"
    )
    compiled = manager.context_packs.compile(pack.context_pack_id, pack.revision)
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    original_build = LaunchPlanner.build
    built: list[Any] = []

    def recording_build(*args: Any, **kwargs: Any) -> Any:
        result = original_build(*args, **kwargs)
        built.append(result)
        return result

    monkeypatch.setattr(LaunchPlanner, "build", staticmethod(recording_build))

    result = service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="c2-accumulated",
        context=ContextBindingRequest.accumulated(
            context_pack_id=pack.context_pack_id,
            context_pack_revision=pack.revision,
        ),
    )

    assert len(built) == 1
    binding = built[0].context.binding
    assert binding is not None
    assert binding.context_pack_id == pack.context_pack_id
    assert binding.context_pack_revision == pack.revision
    assert binding.compiled_context_fingerprint == compiled.compiled_context_fingerprint
    baseline = compiled.text.encode("utf-8")
    assert built[0].invocation.stdin.endswith(baseline)
    assert built[0].invocation.stdin != baseline
    assert result["delegation"]["state"] == "needs_operator"
    assert compiled.text not in str(result)

    manager.context_packs.revise(
        pack.context_pack_id,
        pack.revision,
        _pack_draft(manager, "Later baseline"),
        idempotency_key="c2-revise",
    )
    assert built[0].context.binding.compiled_context_fingerprint == (
        compiled.compiled_context_fingerprint
    )
    assert built[0].invocation.stdin.endswith(baseline)


@pytest.mark.parametrize(
    ("selection", "expected_code"),
    (
        ("missing", "context_binding_missing"),
        ("stale", "context_binding_stale"),
        ("oversized", "context_binding_oversized"),
        ("disabled", "context_binding_unavailable"),
    ),
)
def test_unusable_pack_selection_refuses_before_probes_child_request_and_attempt(
    tmp_path: Path,
    selection: str,
    expected_code: str,
) -> None:
    manager, delegation = _workspace(tmp_path)
    pack = manager.context_packs.publish(
        _pack_draft(manager, "C2 baseline"), idempotency_key="c2-pack"
    )
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    pack_id = pack.context_pack_id
    revision = pack.revision
    if selection == "missing":
        pack_id = "context_pack." + "0" * 25 + "9"
    elif selection == "stale":
        revision = "evt." + "0" * 25 + "9"
    elif selection == "oversized":
        service._context_binding_resolver = ContextBindingResolver(
            compiler=ContextCompiler(max_bytes=1)
        )
    else:
        manager.context_pack_writes_enabled = False
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(ConfigurationError) as caught:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="c2-refused",
            context=ContextBindingRequest.accumulated(
                context_pack_id=pack_id,
                context_pack_revision=revision,
            ),
        )

    assert caught.value.code == expected_code
    assert initialization.calls == 0
    assert runner.invocations == []
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    assert manager.get_delegation(str(delegation["entity_ref"]["id"]))["state"] == "requested"


def test_fresh_launch_continues_unchanged_while_pack_writes_are_disabled(
    tmp_path: Path,
) -> None:
    manager, delegation = _workspace(tmp_path)
    manager.context_pack_writes_enabled = False
    initialization = _Initialization(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)

    result = service.run(
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        idempotency_key="c2-fresh-rollback",
    )

    assert len(runner.invocations) == 1
    assert b"# Agent Commons Context Pack" not in runner.invocations[0].stdin
    assert result["delegation"]["state"] == "needs_operator"


def test_accumulated_binding_survives_crash_before_probe_and_requires_exact_reentry(
    tmp_path: Path,
) -> None:
    manager, delegation = _workspace(tmp_path)
    pack = manager.context_packs.publish(
        _pack_draft(manager, "C2 crash baseline"), idempotency_key="c2-crash-pack"
    )

    class CrashBeforeProbe(_Initialization):
        def probe(self, profile: Any, **values: Any) -> ProviderInitializationStatus:
            del profile, values
            self.calls += 1
            raise RuntimeError("crash before attempt")

    initialization = CrashBeforeProbe(ProviderInitializationState.READY)
    runner = _SuccessWithoutTerminalMcp()
    service = _service(manager, runner=runner, initialization=initialization)
    delegation_id = str(delegation["entity_ref"]["id"])
    revision = str(delegation["revision"])
    selection = ContextBindingRequest.accumulated(
        context_pack_id=pack.context_pack_id,
        context_pack_revision=pack.revision,
    )
    sessions_before = len(manager.sessions.list_sessions())

    with pytest.raises(RuntimeError, match="crash before attempt"):
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-crash-before-attempt",
            context=selection,
        )

    stored = service.context_bindings.get(delegation_id)
    assert stored is not None
    assert stored.metadata.context_pack_id == pack.context_pack_id
    assert stored.metadata.context_pack_revision == pack.revision
    assert stored.metadata.compiled_context_size_bytes > 0
    assert service.attempts.list_attempts() == ()
    assert len(manager.sessions.list_sessions()) == sessions_before
    assert runner.invocations == []

    with pytest.raises(ConfigurationError) as omitted:
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-crash-before-attempt",
        )
    assert omitted.value.code == "context_binding_stale"
    assert initialization.calls == 1

    other_pack = manager.context_packs.publish(
        _pack_draft(manager, "Different C2 baseline"),
        idempotency_key="c2-different-pack",
    )
    with pytest.raises(ConfigurationError) as changed:
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-crash-before-attempt",
            context=ContextBindingRequest.accumulated(
                context_pack_id=other_pack.context_pack_id,
                context_pack_revision=other_pack.revision,
            ),
        )
    assert changed.value.code == "context_binding_stale"
    assert initialization.calls == 1

    service.initialization_probe = _Initialization(ProviderInitializationState.READY)
    result = service.run(
        delegation_id,
        revision,
        idempotency_key="c2-crash-before-attempt",
        context=selection,
    )
    assert result["delegation"]["state"] == "needs_operator"
    assert len(runner.invocations) == 1


def test_same_exact_selection_refuses_if_recompiled_fingerprint_changes(
    tmp_path: Path,
) -> None:
    manager, delegation = _workspace(tmp_path)
    pack = manager.context_packs.publish(
        _pack_draft(manager, "C2 compiler baseline"), idempotency_key="c2-compiler-pack"
    )
    initialization = _Initialization(ProviderInitializationState.HOST_SANDBOX_REFUSED)
    service = _service(
        manager,
        runner=_SuccessWithoutTerminalMcp(),
        initialization=initialization,
    )
    delegation_id = str(delegation["entity_ref"]["id"])
    revision = str(delegation["revision"])
    selection = ContextBindingRequest.accumulated(
        context_pack_id=pack.context_pack_id,
        context_pack_revision=pack.revision,
    )
    with pytest.raises(ConfigurationError):
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-compiler-drift",
            context=selection,
        )
    assert service.context_bindings.get(delegation_id) is not None

    service._context_binding_resolver = ContextBindingResolver(
        compiler=ContextCompiler(compiler_version="context-pack-compiler.v2")
    )
    with pytest.raises(ConfigurationError) as drift:
        service.run(
            delegation_id,
            revision,
            idempotency_key="c2-compiler-drift",
            context=selection,
        )
    assert drift.value.code == "context_binding_stale"
    assert initialization.calls == 1


def test_workspace_ledger_authorization_rejects_unprojected_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, delegation = _workspace(tmp_path)
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign_manager, _ = _workspace(foreign_root)
    foreign = foreign_manager.context_packs.publish(
        _pack_draft(foreign_manager, "foreign baseline"),
        idempotency_key="c2-foreign-pack",
    )
    service = _service(
        manager,
        runner=_SuccessWithoutTerminalMcp(),
        initialization=_Initialization(ProviderInitializationState.READY),
    )
    monkeypatch.setattr(manager.context_packs, "get", lambda *args, **kwargs: foreign)

    with pytest.raises(ConfigurationError) as refused:
        service.run(
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            idempotency_key="c2-foreign-selection",
            context=ContextBindingRequest.accumulated(
                context_pack_id=foreign.context_pack_id,
                context_pack_revision=foreign.revision,
            ),
        )
    assert refused.value.code == "context_binding_unauthorized"
    assert service.attempts.list_attempts() == ()
