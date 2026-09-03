"""J1: integrated Work operator journey over hermetic HTTP routes."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from conftest import PORT, authorized
from fastapi.testclient import TestClient

from agent_commons.integrations.starter_packs import STARTER_PACK_ALLOWED_SKILL_REFS
from agent_commons.runtime import (
    AttemptStore,
    ProcessResult,
    ProviderQualificationStore,
    RunOutcome,
    RunReason,
    default_profile_registry,
)
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import DelegationRuntimeService
from agent_commons.ui.context import UIContext
from agent_commons.ui.server import create_app
from agent_commons.ui.tracker_reads import build_tracker_snapshot
from agent_commons.ui.tracker_routes import MAX_TRACKER_FRAME_BYTES, tracker_events


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", ""])
    pid = process.pid
    assert process.wait() == 0
    return pid


def _executable(path: Path, marker: str) -> Path:
    path.write_text(f"#!{sys.executable}\n# {marker}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


class ControlledRunner:
    """Fake provider runner that lets the test observe a running attempt."""

    def __init__(self, repo: Path, state_root: Path) -> None:
        self.repo = repo
        self.state_root = state_root
        self.started = threading.Event()
        self.release = threading.Event()
        self.mode = "complete"
        self.invocations: list[Any] = []

    def reset(self, mode: str) -> None:
        self.mode = mode
        self.started.clear()
        self.release.clear()

    def run(self, invocation: Any, **values: Any) -> ProcessResult:
        self.invocations.append(invocation)
        pid = _dead_pid()
        values["on_started"](pid)
        self.started.set()
        assert self.release.wait(timeout=10)
        if self.mode == "complete":
            self._complete_as_child(str(values["child_session_id"]))
        return ProcessResult(
            outcome=RunOutcome.SUCCEEDED,
            reason=RunReason.COMPLETED,
            exit_code=0,
            pid=pid,
            duration_seconds=0.01,
            stdout=b"raw provider secret",
            stderr=b"raw provider stderr",
            stdout_bytes_seen=19,
            stderr_bytes_seen=19,
            output_truncated=False,
        )

    def _complete_as_child(self, child_session_id: str) -> None:
        child = CommonsManager(
            self.repo,
            session_id=child_session_id,
            state_root=self.state_root,
        )
        active = [
            record
            for record in child.snapshot().delegations.values()
            if record.get("child_session_id") == child_session_id
            and record.get("state") == "active"
        ]
        assert len(active) == 1
        delegation = active[0]
        target = delegation["target_ref"]
        child.succeed_delegation(
            str(delegation["id"]),
            str(delegation["revision"]),
            summary="bounded task completed through the fake runner",
            result_refs=({"kind": str(target["kind"]), "id": str(target["id"])},),
            idempotency_key=f"j1-child-succeed-{delegation['id']}",
        )


def _profile_config(tmp_path: Path, tools: dict[str, Path]) -> Path:
    path = tmp_path / "work-runtime.yaml"
    provider = tools["provider"].as_posix()
    mcp = tools["mcp"].as_posix()
    git = tools["git"].as_posix()
    path.write_text(
        "profiles:\n"
        "  codex-builder:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        f"    git_executable: {git}\n"
        "    sandbox: workspace-write\n"
        "    trusted_workspace: true\n"
        "  codex-independent-reviewer:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        f"    git_executable: {git}\n"
        "    sandbox: read-only\n"
        "    trusted_workspace: true\n"
        "  claude-builder:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        f"    git_executable: {git}\n"
        "    permission_mode: acceptEdits\n"
        "    trusted_workspace: true\n"
        "  claude-independent-reviewer:\n"
        f"    executable: {provider}\n"
        f"    mcp_executable: {mcp}\n"
        f"    git_executable: {git}\n"
        "    permission_mode: dontAsk\n",
        encoding="utf-8",
    )
    return path


def _work_context(
    workspace: dict[str, Any], tmp_path: Path
) -> tuple[UIContext, ControlledRunner, Any]:
    tools = {
        "provider": _executable(tmp_path / "provider", "provider"),
        "mcp": _executable(tmp_path / "mcp", "mcp"),
        "git": _executable(tmp_path / "git", "git"),
    }
    profile_config = _profile_config(tmp_path, tools)
    manager = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = manager.start_session(
        stable_instance_id="j1-work-operator-window",
        principal="operator",
        client="codex",
        software="codex-desktop",
        role="operator",
    )
    profiles = default_profile_registry(
        codex_executable=tools["provider"].as_posix(),
        claude_executable=tools["provider"].as_posix(),
        mcp_executable=tools["mcp"].as_posix(),
        git_executable=tools["git"].as_posix(),
        trusted_workspace=True,
    )
    runner = ControlledRunner(workspace["repo"], workspace["state_root"])

    def runtime_factory(bound_manager: CommonsManager) -> DelegationRuntimeService:
        return DelegationRuntimeService(
            bound_manager,
            runner=runner,  # type: ignore[arg-type]
            profiles=profiles,
            catalog={
                "skills": [
                    {
                        "id": skill_id,
                        "title": skill_id,
                        "description": "packaged Agent Commons test skill",
                        "instruction": f"Use packaged skill {skill_id}.",
                    }
                    for skill_id in sorted(STARTER_PACK_ALLOWED_SKILL_REFS)
                ],
                "tools": [],
            },
        )

    context = UIContext(
        workspace["repo"],
        state_root=workspace["state_root"],
        writer_session_id=str(session["session_id"]),
        profile_config=profile_config,
        runtime_factory=runtime_factory,
    )
    return (
        context,
        runner,
        profiles,
    )


def _record_qualification(context: UIContext, profiles: Any, profile_id: str) -> None:
    profile = profiles.get(profile_id)
    ProviderQualificationStore(context.manager().paths.state_root).record(
        profile,
        workspace_root=context.repo,
        static_preflight=True,
        initialization_probe=True,
        behavioral_canary=True,
        provider_version="j1-fake-provider",
    )


def _create_task(
    client: TestClient,
    *,
    title: str,
    dependencies: tuple[str, ...] = (),
) -> dict[str, str]:
    response = client.post(
        "/api/tasks",
        json={
            "title": title,
            "description": f"Complete {title}",
            "acceptance_criteria": [f"{title} is verifiably complete"],
            "dependencies": list(dependencies),
            "idempotency_key": f"j1-create-{title}",
        },
        headers=authorized(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return {"id": str(payload["entity_ref"]["id"]), "revision": str(payload["revision"])}


def _request_review(client: TestClient, task_id: str, revision: str, key: str) -> dict[str, Any]:
    response = client.post(
        f"/api/tasks/{task_id}/review-request",
        json={"expected_revision": revision, "idempotency_key": key},
        headers=authorized(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _complete_review(
    workspace: dict[str, Any],
    *,
    chain: dict[str, Any],
    verdict: str,
    suffix: str,
) -> None:
    opener = CommonsManager(workspace["repo"], state_root=workspace["state_root"])
    session = opener.start_session(
        stable_instance_id=f"j1-reviewer-window-{suffix}",
        principal=f"independent-reviewer-{suffix}",
        client="codex",
        software="codex-desktop",
        role="independent-reviewer",
    )
    reviewer = CommonsManager(
        workspace["repo"],
        state_root=workspace["state_root"],
        session_id=str(session["session_id"]),
    )
    reviewer.complete_review(
        str(chain["review_id"]),
        str(chain["review_revision"]),
        target_revision=str(chain["task_revision"]),
        verdict=verdict,
        summary=f"{verdict} by independent reviewer",
        idempotency_key=f"j1-review-{suffix}",
    )


def _accept_reviewed_task(
    client: TestClient,
    workspace: dict[str, Any],
    task: dict[str, str],
    suffix: str,
) -> None:
    chain = _request_review(client, task["id"], task["revision"], f"j1-send-{suffix}")
    _complete_review(workspace, chain=chain, verdict="approved", suffix=suffix)
    accepted = client.post(
        f"/api/tasks/{task['id']}/accept",
        json={
            "expected_revision": chain["task_revision"],
            "summary": f"accepted prerequisite {suffix}",
            "idempotency_key": f"j1-accept-{suffix}",
        },
        headers=authorized(),
    )
    assert accepted.status_code == 200, accepted.text
    task["revision"] = str(accepted.json()["revision"])


def _task_record(context: UIContext, task_id: str) -> dict[str, Any]:
    return dict(context.manager().snapshot().tasks[task_id])


def _tracker(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/work/tracker", headers=authorized())
    assert response.status_code == 200, response.text
    return response.json()


def _tracker_task(snapshot: dict[str, Any], task_id: str) -> dict[str, Any]:
    found = [task for task in snapshot["tasks"] if task["task_id"] == task_id]
    assert len(found) == 1
    return found[0]


def _tracker_source(context: UIContext):
    def source(*, resume_after: int | None = None):
        del resume_after
        context.refresh_if_changed()
        sequence, graph = context.snapshot_frame()
        manager = context.manager()
        attempts = AttemptStore(manager.paths.state_root, read_only=True).list_attempts()
        return build_tracker_snapshot(
            manager.snapshot(),
            attempts,
            generated_at=str(graph["generated_at"]),
            sequence=sequence,
            graph=graph,
        )

    return source


def _drive_tracker_frame(source: Any, *, resume_after: int | None = None) -> bytes:
    async def run() -> bytes:
        generator = tracker_events(
            source,
            resume_after=resume_after,
            poll_seconds=0,
            heartbeat_seconds=0,
        )
        try:
            return await anext(generator)
        finally:
            await generator.aclose()

    frame = asyncio.run(run())
    assert len(frame) <= MAX_TRACKER_FRAME_BYTES
    return frame


def _frame_payload(frame: bytes) -> dict[str, Any]:
    return json.loads(frame.split(b"data: ", 1)[1])


def _assert_no_private_surface(workspace: dict[str, Any], *values: object) -> None:
    forbidden_keys = {
        "prompt",
        "transcript",
        "credentials",
        "operator_config",
        "raw_provider_output",
        "filesystem_path",
        "eta",
        "percentage",
        "progress_percentage",
        "cost",
        "token_count",
        "tokens",
    }

    def walk(value: object) -> list[str]:
        if isinstance(value, dict):
            found = [str(key).lower() for key in value]
            for item in value.values():
                found.extend(walk(item))
            return found
        if isinstance(value, list | tuple):
            found: list[str] = []
            for item in value:
                found.extend(walk(item))
            return found
        return []

    for value in values:
        assert forbidden_keys.isdisjoint(walk(value))
        rendered = json.dumps(value, sort_keys=True).lower()
        assert "raw provider secret" not in rendered
        assert "raw provider stderr" not in rendered
        assert str(workspace["repo"]).lower() not in rendered
        assert str(workspace["state_root"]).lower() not in rendered


def test_integrated_work_operator_journey_is_truthful_and_review_gated(
    workspace: dict[str, Any],
    tmp_path: Path,
) -> None:
    context, runner, profiles = _work_context(workspace, tmp_path)
    manager = context.manager()
    app = create_app(context, token="test-token", port=PORT, api_base="/api")

    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        missing = client.get("/api/work/provider-availability", headers=authorized())
        assert missing.status_code == 200
        missing_availability = missing.json()
        claude_missing = next(
            item for item in missing_availability if item["profile_id"] == "claude-builder"
        )
        assert claude_missing["refusal"]["code"] == "provider_qualification_required"
        assert claude_missing["launchable"] is False

        _record_qualification(context, profiles, "claude-builder")
        available = client.get("/api/work/provider-availability", headers=authorized())
        assert available.status_code == 200
        availability = available.json()
        claude_available = next(
            item for item in availability if item["profile_id"] == "claude-builder"
        )
        assert claude_available["launchable"] is True

        applied = client.post(
            "/api/work/starter-packs/starter.feature-delivery.mock/blueprints/"
            "feature-delivery/apply",
            json={"confirmed": True, "idempotency_key": "j1-starter-pack"},
            headers=authorized(),
        )
        assert applied.status_code == 200, applied.text
        template = next(
            role for role in applied.json()["roles"] if role["source_role_id"] == "implementer"
        )
        hired = client.post(
            "/api/agents",
            json={
                "name": "J1 implementer",
                "rationale": "execute only dependency-ready scoped tasks",
                "from_preset_id": template["agent_id"],
                "idempotency_key": "j1-hire-implementer",
            },
            headers=authorized(),
        )
        assert hired.status_code == 200, hired.text
        role_id = str(hired.json()["entity_ref"]["id"])
        role = manager.get_agent(role_id)
        assert role["template"] is False
        assert role["profile_id"] == "claude-builder"
        assert tuple(role["skills"]) == tuple(template["skills"])
        assert {"commons-start", "commons-coordinate", "commons-record"} <= set(role["skills"])

        unresolved = _create_task(client, title="Unresolved prerequisite")
        blocked = _create_task(
            client,
            title="Blocked dependent",
            dependencies=(unresolved["id"],),
        )
        prerequisite = _create_task(client, title="Accepted prerequisite")
        _accept_reviewed_task(client, workspace, prerequisite, "accepted-prerequisite")
        ready = _create_task(
            client,
            title="Ready dependent",
            dependencies=(prerequisite["id"],),
        )
        assert _task_record(context, blocked["id"])["dependencies"] == [unresolved["id"]]
        assert _task_record(context, ready["id"])["dependencies"] == [prerequisite["id"]]

        tracker = _tracker(client)
        blocked_task = _tracker_task(tracker, blocked["id"])
        ready_task = _tracker_task(tracker, ready["id"])
        assert blocked_task["readiness"] == "blocked"
        assert blocked_task["blocking_dependency_ids"] == [unresolved["id"]]
        assert ready_task["readiness"] == "ready"
        assert ready_task["blocking_dependency_ids"] == []

        refused_launch = client.post(
            "/api/delegations",
            json={
                "agent_id": role_id,
                "task_id": blocked["id"],
                "idempotency_key": "j1-blocked-launch",
            },
            headers=authorized(),
        )
        assert refused_launch.status_code == 409, refused_launch.text
        assert "unresolved dependencies" in refused_launch.json()["error"]["message"]
        assert manager.list_delegations() == []

        before_stale = _task_record(context, ready["id"])
        stale = client.post(
            f"/api/tasks/{ready['id']}/review-request",
            json={
                "expected_revision": "evt.00000000000000000000000000",
                "idempotency_key": "j1-stale-review",
            },
            headers=authorized(),
        )
        assert stale.status_code == 409, stale.text
        after_stale = _task_record(context, ready["id"])
        assert after_stale["state"] == before_stale["state"]
        assert after_stale["revision"] == before_stale["revision"]

        runner.reset("complete")
        launched = client.post(
            "/api/delegations",
            json={
                "agent_id": role_id,
                "task_id": ready["id"],
                "idempotency_key": "j1-ready-launch",
            },
            headers=authorized(),
        )
        assert launched.status_code == 200, launched.text
        assert runner.started.wait(timeout=10)

        source = _tracker_source(context)
        running_payload = _frame_payload(_drive_tracker_frame(source))
        running_run = next(run for run in running_payload["runs"] if run["task_id"] == ready["id"])
        assert running_run["phase"] == "running"

        runner.release.set()
        context.await_launches()
        succeeded_payload = _frame_payload(
            _drive_tracker_frame(source, resume_after=running_payload["sequence"])
        )
        succeeded_run = next(
            run for run in succeeded_payload["runs"] if run["task_id"] == ready["id"]
        )
        assert succeeded_run["phase"] == "succeeded"
        assert manager.get_delegation(launched.json()["delegation_id"])["state"] == "succeeded"
        assert runner.invocations
        stdin = runner.invocations[-1].stdin.decode("utf-8")
        assert "Provider-projected packaged skills" in stdin
        assert "commons-start" in stdin
        assert "commons-coordinate" in stdin
        assert "commons-record" in stdin

        reconnect_gap = _frame_payload(
            _drive_tracker_frame(source, resume_after=succeeded_payload["sequence"] + 10)
        )
        assert reconnect_gap["state"] == "error"
        assert reconnect_gap["freshness"]["resume_gap"] is True
        assert "tracker_sequence_regressed" in reconnect_gap["gaps"]

        current = _task_record(context, ready["id"])
        chain = _request_review(
            client,
            ready["id"],
            str(current.get("effective_revision") or current["revision"]),
            "j1-request-review",
        )
        review = client.get(
            f"/api/entities/review/{chain['review_id']}",
            headers=authorized(),
        )
        assert review.status_code == 200, review.text
        assert review.json()["record"]["target_revision"] == chain["task_revision"]
        premature_accept = client.post(
            f"/api/tasks/{ready['id']}/accept",
            json={
                "expected_revision": chain["task_revision"],
                "summary": "premature acceptance refused",
                "idempotency_key": "j1-premature-accept",
            },
            headers=authorized(),
        )
        assert premature_accept.status_code == 409, premature_accept.text

        _complete_review(workspace, chain=chain, verdict="changes_requested", suffix="changes")
        changes_tracker = _tracker(client)
        changes_task = _tracker_task(changes_tracker, ready["id"])
        assert changes_task["task_state"] == "review"
        assert changes_task["phase"] == "succeeded"
        review_attention = [
            item
            for item in changes_tracker["attention"]
            if item["kind"] == "review" and item["task_id"] == ready["id"]
        ]
        assert review_attention == [
            {
                "kind": "review",
                "item_id": chain["review_id"],
                "task_id": ready["id"],
                "reason_code": "changes_requested",
                "next_action": "revise_work",
            }
        ]
        rejected_accept = client.post(
            f"/api/tasks/{ready['id']}/accept",
            json={
                "expected_revision": chain["task_revision"],
                "summary": "changes-requested verdict cannot accept",
                "idempotency_key": "j1-rejected-accept",
            },
            headers=authorized(),
        )
        assert rejected_accept.status_code == 409, rejected_accept.text

        reopened = client.post(
            f"/api/tasks/{ready['id']}/reopen",
            json={
                "expected_revision": chain["task_revision"],
                "reason": "address the requested review changes",
                "idempotency_key": "j1-reopen-after-changes",
            },
            headers=authorized(),
        )
        assert reopened.status_code == 200, reopened.text
        second_chain = _request_review(
            client,
            ready["id"],
            str(reopened.json()["revision"]),
            "j1-second-review",
        )
        _complete_review(workspace, chain=second_chain, verdict="approved", suffix="approved")
        accepted = client.post(
            f"/api/tasks/{ready['id']}/accept",
            json={
                "expected_revision": second_chain["task_revision"],
                "summary": "accepted after canonical independent approval",
                "idempotency_key": "j1-final-accept",
            },
            headers=authorized(),
        )
        assert accepted.status_code == 200, accepted.text
        assert _task_record(context, ready["id"])["state"] == "accepted"

        runner.reset("no-terminal")
        invalid_task = _create_task(client, title="No terminal MCP task")
        invalid_launch = client.post(
            "/api/delegations",
            json={
                "agent_id": role_id,
                "task_id": invalid_task["id"],
                "idempotency_key": "j1-no-terminal-launch",
            },
            headers=authorized(),
        )
        assert invalid_launch.status_code == 200, invalid_launch.text
        assert runner.started.wait(timeout=10)
        runner.release.set()
        context.await_launches()
        invalid_delegation = manager.get_delegation(invalid_launch.json()["delegation_id"])
        assert invalid_delegation["state"] == "needs_operator"
        assert invalid_delegation["reason_code"] == "invalid_result"
        invalid_tracker = _tracker(client)
        invalid_tracker_task = _tracker_task(invalid_tracker, invalid_task["id"])
        assert invalid_tracker_task["phase"] == "needs_operator"

        runs = client.get("/api/runs", headers=authorized())
        assert runs.status_code == 200
        _assert_no_private_surface(
            workspace,
            missing_availability,
            availability,
            applied.json(),
            hired.json(),
            tracker,
            refused_launch.json(),
            stale.json(),
            launched.json(),
            running_payload,
            succeeded_payload,
            reconnect_gap,
            chain,
            changes_tracker,
            accepted.json(),
            invalid_tracker,
            runs.json(),
        )
