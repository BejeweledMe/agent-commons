from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner, Result

from agent_commons.cli import cli
from agent_commons.services import CommonsManager


def _invoke(runner: CliRunner, repo: Path, *args: str) -> Result:
    return runner.invoke(cli, ["--repo", str(repo), "--json", *args])


def _json(result: Result) -> object:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_exposes_complete_manager_surface() -> None:
    runner = CliRunner()
    top = runner.invoke(cli, ["--help"])
    assert top.exit_code == 0
    for name in (
        "init",
        "session",
        "orient",
        "inbox",
        "objective",
        "task",
        "thread",
        "artifact",
        "review",
        "verification",
        "finding",
        "decision",
        "handoff",
        "claim",
        "event",
        "receipt",
        "views",
        "index",
        "doctor",
    ):
        assert name in top.output

    expected = {
        "session": {"start", "show", "heartbeat", "end"},
        "objective": {"create", "list", "revise", "close"},
        "task": {
            "create",
            "list",
            "take",
            "start",
            "block",
            "unblock",
            "complete",
            "submit",
            "accept",
            "cancel",
            "reopen",
        },
        "thread": {"list", "open", "reply", "resolve"},
        "artifact": {"list", "register", "revise"},
        "review": {"list", "request", "complete"},
        "verification": {"list", "record"},
        "finding": {"list", "report", "promote", "contest", "resolve"},
        "decision": {"list", "propose", "accept", "reject", "defer", "supersede"},
        "handoff": {"list", "create", "acknowledge"},
        "claim": {"acquire", "list", "renew", "release", "break"},
        "event": {"show", "correct", "invalidate", "revoke"},
        "receipt": {"status", "reconcile", "abandon"},
    }
    for group, commands in expected.items():
        help_result = runner.invoke(cli, [group, "--help"])
        assert help_result.exit_code == 0
        for command in commands:
            assert command in help_result.output

    claim_help = runner.invoke(cli, ["claim", "acquire", "--help"])
    assert claim_help.exit_code == 0
    assert "exclusive|advisory" in claim_help.output
    assert "exclusive|shared" not in claim_help.output


def test_cli_init_session_objective_heartbeat_and_read_flow(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    initialized = _json(_invoke(runner, repo, "init"))
    assert initialized["integrations"] == ["codex", "claude"]  # type: ignore[index]
    assert (repo / ".agents" / "skills" / "commons-start" / "SKILL.md").is_file()
    assert (repo / ".claude" / "skills" / "commons-start" / "SKILL.md").is_file()

    started = _json(
        _invoke(
            runner,
            repo,
            "session",
            "start",
            "--stable-instance-id",
            "codex-window-cli-12345678",
            "--principal",
            "operator",
            "--client",
            "codex",
            "--software",
            "codex-cli",
            "--role",
            "builder",
        )
    )
    session_id = started["session_id"]  # type: ignore[index]
    nonce = started["nonce"]  # type: ignore[index]
    created = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session_id,
            "objective",
            "create",
            "--title",
            "Universal manager",
            "--description",
            "Coordinate agents",
            "--acceptance-criterion",
            "works",
            "--idempotency-key",
            "cli-objective",
        )
    )
    event_id = created["event_id"]  # type: ignore[index]
    listed = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session_id,
            "objective",
            "list",
        )
    )
    assert listed[0]["title"] == "Universal manager"  # type: ignore[index]
    shown = _json(_invoke(runner, repo, "event", "show", event_id))
    assert len(shown["canonical_sha256"]) == 64  # type: ignore[index]

    heartbeat = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session_id,
            "session",
            "heartbeat",
            "--nonce",
            nonce,
            "--ttl-seconds",
            "60",
        )
    )
    assert heartbeat["nonce"] != nonce  # type: ignore[index]
    doctor = _json(_invoke(runner, repo, "doctor"))
    assert doctor["ok"] is True  # type: ignore[index]


def test_cli_artifact_is_metadata_only_and_invalid_ref_is_concise(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    session = _json(
        _invoke(
            runner,
            repo,
            "session",
            "start",
            "--stable-instance-id",
            "codex-window-artifact-12345678",
            "--principal",
            "operator",
            "--client",
            "codex",
            "--software",
            "codex-cli",
            "--role",
            "builder",
        )
    )
    source = repo / "proof.txt"
    source.write_text("proof", encoding="utf-8")
    registered = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session["session_id"],  # type: ignore[index]
            "artifact",
            "register",
            str(source),
            "--media-type",
            "text/plain",
            "--metadata-json",
            '{"kind":"proof"}',
            "--idempotency-key",
            "cli-artifact",
        )
    )
    assert registered["content_copied"] is False  # type: ignore[index]
    assert list((repo / ".agent-commons" / "blobs" / "sha256").iterdir()) == []

    invalid = _invoke(
        runner,
        repo,
        "--session-id",
        session["session_id"],  # type: ignore[index]
        "review",
        "request",
        "--target-ref",
        "not-a-ref",
        "--target-revision",
        registered["revision"],  # type: ignore[index]
        "--criterion",
        "correct",
    )
    assert invalid.exit_code != 0
    assert "<kind>:<id>" in invalid.output
    assert "Traceback" not in invalid.output
    error = json.loads(invalid.output)
    assert error == {
        "error": {
            "message": "reference must use '<kind>:<id>' syntax",
            "safe_next_actions": ["Correct the bounded input using the command help, then retry."],
            "type": "ValidationError",
        },
        "ok": False,
    }


def test_cli_doctor_exits_two_for_missing_receipt(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id="doctor-window-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    manager.session_id = session["session_id"]
    manager.create_objective(
        title="Doctor",
        description="Detect damage",
        acceptance_criteria=("fails closed",),
        idempotency_key="doctor-objective",
    )
    namespace = manager._namespace(manager._active_session())
    receipt = manager.events.idempotency.lookup(
        namespace=namespace,
        key="doctor-objective",
    )
    assert receipt is not None
    receipt.path.unlink()

    result = _invoke(runner, repo, "doctor")
    assert result.exit_code == 2
    report = json.loads(result.output)
    assert report["ok"] is False
    assert "idempotency receipt" in report["issues"][0]


def test_cli_can_abandon_an_orphan_receipt_and_restore_doctor(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    manager = CommonsManager(repo)
    session = manager.start_session(
        stable_instance_id="receipt-recovery-window-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="maintainer",
        capabilities=("receipt:abandon",),
    )
    manager.session_id = session["session_id"]
    reservation = manager.events.idempotency.reserve(
        namespace=manager._namespace(manager._active_session()),
        key="lost-cli-operation",
        semantic_sha256="b" * 64,
    )

    abandoned = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session["session_id"],
            "receipt",
            "abandon",
            reservation.key_digest,
            "--reason",
            "the original request is unavailable",
        )
    )

    assert abandoned["event_id"] == reservation.event_id  # type: ignore[index]
    report = _json(_invoke(runner, repo, "doctor"))
    assert report["ok"] is True  # type: ignore[index]


def test_cli_status_and_reconcile_recover_a_fresh_git_clone(tmp_path: Path) -> None:
    runner = CliRunner()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    _json(_invoke(runner, source, "init", "--integration", "codex"))
    source_manager = CommonsManager(source)
    source_session = source_manager.start_session(
        stable_instance_id="receipt-source-window-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    source_manager.session_id = source_session["session_id"]
    source_manager.create_objective(
        title="Portable objective",
        description="Must survive a clone without operational state",
        acceptance_criteria=("clone recovers",),
        idempotency_key="portable-objective",
    )

    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=clone, check=True)
    shutil.copytree(source / ".agent-commons", clone / ".agent-commons")
    clone_session = _json(
        _invoke(
            runner,
            clone,
            "session",
            "start",
            "--stable-instance-id",
            "receipt-clone-window-12345678",
            "--principal",
            "operator",
            "--client",
            "codex",
            "--software",
            "codex-cli",
            "--role",
            "maintainer",
        )
    )
    status = _json(_invoke(runner, clone, "receipt", "status"))
    assert status["ok"] is False  # type: ignore[index]
    assert status["migration_state"] == "legacy"  # type: ignore[index]

    recovered = _json(
        _invoke(
            runner,
            clone,
            "--session-id",
            clone_session["session_id"],  # type: ignore[index]
            "receipt",
            "reconcile",
        )
    )
    assert recovered["ok"] is True  # type: ignore[index]
    assert recovered["derived_receipts"] == 1  # type: ignore[index]
    healthy = _json(_invoke(runner, clone, "receipt", "status"))
    assert healthy["ok"] is True  # type: ignore[index]
    assert healthy["anchor_state"] == "healthy"  # type: ignore[index]


def test_cli_can_acquire_the_advisory_mode_accepted_by_coordination(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    session = _json(
        _invoke(
            runner,
            repo,
            "session",
            "start",
            "--stable-instance-id",
            "codex-window-advisory-12345678",
            "--principal",
            "operator",
            "--client",
            "codex",
            "--software",
            "codex-cli",
            "--role",
            "builder",
        )
    )

    claim = _json(
        _invoke(
            runner,
            repo,
            "--session-id",
            session["session_id"],  # type: ignore[index]
            "claim",
            "acquire",
            "--resource",
            "path:src/api",
            "--mode",
            "advisory",
            "--idempotency-key",
            "cli-advisory-claim",
        )
    )

    assert claim["mode"] == "advisory"  # type: ignore[index]


def test_cli_rejects_unsupported_workspace_policy_as_structured_error(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    config = repo / ".agent-commons" / "workspace.yaml"
    original = config.read_text(encoding="utf-8")
    config.write_text(
        original + "policy:\n  unimplemented_profile: light\n",
        encoding="utf-8",
    )

    result = _invoke(runner, repo, "doctor")

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    error = json.loads(result.output)
    assert error["ok"] is False
    assert error["error"]["type"] == "ConfigurationError"
    assert "unsupported workspace policy keys" in error["error"]["message"]


def test_cli_rejects_null_security_configuration_as_structured_error(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    repo = tmp_path / "project"
    repo.mkdir()
    _json(_invoke(runner, repo, "init", "--integration", "codex"))
    config = repo / ".agent-commons" / "workspace.yaml"
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value["security"] = None
    config.write_text(yaml.safe_dump(value), encoding="utf-8")

    result = _invoke(runner, repo, "doctor")

    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["ok"] is False
    assert error["error"]["type"] == "ConfigurationError"
    assert "security configuration must be a mapping" in error["error"]["message"]
