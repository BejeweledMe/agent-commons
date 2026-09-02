"""Explicit paid-provider compatibility canary for the experimental broker."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agent_commons import __version__
from agent_commons.catalog import empty_catalog
from agent_commons.errors import ConfigurationError
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudeRunnerProfile,
    CodexRunnerProfile,
    ExecutableRole,
    GrokRunnerProfile,
    OperatorLimits,
    ProfileRegistry,
    Provider,
    ProviderInitializationProbe,
    ProviderInitializationStatus,
    ProviderQualificationStore,
    RunnerInvocation,
    RunOutcome,
    SubprocessRunner,
    preflight_profile,
    resolve_trusted_executable,
)
from agent_commons.runtime.model import fixed_profile_environment
from agent_commons.runtime.source_contract import agent_commons_source_sha256

from .delegation_runtime import DelegationRuntimeService
from .manager import CommonsManager

CANARY_SCHEMA = "agent_commons.provider_compatibility_canary.v1"
DEFAULT_CANARY_SKILL_REFS = ("commons-start",)
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
_GROK_BUILD_VERSION = re.compile(
    rf"^grok (?P<major>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<minor>{_NUMERIC_VERSION_COMPONENT})"
    rf"\.(?P<patch>{_NUMERIC_VERSION_COMPONENT})"
    rf"(?: \([^\r\n]+\)(?: \[[^\r\n]+\])?)?$"
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
    profile: ClaudeRunnerProfile | CodexRunnerProfile | GrokRunnerProfile,
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
            extra_env=fixed_profile_environment(profile),
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
    if profile.provider is Provider.CLAUDE:
        pattern = _CLAUDE_CODE_VERSION
    elif profile.provider is Provider.CODEX:
        pattern = _CODEX_CLI_VERSION
    elif profile.provider is Provider.GROK:
        pattern = _GROK_BUILD_VERSION
    else:  # pragma: no cover - Provider is an exhaustive allowlist
        raise ConfigurationError("provider canary has no version parser")
    match = pattern.fullmatch(first_line)
    if match is None:
        return None
    version = f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
    if profile.provider is Provider.CLAUDE:
        return f"{version} (Claude Code)"
    if profile.provider is Provider.CODEX:
        return f"codex-cli {version}"
    if profile.provider is Provider.GROK:
        return f"grok {version}"
    raise ConfigurationError("provider canary has no version renderer")  # pragma: no cover


def _run_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    profile_id: BuiltinProfileId,
    purpose: str = "independent_review",
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
    requester_client: str = "agent-commons",
) -> dict[str, Any]:
    """Run one fixed, isolated provider flow and grade canonical terminal behavior."""

    if not 30 <= wall_time_seconds <= 1800:
        raise ConfigurationError("provider canary wall time must be between 30 and 1800 seconds")
    if purpose not in {"implementation", "independent_review", "verification"}:
        raise ConfigurationError("provider canary purpose is unsupported")
    if any(not isinstance(item, str) or not item for item in skill_refs):
        raise ConfigurationError("provider canary skill_refs must be non-empty strings")
    profile = profiles.get(profile_id)
    if not isinstance(profile, (ClaudeRunnerProfile, CodexRunnerProfile, GrokRunnerProfile)):
        raise ConfigurationError("provider canary requires an allowlisted provider profile")
    if profile_id.independent_reviewer != (purpose != "implementation"):
        raise ConfigurationError("provider canary purpose does not match its profile")
    if requester_client not in {"agent-commons", "claude", "codex", "grok"}:
        raise ConfigurationError("provider canary requester client is unsupported")

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
        CommonsManager.initialize(
            repo,
            integrations=("grok",) if profile.provider is Provider.GROK else (),
            workspace_name="provider-canary",
        )

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
            purpose=purpose,
            runner=process_runner,
        )
        base_report: dict[str, Any] = {
            "schema": CANARY_SCHEMA,
            "agent_commons_version": __version__,
            "agent_commons_source_sha256": agent_commons_source_sha256(),
            "provider": profile.provider.value,
            "profile_id": profile_id.value,
            "purpose": purpose,
            "model": profile.model,
            "provider_version": provider_version,
            "requester_client": requester_client,
            "skill_refs": list(skill_refs),
            "preflight": preflight,
        }
        if not preflight["ok"]:
            report = {
                **base_report,
                "ok": False,
                "provider_work_process_started": False,
                "workflow_diagnostic_code": "preflight_failed",
            }
            if qualification_state_root is not None:
                ProviderQualificationStore(qualification_state_root).record(
                    profile,
                    workspace_root=repo,
                    static_preflight=False,
                    initialization_probe=False,
                    behavioral_canary=False,
                    provider_version=provider_version,
                )
            return report

        initialization = ProviderInitializationProbe(runner=process_runner).probe(
            profile,
            workspace_root=repo,
        )
        base_report["initialization"] = {
            "state": initialization.state.value,
            "supported": initialization.supported,
            "blocks_launch": initialization.blocks_launch,
        }
        if initialization.blocks_launch:
            report = {
                **base_report,
                "ok": False,
                "provider_work_process_started": False,
                "workflow_diagnostic_code": "provider_initialization_failed",
            }
            if qualification_state_root is not None:
                ProviderQualificationStore(qualification_state_root).record(
                    profile,
                    workspace_root=repo,
                    static_preflight=True,
                    initialization_probe=False,
                    behavioral_canary=False,
                    provider_version=provider_version,
                )
            return report

        manager = CommonsManager(repo, state_root=state_root)
        parent = manager.start_session(
            stable_instance_id=f"provider-canary-{requester_client}-parent-session",
            principal="local-operator",
            client=requester_client,
            software=(
                "claude-code"
                if requester_client == "claude"
                else "grok-build"
                if requester_client == "grok"
                else "provider-canary"
            ),
            role="compatibility-canary",
            model_family=("anthropic" if requester_client == "claude" else None),
            ttl_seconds=wall_time_seconds + 300,
        )
        manager.session_id = parent["session_id"]
        task = manager.create_task(
            title="Run the fixed provider compatibility canary",
            description=(
                "Inspect src/canary.py through the scoped worker tools and record the "
                "purpose-specific bounded terminal result."
            ),
            acceptance_criteria=("The source defines answer() and returns the integer 42.",),
            priority="normal",
            idempotency_key="provider-canary-task",
        )
        artifact = manager.register_artifact(
            source,
            media_type="text/x-python",
            classification="internal",
            idempotency_key="provider-canary-artifact",
        )
        task = manager.start_task(
            task["entity_ref"]["id"],
            task["revision"],
            idempotency_key="provider-canary-task-start",
        )
        if purpose != "implementation":
            task = manager.complete_task(
                task["entity_ref"]["id"],
                task["revision"],
                summary="The fixed canary source is registered as immutable evidence.",
                artifact_refs=(artifact["entity_ref"],),
                idempotency_key="provider-canary-task-complete",
            )
        review = (
            manager.request_review(
                target_ref=task["entity_ref"],
                target_revision=task["revision"],
                criteria=("Inspect the exact scoped source and record a bounded verdict.",),
                independent=True,
                idempotency_key="provider-canary-review",
            )
            if purpose == "independent_review"
            else None
        )
        role = (
            manager.create_agent(
                name=f"Provider canary {profile_id.value}",
                profile_id=profile_id.value,
                rationale="Bind packaged skill projection to the compatibility canary.",
                skills=skill_refs,
                idempotency_key="provider-canary-skilled-role",
            )
            if skill_refs
            else None
        )
        target = review if review is not None else task
        delegation = manager.create_delegation(
            target_ref=target["entity_ref"],
            target_revision=target["revision"],
            target_profile=profile_id.value,
            purpose=purpose,
            limits={
                "max_depth": 0,
                "wall_time_seconds": wall_time_seconds,
                "max_attempts": 1,
                "max_concurrency": 1,
                "budget": {"unit": "provider_units", "limit": 1},
            },
            on_behalf_of_agent_id=(str(role["entity_ref"]["id"]) if role is not None else None),
            idempotency_key="provider-canary-delegation",
        )
        service = DelegationRuntimeService(
            manager,
            profiles=profiles,
            operator_limits=operator_limits,
            runner=process_runner,
            initialization_probe=_FixedInitialization(initialization),
            qualification_required=False,
            catalog=_skill_catalog(skill_refs),
        )
        try:
            result = service.run(
                delegation["entity_ref"]["id"],
                delegation["revision"],
                idempotency_key="provider-canary-launch",
            )
        except ConfigurationError as exc:
            auth_state = getattr(exc, "provider_auth_state", None)
            if auth_state is None:
                raise
            attempts = service.attempts.list_attempts()
            sessions = manager.sessions.list_sessions()
            current = manager.get_delegation(delegation["entity_ref"]["id"])
            manager.cancel_delegation(
                current["id"],
                current["revision"],
                reason="Isolated provider canary cleanup after an auth refusal.",
                idempotency_key="provider-canary-auth-cleanup",
            )
            manager.end_session(nonce=parent["nonce"])
            report = {
                **base_report,
                "ok": False,
                "provider_work_process_started": False,
                "provider_auth_state": auth_state,
                "workflow_diagnostic_code": getattr(
                    exc,
                    "code",
                    "provider_auth_unknown",
                ),
                "canonical_state_before_cleanup": current["state"],
                "attempt_reserved": bool(attempts),
                "child_session_created": len(sessions) != 1,
            }
            if qualification_state_root is not None:
                ProviderQualificationStore(qualification_state_root).record(
                    profile,
                    workspace_root=repo,
                    static_preflight=True,
                    initialization_probe=True,
                    behavioral_canary=False,
                    provider_version=provider_version,
                )
            return report
        joined = next(
            item
            for item in service.list_attempts(diagnostic=True)
            if item["correlation"]["delegation_id"] == delegation["entity_ref"]["id"]
        )
        canonical = result["delegation"]
        child_session = manager.show_session(result["attempt"]["correlation"]["child_session_id"])
        if review is not None:
            expected_result_refs = [review["entity_ref"]]
        elif purpose == "implementation":
            expected_result_refs = [task["entity_ref"]]
        else:
            verifications = [
                item
                for item in manager.list_verifications()
                if item.get("target_ref") == task["entity_ref"]
                and item.get("target_revision") == task["revision"]
                and (item.get("actor") or {}).get("session_id")
                == result["attempt"]["correlation"]["child_session_id"]
            ]
            expected_result_refs = (
                [
                    {
                        "kind": "verification",
                        "id": str(verifications[0]["id"]),
                    }
                ]
                if len(verifications) == 1
                else []
            )
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
        report = {
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
            "terminal_tool_rejection_details": joined["terminal_tool_rejection_details"],
            "terminal_tool_rejection_details_truncated": joined[
                "terminal_tool_rejection_details_truncated"
            ],
            "child_session_closed": child_session["effective_status"] == "closed",
        }
        if qualification_state_root is not None:
            ProviderQualificationStore(qualification_state_root).record(
                profile,
                workspace_root=repo,
                static_preflight=True,
                initialization_probe=True,
                behavioral_canary=ok,
                provider_version=provider_version,
            )
        return report


class _FixedInitialization:
    """Reuse the separately reported probe instead of running it twice."""

    def __init__(self, status: ProviderInitializationStatus) -> None:
        self.status = status

    def probe(self, profile: Any, *, workspace_root: Path) -> ProviderInitializationStatus:
        del profile, workspace_root
        return self.status


def _skill_catalog(skill_refs: tuple[str, ...]) -> dict[str, list[dict[str, str]]]:
    catalog = empty_catalog()
    catalog["skills"] = [
        {
            "id": skill_ref,
            "title": skill_ref,
            "description": "Provider canary packaged skill projection.",
            "instruction": f"Use packaged skill {skill_ref}.",
        }
        for skill_ref in skill_refs
    ]
    return catalog


def run_claude_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    purpose: str = "independent_review",
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated Claude review and grade canonical terminal behavior."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        purpose=purpose,
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
    )


def run_codex_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    purpose: str = "independent_review",
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated Codex review and grade canonical terminal behavior."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER,
        purpose=purpose,
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
    )


def run_grok_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    purpose: str = "independent_review",
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
) -> dict[str, Any]:
    """Run one fixed, isolated Grok review and grade canonical terminal behavior."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.GROK_INDEPENDENT_REVIEWER,
        purpose=purpose,
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
    )


def run_claude_builder_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
) -> dict[str, Any]:
    """Run the fixed scoped terminal flow for the advertised Claude builder."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CLAUDE_BUILDER,
        purpose="implementation",
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
    )


def run_codex_builder_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
    requester_client: str = "agent-commons",
) -> dict[str, Any]:
    """Run the fixed scoped terminal flow for the advertised Codex builder."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.CODEX_BUILDER,
        purpose="implementation",
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
        requester_client=requester_client,
    )


def run_grok_builder_compatibility_canary(
    profiles: ProfileRegistry,
    *,
    skill_refs: tuple[str, ...] = DEFAULT_CANARY_SKILL_REFS,
    operator_limits: OperatorLimits | None = None,
    wall_time_seconds: int = 300,
    runner: SubprocessRunner | None = None,
    qualification_state_root: Path | None = None,
    requester_client: str = "agent-commons",
) -> dict[str, Any]:
    """Run the fixed scoped terminal flow for the advertised Grok builder."""

    return _run_compatibility_canary(
        profiles,
        profile_id=BuiltinProfileId.GROK_BUILDER,
        purpose="implementation",
        skill_refs=skill_refs,
        operator_limits=operator_limits,
        wall_time_seconds=wall_time_seconds,
        runner=runner,
        qualification_state_root=qualification_state_root,
        requester_client=requester_client,
    )
