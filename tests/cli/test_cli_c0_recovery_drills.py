"""Hermetic C0 characterization for retained CLI recovery and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from agent_commons.cli import cli
from agent_commons.services import CommonsManager


def _invoke(runner: CliRunner, repo: Path, *arguments: str):
    return runner.invoke(cli, ["--repo", str(repo), "--json", *arguments])


def _initialize_workspace(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    CommonsManager.initialize(repo, integrations=(), workspace_name=name)
    return repo


def test_receipt_reconcile_restores_doctor_after_missing_receipt(tmp_path: Path) -> None:
    """The documented CLI recovery path derives a missing receipt from the ledger."""

    repo = _initialize_workspace(tmp_path, "c0-receipt-recovery")
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id="c0-receipt-recovery-writer",
        principal="operator",
        client="pytest",
        software="pytest",
        role="builder",
    )
    manager.session_id = str(session["session_id"])
    manager.create_objective(
        title="Recover a receipt",
        description="Characterize the retained recovery command.",
        acceptance_criteria=("doctor becomes healthy",),
        idempotency_key="c0-receipt-recovery-objective",
    )
    namespace = manager._namespace(manager._active_session())
    receipt = manager.events.idempotency.lookup(
        namespace=namespace,
        key="c0-receipt-recovery-objective",
    )
    assert receipt is not None
    receipt.path.unlink()

    runner = CliRunner()
    status_before = _invoke(runner, repo, "receipt", "status")
    doctor_before = _invoke(runner, repo, "doctor")

    assert status_before.exit_code == 0, status_before.output
    assert json.loads(status_before.output)["ok"] is False
    assert doctor_before.exit_code == 2
    assert "idempotency receipt" in json.loads(doctor_before.output)["issues"][0]

    reconciled = _invoke(
        runner,
        repo,
        "--session-id",
        str(session["session_id"]),
        "receipt",
        "reconcile",
    )
    status_after = _invoke(runner, repo, "receipt", "status")
    doctor_after = _invoke(runner, repo, "doctor")

    assert reconciled.exit_code == 0, reconciled.output
    assert json.loads(reconciled.output)["derived_receipts"] == 1
    assert status_after.exit_code == 0, status_after.output
    assert json.loads(status_after.output)["ok"] is True
    assert doctor_after.exit_code == 0, doctor_after.output
    assert json.loads(doctor_after.output)["ok"] is True


def test_broker_preflight_refuses_missing_mcp_without_provider_attempt(tmp_path: Path) -> None:
    """A diagnostic preflight fails closed and allocates neither runtime nor an attempt."""

    repo = _initialize_workspace(tmp_path, "c0-preflight-refusal")
    config = tmp_path / "profiles.yaml"
    config.write_text(
        "profiles:\n"
        "  claude-independent-reviewer:\n"
        "    executable: /bin/echo\n"
        "    mcp_executable: agent-commons-mcp-missing-for-c0-test\n"
        "    git_executable: /usr/bin/git\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )

    result = _invoke(
        CliRunner(),
        repo,
        "broker",
        "preflight",
        "claude-independent-reviewer",
        "--purpose",
        "independent_review",
        "--profile-config",
        str(config),
    )

    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["checks"]["mcp_executable"]["diagnostic_code"] == "mcp_executable_unavailable"
    assert body["provider_help_process_started"] is False
    assert body["consumed_delegation_attempt"] is False
    assert not (CommonsManager(repo).paths.state_root / "runtime").exists()
