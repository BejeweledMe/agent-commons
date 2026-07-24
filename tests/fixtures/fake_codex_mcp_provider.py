"""Credential-free Codex-shaped provider used by stdio contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_ROOT_HELP_FLAGS = "--ask-for-approval --sandbox"
_EXEC_HELP_FLAGS = "--config --ignore-user-config --strict-config --json"
_CONFIG_PREFIX = "mcp_servers.agent-commons."


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


def _config(arguments: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for index, argument in enumerate(arguments):
        if argument not in {"-c", "--config"}:
            continue
        key, separator, raw_value = arguments[index + 1].partition("=")
        if separator and key.startswith(_CONFIG_PREFIX):
            values[key.removeprefix(_CONFIG_PREFIX)] = tomllib.loads(
                f"value = {raw_value}"
            )["value"]
    return values


async def _run() -> None:
    body = _config(sys.argv[1:])
    if body.get("required") is not True:
        raise RuntimeError("agent-commons MCP server is not required")
    mcp_arguments = list(body["args"])
    delegation_id = mcp_arguments[mcp_arguments.index("--delegation-id") + 1]
    enabled_tools = set(body["enabled_tools"])
    required_tools = {
        "commons_list_reviews",
        "commons_show_review",
        "commons_show_delegation",
        "commons_workspace_files",
        "commons_workspace_read",
        "commons_complete_review",
        "commons_succeed_delegation",
    }
    if not required_tools.issubset(enabled_tools):
        raise RuntimeError("Codex enabled_tools contract is incomplete")
    parameters = StdioServerParameters(
        command=body["command"],
        args=mcp_arguments,
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if not enabled_tools.issubset(names):
                raise RuntimeError("worker MCP tool contract is incomplete")

            reviews = _value(
                await session.call_tool("commons_list_reviews", {"state": "requested"})
            )
            if len(reviews) != 1:
                raise RuntimeError("worker MCP did not expose exactly one review")
            review = _value(
                await session.call_tool(
                    "commons_show_review",
                    {"review_id": reviews[0]["id"]},
                )
            )
            files = _value(
                await session.call_tool(
                    "commons_workspace_files",
                    {"prefix": "src", "max_items": 50},
                )
            )
            source = next(item for item in files if item["path"].endswith(".py"))
            read = _value(
                await session.call_tool(
                    "commons_workspace_read",
                    {"path": source["path"], "expected_sha256": source["sha256"]},
                )
            )
            if "return 42" not in read["content"]:
                raise RuntimeError("scoped source read returned unexpected content")

            _value(
                await session.call_tool(
                    "commons_complete_review",
                    {
                        "review_id": review["id"],
                        "expected_revision": review["revision"],
                        "target_revision": review["target_revision"],
                        "verdict": "approved",
                        "summary": (
                            "Hermetic Codex provider inspected the exact scoped source "
                            "over real MCP stdio."
                        ),
                        "idempotency_key": "hermetic-codex-review-complete",
                        "evidence_refs": None,
                    },
                )
            )
            delegation = _value(
                await session.call_tool(
                    "commons_show_delegation",
                    {"delegation_id": delegation_id},
                )
            )
            _value(
                await session.call_tool(
                    "commons_succeed_delegation",
                    {
                        "delegation_id": delegation["id"],
                        "expected_revision": delegation["revision"],
                        "summary": "Hermetic Codex real-stdio review completed.",
                        "result_refs": [f"review:{review['id']}"],
                        "idempotency_key": "hermetic-codex-delegation-succeed",
                    },
                )
            )


if __name__ == "__main__":
    if "--version" in sys.argv:
        print("codex-cli 0.0.0")
    elif "--help" in sys.argv:
        print(_EXEC_HELP_FLAGS if "exec" in sys.argv else _ROOT_HELP_FLAGS)
    else:
        asyncio.run(_run())
        print(json.dumps({"type": "turn.completed", "usage": {}}))
