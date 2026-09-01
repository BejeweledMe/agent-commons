"""Workspace-level commands for the Agent Commons CLI."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from importlib import metadata

import click
import yaml

from agent_commons import __version__
from agent_commons.config import CommonsPaths
from agent_commons.core.ids import is_typed_id
from agent_commons.runtime import ATTEMPT_SCHEMA, REQUEST_SCHEMA
from agent_commons.runtime.source_contract import agent_commons_source_sha256
from agent_commons.services import CommonsManager

from ._shared import CLIState


def _installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@click.command("init")
@click.option("--integration", multiple=True, type=click.Choice(("codex", "claude", "grok")))
@click.option("--workspace-name")
@click.option("--replace-onboarding", is_flag=True)
@click.option("--replace-skills", is_flag=True)
@click.pass_obj
def init_command(
    state: CLIState,
    integration: tuple[str, ...],
    workspace_name: str | None,
    replace_onboarding: bool,
    replace_skills: bool,
) -> None:
    """Initialize or safely update a workspace and client integrations."""

    if state.read_only:
        from agent_commons.errors import ValidationError

        raise ValidationError("init is unavailable in read-only mode")
    selected = integration or ("codex", "claude", "grok")
    state.emit(
        CommonsManager.initialize(
            state.repo,
            integrations=selected,
            workspace_name=workspace_name,
            replace_onboarding=replace_onboarding,
            replace_skills=replace_skills,
        )
    )


@click.command("search")
@click.argument("query")
@click.option("--limit", type=int, default=25, show_default=True)
@click.option("--kind", "subject_kind", help="Restrict to one entity kind, e.g. agent or task.")
@click.pass_obj
def search_command(state: CLIState, query: str, limit: int, subject_kind: str | None) -> None:
    """Search canonical history by free text.

    Reads the rebuildable projection, which is synchronized first.  Results
    point at event IDs; inspect them canonically before relying on one.
    """

    state.emit(state.manager().search_history(query, limit=limit, subject_kind=subject_kind))


@click.command("support")
@click.option("--show-paths", is_flag=True, help="Include resolved local paths explicitly.")
@click.pass_obj
def support_command(state: CLIState, show_paths: bool) -> None:
    """Report secret-free component and state availability for support requests."""

    paths = CommonsPaths.for_workspace(
        state.repo,
        state_root=state.state_root,
        state_base=state.state_base,
        state_source=state.state_source,
    )
    workspace_id: str | None = None
    config_path = paths.commons_root / "workspace.yaml"
    if config_path.is_file() and not config_path.is_symlink():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            config = None
        if isinstance(config, dict) and is_typed_id(config.get("workspace_id"), "workspace"):
            workspace_id = config["workspace_id"]
            paths = paths.for_workspace_id(workspace_id)
    ownership = paths.ownership_report(workspace_id) if workspace_id is not None else None
    report = {
        "schema": "agent_commons.support.v1",
        "agent_commons_version": __version__,
        "agent_commons_source_sha256": agent_commons_source_sha256(),
        "workspace_schema": "agent-commons.workspace.v1",
        "runtime_request_schema": REQUEST_SCHEMA,
        "runtime_attempt_schema": ATTEMPT_SCHEMA,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": sys.platform,
        "supported_platform": True,
        "supported_operating_systems": ["darwin", "linux"],
        "core_release_stage": "alpha",
        "broker_release_stage": "experimental",
        "canonical_workspace_available": paths.commons_root.is_dir(),
        "state_root_explicit": paths.state_mode == "exact",
        "state_config_source": paths.state_source,
        "state_mode": paths.state_mode,
        "workspace_id": workspace_id,
        "state_owner_status": ownership["status"] if ownership else "workspace-unavailable",
        "state_owner_match": ownership["match"] if ownership else None,
        "state_root_exists": paths.state_root.is_dir(),
        "state_root_readable": paths.state_root.is_dir() and os.access(paths.state_root, os.R_OK),
        "state_root_writable": paths.state_root.is_dir() and os.access(paths.state_root, os.W_OK),
        "mcp_extra_available": _module_available("mcp.server.fastmcp"),
        "mcp_package_version": _installed_version("mcp"),
        "opentelemetry_api_available": _module_available("opentelemetry.trace"),
        "opentelemetry_api_version": _installed_version("opentelemetry-api"),
        "read_only": state.read_only,
    }
    if show_paths:
        report["resolved_repo"] = str(paths.repo_root)
        report["resolved_commons_root"] = str(paths.commons_root)
        report["resolved_state_root"] = str(paths.state_root)
        report["resolved_state_base"] = str(paths.state_base) if paths.state_base else None
    state.emit(report)


@click.command("doctor")
@click.pass_context
def doctor_command(ctx: click.Context) -> None:
    """Validate history, receipts, manifests, projections, coordination, and index."""

    state: CLIState = ctx.obj
    report = state.manager().doctor()
    state.emit(report)
    if not report["ok"]:
        ctx.exit(2)
