from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_commons.services import CommonsManager
from tests.ui.conftest import authorized
from tests.ui.test_launch import _client, _launch_workspace


def test_ready_ui_task_runs_through_real_worker_report_handoff(workspace):
    fixture = _launch_workspace(workspace)
    manager = fixture["manager"]
    task_id = fixture["task_id"]
    initial = manager.snapshot().tasks[task_id]
    assert initial["state"] == "ready"
    handed_off = {}

    def worker_protocol(child_session_id):
        child = CommonsManager(
            workspace["repo"], state_root=workspace["state_root"], session_id=child_session_id
        )
        snapshot = child.snapshot()
        task = snapshot.tasks[task_id]
        run = next(
            item
            for item in snapshot.delegations.values()
            if item.get("child_session_id") == child_session_id
        )
        assert task["state"] == "active"
        assert run["target_revision"] == task["revision"] != initial["revision"]

        async def handoff():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "agent_commons.mcp.server",
                    "--repo",
                    str(workspace["repo"]),
                    "--state-root",
                    str(workspace["state_root"]),
                    "--session-id",
                    child_session_id,
                    "--delegation-id",
                    run["id"],
                    "--git-executable",
                    "/usr/bin/git",
                ],
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()

                    async def call(name, **arguments):
                        result = await session.call_tool(name, arguments)
                        assert not result.isError, result
                        return result.structuredContent

                    report = await call(
                        "commons_publish_text_result",
                        title="UI report",
                        summary="Ready work implemented",
                        checks=["Fixture passed"],
                        content="Retained report",
                        expected_task_revision=task["revision"],
                        idempotency_key="ui-report-v1",
                    )
                    completed = await call(
                        "commons_complete_task_result",
                        expected_task_revision=task["revision"],
                        summary="Done",
                        artifact_ids=[report["artifact_id"]],
                        idempotency_key="ui-complete-v1",
                    )
                    submitted = await call(
                        "commons_submit_task_result",
                        expected_task_revision=completed["revision"],
                        summary="Review required",
                        artifact_ids=[report["artifact_id"]],
                        idempotency_key="ui-submit-v1",
                    )
                    await call(
                        "commons_finalize_task_result",
                        expected_task_revision=submitted["revision"],
                        summary="Handed off",
                        idempotency_key="ui-finalize-v1",
                    )
                    handed_off.update(report=report, submitted=submitted)

        asyncio.run(asyncio.wait_for(handoff(), timeout=45))

    fixture["runner"].after_start = worker_protocol
    body = {
        "agent_id": fixture["role_id"],
        "task_id": task_id,
        "idempotency_key": "ready-ui-launch-v1",
    }
    with _client(fixture["context"]) as client:
        response = client.post("/api/delegations", json=body, headers=authorized())
        assert response.status_code == 200, response.text
        fixture["context"].await_launches()
        assert handed_off
        run = manager.get_delegation(response.json()["delegation_id"])
        assert run["state"] == "succeeded"
        assert manager.snapshot().tasks[task_id]["state"] == "review"
        assert not manager.snapshot().reviews
        replay = client.post("/api/delegations", json=body, headers=authorized())
        assert replay.status_code == 200, replay.text
        fixture["context"].await_launches()
        assert replay.json()["delegation_id"] == run["id"]
        assert fixture["runner"].calls == 1
