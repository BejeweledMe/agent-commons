from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_commons.errors import ConfigurationError, LifecycleConflictError, ValidationError
from agent_commons.mcp.server import INDEPENDENT_REVIEW_WORKER_TOOL_NAMES, build_server
from agent_commons.runtime import (
    BuiltinProfileId,
    ClaudePermissionMode,
    ClaudeRunnerProfile,
    TerminalToolAuditStore,
)
from agent_commons.services import CommonsManager


class FakeServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any) -> Any:
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register

    def run(self, *, transport: str) -> None:
        raise AssertionError(f"unexpected transport run: {transport}")


class FakeRuntime:
    def profile_summaries(self) -> list[dict[str, Any]]:
        return [{"profile_id": "claude-independent-reviewer"}]

    def list_attempts(self) -> list[dict[str, Any]]:
        return []

    def run(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("worker-scope test must not launch a provider")

    def reconcile(self) -> list[dict[str, Any]]:
        raise AssertionError("worker-scope test must not reconcile runtime state")


def _workspace(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ("/usr/bin/git", "init", "-q", str(repo)),
        check=True,
        capture_output=True,
    )
    source = repo / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    reviewable = repo / "src" / "reviewable_gate.py"
    reviewable.write_text(
        "def release_gate():\n    return gated_argv(provider_argv)\ntoken = CancellationToken()\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("evidence/\n.env\n*.pem\n", encoding="utf-8")
    (repo / ".env").write_text("LOCAL_ONLY=value\n", encoding="utf-8")
    (repo / "private.pem").write_text("not-a-real-key\n", encoding="utf-8")
    evidence = repo / "evidence" / "review.txt"
    evidence.parent.mkdir()
    evidence.write_text(
        "registered review evidence\ncredential-free: it validates provider help\n",
        encoding="utf-8",
    )
    unrelated_evidence = repo / "evidence" / "unrelated.txt"
    unrelated_evidence.write_text("unrelated evidence\n", encoding="utf-8")
    (tmp_path / "canary.txt").write_text("outside workspace\n", encoding="utf-8")

    CommonsManager.initialize(repo, integrations=(), workspace_name="worker-scope")
    parent = CommonsManager(repo, state_root=tmp_path / "state")
    parent_session = parent.start_session(
        stable_instance_id="worker-scope-parent-12345678",
        principal="operator",
        client="codex",
        software="codex-cli",
        role="builder",
    )
    parent.session_id = parent_session["session_id"]
    child_session = parent.start_session(
        stable_instance_id="worker-scope-child-12345678",
        principal="operator",
        client="claude",
        software="claude-code",
        role="independent-reviewer",
    )

    task = parent.create_task(
        title="Review the scoped workspace",
        description="The worker may inspect only this exact review subject.",
        acceptance_criteria=("the scoped review is completed",),
        priority="high",
        idempotency_key="worker-scope-task",
    )
    artifact = parent.register_artifact(
        evidence,
        media_type="text/plain",
        idempotency_key="worker-scope-artifact",
    )
    task_started = parent.start_task(
        task["entity_ref"]["id"],
        task["revision"],
        idempotency_key="worker-scope-task-start",
    )
    task = parent.complete_task(
        task["entity_ref"]["id"],
        task_started["revision"],
        summary="The exact review evidence is registered.",
        artifact_refs=(artifact["entity_ref"],),
        idempotency_key="worker-scope-task-complete",
    )
    verification = parent.record_verification(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        claim="The registered review evidence was reproduced.",
        evidence_refs=(artifact["entity_ref"],),
        method="Compared the exact registered artifact.",
        outcome="passed",
        idempotency_key="worker-scope-verification",
    )
    review = parent.request_review(
        target_ref=task["entity_ref"],
        target_revision=task["revision"],
        criteria=("Inspect only the immutable snapshot",),
        idempotency_key="worker-scope-review",
    )
    unrelated_task = parent.create_task(
        title="Unrelated task",
        description="This task and its review must remain invisible to the worker.",
        acceptance_criteria=("unrelated review remains isolated",),
        priority="normal",
        idempotency_key="worker-scope-unrelated-task",
    )
    unrelated_artifact = parent.register_artifact(
        unrelated_evidence,
        media_type="text/plain",
        idempotency_key="worker-scope-unrelated-artifact",
    )
    unrelated_started = parent.start_task(
        unrelated_task["entity_ref"]["id"],
        unrelated_task["revision"],
        idempotency_key="worker-scope-unrelated-task-start",
    )
    unrelated_task = parent.complete_task(
        unrelated_task["entity_ref"]["id"],
        unrelated_started["revision"],
        summary="The unrelated evidence is registered separately.",
        artifact_refs=(unrelated_artifact["entity_ref"],),
        idempotency_key="worker-scope-unrelated-task-complete",
    )
    unrelated_review = parent.request_review(
        target_ref=unrelated_task["entity_ref"],
        target_revision=unrelated_task["revision"],
        criteria=("Review unrelated work",),
        idempotency_key="worker-scope-unrelated-review",
    )
    delegation = parent.create_delegation(
        target_ref=review["entity_ref"],
        target_revision=review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        idempotency_key="worker-scope-delegation",
    )
    requested_only = parent.create_delegation(
        target_ref=unrelated_review["entity_ref"],
        target_revision=unrelated_review["revision"],
        target_profile="claude-independent-reviewer",
        purpose="independent_review",
        limits={
            "max_depth": 0,
            "wall_time_seconds": 300,
            "max_attempts": 1,
            "max_concurrency": 1,
            "budget": {"unit": "micro_usd", "limit": 50_000},
        },
        idempotency_key="worker-scope-requested-only-delegation",
    )
    started = parent.start_delegation(
        delegation["entity_ref"]["id"],
        delegation["revision"],
        child_session_id=child_session["session_id"],
        attempt=1,
        idempotency_key="worker-scope-start",
    )
    child = CommonsManager(
        repo,
        session_id=child_session["session_id"],
        state_root=parent.paths.state_root,
    )
    return {
        "repo": repo,
        "source": source,
        "reviewable": reviewable,
        "evidence": evidence,
        "parent": parent,
        "child": child,
        "task": task,
        "review": review,
        "artifact": artifact,
        "verification": verification,
        "unrelated_task": unrelated_task,
        "unrelated_review": unrelated_review,
        "unrelated_artifact": unrelated_artifact,
        "delegation": delegation,
        "requested_only": requested_only,
        "started": started,
    }


def _worker_server(
    workspace: dict[str, Any], *, git_executable: str = "/usr/bin/git"
) -> FakeServer:
    server = build_server(
        workspace["repo"],
        manager=workspace["child"],
        runtime=FakeRuntime(),
        delegation_id=workspace["delegation"]["entity_ref"]["id"],
        binding_wait_seconds=0,
        git_executable=git_executable,
        server_factory=FakeServer,
    )
    assert isinstance(server, FakeServer)
    return server


def test_worker_snapshot_accepts_an_operator_configured_trusted_git(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    wrapper = tmp_path / "operator-git"
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/git "$@"\n', encoding="utf-8")
    wrapper.chmod(0o700)

    server = _worker_server(workspace, git_executable=str(wrapper))

    assert any(
        item["path"] == "src/app.py" for item in server.tools["commons_workspace_files"]("src", 10)
    )


def test_worker_snapshot_never_follows_a_symlinked_parent_component(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("outside secret\n", encoding="utf-8")
    (workspace["repo"] / "linked").symlink_to(outside, target_is_directory=True)
    blob = (
        subprocess.run(
            ("/usr/bin/git", "-C", str(workspace["repo"]), "hash-object", "-w", "--stdin"),
            input=b"indexed placeholder\n",
            check=True,
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    subprocess.run(
        (
            "/usr/bin/git",
            "-C",
            str(workspace["repo"]),
            "update-index",
            "--add",
            "--cacheinfo",
            "100644",
            blob,
            "linked/canary.txt",
        ),
        check=True,
        capture_output=True,
    )

    server = _worker_server(workspace)

    assert "linked/canary.txt" not in {
        item["path"] for item in server.tools["commons_workspace_files"]("", 500)
    }
    with pytest.raises(LifecycleConflictError, match="outside the delegated snapshot"):
        server.tools["commons_workspace_read"]("linked/canary.txt", None)


def test_explicit_binding_never_falls_back_to_root_and_worker_catalog_is_scoped(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    requested_id = workspace["requested_only"]["entity_ref"]["id"]
    active_id = workspace["delegation"]["entity_ref"]["id"]

    with pytest.raises(ConfigurationError, match="not canonically started"):
        build_server(
            workspace["repo"],
            manager=workspace["child"],
            delegation_id=requested_id,
            binding_wait_seconds=0,
            server_factory=FakeServer,
        )
    with pytest.raises(ConfigurationError, match="does not match"):
        build_server(
            workspace["repo"],
            manager=workspace["parent"],
            delegation_id=active_id,
            binding_wait_seconds=0,
            server_factory=FakeServer,
        )

    server = _worker_server(workspace)
    expected_tools = {
        "commons_orient",
        "commons_inbox",
        "commons_list_tasks",
        "commons_list_delegations",
        "commons_show_delegation",
        "commons_list_reviews",
        "commons_show_review",
        "commons_list_verifications",
        "commons_show_verification",
        "commons_show_artifact",
        "commons_read_artifact",
        "commons_complete_review",
        "commons_record_verification",
        "commons_delegation_input_needed",
        "commons_succeed_delegation",
        "commons_delegation_needs_operator",
        "commons_workspace_files",
        "commons_workspace_read",
        "commons_workspace_search",
    }
    assert expected_tools == set(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES)
    assert set(server.tools) == expected_tools
    profile = ClaudeRunnerProfile(
        profile_id=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER,
        executable="/bin/echo",
        mcp_executable="/bin/echo",
        git_executable="/usr/bin/git",
        permission_mode=ClaudePermissionMode.DONT_ASK,
    )
    invocation = profile.build_invocation(
        "Review the exact worker contract",
        workspace_root=workspace["repo"],
        state_root=workspace["parent"].paths.state_root,
        delegation_id=active_id,
    )
    allowed = invocation.argv[invocation.argv.index("--allowed-tools") + 1]
    assert {
        name.removeprefix("mcp__agent-commons__") for name in allowed.split(",")
    } == expected_tools
    assert "commons_request_delegation" not in server.tools
    assert "commons_cancel_delegation" not in server.tools
    assert "commons_run_delegation" not in server.tools

    assert [item["id"] for item in server.tools["commons_list_delegations"](None)] == [active_id]
    assert [item["id"] for item in server.tools["commons_list_reviews"](None)] == [
        workspace["review"]["entity_ref"]["id"]
    ]
    assert [item["id"] for item in server.tools["commons_list_tasks"](None)] == [
        workspace["task"]["entity_ref"]["id"]
    ]
    verification_id = workspace["verification"]["entity_ref"]["id"]
    assert [item["id"] for item in server.tools["commons_list_verifications"]()] == [
        verification_id
    ]
    assert server.tools["commons_show_verification"](verification_id)["id"] == verification_id
    recorded = server.tools["commons_record_verification"](
        f"task:{workspace['task']['entity_ref']['id']}",
        workspace["task"]["revision"],
        "The reviewer independently reproduced the bound artifact.",
        "Compared the exact manifest-bound bytes.",
        "passed",
        [f"artifact:{workspace['artifact']['entity_ref']['id']}"],
        "worker-scope-review-verification",
    )
    assert recorded["event_type"] == "verification.recorded"
    with pytest.raises(LifecycleConflictError, match="exact target scope"):
        server.tools["commons_record_verification"](
            f"task:{workspace['unrelated_task']['entity_ref']['id']}",
            workspace["unrelated_task"]["revision"],
            "This unrelated verification must be rejected.",
            "No method is authorized outside the exact target.",
            "failed",
            [f"artifact:{workspace['unrelated_artifact']['entity_ref']['id']}"],
            "worker-scope-unrelated-verification",
        )
    with pytest.raises(LifecycleConflictError, match="bound delegation"):
        server.tools["commons_show_delegation"](requested_id)
    with pytest.raises(LifecycleConflictError, match="bound review"):
        server.tools["commons_show_review"](workspace["unrelated_review"]["entity_ref"]["id"])


def test_worker_reader_denies_sensitive_and_outside_files_and_unrelated_results(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    server = _worker_server(workspace)
    files = server.tools["commons_workspace_files"]("", 500)
    visible_paths = {item["path"] for item in files}

    assert "src/app.py" in visible_paths
    assert ".env" not in visible_paths
    assert "private.pem" not in visible_paths
    source_item = next(item for item in files if item["path"] == "src/app.py")
    source = server.tools["commons_workspace_read"]("src/app.py", source_item["sha256"])
    assert "return 42" in source["content"]
    assert source["redactions"] == []
    assert server.tools["commons_workspace_search"]("return 42", "src", 10) == [
        {"path": "src/app.py", "line": 2, "text": "    return 42"}
    ]
    reviewable_item = next(item for item in files if item["path"] == "src/reviewable_gate.py")
    reviewable = server.tools["commons_workspace_read"](
        "src/reviewable_gate.py", reviewable_item["sha256"]
    )
    assert "gated_argv(provider_argv)" in reviewable["content"]
    assert "CancellationToken" not in reviewable["content"]
    assert reviewable["redactions"] == [
        {
            "line": 3,
            "categories": ["credential_assignment"],
            "classifications": ["secret"],
        }
    ]
    assert server.tools["commons_workspace_search"]("gated_argv", "src", 10) == [
        {
            "path": "src/reviewable_gate.py",
            "line": 2,
            "text": "    return gated_argv(provider_argv)",
        }
    ]
    with pytest.raises(ValidationError, match="remain relative"):
        server.tools["commons_workspace_read"]("../canary.txt", None)
    with pytest.raises(LifecycleConflictError, match="outside the delegated snapshot"):
        server.tools["commons_workspace_read"](".env", None)

    child: CommonsManager = workspace["child"]
    unrelated_review = workspace["unrelated_review"]
    with pytest.raises(LifecycleConflictError, match="exact bound review"):
        child.complete_review(
            unrelated_review["entity_ref"]["id"],
            unrelated_review["revision"],
            target_revision=workspace["unrelated_task"]["revision"],
            verdict="approved",
            summary="This delegated child must not be able to approve unrelated work.",
            idempotency_key="worker-scope-illegal-direct-review",
        )
    with pytest.raises(LifecycleConflictError, match="outside its delegation scope"):
        server.tools["commons_complete_review"](
            unrelated_review["entity_ref"]["id"],
            unrelated_review["revision"],
            workspace["unrelated_task"]["revision"],
            "approved",
            "This MCP write must remain scoped.",
            "worker-scope-illegal-mcp-review",
            None,
        )
    with pytest.raises(LifecycleConflictError, match="outside its delegation scope"):
        server.tools["commons_delegation_needs_operator"](
            workspace["requested_only"]["entity_ref"]["id"],
            workspace["requested_only"]["revision"],
            "orphaned",
            "A worker cannot classify an unrelated delegation.",
            "worker-scope-illegal-outcome",
        )
    with pytest.raises(LifecycleConflictError, match="exact completed review"):
        server.tools["commons_succeed_delegation"](
            workspace["delegation"]["entity_ref"]["id"],
            workspace["started"]["revision"],
            "An unrelated review cannot satisfy this delegation.",
            [f"review:{unrelated_review['entity_ref']['id']}"],
            "worker-scope-illegal-result",
        )

    assert child.get_delegation(workspace["delegation"]["entity_ref"]["id"])["state"] == "active"
    assert (
        next(
            review
            for review in child.list_reviews(state=None)
            if review["id"] == unrelated_review["entity_ref"]["id"]
        )["state"]
        == "requested"
    )


def test_registered_artifact_is_manifest_bound_scoped_and_quiescent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    server = _worker_server(workspace)
    artifact_id = workspace["artifact"]["entity_ref"]["id"]
    unrelated_id = workspace["unrelated_artifact"]["entity_ref"]["id"]

    shown = server.tools["commons_show_artifact"](artifact_id)
    assert shown["artifact"]["id"] == artifact_id
    assert shown["manifest"]["source"] == {"path": "evidence/review.txt"}
    read = server.tools["commons_read_artifact"](artifact_id)
    assert read["path"] == "evidence/review.txt"
    assert read["content"] == ("registered review evidence\n[agent-commons redacted source line]\n")
    assert read["redactions"] == [
        {
            "line": 2,
            "categories": ["credential_assignment"],
            "classifications": ["secret"],
        }
    ]
    with pytest.raises(LifecycleConflictError, match="bound task artifact"):
        server.tools["commons_show_artifact"](unrelated_id)
    with pytest.raises(LifecycleConflictError, match="bound task artifact"):
        server.tools["commons_read_artifact"](unrelated_id)

    workspace["evidence"].write_text("tampered review evidence\n", encoding="utf-8")
    with pytest.raises(LifecycleConflictError, match="registered review artifact changed"):
        server.tools["commons_complete_review"](
            workspace["review"]["entity_ref"]["id"],
            workspace["review"]["revision"],
            workspace["task"]["revision"],
            "approved",
            "Tampered evidence must not receive a canonical verdict.",
            "worker-scope-tampered-artifact-review",
            None,
        )

    assert (
        next(
            review
            for review in workspace["parent"].list_reviews(state=None)
            if review["id"] == workspace["review"]["entity_ref"]["id"]
        )["state"]
        == "requested"
    )


def test_snapshot_mutation_and_active_cancel_both_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    worker_server = _worker_server(workspace)
    source_item = next(
        item
        for item in worker_server.tools["commons_workspace_files"]("src", 10)
        if item["path"] == "src/app.py"
    )
    workspace["source"].write_text(
        "def answer() -> int:\n    return 43\n",
        encoding="utf-8",
    )

    with pytest.raises(LifecycleConflictError, match="changed after reviewer snapshot"):
        worker_server.tools["commons_workspace_read"]("src/app.py", source_item["sha256"])
    with pytest.raises(LifecycleConflictError, match="workspace changed"):
        worker_server.tools["commons_complete_review"](
            workspace["review"]["entity_ref"]["id"],
            workspace["review"]["revision"],
            workspace["task"]["revision"],
            "approved",
            "A changed snapshot must not receive a canonical verdict.",
            "worker-scope-mutated-review",
            None,
        )

    root_server = build_server(
        workspace["repo"],
        manager=workspace["parent"],
        runtime=FakeRuntime(),
        server_factory=FakeServer,
    )
    assert isinstance(root_server, FakeServer)
    delegation_id = workspace["delegation"]["entity_ref"]["id"]
    with pytest.raises(LifecycleConflictError, match="active runtime cancellation"):
        root_server.tools["commons_cancel_delegation"](
            delegation_id,
            workspace["started"]["revision"],
            "Do not record cancellation before provider termination.",
            "worker-scope-active-cancel",
        )

    assert workspace["parent"].get_delegation(delegation_id)["state"] == "active"
    assert (
        next(
            review
            for review in workspace["parent"].list_reviews(state=None)
            if review["id"] == workspace["review"]["entity_ref"]["id"]
        )["state"]
        == "requested"
    )


def test_terminal_delegation_revokes_the_captured_worker_catalog(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    server = _worker_server(workspace)
    delegation_id = workspace["delegation"]["entity_ref"]["id"]

    ended = server.tools["commons_delegation_needs_operator"](
        delegation_id,
        workspace["started"]["revision"],
        "invalid_result",
        "Stop this worker without granting any lingering MCP authority.",
        "worker-scope-end-authority",
    )
    assert ended["event_type"] == "delegation.needs_operator"
    assert workspace["parent"].get_delegation(delegation_id)["state"] == "needs_operator"
    audit = TerminalToolAuditStore(workspace["parent"].paths.state_root).get(delegation_id)
    assert audit.terminal_tool_calls == 1
    assert audit.terminal_tool_completions == 1
    assert audit.terminal_tool_rejections == 0

    with pytest.raises(LifecycleConflictError, match="worker MCP authority ended"):
        server.tools["commons_workspace_read"]("src/app.py", None)
    with pytest.raises(LifecycleConflictError, match="worker MCP authority ended"):
        server.tools["commons_complete_review"](
            workspace["review"]["entity_ref"]["id"],
            workspace["review"]["revision"],
            workspace["task"]["revision"],
            "approved",
            "A terminal worker must not retain review authority.",
            "worker-scope-review-after-terminal",
            None,
        )
