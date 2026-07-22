from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner, Result

from agent_commons.cli import cli
from agent_commons.services import CommonsManager

LIMITS = {
    "max_depth": 1,
    "wall_time_seconds": 600,
    "max_attempts": 1,
    "max_concurrency": 1,
    "budget": {"unit": "tokens", "limit": 8000},
}


def _invoke(runner: CliRunner, repo: Path, session_id: str, *args: str) -> Result:
    return runner.invoke(
        cli,
        ["--repo", str(repo), "--session-id", session_id, "--json", *args],
    )


def _json(result: Result) -> dict | list:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _workspace(tmp_path: Path) -> tuple[Path, CommonsManager, dict, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name="delegation-cli")
    manager = CommonsManager(repo)
    parent = manager.start_session(
        stable_instance_id="delegation-cli-parent-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    manager.session_id = parent["session_id"]
    child_manager = CommonsManager(repo)
    child = child_manager.start_session(
        stable_instance_id="delegation-cli-child-12345678",
        principal="operator",
        client="claude",
        software="claude-code",
        role="independent-reviewer",
    )
    return repo, manager, parent, child


def _task(manager: CommonsManager, key: str) -> dict:
    return manager.create_task(
        title="Delegation CLI target",
        description="Exercise the public command contract.",
        acceptance_criteria=("commands are machine-readable",),
        idempotency_key=key,
    )


def test_cli_delegation_create_show_list_start_and_succeed(tmp_path: Path) -> None:
    repo, manager, parent, child = _workspace(tmp_path)
    runner = CliRunner()
    task = _task(manager, "cli-delegation-target")
    task_id = task["entity_ref"]["id"]

    created = _json(
        _invoke(
            runner,
            repo,
            parent["session_id"],
            "delegation",
            "create",
            "--target-ref",
            f"task:{task_id}",
            "--target-revision",
            task["revision"],
            "--target-profile",
            "claude-builder",
            "--purpose",
            "implementation",
            "--limits-json",
            json.dumps(LIMITS),
            "--idempotency-key",
            "cli-delegation-create",
        )
    )
    assert isinstance(created, dict)
    delegation_id = created["entity_ref"]["id"]

    shown = _json(
        _invoke(
            runner,
            repo,
            parent["session_id"],
            "delegation",
            "show",
            delegation_id,
        )
    )
    assert isinstance(shown, dict)
    assert shown["state"] == "requested"
    listed = _json(
        _invoke(
            runner,
            repo,
            parent["session_id"],
            "delegation",
            "list",
            "--state",
            "requested",
        )
    )
    assert isinstance(listed, list)
    assert [item["id"] for item in listed] == [delegation_id]

    started = _json(
        _invoke(
            runner,
            repo,
            parent["session_id"],
            "delegation",
            "start",
            delegation_id,
            created["revision"],
            "--child-session-id",
            child["session_id"],
            "--idempotency-key",
            "cli-delegation-start",
        )
    )
    assert isinstance(started, dict)
    succeeded = _json(
        _invoke(
            runner,
            repo,
            child["session_id"],
            "delegation",
            "succeed",
            delegation_id,
            started["revision"],
            "--summary",
            "The independent check completed.",
            "--result-ref",
            f"task:{task_id}",
            "--idempotency-key",
            "cli-delegation-succeed",
        )
    )
    assert isinstance(succeeded, dict)
    assert manager.get_delegation(delegation_id)["state"] == "succeeded"


def test_cli_rejects_stale_target_revision_without_traceback(tmp_path: Path) -> None:
    repo, manager, parent, _ = _workspace(tmp_path)
    runner = CliRunner()
    task = _task(manager, "cli-stale-target")
    task_id = task["entity_ref"]["id"]
    manager.start_task(task_id, task["revision"], idempotency_key="cli-move-target")

    result = _invoke(
        runner,
        repo,
        parent["session_id"],
        "delegation",
        "create",
        "--target-ref",
        f"task:{task_id}",
        "--target-revision",
        task["revision"],
        "--target-profile",
        "claude-independent-reviewer",
        "--purpose",
        "independent_review",
        "--limits-json",
        json.dumps(LIMITS),
        "--idempotency-key",
        "cli-stale-delegation",
    )

    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["error"]["type"] == "LifecycleConflictError"
    assert "target_revision" in error["error"]["message"]
    assert "Traceback" not in result.output


def test_cli_recovers_requested_work_after_requester_closes(tmp_path: Path) -> None:
    repo, manager, parent, _ = _workspace(tmp_path)
    runner = CliRunner()
    task = _task(manager, "cli-recovery-target")
    created = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        idempotency_key="cli-recovery-request",
    )
    manager.sessions.close(parent["session_id"], nonce=parent["nonce"])

    recovery = CommonsManager(repo)
    recovery_session = recovery.start_session(
        stable_instance_id="delegation-cli-recovery-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="operator-recovery",
        capabilities=("delegation:recover",),
    )
    result = _json(
        _invoke(
            runner,
            repo,
            recovery_session["session_id"],
            "delegation",
            "recover",
            created["entity_ref"]["id"],
            created["revision"],
            "--reason",
            "The requester closed before launch.",
            "--idempotency-key",
            "cli-recovery-transition",
        )
    )

    assert isinstance(result, dict)
    assert result["event_type"] == "delegation.recovered"
    assert recovery.get_delegation(created["entity_ref"]["id"])["state"] == "cancelled"


def test_cli_recovery_failure_is_structured_and_has_no_traceback(tmp_path: Path) -> None:
    repo, manager, parent, _ = _workspace(tmp_path)
    runner = CliRunner()
    task = _task(manager, "cli-recovery-live-target")
    created = manager.create_delegation(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        target_profile="claude-builder",
        purpose="implementation",
        limits=LIMITS,
        idempotency_key="cli-recovery-live-request",
    )
    result = _invoke(
        runner,
        repo,
        parent["session_id"],
        "delegation",
        "recover",
        created["entity_ref"]["id"],
        created["revision"],
        "--reason",
        "The requester is actually live.",
        "--idempotency-key",
        "cli-recovery-live-transition",
    )

    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["error"]["type"] == "LifecycleConflictError"
    assert "required capability" in error["error"]["message"]
    assert "Traceback" not in result.output
