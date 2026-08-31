"""Credential-free Claude-shaped provider used by the stdio contract test."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_HELP_FLAGS = (
    "--print --verbose --output-format --permission-mode "
    "--no-session-persistence --disable-slash-commands --setting-sources "
    "--mcp-config --strict-mcp-config --allowed-tools --disallowed-tools "
    "--tools --max-budget-usd"
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


async def _run() -> None:
    arguments = sys.argv[1:]
    if "--tools" in arguments and arguments[arguments.index("--tools") + 1] != "ToolSearch":
        raise RuntimeError("reviewer tool discovery is disabled or over-broad")
    config = json.loads(arguments[arguments.index("--mcp-config") + 1])
    body = config["mcpServers"]["agent-commons"]
    mcp_arguments = list(body["args"])
    delegation_id = mcp_arguments[mcp_arguments.index("--delegation-id") + 1]
    # Claude launches configured MCP children with the provider environment.  The
    # generic SDK client intentionally sanitizes its default environment, so the
    # fixture forwards it explicitly to model that provider boundary accurately.
    parameters = StdioServerParameters(
        command=body["command"],
        args=mcp_arguments,
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            allowed_tools = set(arguments[arguments.index("--allowed-tools") + 1].split(","))
            expected_allowed_tools = {f"mcp__agent-commons__{name}" for name in names}
            if allowed_tools != expected_allowed_tools:
                raise RuntimeError("reviewer allowed-tools projection is not exact")
            required = {
                "commons_show_delegation",
                "commons_repo_files",
                "commons_repo_read",
                "commons_list_tasks",
                "commons_read_artifact",
            }
            if not required.issubset(names):
                raise RuntimeError("worker MCP tool contract is incomplete")

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
                            "commons_read_artifact", {"artifact_id": artifact_ref["id"]}
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
                                "Hermetic provider inspected the exact scoped source "
                                "over real MCP stdio."
                            ),
                        },
                    )
                )
                return
            if "commons_succeed_delegation" not in names:
                raise RuntimeError("worker MCP terminal tool contract is incomplete")
            if "commons_record_verification" in names:
                delegation = _value(
                    await session.call_tool(
                        "commons_show_delegation",
                        {"delegation_id": delegation_id},
                    )
                )
                target_ref = delegation["target_ref"]
                tasks = _value(await session.call_tool("commons_list_tasks", {"state": None}))
                target_task = next(item for item in tasks if item["id"] == target_ref["id"])
                evidence_refs = [
                    f"{item['kind']}:{item['id']}" for item in target_task["artifact_refs"]
                ]
                if not evidence_refs:
                    raise RuntimeError("verification canary has no immutable evidence")
                verification = _value(
                    await session.call_tool(
                        "commons_record_verification",
                        {
                            "target_ref": f"{target_ref['kind']}:{target_ref['id']}",
                            "target_revision": delegation["target_revision"],
                            "claim": "The isolated source returns the expected integer.",
                            "method": "Read the exact scoped source over real MCP stdio.",
                            "outcome": "passed",
                            "evidence_refs": evidence_refs,
                            "idempotency_key": "hermetic-provider-verification-record",
                        },
                    )
                )
                result_refs = [f"verification:{verification['entity_ref']['id']}"]
            else:
                delegation = _value(
                    await session.call_tool(
                        "commons_show_delegation",
                        {"delegation_id": delegation_id},
                    )
                )
                target = delegation["target_ref"]
                result_refs = [f"{target['kind']}:{target['id']}"]
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
                        "summary": "Hermetic real-stdio review completed.",
                        "result_refs": result_refs,
                        "idempotency_key": "hermetic-provider-delegation-succeed",
                    },
                )
            )


if __name__ == "__main__":
    if sys.argv[1:] == ["auth", "status", "--json"]:
        print(json.dumps({"loggedIn": True}))
    elif sys.argv[1:] == ["mcp", "list"]:
        print("No MCP servers configured")
    elif sys.argv[1:] == ["auth", "login", "--claudeai"]:
        print("provider-owned browser login completed")
    elif "--version" in sys.argv:
        print("0.0.0 (Claude Code)")
    elif "--help" in sys.argv:
        print(_HELP_FLAGS)
    else:
        asyncio.run(_run())
        print(json.dumps({"type": "result", "result": "canonical outcome recorded"}))
