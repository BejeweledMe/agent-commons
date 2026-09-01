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


def _assert_commons_start_projection(prompt: str) -> None:
    if "Provider-projected packaged skills" not in prompt or "name: commons-start" not in prompt:
        raise RuntimeError("commons-start skill projection did not reach Codex stdin")


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
            values[key.removeprefix(_CONFIG_PREFIX)] = tomllib.loads(f"value = {raw_value}")[
                "value"
            ]
    return values


async def _run() -> None:
    _assert_commons_start_projection(sys.stdin.read())
    body = _config(sys.argv[1:])
    if body.get("required") is not True:
        raise RuntimeError("agent-commons MCP server is not required")
    mcp_arguments = list(body["args"])
    delegation_id = mcp_arguments[mcp_arguments.index("--delegation-id") + 1]
    enabled_tools = set(body["enabled_tools"])
    required_tools = {
        "commons_show_delegation",
        "commons_repo_files",
        "commons_repo_read",
        "commons_list_tasks",
        "commons_read_artifact",
    }
    if not required_tools.issubset(enabled_tools):
        raise RuntimeError("Codex enabled_tools contract is incomplete")
    # Real Codex does not forward the provider process environment verbatim to
    # configured MCP children.  Keep this fixture honest: delegated identity
    # must travel in the generated MCP argv, not only in the broker env.
    mcp_env = dict(os.environ)
    mcp_env.pop("AGENT_COMMONS_SESSION_ID", None)
    parameters = StdioServerParameters(
        command=body["command"],
        args=mcp_arguments,
        env=mcp_env,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if not enabled_tools.issubset(names):
                raise RuntimeError("worker MCP tool contract is incomplete")

            delegation = _value(
                await session.call_tool(
                    "commons_show_delegation",
                    {"delegation_id": delegation_id},
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

            if "commons_finalize_review" in enabled_tools:
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
                                "Hermetic Codex provider inspected the exact scoped source "
                                "over real MCP stdio."
                            ),
                        },
                    )
                )
                return
            elif "commons_record_verification" in enabled_tools:
                target = delegation["target_ref"]
                tasks = _value(await session.call_tool("commons_list_tasks", {"state": None}))
                target_task = next(item for item in tasks if item["id"] == target["id"])
                evidence_refs = [
                    f"{item['kind']}:{item['id']}" for item in target_task["artifact_refs"]
                ]
                if not evidence_refs:
                    raise RuntimeError("verification canary has no immutable evidence")
                verification = _value(
                    await session.call_tool(
                        "commons_record_verification",
                        {
                            "target_ref": f"{target['kind']}:{target['id']}",
                            "target_revision": delegation["target_revision"],
                            "claim": "The isolated source returns the expected integer.",
                            "method": "Read the exact scoped source over real MCP stdio.",
                            "outcome": "passed",
                            "evidence_refs": evidence_refs,
                            "idempotency_key": "hermetic-codex-verification-record",
                        },
                    )
                )
                result_refs = [f"verification:{verification['entity_ref']['id']}"]
            else:
                target = delegation["target_ref"]
                result_refs = [f"{target['kind']}:{target['id']}"]
            if "commons_succeed_delegation" not in enabled_tools:
                raise RuntimeError("Codex terminal tool contract is incomplete")
            _value(
                await session.call_tool(
                    "commons_succeed_delegation",
                    {
                        "delegation_id": delegation["id"],
                        "expected_revision": delegation["revision"],
                        "summary": "Hermetic Codex real-stdio work completed.",
                        "result_refs": result_refs,
                        "idempotency_key": "hermetic-codex-delegation-succeed",
                    },
                )
            )


if __name__ == "__main__":
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using hermetic fixture", file=sys.stderr)
    elif sys.argv[1:] == ["login"]:
        print("provider-owned browser login completed")
    elif sys.argv[1:] == ["app-server", "--stdio"]:
        # Match the real Codex initialization contract: the app server starts
        # without model work and exits cleanly when its stdin is already EOF.
        pass
    elif "--version" in sys.argv:
        print("codex-cli 0.0.0")
    elif "--help" in sys.argv:
        print(_EXEC_HELP_FLAGS if "exec" in sys.argv else _ROOT_HELP_FLAGS)
    else:
        asyncio.run(_run())
        print(json.dumps({"type": "turn.completed", "usage": {}}))
