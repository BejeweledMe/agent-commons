"""Explicit paid-provider compatibility canary for the experimental broker."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agent_commons import __version__
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    ExecutableRole,
    OperatorLimits,
    ProfileRegistry,
    Provider,
    RunnerInvocation,
    RunOutcome,
    SubprocessRunner,
    preflight_profile,
    resolve_trusted_executable,
)
from agent_commons.runtime.source_contract import agent_commons_source_sha256

from .delegation_runtime import DelegationRuntimeService
from .manager import CommonsManager

CANARY_SCHEMA = "agent_commons.provider_compatibility_canary.v1"
_NUMERIC_VERSION_COMPONENT = r"(?:0|[1-9][0-9]{0,5})"
_CLAUDE_CODE_VERSION = re.compile(
    rf"^(?P<major>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<minor>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<patch>{_NUMERIC_VERSION_COMPONENT}) \(Claude Code\)$"
)
_CODEX_CLI_VERSION = re.compile(
    rf"^codex-cli (?P<major>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<minor>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<patch>{_NUMERIC_VERSION_COMPONENT})$"
)


def _run_git(git_executable: str, *args: str, cwd: Path | None = None) -> None:
    try:
        subprocess.run(
            (git_executable, *args),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigurationError(
            "provider canary could not prepare its isolated Git fixture"
        ) from exc


def _provider_version(
    profile: ClaudeRunnerProfile | CodexRunnerProfile,
    *,
    workspace_root: Path,
    runner: SubprocessRunner,
) -> str | None:
    executable = resolve_trusted_executable(
        profile.executable,
        workspace_root=workspace_root,
        role=ExecutableRole.PROVIDER,
    )
    result = runner.run(
        RunnerInvocation(
            provider=profile.provider,
            profile_id=profile.profile_id,
            argv=(executable, "--version"),
            stdin=b"",
        ),
        cwd=workspace_root,
        child_session_id="session.provider-canary-version",
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    if result.outcome is not RunOutcome.SUCCEEDED:
        return None
    value = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace").strip()
    first_line = value.splitlines()[0].strip() if value else ""
    pattern = _CLAUDE_CODE_VERSION if profile.provider is Provider.CLAUDE else _CODEX_CLI_VERSION
    match = pattern.fullmatch(first_line)
    if match is None:
        return None
    version = f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
    return (
        f"{version} (Claude Code)"
        if profile.provider is Provider.CLAUDE
        else f"codex-cli {version}"
    )


def _run_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    profile_id: BuiltinProfileId,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated provider review and grade canonical terminal behavior."""

    if not 30 <= wall_time_seconds <= 1800:
        raise ConfigurationError("provider canary wall time must be between 30 and 1800 seconds")
    profile = profiles.get(profile_id)
    if not isinstance(profile, (ClaudeRunnerProfile, CodexRunnerProfile)) or (
        not profile_id.independent_reviewer
    ):
        raise ConfigurationError("provider canary requires an independent-review profile")

    process_runner = runner or SubprocessRunner()
    with tempfile.TemporaryDirectory(prefix="agent-commons-provider-canary-") as temporary:
        fixture_root = Path(temporary)
        repo = fixture_root / "workspace"
        state_root = fixture_root / "state"
        repo.mkdir(mode=0o700)

        git_executable = resolve_trusted_executable(
            profile.git_executable,
            workspace_root=repo,
            role=ExecutableRole.GIT,
        )
        _run_git(git_executable, "init", "-q", str(repo))
        source = repo / "src" / "canary.py"
        source.parent.mkdir()
        source.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            ".agent-commons/events/\n.agent-commons/manifests/\n.agent-commons/blobs/\n",
            encoding="utf-8",
        )
        _run_git(git_executable, "add", ".gitignore", "src/canary.py", cwd=repo)
        CommonsManager.initialize(repo, integrations=(), workspace_name="provider-canary")

        provider_version = _provider_version(
            profile,
            workspace_root=repo,
            runner=process_runner,
        )
        preflight = preflight_profile(
            profiles,
            profile_id,
            workspace_root=repo,
            state_root=state_root,
            purpose="independent_review",
            runner=process_runner,
        )
        base_report: dict[str, Any] = {
            "schema": CANARY_SCHEMA,
            "agent_commons_version": __version__,
            "agent_commons_source_sha256": agent_commons_source_sha256(),
            "provider": Provider.CLAUDE.value,
            "profile_id": profile_id.value,
            "model": profile.model,
            "provider_version": provider_version,
            "preflight": preflight,
        }
        if not preflight["ok"]:
            return {
                **base_report,
                "ok": False,
                "provider_work_process_started": False,
                "workflow_diagnostic_code": "preflight_failed",
            }

        manager = CommonsManager(repo, state_root=state_root)
        parent = manager.start_session(
            stable_instance_id="provider-canary-parent-session",
            principal="local-operator",
            client="agent-commons",
            software="provider-canary",
            role="compatibility-canary",
            ttl_seconds=wall_time_seconds + 300,
        )
        manager.session_id = parent["session_id"]
        task = manager.create_task(
            title="Review the fixed provider compatibility canary",
            description=(
                "Inspect src/canary.py through the scoped worker tools and record a bounded "
                "independent review."
            ),
            acceptance_criteria=("The source defines answer() and returns the integer 42.",),
            priority="normal",
            idempotency_key="provider-canary-task",
        )
        review = manager.request_review(
            target_ref=task["entity_ref"],
            target_revision=task["revision"],
            criteria=("Inspect the exact scoped source and record a bounded verdict.",),
            independent=True,
            idempotency_key="provider-canary-review",
        )
        delegation = manager.create_delegation(
            target_ref=review["entity_ref"],
            target_revision=review["revision"],
            target_profile=profile_id.value,
            purpose="independent_review",
            limits={
                "max_depth": 0,
                "wall_time_seconds": wall_time_seconds,
                "max_attempts": 1,
                "max_concurrency": 1,
                "budget": {"unit": "provider_units", "limit": 1},
            },
            idempotency_key="provider-canary-delegation",
        )
        service = DelegationRuntimeService(
            manager,
            profiles=profiles,
            operator_limits=operator_limits,
            runner=process_runner,
        )
        result = service.run(
            delegation["entity_ref"]["id"],
            delegation["revision"],
            idempotency_key="provider-canary-launch",
        )
        joined = next(
            item
            for item in service.list_attempts(diagnostic=True)
            if item["correlation"]["delegation_id"] == delegation["entity_ref"]["id"]
        )
        canonical = result["delegation"]
        child_session = manager.show_session(result["attempt"]["correlation"]["child_session_id"])
        expected_result_refs = [review["entity_ref"]]
        ok = (
            result["process"]["outcome"] == "succeeded"
            and canonical["state"] == "succeeded"
            and canonical.get("result_refs") == expected_result_refs
            and joined["canonical_state"] == "succeeded"
            and joined["workflow_diagnostic_code"] == "none"
            and joined["process_canonical_mismatch"] is False
            and joined["terminal_tool_calls"] == 1
            and joined["terminal_tool_completions"] == 1
            and joined["terminal_tool_rejections"] == 0
            and child_session["effective_status"] == "closed"
        )
        if canonical["state"] == "requested":
            manager.cancel_delegation(
                canonical["id"],
                canonical["revision"],
                reason="Isolated provider canary cleanup after a pre-start failure.",
                idempotency_key="provider-canary-cleanup",
            )
        manager.end_session(nonce=parent["nonce"])

        process = result["process"]
        return {
            **base_report,
            "ok": ok,
            "provider_work_process_started": True,
            "process": {
                "outcome": process["outcome"],
                "exit_code": process["exit_code"],
                "duration_seconds": process["duration_seconds"],
                "stdout_bytes_seen": process["stdout_bytes_seen"],
                "stderr_bytes_seen": process["stderr_bytes_seen"],
                "output_truncated": process["output_truncated"],
            },
            "canonical_state": canonical["state"],
            "workflow_diagnostic_code": joined["workflow_diagnostic_code"],
            "process_canonical_mismatch": joined["process_canonical_mismatch"],
            "terminal_tool_calls": joined["terminal_tool_calls"],
            "terminal_tool_completions": joined["terminal_tool_completions"],
            "terminal_tool_rejections": joined["terminal_tool_rejections"],
            "child_session_closed": child_session["effective_status"] == "closed",
        }


def run_claude_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated Claude review and grade canonical terminal behavior."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
    )


def run_codex_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated Codex review and grade canonical terminal behavior."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
    )
