"""Internal DemoRunner mechanics tolerate unresolved provider executables.

The runner remains a development seam for simulating implementation behavior;
it is not a product route, first-run offer, or promise to complete an operator
workflow. Pre-start validation tolerates exactly ``ExecutableResolutionError``
only for this runner, which never launches a provider, MCP, or git process.
Everything else ``build_invocation`` refuses stays refused, demo or not, and
outside demo mode nothing changes at all.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError, ValidationError
from agent_commons.runtime import ProfileRegistry
from agent_commons.runtime.demo import DemoRunner, demo_tolerant_profiles
from agent_commons.runtime.model import (
    DEMO_UNRESOLVED_EXECUTABLE,
    ExecutableResolutionError,
    ExecutableRole,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import (
    DelegationRuntimeService,
    load_runtime_configuration,
)

#: Absolute paths that cannot exist and basenames that cannot be on PATH: both
#: legs of trusted resolution fail, exactly like a machine with no provider
#: CLI installed.
_MISSING_CLAUDE = "/nonexistent/agent-commons-tests/claude"
_MISSING_CODEX = "codex-agent-commons-tests-not-installed"
_MISSING_MCP = "agent-commons-mcp-tests-not-installed"
_MISSING_GIT = "/nonexistent/agent-commons-tests/git"


def _unresolvable_profiles(*, trusted_workspace: bool = True) -> ProfileRegistry:
    return ProfileRegistry.from_mapping(
        {
            "profiles": {
                "claude-builder": {
                    "executable": _MISSING_CLAUDE,
                    "mcp_executable": _MISSING_MCP,
                    "git_executable": _MISSING_GIT,
                    "permission_mode": "acceptEdits",
                    "trusted_workspace": trusted_workspace,
                },
                "claude-independent-reviewer": {
                    "executable": _MISSING_CLAUDE,
                    "mcp_executable": _MISSING_MCP,
                    "git_executable": _MISSING_GIT,
                    "permission_mode": "dontAsk",
                },
                "codex-builder": {
                    "executable": _MISSING_CODEX,
                    "mcp_executable": _MISSING_MCP,
                    "git_executable": _MISSING_GIT,
                    "sandbox": "workspace-write",
                    "approval_policy": "never",
                    "trusted_workspace": trusted_workspace,
                },
            }
        }
    )


def _workspace(tmp_path: Path) -> tuple[CommonsManager, dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="demo-mode")
    manager = CommonsManager(repo, state_root=tmp_path / "state")
    parent = manager.start_session(
        stable_instance_id="demo-parent-session-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator",
        ttl_seconds=8 * 60 * 60,
    )
    manager.session_id = parent["session_id"]
    task = manager.create_task(
        title="See the loop close without a subscription",
        description="The demo runner should complete this implementation run.",
        acceptance_criteria=("the delegation reaches succeeded",),
        idempotency_key="demo-target-task",
    )
    return manager, task


def _implementation_delegation(
    manager: CommonsManager,
    task: dict[str, Any],
    *,
    target_profile: str,
) -> dict[str, Any]:
    return manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile=target_profile,
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key=f"demo-delegation-{target_profile}",
    )


class _MustNotRun:
    """A non-demo injected runner; launching through it would fail the test."""

    def run(self, invocation: Any, **values: Any) -> Any:  # pragma: no cover
        del invocation, values
        pytest.fail("pre-start validation should have refused before any runner call")


def test_demo_implementation_run_succeeds_when_no_executable_resolves(
    tmp_path: Path,
) -> None:
    """An internal config still binds the runner seam without executables."""

    manager, task = _workspace(tmp_path)
    delegation = _implementation_delegation(manager, task, target_profile="claude-builder")
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        "demo: true\n"
        "profiles:\n"
        "  claude-builder:\n"
        f"    executable: {_MISSING_CLAUDE}\n"
        f"    mcp_executable: {_MISSING_MCP}\n"
        f"    git_executable: {_MISSING_GIT}\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )
    config = load_runtime_configuration(config_path, workspace_root=manager.repo_root)
    assert config.demo is True
    service = DelegationRuntimeService(
        manager,
        profiles=config.profiles,
        operator_limits=config.limits,
        runner=DemoRunner(manager.paths.state_root),  # type: ignore[arg-type]
    )

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="demo-launch-claude",
    )

    assert result["delegation"]["state"] == "succeeded"
    summary = str(result["delegation"].get("summary", "")).lower()
    assert "demo" in summary
    assert "no provider" in summary


def test_a_role_hired_on_a_model_still_runs_in_demo_mode(tmp_path: Path) -> None:
    """Model selection and the internal demo seam must not be mutually exclusive.

    The launch path substitutes the role's model with
    ``dataclasses.replace(profile, model=...)``, and in demo mode the profile
    it replaces is a demo-tolerant one.  ``replace`` reconstructs through the
    object's own class, so the copy has to come back tolerant -- a strict copy
    would veto this run over an unresolvable executable. Whole run, not the
    profile object: this goes through the same registry the broker is built
    with.
    """

    manager, task = _workspace(tmp_path)
    role = manager.create_agent(
        name="Demo builder",
        profile_id="claude-builder",
        rationale="hired on a chosen model, on a machine with no provider",
        model="claude-opus-4-6",
        idempotency_key="demo-model-role",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        on_behalf_of_agent_id=role["entity_ref"]["id"],
        idempotency_key="demo-model-delegation",
    )
    service = DelegationRuntimeService(
        manager,
        profiles=_unresolvable_profiles(),
        runner=DemoRunner(manager.paths.state_root),  # type: ignore[arg-type]
    )

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="demo-model-launch",
    )

    assert result["delegation"]["state"] == "succeeded"
    assert "no provider" in str(result["delegation"].get("summary", "")).lower()


def test_demo_codex_run_succeeds_when_no_executable_resolves(tmp_path: Path) -> None:
    """The Codex build order resolves MCP and git before the provider; the
    demo tolerance covers that path too."""

    manager, task = _workspace(tmp_path)
    delegation = _implementation_delegation(manager, task, target_profile="codex-builder")
    service = DelegationRuntimeService(
        manager,
        profiles=_unresolvable_profiles(),
        runner=DemoRunner(manager.paths.state_root),  # type: ignore[arg-type]
    )

    result = service.run(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        idempotency_key="demo-launch-codex",
    )

    assert result["delegation"]["state"] == "succeeded"
    assert "no provider" in str(result["delegation"].get("summary", "")).lower()


def test_without_demo_an_unresolvable_executable_is_still_refused(tmp_path: Path) -> None:
    """The default (real) runner keeps the exact pre-start refusal: same
    exception type, same sanitized diagnostic code, no delegation transition."""

    manager, task = _workspace(tmp_path)
    delegation = _implementation_delegation(manager, task, target_profile="claude-builder")
    service = DelegationRuntimeService(manager, profiles=_unresolvable_profiles())

    with pytest.raises(ConfigurationError, match="refused before any delegation attempt") as err:
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="strict-launch",
        )

    assert getattr(err.value, "code", None) == "provider_start_failed"
    assert manager.get_delegation(delegation["entity_ref"]["id"])["state"] == "requested"


def test_an_injected_non_demo_runner_gets_no_tolerance(tmp_path: Path) -> None:
    """The relief keys on the DemoRunner binding itself, not on the mere fact
    that a runner was injected: any other runner keeps the strict registry."""

    manager, task = _workspace(tmp_path)
    delegation = _implementation_delegation(manager, task, target_profile="claude-builder")
    service = DelegationRuntimeService(
        manager,
        profiles=_unresolvable_profiles(),
        runner=_MustNotRun(),  # type: ignore[arg-type]
    )

    with pytest.raises(ConfigurationError, match="refused before any delegation attempt"):
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="injected-strict-launch",
        )
    assert manager.get_delegation(delegation["entity_ref"]["id"])["state"] == "requested"


def test_demo_mode_keeps_the_trusted_workspace_refusal(tmp_path: Path) -> None:
    """Tolerating an unresolvable executable must not tolerate a missing
    ``trusted_workspace`` opt-in: that refusal survives demo mode intact."""

    manager, task = _workspace(tmp_path)
    delegation = _implementation_delegation(manager, task, target_profile="claude-builder")
    service = DelegationRuntimeService(
        manager,
        profiles=_unresolvable_profiles(trusted_workspace=False),
        runner=DemoRunner(manager.paths.state_root),  # type: ignore[arg-type]
    )

    with pytest.raises(ConfigurationError) as err:
        service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="demo-untrusted-launch",
        )

    assert getattr(err.value, "code", None) == "trusted_workspace_required"
    assert manager.get_delegation(delegation["entity_ref"]["id"])["state"] == "requested"


def test_demo_profiles_keep_every_non_resolution_build_refusal(tmp_path: Path) -> None:
    """The wrapped profiles run the wrapped ``build_invocation`` unchanged, so
    the delegation binding, reviewer purpose rules, and budget support are all
    still refused even though every executable here is unresolvable."""

    repo = tmp_path / "repo"
    repo.mkdir()
    profiles = demo_tolerant_profiles(_unresolvable_profiles())

    codex = profiles.get("codex-builder")
    with pytest.raises(ConfigurationError, match="exact delegation binding"):
        codex.build_invocation("do the work", workspace_root=repo)
    with pytest.raises(ConfigurationError, match="cannot enforce a monetary launch budget"):
        codex.build_invocation(
            "do the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
            max_budget_microusd=1_000,
        )

    reviewer = profiles.get("claude-independent-reviewer")
    with pytest.raises(ConfigurationError, match="review purpose"):
        reviewer.build_invocation(
            "review the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
            worker_purpose="implementation",
        )


def test_demo_relief_sits_below_config_validation(tmp_path: Path) -> None:
    """Reviewer launch modes and model names are validated while the config is
    parsed, before any runner exists; demo mode cannot reach past that."""

    with pytest.raises(ConfigurationError, match="read-only sandbox"):
        ProfileRegistry.from_mapping(
            {
                "profiles": {
                    "codex-independent-reviewer": {
                        "executable": _MISSING_CODEX,
                        "sandbox": "workspace-write",
                        "approval_policy": "never",
                        "trusted_workspace": True,
                    }
                }
            }
        )
    with pytest.raises(ValidationError, match="model"):
        ProfileRegistry.from_mapping(
            {
                "profiles": {
                    "claude-builder": {
                        "executable": _MISSING_CLAUDE,
                        "model": "-not-a-model",
                        "trusted_workspace": True,
                    }
                }
            }
        )


def test_model_replacement_keeps_a_demo_profile_demo_tolerant(tmp_path: Path) -> None:
    """The hire path selects a model with ``dataclasses.replace(profile,
    model=...)``.  A demo-tolerant profile must be a real dataclass so that
    call succeeds at all, and the replaced profile must come back still
    demo-tolerant -- otherwise model selection and demo mode are mutually
    exclusive, and the strictness returns silently only on the replaced
    copy."""

    repo = tmp_path / "repo"
    repo.mkdir()
    profiles = demo_tolerant_profiles(_unresolvable_profiles())

    replaced = dataclasses.replace(profiles.get("claude-builder"), model="claude-opus-4")
    assert replaced.model == "claude-opus-4"
    invocation = replaced.build_invocation(
        "do the work",
        workspace_root=repo,
        delegation_id="delegation-0001",
    )
    assert DEMO_UNRESOLVED_EXECUTABLE in " ".join(invocation.argv)

    # Replacing back to no model keeps working too: reconstruction happens
    # through the tolerant class, not the strict base.
    again = dataclasses.replace(replaced, model=None)
    assert type(again) is type(profiles.get("claude-builder"))


def test_every_non_resolution_refusal_survives_a_model_replacement(tmp_path: Path) -> None:
    """Demo tolerance forgives exactly ``ExecutableResolutionError``; after a
    ``dataclasses.replace`` the replaced profile still refuses everything else
    ``build_invocation`` refuses -- the delegation binding, the budget rule,
    the ``trusted_workspace`` opt-in, and config validation itself."""

    repo = tmp_path / "repo"
    repo.mkdir()
    profiles = demo_tolerant_profiles(_unresolvable_profiles())

    codex = dataclasses.replace(profiles.get("codex-builder"), model="gpt-5-codex")
    with pytest.raises(ConfigurationError, match="exact delegation binding"):
        codex.build_invocation("do the work", workspace_root=repo)
    with pytest.raises(ConfigurationError, match="cannot enforce a monetary launch budget"):
        codex.build_invocation(
            "do the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
            max_budget_microusd=1_000,
        )

    untrusted = dataclasses.replace(
        profiles.get("claude-builder"), model="claude-opus-4", trusted_workspace=False
    )
    with pytest.raises(ConfigurationError, match="trusted_workspace"):
        untrusted.build_invocation(
            "do the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
        )

    # __post_init__ re-runs on reconstruction, so an invalid replacement value
    # is refused at replace time exactly as the strict config parser would.
    with pytest.raises(ValidationError, match="model"):
        dataclasses.replace(profiles.get("claude-builder"), model="-not-a-model")


def test_placeholder_fills_only_the_unresolvable_leg(tmp_path: Path) -> None:
    """A wrapped profile still resolves what resolves; the inert placeholder
    stands in solely for the executables that do not, and the same profile
    outside the wrapper raises the same ``ExecutableResolutionError`` it
    always did."""

    repo = tmp_path / "repo"
    repo.mkdir()
    strict = ProfileRegistry.from_mapping(
        {
            "profiles": {
                "claude-builder": {
                    "executable": "/bin/echo",
                    "mcp_executable": _MISSING_MCP,
                    "git_executable": "/usr/bin/git",
                    "permission_mode": "acceptEdits",
                    "trusted_workspace": True,
                }
            }
        }
    )

    with pytest.raises(ExecutableResolutionError) as err:
        strict.get("claude-builder").build_invocation(
            "do the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
        )
    assert err.value.role is ExecutableRole.MCP

    invocation = (
        demo_tolerant_profiles(strict)
        .get("claude-builder")
        .build_invocation(
            "do the work",
            workspace_root=repo,
            delegation_id="delegation-0001",
        )
    )
    assert invocation.argv[0] == "/bin/echo"
    assert DEMO_UNRESOLVED_EXECUTABLE in " ".join(invocation.argv)
