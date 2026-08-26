"""Console entry point for the optional local Agent Commons MCP server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from agent_commons import __version__
from agent_commons.errors import CommonsError, ConfigurationError
from agent_commons.runtime import resolve_trusted_executable
from agent_commons.runtime.source_contract import agent_commons_source_sha256
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import (
    DelegationRuntimeService,
    load_runtime_configuration,
    telemetry_sink,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-commons-mcp",
        description="Run the optional local Agent Commons MCP server over stdio.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--state-root",
        type=Path,
        default=os.environ.get("AGENT_COMMONS_STATE_ROOT"),
        help="Explicit operator-authorized operational state directory.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate imports and the root tool catalog without opening stdio or writing state.",
    )
    parser.add_argument(
        "--stdio-preflight-purpose",
        choices=("implementation", "independent_review", "verification"),
        help="Run a read-only stdio handshake exposing one worker tool catalog.",
    )
    parser.add_argument(
        "--session-id",
        default=os.environ.get("AGENT_COMMONS_SESSION_ID"),
        help="Active writer session; defaults to AGENT_COMMONS_SESSION_ID.",
    )
    parser.add_argument(
        "--delegation-id",
        default=os.environ.get("AGENT_COMMONS_DELEGATION_ID"),
        help="Broker-bound delegation; defaults to AGENT_COMMONS_DELEGATION_ID.",
    )
    parser.add_argument(
        "--git-executable",
        default="/usr/bin/git",
        help="Operator-selected trusted Git executable for scoped workspace reads.",
    )
    parser.add_argument(
        "--enable-runtime",
        action="store_true",
        help="Expose bounded broker run/status/reconcile tools to this MCP client.",
    )
    parser.add_argument(
        "--disable-controls",
        action="store_true",
        help="Hide parent guidance/checkpoint control tools from this MCP client.",
    )
    parser.add_argument(
        "--profile-config",
        type=Path,
        help="Operator-owned strict YAML profile configuration.",
    )
    parser.add_argument(
        "--telemetry",
        choices=("none", "local", "otel"),
        default="none",
        help="Optional metadata-only runtime telemetry sink.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console entry point.  Stdout is reserved exclusively for MCP frames."""

    # Server re-exports this entry point, so importing it at module scope
    # would create a cycle.
    from agent_commons.mcp.server import (
        IMPLEMENTATION_WORKER_TOOL_NAMES,
        INDEPENDENT_REVIEW_WORKER_TOOL_NAMES,
        VERIFICATION_WORKER_TOOL_NAMES,
        build_server,
    )

    arguments = _parser().parse_args(argv)
    try:
        manager = CommonsManager(
            arguments.repo.expanduser().resolve(),
            session_id=arguments.session_id,
            state_root=arguments.state_root,
            read_only=arguments.preflight or arguments.stdio_preflight_purpose is not None,
        )
        if arguments.preflight:
            git = resolve_trusted_executable(
                arguments.git_executable,
                workspace_root=manager.repo_root,
            )
            server = build_server(
                arguments.repo.expanduser().resolve(),
                manager=manager,
                git_executable=git,
                enable_controls=not arguments.disable_controls,
            )
            if not hasattr(server, "list_tools"):
                raise ConfigurationError("FastMCP server does not expose its tool catalog")
            tools = asyncio.run(server.list_tools())  # type: ignore[attr-defined]
            names = sorted(tool.name for tool in tools)
            worker_catalogs = {
                "implementation": sorted(IMPLEMENTATION_WORKER_TOOL_NAMES),
                "independent_review": sorted(INDEPENDENT_REVIEW_WORKER_TOOL_NAMES),
                "verification": sorted(VERIFICATION_WORKER_TOOL_NAMES),
            }
            body = {
                "schema": "agent_commons.mcp_preflight.v2",
                "agent_commons_version": __version__,
                "agent_commons_source_sha256": agent_commons_source_sha256(),
                "tool_count": len(names),
                "tool_catalog_sha256": hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
                "worker_catalogs": {
                    purpose: {
                        "tool_names": catalog,
                        "tool_catalog_sha256": hashlib.sha256(
                            "\n".join(catalog).encode("utf-8")
                        ).hexdigest(),
                    }
                    for purpose, catalog in worker_catalogs.items()
                },
            }
            print(json.dumps(body, sort_keys=True, separators=(",", ":")))
            return 0
        runtime = None
        if arguments.enable_runtime:
            runtime_config = load_runtime_configuration(
                arguments.profile_config,
                workspace_root=manager.repo_root,
            )
            runtime = DelegationRuntimeService(
                manager,
                profiles=runtime_config.profiles,
                operator_limits=runtime_config.limits,
                catalog=runtime_config.catalog,
                telemetry=telemetry_sink(arguments.telemetry, manager),
            )
        server = build_server(
            arguments.repo.expanduser().resolve(),
            session_id=arguments.session_id,
            manager=manager,
            runtime=runtime,
            delegation_id=arguments.delegation_id,
            catalog_only_purpose=arguments.stdio_preflight_purpose,
            git_executable=arguments.git_executable,
            enable_controls=not arguments.disable_controls,
        )
        server.run(transport="stdio")
    except (CommonsError, FileNotFoundError) as exc:
        print(f"agent-commons-mcp: {exc}", file=sys.stderr)
        return 2
    return 0
