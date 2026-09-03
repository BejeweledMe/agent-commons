"""Credential-free Grok-shaped provider used by stdio contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_HELP_FLAGS = (
    "--single --cwd --output-format --always-approve --no-alt-screen --max-turns "
    "--model --sandbox --allow --tools --disallowed-tools --no-plan --no-subagents "
    "--disable-web-search --rules"
)


def _value(result: Any) -> Any:
    if getattr(result, "isError", False):
        details = " ".join(
            str(getattr(block, "text", "")) for block in getattr(result, "content", ())
        )
        raise RuntimeError(f"MCP tool returned an error: {details}")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    for block in getattr(result, "content", ()):
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError("MCP tool returned no structured value")


def _prompt(arguments: list[str]) -> str:
    if "-p" not in arguments:
        raise RuntimeError("Grok prompt argument is missing")
    return arguments[arguments.index("-p") + 1]


def _assert_commons_start_projection(prompt: str) -> None:
    if "Provider-projected packaged skills" not in prompt or "name: commons-start" not in prompt:
        raise RuntimeError("commons-start skill projection did not reach Grok prompt argv")


def _mcp_parameters() -> StdioServerParameters:
    command = os.environ["AGENT_COMMONS_GROK_MCP_COMMAND"]
    repo = Path(os.environ["AGENT_COMMONS_REPO_ROOT"]).resolve()
    args = [
        "--repo",
        str(repo),
        "--state-root",
        os.environ["AGENT_COMMONS_STATE_ROOT"],
        "--delegation-id",
        os.environ["AGENT_COMMONS_DELEGATION_ID"],
        "--session-id",
        os.environ["AGENT_COMMONS_SESSION_ID"],
        "--git-executable",
        os.environ["AGENT_COMMONS_GIT_EXECUTABLE"],
    ]
    return StdioServerParameters(command=command, args=args, env=dict(os.environ))


async def _run() -> None:
    arguments = sys.argv[1:]
    _assert_commons_start_projection(_prompt(arguments))
    if not any(item == "MCPTool(agent-commons__commons_show_delegation)" for item in arguments):
        raise RuntimeError("Grok MCP allowlist is missing the delegation read tool")
    async with stdio_client(_mcp_parameters()) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            required = {
                "commons_show_delegation",
                "commons_repo_files",
                "commons_repo_read",
            }
            if not required.issubset(names):
                raise RuntimeError("Grok worker MCP tool contract is incomplete")
            delegation = _value(
                await session.call_tool(
                    "commons_show_delegation",
                    {"delegation_id": os.environ["AGENT_COMMONS_DELEGATION_ID"]},
                )
            )
            files = _value(
                await session.call_tool(
                    "commons_repo_files",
                    {"prefix": "src", "max_items": 50},
                )
            )
            source = next(item for item in files if item["path"].endswith(".py"))
            read = _value(
                await session.call_tool(
                    "commons_repo_read",
                    {"path": source["path"], "expected_sha256": source["sha256"]},
                )
            )
            if "return 42" not in read["content"]:
                raise RuntimeError("scoped source read returned unexpected content")
            if "commons_finalize_review" in names:
                reviews = _value(
                    await session.call_tool("commons_list_reviews", {"state": "requested"})
                )
                if len(reviews) != 1:
                    raise RuntimeError("worker MCP did not expose exactly one review")
                _value(
                    await session.call_tool(
                        "commons_show_review",
                        {"review_id": reviews[0]["id"]},
                    )
                )
                tasks = _value(await session.call_tool("commons_list_tasks", {"state": None}))
                for artifact_ref in tasks[0].get("artifact_refs", []):
                    registered = _value(
                        await session.call_tool(
                            "commons_read_artifact",
                            {"artifact_id": artifact_ref["id"]},
                        )
                    )
                    if "return 42" not in registered["content"]:
                        raise RuntimeError("registered source read returned unexpected content")
                _value(
                    await session.call_tool(
                        "commons_finalize_review",
                        {
                            "verdict": "approved",
                            "summary": (
                                "Hermetic Grok provider inspected the exact scoped source "
                                "over real MCP stdio."
                            ),
                        },
                    )
                )
                return
            if "commons_succeed_delegation" not in names:
                raise RuntimeError("Grok terminal tool contract is incomplete")
            target = delegation["target_ref"]
            _value(
                await session.call_tool(
                    "commons_succeed_delegation",
                    {
                        "delegation_id": delegation["id"],
                        "expected_revision": delegation["revision"],
                        "summary": "Hermetic Grok real-stdio work completed.",
                        "result_refs": [f"{target['kind']}:{target['id']}"],
                        "idempotency_key": "hermetic-grok-delegation-succeed",
                    },
                )
            )


if __name__ == "__main__":
    if "--version" in sys.argv:
        print("grok 0.0.0")
    elif sys.argv[1:] == ["models"]:
        print("You are logged in with hermetic-test-account")
        print("Available models:")
        print("- canary-model")
    elif sys.argv[1:] == ["inspect", "--json"]:
        print(
            json.dumps(
                {
                    "projectTrusted": True,
                    "mcpServers": [{"name": "agent-commons"}],
                    "hooks": [],
                    "plugins": [],
                    "lspServers": [],
                    "mcpConfigProblems": [],
                }
            )
        )
    elif "--help" in sys.argv:
        print(_HELP_FLAGS)
    else:
        asyncio.run(_run())
        print(json.dumps({"type": "turn.completed"}))
