from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agent_commons.cli import CLIState, _emit_broker_run_result, cli
from agent_commons.runtime import (
    AttemptSpec,
    AttemptState,
    AttemptStore,
    BuiltinProfileId,
    CorrelationIds,
    ProcessResult,
    Provider,
    RunOutcome,
    RunReason,
    RuntimePolicy,
    checkout_fingerprint,
)
from agent_commons.services import CommonsManager


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def test_human_broker_result_prints_exactly_one_child_state_notice_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notice = (
        "Child state root: passed the launching workspace root explicitly; ignored ambient "
        "AGENT_COMMONS_STATE_BASE and AGENT_COMMONS_STATE_ROOT."
    )
    state = CLIState(tmp_path, None, False, None, None, "default", False)

    _emit_broker_run_result(
        state,
        {"child_state_resolution": notice, "delegation": {"state": "succeeded"}},
    )

    output = capsys.readouterr().out
    assert output.splitlines()[0] == notice
    assert output.count(notice) == 1
    assert "child_state_resolution" not in output


def _requested_builder_delegation(
    tmp_path: Path, *, workspace_name: str
) -> tuple[Path, CommonsManager, dict[str, Any], dict[str, Any]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name=workspace_name)
    manager = CommonsManager(repo)
    parent = manager.start_session(
        stable_instance_id=f"{workspace_name}-parent-session",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    manager.session_id = str(parent["session_id"])
    task = manager.create_task(
        title="Build a small landing page",
        description="Exercise broker launch admission before provider work starts.",
        acceptance_criteria=("The landing page is recorded canonically.",),
        idempotency_key=f"{workspace_name}-task",
    )
    delegation = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=str(task["revision"]),
        target_profile="codex-builder",
        purpose="implementation",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 60,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "provider_units", "limit": 1},
        },
        idempotency_key=f"{workspace_name}-delegation",
    )
    return repo, manager, parent, delegation


def test_broker_run_rejects_a_foreign_requester_with_safe_recovery_guidance(
    tmp_path: Path,
) -> None:
    repo, manager, _parent, delegation = _requested_builder_delegation(
        tmp_path,
        workspace_name="broker-requester-recovery",
    )
    foreign = manager.start_session(
        stable_instance_id="broker-requester-recovery-foreign",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--session-id",
            str(foreign["session_id"]),
            "--json",
            "broker",
            "run",
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            "--idempotency-key",
            "foreign-requester-launch",
        ],
    )

    assert result.exit_code == 1
    body = json.loads(result.output)
    error = body["error"]
    assert error["code"] == "requester_session_required"
    assert "delegation:recover" in error["message"]
    assert "manually" in error["message"]
    actions = " ".join(error["safe_next_actions"])
    assert "Return to the active canonical requester session" in actions
    assert "operator-authorized delegation:recover" in actions
    assert "manually" in actions
    assert manager.get_delegation(str(delegation["entity_ref"]["id"]))["state"] == "requested"
    runtime = manager.paths.state_root / "runtime" / "requests"
    assert not runtime.exists() or list(runtime.glob("*.json")) == []


@pytest.mark.parametrize(
    ("profile_body", "diagnostic_code", "action_fragment"),
    (
        (
            "    executable: /bin/echo\n"
            "    mcp_executable: /bin/echo\n"
            "    git_executable: /usr/bin/git\n"
            "    sandbox: workspace-write\n"
            "    trusted_workspace: false\n",
            "trusted_workspace_required",
            "manual workflow",
        ),
        (
            "    executable: /definitely/missing-codex-for-test\n"
            "    mcp_executable: /bin/echo\n"
            "    git_executable: /usr/bin/git\n"
            "    sandbox: workspace-write\n"
            "    trusted_workspace: true\n",
            "provider_start_failed",
            "manual workflow",
        ),
        (
            "    executable: /bin/echo\n"
            "    mcp_executable: /bin/echo\n"
            "    git_executable: git-missing-for-broker-test\n"
            "    sandbox: workspace-write\n"
            "    trusted_workspace: true\n",
            "git_executable_unavailable",
            "manual workflow",
        ),
    ),
)
def test_broker_run_reports_prestart_profile_failures_without_consuming_an_attempt(
    tmp_path: Path,
    profile_body: str,
    diagnostic_code: str,
    action_fragment: str,
) -> None:
    repo, manager, parent, delegation = _requested_builder_delegation(
        tmp_path,
        workspace_name=f"broker-prestart-{diagnostic_code}",
    )
    config = tmp_path / "runtime.yaml"
    config.write_text(
        "profiles:\n  codex-builder:\n" + profile_body,
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--session-id",
            str(parent["session_id"]),
            "--json",
            "broker",
            "run",
            str(delegation["entity_ref"]["id"]),
            str(delegation["revision"]),
            "--idempotency-key",
            f"{diagnostic_code}-launch",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 1
    body = json.loads(result.output)
    error = body["error"]
    assert error["code"] == diagnostic_code
    actions = " ".join(error["safe_next_actions"])
    assert "preflight" in actions
    assert action_fragment in actions
    assert manager.get_delegation(str(delegation["entity_ref"]["id"]))["state"] == "requested"
    runtime = manager.paths.state_root / "runtime" / "requests"
    assert not runtime.exists() or list(runtime.glob("*.json")) == []


def test_broker_run_sanitizes_rejected_operator_values_in_json_and_human_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, manager, parent, delegation = _requested_builder_delegation(
        tmp_path,
        workspace_name="broker-sanitized-config",
    )
    rejected_value = "git-SUPERSECRET-operator-path-value"
    config = tmp_path / "runtime-secret.yaml"
    config.write_text(
        "profiles:\n"
        "  codex-builder:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        f"    git_executable: {rejected_value}\n"
        "    sandbox: workspace-write\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    base_arguments = [
        "--repo",
        str(repo),
        "--session-id",
        str(parent["session_id"]),
    ]
    run_arguments = [
        "broker",
        "run",
        str(delegation["entity_ref"]["id"]),
        str(delegation["revision"]),
        "--idempotency-key",
        "sanitized-config-launch",
        "--profile-config",
        str(config),
    ]

    json_result = CliRunner().invoke(cli, [*base_arguments, "--json", *run_arguments])
    human_result = CliRunner().invoke(cli, [*base_arguments, *run_arguments])

    assert json_result.exit_code == 1
    json_error = json.loads(json_result.output)["error"]
    assert json_error["code"] == "git_executable_unavailable"
    assert "configured Git executable is unavailable" in json_error["message"]
    assert rejected_value not in json_result.output
    assert human_result.exit_code == 1
    assert "configured Git executable is unavailable" in human_result.output
    assert rejected_value not in human_result.output
    assert manager.get_delegation(str(delegation["entity_ref"]["id"]))["state"] == "requested"
    runtime = manager.paths.state_root / "runtime" / "requests"
    assert not runtime.exists() or list(runtime.glob("*.json")) == []


def test_delegation_show_includes_the_sanitized_failed_attempt_diagnostic(
    tmp_path: Path,
) -> None:
    repo, manager, parent, delegation = _requested_builder_delegation(
        tmp_path,
        workspace_name="delegation-show-stderr",
    )
    delegation_id = str(delegation["entity_ref"]["id"])
    projected = manager.get_delegation(delegation_id)
    policy = RuntimePolicy(
        remaining_depth=1,
        max_fanout=1,
        max_attempts=1,
        max_concurrency=1,
    )
    child = policy.derive_child()
    store = AttemptStore(manager.paths.state_root)
    reserved = store.reserve(
        AttemptSpec(
            idempotency_key="delegation-show-stderr-attempt",
            profile_id=BuiltinProfileId.CODEX_BUILDER,
            provider=Provider.CODEX,
            correlation=CorrelationIds(
                delegation_id=delegation_id,
                target_kind="task",
                target_id=str(projected["target_ref"]["id"]),
                target_revision=str(projected["target_revision"]),
                parent_session_id=str(parent["session_id"]),
                child_session_id="session.childdelegationshow000000000001",
            ),
            parent_policy=policy,
            child_policy=child,
            checkout_fingerprint=checkout_fingerprint(repo),
        ),
        parent_policy=policy,
    ).attempt
    store.transition(reserved.attempt_id, AttemptState.LAUNCHING, reason="process_starting")
    store.transition(
        reserved.attempt_id,
        AttemptState.RUNNING,
        reason="process_started",
        pid=321,
    )
    store.finish(
        reserved.attempt_id,
        ProcessResult(
            outcome=RunOutcome.FAILED,
            reason=RunReason.NONZERO_EXIT,
            exit_code=1,
            pid=321,
            duration_seconds=0.1,
            stdout=b"",
            stderr=b"ERROR required MCP server failed to initialize",
            stdout_bytes_seen=0,
            stderr_bytes_seen=46,
            output_truncated=False,
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--session-id",
            str(parent["session_id"]),
            "--json",
            "delegation",
            "show",
            delegation_id,
        ],
    )

    assert result.exit_code == 0, result.output
    attempt = json.loads(result.output)["runtime_attempts"][0]
    assert attempt["stderr_diagnostic_tail"] == ("ERROR required MCP server failed to initialize")
    assert attempt["stderr_diagnostic_tail_truncated"] is False
    assert attempt["workflow_diagnostic_code"] == "provider_nonzero_unknown"


def test_broker_cli_is_discoverable_bounded_and_feature_configurable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-cli")
    runner = CliRunner()

    help_result = runner.invoke(cli, ["broker", "--help"])
    assert help_result.exit_code == 0
    for command in ("profiles", "preflight", "canary", "attempts", "run", "reconcile"):
        assert command in help_result.output

    run_help = runner.invoke(cli, ["broker", "run", "--help"])
    assert run_help.exit_code == 0
    assert "--idempotency-key" in run_help.output
    assert "--retry" in run_help.output
    assert "--context-pack-id" in run_help.output
    assert "--context-pack-revision" in run_help.output
    # One catalogue path shared with the panel: the launcher names the same file
    # the UI edits, with the same flag (M8).
    assert "--role-catalog" in run_help.output
    for forbidden in ("--command", "--prompt", "--environment", "--executable"):
        assert forbidden not in run_help.output
    incomplete_context = runner.invoke(
        cli,
        [
            "--repo",
            str(repo),
            "broker",
            "run",
            "delegation.01K00000000000000000000000",
            "evt.01K00000000000000000000000",
            "--idempotency-key",
            "bounded-context-selection",
            "--context-pack-id",
            "context_pack.01K00000000000000000000000",
        ],
    )
    assert incomplete_context.exit_code == 2
    assert "must be supplied together" in incomplete_context.output

    canary_help = runner.invoke(cli, ["broker", "canary", "--help"])
    assert canary_help.exit_code == 0
    assert "--confirm-provider-run" in canary_help.output
    assert "--profile" in canary_help.output
    assert "--wall-time-seconds" in canary_help.output
    for forbidden in ("--command", "--prompt", "--environment", "--executable", "--model"):
        assert forbidden not in canary_help.output
    unconfirmed_canary = runner.invoke(cli, ["broker", "canary"])
    assert unconfirmed_canary.exit_code == 2
    assert "--confirm-provider-run" in unconfirmed_canary.output

    profiles = runner.invoke(
        cli,
        ["--repo", str(repo), "--json", "broker", "profiles"],
    )
    assert profiles.exit_code == 0, profiles.output
    values = json.loads(profiles.output)
    assert {item["profile_id"] for item in values} == {
        "codex-builder",
        "codex-independent-reviewer",
        "claude-builder",
        "claude-independent-reviewer",
        "grok-builder",
        "grok-independent-reviewer",
    }
    assert all("executable" not in item and "argv" not in item for item in values)
    assert not (CommonsManager(repo).paths.state_root / "runtime").exists()

    attempts = runner.invoke(
        cli,
        ["--repo", str(repo), "--json", "broker", "attempts"],
    )
    assert attempts.exit_code == 0, attempts.output
    assert json.loads(attempts.output) == []


def test_broker_preflight_exits_nonzero_for_an_incompatible_runtime(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-preflight-failure")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /usr/bin/false\n"
        "    mcp_executable: /usr/bin/false\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "claude-independent-reviewer",
            "--purpose",
            "independent_review",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["consumed_delegation_attempt"] is False


def test_broker_preflight_validates_the_generated_codex_mcp_contract(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-codex-preflight")
    provider_source = (
        Path(__file__).parents[1] / "fixtures" / "fake_codex_mcp_provider.py"
    ).read_text(encoding="utf-8")
    provider = _executable(tmp_path / "fake-codex", provider_source)
    mcp = _executable(
        tmp_path / "agent-commons-mcp",
        "from agent_commons.mcp.server import main\nraise SystemExit(main())\n",
    )
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  codex-builder:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        "    git_executable: /usr/bin/git\n"
        "    sandbox: workspace-write\n"
        "    trusted_workspace: true\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "codex-builder",
            "--purpose",
            "implementation",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["checks"]["mcp_contract"]["ok"] is True
    assert body["checks"]["mcp_handshake"]["ok"] is True
    assert body["provider_work_process_started"] is False


def test_broker_preflight_passes_the_workspace_bound_state_base_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    initialized = CommonsManager.initialize(
        repo,
        integrations=(),
        workspace_name="broker-state-base",
    )
    state_base = tmp_path / "operator-state"
    captured: dict[str, object] = {}

    def capture_preflight(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"ok": True, "consumed_delegation_attempt": False}

    monkeypatch.setattr("agent_commons.cli.preflight_profile", capture_preflight)

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--state-base",
            str(state_base),
            "--json",
            "broker",
            "preflight",
            "claude-independent-reviewer",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["state_root"] == (state_base / "workspaces" / initialized["workspace_id"])
    assert not state_base.exists()


def test_broker_preflight_reports_a_missing_mcp_executable_precisely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-missing-mcp")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: agent-commons-mcp-missing-for-test\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "preflight",
            "claude-independent-reviewer",
            "--purpose",
            "independent_review",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["checks"]["mcp_executable"]["diagnostic_code"] == ("mcp_executable_unavailable")
    assert body["provider_help_process_started"] is False
    assert body["consumed_delegation_attempt"] is False


def test_broker_canary_emits_its_safe_failure_before_status_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-canary-failure")
    monkeypatch.setattr(
        "agent_commons.cli.run_claude_compatibility_canary",
        lambda *_args, **_kwargs: {
            "schema": "agent_commons.provider_compatibility_canary.v1",
            "ok": False,
            "workflow_diagnostic_code": "terminal_tool_not_called",
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "canary",
            "--confirm-provider-run",
        ],
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["workflow_diagnostic_code"] == "terminal_tool_not_called"


def test_broker_canary_selects_the_codex_compatibility_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-codex-canary")
    monkeypatch.setattr(
        "agent_commons.cli.run_claude_compatibility_canary",
        lambda *_args, **_kwargs: pytest.fail("Claude canary should not run"),
    )
    monkeypatch.setattr(
        "agent_commons.cli.run_codex_compatibility_canary",
        lambda *_args, **_kwargs: {
            "schema": "agent_commons.provider_compatibility_canary.v1",
            "ok": True,
            "profile_id": "codex-independent-reviewer",
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "canary",
            "--profile",
            "codex-independent-reviewer",
            "--confirm-provider-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile_id"] == "codex-independent-reviewer"


def test_broker_canary_selects_the_grok_compatibility_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-grok-canary")
    monkeypatch.setattr(
        "agent_commons.cli.run_claude_compatibility_canary",
        lambda *_args, **_kwargs: pytest.fail("Claude canary should not run"),
    )
    monkeypatch.setattr(
        "agent_commons.cli.run_codex_compatibility_canary",
        lambda *_args, **_kwargs: pytest.fail("Codex canary should not run"),
    )
    monkeypatch.setattr(
        "agent_commons.cli.run_grok_compatibility_canary",
        lambda *_args, **_kwargs: {
            "schema": "agent_commons.provider_compatibility_canary.v1",
            "ok": True,
            "profile_id": "grok-independent-reviewer",
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "canary",
            "--profile",
            "grok-independent-reviewer",
            "--confirm-provider-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile_id"] == "grok-independent-reviewer"


def test_broker_profile_config_rejects_unknown_authority_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-config")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: claude\n"
        "    arbitrary_environment:\n"
        "      SECRET: value\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["error"]["type"] == "ConfigurationError"
    assert error["error"]["safe_next_actions"]
    assert "unsupported fields" in error["error"]["message"]
    assert "SECRET" not in result.output


def test_broker_profile_config_keeps_effective_operator_caps_private(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="broker-limits")
    config = tmp_path / "runtime.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /usr/bin/false\n"
        "    mcp_executable: /usr/bin/false\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n"
        "limits:\n"
        "  global_concurrency: 1\n"
        "  queue_capacity: 2\n"
        "  provider_concurrency:\n"
        "    claude: 1\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    availability = json.loads(result.output)[0]
    assert "operator_limits" not in availability
    rendered = json.dumps(availability, sort_keys=True)
    for forbidden in (
        "global_concurrency",
        "provider_concurrency",
        "profile_concurrency",
        "parent_provider_units",
        "queue_capacity",
        "queue_wait_seconds",
    ):
        assert forbidden not in rendered

    link = tmp_path / "profiles-link.yaml"
    link.symlink_to(config)
    symlinked = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(link),
        ],
    )
    assert symlinked.exit_code == 1
    assert json.loads(symlinked.output)["error"]["type"] == "ConfigurationError"

    writable = tmp_path / "profiles-writable.yaml"
    writable.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    writable.chmod(0o666)
    unsafe_mode = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(writable),
        ],
    )
    assert unsafe_mode.exit_code == 1
    assert "group/world writable" in json.loads(unsafe_mode.output)["error"]["message"]

    workspace_config = repo / "profiles.yaml"
    workspace_config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: /bin/echo\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )
    inside_workspace = CliRunner().invoke(
        cli,
        [
            "--repo",
            str(repo),
            "--json",
            "broker",
            "profiles",
            "--profile-config",
            str(workspace_config),
        ],
    )
    assert inside_workspace.exit_code == 1
    assert (
        "outside the delegated workspace" in json.loads(inside_workspace.output)["error"]["message"]
    )
