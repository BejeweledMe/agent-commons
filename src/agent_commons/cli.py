"""Click command line interface for Agent Commons."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shlex
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

import click
import yaml
from click.core import ParameterSource

from agent_commons import __version__
from agent_commons.config import CommonsPaths
from agent_commons.core.ids import is_typed_id
from agent_commons.core.refs import parse_ref
from agent_commons.errors import CommonsError, ConfigurationError, ValidationError
from agent_commons.platform_support import require_supported_platform
from agent_commons.runtime import (
    ATTEMPT_SCHEMA,
    REQUEST_SCHEMA,
    AttemptStore,
    BuiltinProfileId,
    TerminalToolAuditStore,
    error_safe_next_actions,
    preflight_profile,
)
from agent_commons.runtime.source_contract import agent_commons_source_sha256
from agent_commons.services import CommonsManager
from agent_commons.services.delegation_runtime import (
    DelegationRuntimeService,
    load_profile_registry,
    load_runtime_configuration,
    profile_summaries,
    telemetry_sink,
)
from agent_commons.services.provider_canary import (
    run_claude_compatibility_canary,
    run_codex_compatibility_canary,
)
from agent_commons.ui import STARTED_SCHEMA


class CommonsGroup(click.Group):
    """Render domain failures as concise, non-traceback CLI errors."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except CommonsError as exc:
            return self._render_error(ctx, exc)
        except FileNotFoundError as exc:
            return self._render_error(ctx, exc)

    @staticmethod
    def _render_error(ctx: click.Context, exc: Exception) -> Any:
        state = ctx.obj
        safe_actions = list(getattr(exc, "safe_next_actions", error_safe_next_actions(exc)))
        if isinstance(state, CLIState) and state.json_output:
            error: dict[str, Any] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "safe_next_actions": safe_actions,
            }
            if code := getattr(exc, "code", None):
                error["code"] = code
            if details := getattr(exc, "details", None):
                error["details"] = details
            state.emit(
                {
                    "ok": False,
                    "error": error,
                }
            )
            ctx.exit(1)
        actions = "\n".join(f"  - {action}" for action in safe_actions)
        raise click.ClickException(f"{exc}\nSafe next actions:\n{actions}") from exc


@dataclass
class CLIState:
    repo: Path
    session_id: str | None
    json_output: bool
    state_root: Path | None
    state_base: Path | None
    state_source: str
    read_only: bool

    def manager(self) -> CommonsManager:
        return CommonsManager(
            self.repo,
            session_id=self.session_id,
            state_root=self.state_root,
            state_base=self.state_base,
            state_source=self.state_source,
            read_only=self.read_only,
        )

    def emit(self, value: Any) -> None:
        if self.json_output:
            click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return
        click.echo(yaml.safe_dump(value, allow_unicode=True, sort_keys=False).rstrip())


def _json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return parsed


_REFERENCE_KINDS = (
    "artifact",
    "decision",
    "delegation",
    "event",
    "finding",
    "handoff",
    "manifest",
    "objective",
    "review",
    "task",
    "thread",
    "verification",
)


def _input_error(
    message: str, *, code: str, field: str, allowed_kinds: tuple[str, ...]
) -> ValidationError:
    error = ValidationError(message)
    error.code = code  # type: ignore[attr-defined]
    error.details = {  # type: ignore[attr-defined]
        "field": field,
        "allowed_kinds": list(allowed_kinds),
        "example": f"{allowed_kinds[0]}:<id>",
    }
    return error


def _ref(
    value: str,
    *,
    field: str,
    allowed_kinds: tuple[str, ...] = _REFERENCE_KINDS,
) -> dict[str, str]:
    try:
        parsed = parse_ref(value)
    except ValidationError as exc:
        raise _input_error(
            f"{field} must use '<kind>:<id>' syntax; example: {allowed_kinds[0]}:<id>",
            code="invalid_typed_ref",
            field=field,
            allowed_kinds=allowed_kinds,
        ) from exc
    if parsed.kind not in allowed_kinds:
        raise _input_error(
            f"{field} kind must be one of: {', '.join(allowed_kinds)}",
            code="unsupported_ref_kind",
            field=field,
            allowed_kinds=allowed_kinds,
        )
    return parsed.as_dict()


def _refs(
    values: tuple[str, ...],
    *,
    field: str = "reference",
    allowed_kinds: tuple[str, ...] = _REFERENCE_KINDS,
) -> list[dict[str, str]]:
    return [_ref(value, field=field, allowed_kinds=allowed_kinds) for value in values]


def _idem(function: Any) -> Any:
    return click.option(
        "--idempotency-key",
        help="Stable retry identity for this canonical write.",
    )(function)


def _expected(function: Any) -> Any:
    function = click.argument("expected_revision")(function)
    return click.argument("entity_id")(function)


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


@click.group(cls=CommonsGroup)
@click.version_option(__version__, prog_name="agent-commons")
@click.option(
    "--repo",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("."),
    show_default=True,
    help="Project repository containing .agent-commons.",
)
@click.option(
    "--session-id",
    envvar="AGENT_COMMONS_SESSION_ID",
    help="Explicit active Agent Commons session identity.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--state-root",
    type=click.Path(path_type=Path, file_okay=False),
    envvar="AGENT_COMMONS_STATE_ROOT",
    help="Explicit operator-authorized operational state directory.",
)
@click.option(
    "--state-base",
    type=click.Path(path_type=Path, file_okay=False),
    envvar="AGENT_COMMONS_STATE_BASE",
    help="Operator state base; each workspace uses a workspace-ID namespace.",
)
@click.option(
    "--read-only",
    is_flag=True,
    help="Inspect existing state without creating cache, index, session, or claim files.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    repo: Path,
    session_id: str | None,
    json_output: bool,
    state_root: Path | None,
    state_base: Path | None,
    read_only: bool,
) -> None:
    """Coordinate heterogeneous coding agents through one immutable commons."""

    require_supported_platform()
    root_source = ctx.get_parameter_source("state_root")
    base_source = ctx.get_parameter_source("state_base")
    root_rank = 2 if root_source is ParameterSource.COMMANDLINE else 1
    base_rank = 2 if base_source is ParameterSource.COMMANDLINE else 1
    if state_root is not None and state_base is not None:
        if base_rank > root_rank:
            state_root = None
        else:
            state_base = None
    if state_root is not None:
        selected_source = (
            "flag:state-root"
            if root_source is ParameterSource.COMMANDLINE
            else "env:AGENT_COMMONS_STATE_ROOT"
        )
    elif state_base is not None:
        selected_source = (
            "flag:state-base"
            if base_source is ParameterSource.COMMANDLINE
            else "env:AGENT_COMMONS_STATE_BASE"
        )
    else:
        selected_source = "default"
    ctx.obj = CLIState(
        repo.expanduser().resolve(),
        session_id,
        json_output,
        state_root.expanduser().resolve() if state_root is not None else None,
        state_base.expanduser().resolve() if state_base is not None else None,
        selected_source,
        read_only,
    )


@cli.command("init")
@click.option("--integration", multiple=True, type=click.Choice(("codex", "claude")))
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
        raise ValidationError("init is unavailable in read-only mode")
    selected = integration or ("codex", "claude")
    state.emit(
        CommonsManager.initialize(
            state.repo,
            integrations=selected,
            workspace_name=workspace_name,
            replace_onboarding=replace_onboarding,
            replace_skills=replace_skills,
        )
    )


@cli.command("ui")
@click.option(
    "--port",
    type=click.IntRange(0, 65535),
    default=0,
    show_default=True,
    help="Loopback port; 0 selects an ephemeral port.",
)
@click.option("--no-browser", is_flag=True, help="Do not open a browser automatically.")
@click.option(
    "--enable-writes",
    is_flag=True,
    help="Allow the role panel to record canonical events under the active session.",
)
@click.option(
    "--role-catalog",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Operator-owned catalogue of selectable skills and tools.",
)
@click.option(
    "--enable-catalog-editing",
    is_flag=True,
    help="Also allow editing that catalogue from the panel. Separate from "
    "--enable-writes: this changes what child processes may run.",
)
@click.option(
    "--profile-config",
    "profile_config",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Operator-owned runtime profile config; required to launch runs from the panel.",
)
@click.option(
    "--enable-launch",
    is_flag=True,
    help="Allow the panel to launch a delegation (spawn a provider run). Separate "
    "from --enable-writes: this starts a billable subscription process.",
)
@click.pass_obj
def ui_command(
    state: CLIState,
    port: int,
    no_browser: bool,
    enable_writes: bool,
    role_catalog: Path | None,
    enable_catalog_editing: bool,
    profile_config: Path | None,
    enable_launch: bool,
) -> None:
    """Serve a local view of this workspace on loopback; read-only by default.

    With --enable-writes the role panel records through the same
    ``CommonsManager`` the CLI and MCP adapter use.  There is no second write
    path.  It binds 127.0.0.1 only; there is deliberately no --host flag.
    """

    try:
        from agent_commons.ui.context import UIContext
        from agent_commons.ui.server import serve
    except ImportError as exc:  # pragma: no cover - exercised with a stubbed import
        raise ConfigurationError("UI support is not installed; install agent-commons[ui]") from exc

    if enable_catalog_editing and role_catalog is None:
        raise ConfigurationError(
            "--enable-catalog-editing requires --role-catalog naming the file to edit"
        )
    writer_session_id = None
    if enable_writes:
        # Writes need the operator's own session, resolved and checked exactly
        # as the CLI resolves it, so the UI cannot record under a nameless actor.
        # Refuse here rather than at the first POST: the failure belongs where
        # the operator is still watching, not in a browser tab an hour later.
        if state.session_id is None:
            error = ValidationError("--enable-writes requires an explicitly selected session")
            error.code = "session_not_selected"  # type: ignore[attr-defined]
            error.details = {"selection": "AGENT_COMMONS_SESSION_ID or --session-id"}  # type: ignore[attr-defined]
            raise error
        writer_session_id = str(state.manager().show_session(state.session_id)["session_id"])

    if role_catalog is not None:
        # Load the catalogue once at startup so an invalid file fails here, while
        # the operator is still at the terminal, rather than turning every
        # catalogue view into an opaque 500 later (round 2, product; round 1 L7).
        from agent_commons.catalog import load_role_catalog

        load_role_catalog(role_catalog, workspace_root=state.repo)

    if enable_launch:
        if not enable_writes:
            raise ConfigurationError("--enable-launch requires --enable-writes")
        if profile_config is None:
            raise ConfigurationError(
                "--enable-launch requires --profile-config naming the runtime profile config"
            )
        # Fail here, at the terminal, if the profile config is invalid — not at
        # the first launch from a browser tab.
        from agent_commons.services.delegation_runtime import load_runtime_configuration

        load_runtime_configuration(profile_config, workspace_root=state.repo)

    context = UIContext(
        state.repo,
        state_root=state.state_root,
        state_base=state.state_base,
        state_source=state.state_source,
        writer_session_id=writer_session_id,
        catalog_path=role_catalog,
        catalog_editing=enable_catalog_editing,
        profile_config=profile_config,
        launch_enabled=enable_launch,
    )

    def emit(bound_port: int, token: str) -> None:
        url = f"http://127.0.0.1:{bound_port}/#t={token}"
        if state.json_output:
            state.emit(
                {
                    "schema": STARTED_SCHEMA,
                    "url": url,
                    "port": bound_port,
                    "token": token,
                    "repo": str(state.repo),
                    "read_only": not enable_writes,
                    "writer_session_id": writer_session_id,
                    "catalog_editing": enable_catalog_editing,
                }
            )
            return
        click.echo(
            "Agent Commons UI — read-only" if not enable_writes else "Agent Commons UI — writable"
        )
        click.echo(f"  url     {url}")
        click.echo(f"  bind    127.0.0.1:{bound_port} — loopback only; there is no --host flag")
        if enable_writes:
            click.echo(f"  writes  enabled as {writer_session_id} through CommonsManager")
            click.echo("          anyone holding this token writes as that session")
        else:
            click.echo("  writes  disabled — this server records no canonical event")
        if enable_catalog_editing:
            click.echo(f"  catalog editable at {role_catalog}")
            click.echo("          adding a skill changes what delegated runs are told to do")
        click.echo("  trust   loopback reachability alone is not authentication")
        click.echo("  note    the token is not stored on disk; opening a browser exposes")
        click.echo("          the URL to other processes of this user via the process list")
        click.echo("  stop    Ctrl-C")

    serve(context, port=port, open_browser=not no_browser, emit=emit)


@cli.group("chat")
def chat_group() -> None:
    """The main conversation between a person and the top of the org."""


@chat_group.command("open")
@click.option("--subject", required=True)
@click.option("--message", required=True, help="The task, in your own words.")
@click.option("--objective-id", help="Bind this chat to the objective it is about.")
@_idem
@click.pass_obj
def chat_open(
    state: CLIState,
    subject: str,
    message: str,
    objective_id: str | None,
    idempotency_key: str | None,
) -> None:
    """Open the main chat, addressed to every role that answers to you."""

    state.emit(
        state.manager().open_engagement(
            subject=subject,
            body=message,
            objective_id=objective_id,
            idempotency_key=idempotency_key,
        )
    )


@chat_group.command("show")
@click.option("--include-resolved", is_flag=True)
@click.pass_obj
def chat_show(state: CLIState, include_resolved: bool) -> None:
    """Show the main chats with their messages and who they address."""

    state.emit(state.manager().list_engagements(include_resolved=include_resolved))


@chat_group.command("say")
@click.argument("thread_id")
@click.argument("expected_revision")
@click.option("--message", required=True)
@_idem
@click.pass_obj
def chat_say(
    state: CLIState,
    thread_id: str,
    expected_revision: str,
    message: str,
    idempotency_key: str | None,
) -> None:
    """Post a message into a main chat."""

    state.emit(
        state.manager().reply_thread(
            thread_id, expected_revision, body=message, idempotency_key=idempotency_key
        )
    )


@cli.command("search")
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


@cli.command("support")
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
        "broker_release_stage": "experimental_manual_opt_in",
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


@cli.group("session")
def session_group() -> None:
    """Manage explicit writer identities and ownership nonces."""


@session_group.command("start")
@click.option("--stable-instance-id", required=True)
@click.option("--principal", required=True)
@click.option("--client", required=True)
@click.option("--software", required=True)
@click.option("--role", required=True)
@click.option("--capability", multiple=True)
@click.option("--model-family")
@click.option("--model")
@click.option("--source-producer-json")
@click.option("--ttl-seconds", type=click.IntRange(min=1), default=8 * 3600, show_default=True)
@click.option("--shell-export", type=click.Choice(("zsh", "bash", "fish")))
@click.pass_obj
def session_start(
    state: CLIState,
    stable_instance_id: str,
    principal: str,
    client: str,
    software: str,
    role: str,
    capability: tuple[str, ...],
    model_family: str | None,
    model: str | None,
    source_producer_json: str | None,
    ttl_seconds: int,
    shell_export: str | None,
) -> None:
    """Open an explicit session; preserve the returned nonce privately."""

    if shell_export is not None and state.json_output:
        raise ValidationError("--shell-export cannot be combined with --json")
    producer = (
        _json_object(source_producer_json, "source_producer_json") if source_producer_json else None
    )
    result = state.manager().start_session(
        stable_instance_id=stable_instance_id,
        principal=principal,
        client=client,
        software=software,
        role=role,
        capabilities=capability,
        model_family=model_family,
        model=model,
        source_producer=producer,
        ttl_seconds=ttl_seconds,
    )
    if shell_export is None:
        state.emit(result)
        return
    session_id = shlex.quote(str(result["session_id"]))
    nonce = shlex.quote(str(result["nonce"]))
    if shell_export == "fish":
        click.echo(f"set -gx AGENT_COMMONS_SESSION_ID {session_id}")
        click.echo(f"set -gx AGENT_COMMONS_SESSION_NONCE {nonce}")
    else:
        click.echo(f"export AGENT_COMMONS_SESSION_ID={session_id}")
        click.echo(f"export AGENT_COMMONS_SESSION_NONCE={nonce}")


@session_group.command("show")
@click.argument("target_session_id", required=False)
@click.pass_obj
def session_show(state: CLIState, target_session_id: str | None) -> None:
    """Show one session, or active sessions when no identity is selected."""

    state.emit(state.manager().show_session(target_session_id))


@session_group.command("current")
@click.pass_obj
def session_current(state: CLIState) -> None:
    """Show only the explicitly selected session, without its ownership nonce."""

    if state.session_id is None:
        error = ValidationError("no Agent Commons session is explicitly selected")
        error.code = "session_not_selected"  # type: ignore[attr-defined]
        error.details = {"selection": "AGENT_COMMONS_SESSION_ID or --session-id"}  # type: ignore[attr-defined]
        raise error
    state.emit(state.manager().show_session(state.session_id))


@session_group.command("heartbeat")
@click.option("--nonce", required=True)
@click.option("--ttl-seconds", type=click.IntRange(min=1), default=8 * 3600, show_default=True)
@click.pass_obj
def session_heartbeat(state: CLIState, nonce: str, ttl_seconds: int) -> None:
    """Renew the selected session and rotate its ownership nonce."""

    state.emit(state.manager().heartbeat_session(nonce=nonce, ttl_seconds=ttl_seconds))


@session_group.command("end")
@click.option("--nonce", required=True)
@click.pass_obj
def session_end(state: CLIState, nonce: str) -> None:
    """Close the selected session."""

    state.emit(state.manager().end_session(nonce=nonce))


@cli.command("orient")
@click.option("--max-items", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--verbose", is_flag=True, help="Include detailed projected records.")
@click.option(
    "--fresh", is_flag=True, help="Replay and validate canonical files instead of SQLite."
)
@click.pass_obj
def orient_command(state: CLIState, max_items: int, verbose: bool, fresh: bool) -> None:
    """Read the role-filtered current workspace brief."""

    state.emit(state.manager().orient(max_items=max_items, verbose=verbose, fresh=fresh))


@cli.command("inbox")
@click.option("--max-items", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--verbose", is_flag=True, help="Include detailed projected records.")
@click.option(
    "--fresh", is_flag=True, help="Replay and validate canonical files instead of SQLite."
)
@click.pass_obj
def inbox_command(state: CLIState, max_items: int, verbose: bool, fresh: bool) -> None:
    """Read open discussions and handoffs addressed to this session."""

    state.emit(state.manager().inbox(max_items=max_items, verbose=verbose, fresh=fresh))


@cli.group("objective")
def objective_group() -> None:
    """Manage project objectives."""


@objective_group.command("create")
@click.option("--title", required=True)
@click.option("--description", required=True)
@click.option("--acceptance-criterion", multiple=True, required=True)
@_idem
@click.pass_obj
def objective_create(
    state: CLIState,
    title: str,
    description: str,
    acceptance_criterion: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Create an objective."""

    state.emit(
        state.manager().create_objective(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criterion,
            idempotency_key=idempotency_key,
        )
    )


@objective_group.command("list")
@click.pass_obj
def objective_list(state: CLIState) -> None:
    """List projected objectives."""

    state.emit(state.manager().list_objectives())


@objective_group.command("revise")
@click.argument("objective_id")
@click.argument("expected_revision")
@click.option("--changes-json", required=True)
@_idem
@click.pass_obj
def objective_revise(
    state: CLIState,
    objective_id: str,
    expected_revision: str,
    changes_json: str,
    idempotency_key: str | None,
) -> None:
    """Revise objective fields with exact revision CAS."""

    state.emit(
        state.manager().revise_objective(
            objective_id,
            expected_revision,
            changes=_json_object(changes_json, "changes_json"),
            idempotency_key=idempotency_key,
        )
    )


@objective_group.command("close")
@click.argument("objective_id")
@click.argument("expected_revision")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def objective_close(
    state: CLIState,
    objective_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Close an objective with exact revision CAS."""

    state.emit(
        state.manager().close_objective(
            objective_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("task")
def task_group() -> None:
    """Manage the task lifecycle."""


@task_group.command("create")
@click.option("--title", required=True)
@click.option("--description", required=True)
@click.option("--acceptance-criterion", multiple=True, required=True)
@click.option("--priority", default="normal", show_default=True)
@click.option("--dependency", multiple=True)
@_idem
@click.pass_obj
def task_create(
    state: CLIState,
    title: str,
    description: str,
    acceptance_criterion: tuple[str, ...],
    priority: str,
    dependency: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Create a task."""

    state.emit(
        state.manager().create_task(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criterion,
            priority=priority,
            dependencies=dependency,
            idempotency_key=idempotency_key,
        )
    )


@task_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def task_list(state: CLIState, state_filter: str | None) -> None:
    """List tasks, optionally filtered by projected state."""

    state.emit(state.manager().list_tasks(state=state_filter))


@task_group.command("revise")
@_expected
@click.option("--changes-json", required=True)
@_idem
@click.pass_obj
def task_revise(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    changes_json: str,
    idempotency_key: str | None,
) -> None:
    """Revise task wording with exact revision CAS."""

    state.emit(
        state.manager().revise_task(
            entity_id,
            expected_revision,
            changes=_json_object(changes_json, "changes_json"),
            idempotency_key=idempotency_key,
        )
    )


def _simple_task_transition(
    state: CLIState,
    method: str,
    entity_id: str,
    expected_revision: str,
    idempotency_key: str | None,
    **fields: Any,
) -> None:
    manager = state.manager()
    state.emit(
        getattr(manager, method)(
            entity_id,
            expected_revision,
            idempotency_key=idempotency_key,
            **fields,
        )
    )


@task_group.command("take")
@_expected
@_idem
@click.pass_obj
def task_take(
    state: CLIState, entity_id: str, expected_revision: str, idempotency_key: str | None
) -> None:
    """Assign a ready task to this session."""

    _simple_task_transition(state, "take_task", entity_id, expected_revision, idempotency_key)


@task_group.command("start")
@_expected
@_idem
@click.pass_obj
def task_start(
    state: CLIState, entity_id: str, expected_revision: str, idempotency_key: str | None
) -> None:
    """Start a ready or assigned task."""

    _simple_task_transition(state, "start_task", entity_id, expected_revision, idempotency_key)


@task_group.command("block")
@_expected
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def task_block(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Block an active task."""

    _simple_task_transition(
        state, "block_task", entity_id, expected_revision, idempotency_key, reason=reason
    )


@task_group.command("unblock")
@_expected
@click.option("--resolution", required=True)
@_idem
@click.pass_obj
def task_unblock(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    resolution: str,
    idempotency_key: str | None,
) -> None:
    """Return a blocked task to active work."""

    _simple_task_transition(
        state,
        "unblock_task",
        entity_id,
        expected_revision,
        idempotency_key,
        resolution=resolution,
    )


def _task_with_artifacts(
    state: CLIState,
    method: str,
    entity_id: str,
    expected_revision: str,
    summary: str,
    artifact_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    state.emit(
        getattr(state.manager(), method)(
            entity_id,
            expected_revision,
            summary=summary,
            artifact_refs=_refs(artifact_ref, field="artifact_ref", allowed_kinds=("artifact",)),
            idempotency_key=idempotency_key,
        )
    )


@task_group.command("complete")
@_expected
@click.option("--summary", required=True)
@click.option("--artifact-ref", multiple=True)
@_idem
@click.pass_obj
def task_complete(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    artifact_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Complete active implementation work."""

    _task_with_artifacts(
        state,
        "complete_task",
        entity_id,
        expected_revision,
        summary,
        artifact_ref,
        idempotency_key,
    )


@task_group.command("submit")
@_expected
@click.option("--summary", required=True)
@click.option("--artifact-ref", multiple=True)
@_idem
@click.pass_obj
def task_submit(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    artifact_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Submit completed work for review."""

    _task_with_artifacts(
        state,
        "submit_task",
        entity_id,
        expected_revision,
        summary,
        artifact_ref,
        idempotency_key,
    )


@task_group.command("accept")
@_expected
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def task_accept(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Accept independently approved submitted work."""

    _simple_task_transition(
        state, "accept_task", entity_id, expected_revision, idempotency_key, summary=summary
    )


@task_group.command("cancel")
@_expected
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def task_cancel(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Cancel an eligible task."""

    _simple_task_transition(
        state, "cancel_task", entity_id, expected_revision, idempotency_key, reason=reason
    )


@task_group.command("reopen")
@_expected
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def task_reopen(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Reopen a terminal or submitted task."""

    _simple_task_transition(
        state, "reopen_task", entity_id, expected_revision, idempotency_key, reason=reason
    )


_DELEGATION_PROFILES = (
    "codex-builder",
    "codex-independent-reviewer",
    "claude-builder",
    "claude-independent-reviewer",
)
_DELEGATION_PURPOSES = ("implementation", "independent_review", "verification")
_DELEGATION_REASON_CODES = (
    "provider_unavailable",
    "provider_auth",
    "rate_limited",
    "policy_denied",
    "launch_failed",
    "runtime_error",
    "invalid_result",
    "integrity_error",
    "budget_exhausted",
    "orphaned",
    "unknown",
)
_BOUNDED_DELEGATION_LIMITS_EXAMPLE = (
    '{"max_depth":0,"wall_time_seconds":600,"max_attempts":1,'
    '"max_concurrency":1,"budget":{"unit":"provider_units","limit":1}}'
)
_DELEGATION_CREATE_HELP = (
    "Create a requested delegation bound to one exact target revision.\n\n\b\n"
    "This release launches leaf workers only: max_depth must be 0.\n\n\b\n"
    "Complete bounded limits example (copy/paste):\n"
    f"  --limits-json '{_BOUNDED_DELEGATION_LIMITS_EXAMPLE}'"
)


@cli.group("delegation")
def delegation_group() -> None:
    """Record bounded cross-agent delegation lifecycles."""


@delegation_group.command("create", help=_DELEGATION_CREATE_HELP)
@click.option("--target-ref", required=True)
@click.option("--target-revision", required=True)
@click.option("--target-profile", type=click.Choice(_DELEGATION_PROFILES), required=True)
@click.option("--purpose", type=click.Choice(_DELEGATION_PURPOSES), required=True)
@click.option(
    "--limits-json",
    required=True,
    help="Bounded time, attempt, concurrency, provider budget, and max_depth=0 object.",
)
@click.option("--parent-delegation-id")
@click.option(
    "--on-behalf-of",
    "on_behalf_of_agent_id",
    help="Standing role this run acts for; the run itself stays terminal and unnamed.",
)
@_idem
@click.pass_obj
def delegation_create(
    state: CLIState,
    target_ref: str,
    target_revision: str,
    target_profile: str,
    purpose: str,
    limits_json: str,
    parent_delegation_id: str | None,
    on_behalf_of_agent_id: str | None,
    idempotency_key: str | None,
) -> None:
    """Create a requested delegation bound to one exact target revision."""

    result = state.manager().create_delegation(
        target_ref=_ref(target_ref, field="target_ref"),
        target_revision=target_revision,
        target_profile=target_profile,
        purpose=purpose,
        limits=_json_object(limits_json, "limits_json"),
        parent_delegation_id=parent_delegation_id,
        on_behalf_of_agent_id=on_behalf_of_agent_id,
        idempotency_key=idempotency_key,
    )
    state.emit(result)
    if state.json_output:
        return

    delegation_id = str(result["entity_ref"]["id"])
    requested_revision = str(result["revision"])
    requester_session_id = str(state.session_id)
    command_prefix = [sys.executable, "-m", "agent_commons", "--repo", str(state.repo)]
    if state.state_root is not None:
        command_prefix.extend(("--state-root", str(state.state_root)))
    elif state.state_base is not None:
        command_prefix.extend(("--state-base", str(state.state_base)))
    preflight = [
        *command_prefix,
        "broker",
        "preflight",
        target_profile,
        "--purpose",
        purpose,
    ]
    launch = [
        *command_prefix,
        "broker",
        "run",
        delegation_id,
        requested_revision,
        "--idempotency-key",
        f"launch-{delegation_id}",
    ]
    click.echo()
    click.echo(
        "Only the canonical requester session may launch this delegation, "
        "and it must remain active."
    )
    click.echo("Safe next actions:")
    click.echo("  1. In a shell where that session is still active, select it:")
    click.echo(f"     export AGENT_COMMONS_SESSION_ID={shlex.quote(requester_session_id)}")
    click.echo(
        "  If you use a non-default operator config, add "
        "--profile-config /absolute/path/runtime.yaml to both commands below."
    )
    click.echo("  2. Check the provider/runtime without consuming an attempt:")
    click.echo(f"     {shlex.join(preflight)}")
    click.echo("  3. Launch this exact delegation revision:")
    click.echo(f"     {shlex.join(launch)}")


_GRANT_LEVELS = ("deny", "ask", "auto")


@cli.group("agent")
def agent_group() -> None:
    """Manage standing roles: the staff index behind the situational runs."""


@agent_group.command("create")
@click.option("--name", required=True, help="Job title shown in the staff index.")
@click.option("--profile", "profile_id", type=click.Choice(_DELEGATION_PROFILES), required=True)
@click.option("--rationale", required=True, help="Why this role exists; kept forever.")
@click.option(
    "--context-mode",
    type=click.Choice(("fresh", "accumulated")),
    default="fresh",
    show_default=True,
    help="'fresh' never hands the role its own earlier verdict on the same subject.",
)
@click.option("--create-roles", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option("--retire-roles", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option("--open-links", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option(
    "--turnover-budget",
    type=int,
    help="Creations plus retirements allowed below this role. Required to grant either.",
)
@click.option(
    "--retire-with-task",
    "retire_with_task",
    help="Retire this role automatically when that task is accepted or cancelled.",
)
@click.option("--skill", "skills", multiple=True, help="Operator catalogue skill id.")
@click.option("--tool", "tools", multiple=True, help="Narrow the profile's tools to these ids.")
@click.option("--template", is_flag=True, help="Store as a reusable preset that never runs.")
@click.option("--created-by-agent", "created_by_agent_id", help="Proposing role, when confirming.")
@_idem
@click.pass_obj
def agent_create(
    state: CLIState,
    name: str,
    profile_id: str,
    rationale: str,
    context_mode: str,
    create_roles: str,
    retire_roles: str,
    open_links: str,
    turnover_budget: int | None,
    retire_with_task: str | None,
    skills: tuple[str, ...],
    tools: tuple[str, ...],
    template: bool,
    created_by_agent_id: str | None,
    idempotency_key: str | None,
) -> None:
    """Create a standing role. Grants default to deny; a lifetime beats a grant."""

    lifetime = (
        {"kind": "task_scoped", "task_id": retire_with_task}
        if retire_with_task
        else {"kind": "persistent"}
    )
    if "auto" in {create_roles, retire_roles, open_links} and not state.json_output:
        # Do not accept `auto` silently: it is currently withheld and every role
        # runs at an effective ceiling of `ask`.  Say so for a human at the
        # terminal (round 2, all lenses).  A --json consumer reads the effective
        # level off the record instead, so the machine output stays clean.
        click.echo(
            "note: the automatic grant level is currently withheld; this role's "
            "effective level is 'ask' (a human confirms each staff action). "
            "See docs/adr/0009-agents-as-first-class-roles.md.",
            err=True,
        )
    state.emit(
        state.manager().create_agent(
            name=name,
            profile_id=profile_id,
            rationale=rationale,
            context_mode=context_mode,
            grants={
                "create_roles": create_roles,
                "retire_roles": retire_roles,
                "open_links": open_links,
            },
            turnover_budget=turnover_budget,
            lifetime=lifetime,
            skills=skills,
            tool_allowlist=tools,
            template=template,
            created_by_agent_id=created_by_agent_id,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("propose")
@click.option("--name", required=True)
@click.option("--profile", "profile_id", type=click.Choice(_DELEGATION_PROFILES), required=True)
@click.option("--rationale", required=True)
@click.option(
    "--context-mode",
    type=click.Choice(("fresh", "accumulated")),
    default="fresh",
    show_default=True,
)
@click.option("--create-roles", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option("--retire-roles", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option("--open-links", type=click.Choice(_GRANT_LEVELS), default="deny", show_default=True)
@click.option("--turnover-budget", type=int)
@_idem
@click.pass_obj
def agent_propose(
    state: CLIState,
    name: str,
    profile_id: str,
    rationale: str,
    context_mode: str,
    create_roles: str,
    retire_roles: str,
    open_links: str,
    turnover_budget: int | None,
    idempotency_key: str | None,
) -> None:
    """Ask a human for a role this session's role may not record itself.

    Only a session running as a role can propose one.  The proposal grants
    nothing until `agent approve` confirms it.
    """

    state.emit(
        state.manager().propose_agent(
            name=name,
            profile_id=profile_id,
            rationale=rationale,
            context_mode=context_mode,
            grants={
                "create_roles": create_roles,
                "retire_roles": retire_roles,
                "open_links": open_links,
            },
            turnover_budget=turnover_budget,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("proposals")
@click.pass_obj
def agent_proposals(state: CLIState) -> None:
    """List open role proposals awaiting a human decision."""

    state.emit(state.manager().list_agent_proposals())


@agent_group.command("approve")
@click.argument("thread_id")
@_idem
@click.pass_obj
def agent_approve(state: CLIState, thread_id: str, idempotency_key: str | None) -> None:
    """Create exactly the role a proposal asked for, crediting its proposer."""

    state.emit(state.manager().approve_agent_proposal(thread_id, idempotency_key=idempotency_key))


@agent_group.command("decline")
@click.argument("thread_id")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def agent_decline(
    state: CLIState,
    thread_id: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Reject a role proposal and remove it from the open queue."""

    state.emit(
        state.manager().decline_agent_proposal(
            thread_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("list")
@click.option("--include-retired", is_flag=True, help="Show roles that have left service.")
@click.pass_obj
def agent_list(state: CLIState, include_retired: bool) -> None:
    """List standing roles with their effective authority and provenance."""

    state.emit(state.manager().list_agents(include_retired=include_retired))


@agent_group.command("show")
@click.argument("agent_id")
@click.pass_obj
def agent_show(state: CLIState, agent_id: str) -> None:
    """Show one role, its lineage, effective grants, and what blocks retirement."""

    state.emit(state.manager().get_agent(agent_id))


@agent_group.command("reconfigure")
@click.argument("agent_id")
@click.argument("expected_revision")
@click.option("--changes-json", required=True, help="Mutable fields only; identity is immutable.")
@click.option("--reason", required=True)
@click.option(
    "--isolation-downgrade-reason",
    help="Required to weaken context isolation; needs the agent:isolation_downgrade capability.",
)
@_idem
@click.pass_obj
def agent_reconfigure(
    state: CLIState,
    agent_id: str,
    expected_revision: str,
    changes_json: str,
    reason: str,
    isolation_downgrade_reason: str | None,
    idempotency_key: str | None,
) -> None:
    """Change a role's mutable settings. Isolation may be strengthened freely."""

    state.emit(
        state.manager().reconfigure_agent(
            agent_id,
            expected_revision,
            changes=_json_object(changes_json, "changes_json"),
            reason=reason,
            isolation_downgrade_reason=isolation_downgrade_reason,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("retire")
@click.argument("agent_id")
@click.option("--expected-revision")
@click.option("--reason", required=True)
@click.option(
    "--cascade",
    is_flag=True,
    help="Also retire every role this one created, transitively, or refuse as a whole.",
)
@_idem
@click.pass_obj
def agent_retire(
    state: CLIState,
    agent_id: str,
    expected_revision: str | None,
    reason: str,
    cascade: bool,
    idempotency_key: str | None,
) -> None:
    """Take a role out of service. Nothing is deleted; the history stays."""

    state.emit(
        state.manager().retire_agent(
            agent_id,
            expected_revision,
            reason=reason,
            cascade=cascade,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("link")
@click.option("--from-agent", "from_agent_id", required=True)
@click.option("--to-agent", "to_agent_id", required=True)
@click.option(
    "--action",
    "allowed_action",
    type=click.Choice(("ask", "handoff_work")),
    default="ask",
    show_default=True,
    help="What the link permits: a bounded question, or handing work over.",
)
@click.option(
    "--deadline-seconds",
    type=int,
    default=None,
    help="Optional intended horizon, recorded only. Nothing enforces it: a link "
    "lives until it is explicitly closed.",
)
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def agent_link(
    state: CLIState,
    from_agent_id: str,
    to_agent_id: str,
    allowed_action: str,
    deadline_seconds: int | None,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Open a link between two roles; it lives until explicitly closed."""

    state.emit(
        state.manager().open_agent_link(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            allowed_action=allowed_action,
            deadline_seconds=deadline_seconds,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@agent_group.command("unlink")
@click.argument("link_id")
@click.argument("expected_revision")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def agent_unlink(
    state: CLIState,
    link_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Close a temporary link before its deadline."""

    state.emit(
        state.manager().close_agent_link(
            link_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@delegation_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def delegation_list(state: CLIState, state_filter: str | None) -> None:
    """List delegations, optionally filtered by projected state."""

    state.emit(state.manager().list_delegations(state=state_filter))


@delegation_group.command("show")
@click.argument("delegation_id")
@click.pass_obj
def delegation_show(state: CLIState, delegation_id: str) -> None:
    """Show one delegation with bounded local runtime diagnostics."""

    manager = state.manager()
    service = DelegationRuntimeService(
        manager,
        attempts=AttemptStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=True,
        ),
        tool_audit=TerminalToolAuditStore(
            manager.paths.state_root,
            security_policy=manager.policy,
            read_only=True,
        ),
    )
    state.emit(service.show_delegation(delegation_id))


def _delegation_transition(
    state: CLIState,
    method: str,
    entity_id: str,
    expected_revision: str,
    idempotency_key: str | None,
    **fields: Any,
) -> None:
    state.emit(
        getattr(state.manager(), method)(
            entity_id,
            expected_revision,
            idempotency_key=idempotency_key,
            **fields,
        )
    )


@delegation_group.command("start")
@_expected
@click.option("--child-session-id", required=True)
@click.option("--attempt", type=click.IntRange(min=1, max=32), default=1, show_default=True)
@_idem
@click.pass_obj
def delegation_start(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    child_session_id: str,
    attempt: int,
    idempotency_key: str | None,
) -> None:
    """Bind a distinct child session and activate a requested delegation."""

    _delegation_transition(
        state,
        "start_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        child_session_id=child_session_id,
        attempt=attempt,
    )


@delegation_group.command("input-needed")
@_expected
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def delegation_input_needed(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Mark an active delegation as waiting for bounded operator input."""

    _delegation_transition(
        state,
        "mark_delegation_input_needed",
        entity_id,
        expected_revision,
        idempotency_key,
        summary=summary,
    )


@delegation_group.command("resume")
@_expected
@click.option("--resolution", required=True)
@_idem
@click.pass_obj
def delegation_resume(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    resolution: str,
    idempotency_key: str | None,
) -> None:
    """Resume a delegation after its input request is resolved."""

    _delegation_transition(
        state,
        "resume_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        resolution=resolution,
    )


@delegation_group.command("succeed")
@_expected
@click.option("--summary", required=True)
@click.option("--result-ref", multiple=True, required=True)
@_idem
@click.pass_obj
def delegation_succeed(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    result_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Finish an active delegation with canonical result references."""

    _delegation_transition(
        state,
        "succeed_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        summary=summary,
        result_refs=_refs(result_ref, field="result_ref"),
    )


@delegation_group.command("fail")
@_expected
@click.option("--reason-code", type=click.Choice(_DELEGATION_REASON_CODES), required=True)
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def delegation_fail(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason_code: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Finish a delegation with a stable failure classification."""

    _delegation_transition(
        state,
        "fail_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        reason_code=reason_code,
        summary=summary,
    )


@delegation_group.command("cancel")
@_expected
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def delegation_cancel(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Cancel a non-terminal delegation."""

    _delegation_transition(
        state,
        "cancel_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        reason=reason,
    )


@delegation_group.command("recover")
@_expected
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def delegation_recover(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Recover requested work after its requester becomes unavailable."""

    _delegation_transition(
        state,
        "recover_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        reason=reason,
    )


@delegation_group.command("time-out")
@_expected
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def delegation_time_out(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Finish a delegation after its hard wall-time limit."""

    _delegation_transition(
        state,
        "time_out_delegation",
        entity_id,
        expected_revision,
        idempotency_key,
        summary=summary,
    )


@delegation_group.command("needs-operator")
@_expected
@click.option("--reason-code", type=click.Choice(_DELEGATION_REASON_CODES), required=True)
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def delegation_needs_operator(
    state: CLIState,
    entity_id: str,
    expected_revision: str,
    reason_code: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Finish a delegation that requires explicit operator recovery."""

    _delegation_transition(
        state,
        "mark_delegation_needs_operator",
        entity_id,
        expected_revision,
        idempotency_key,
        reason_code=reason_code,
        summary=summary,
    )


def _runtime_service(
    state: CLIState,
    profile_config: Path | None,
    telemetry: str = "none",
    role_catalog: Path | None = None,
) -> DelegationRuntimeService:
    manager = state.manager()
    config = load_runtime_configuration(profile_config, workspace_root=state.repo)
    # One catalogue for the panel and the launcher.  The panel edits the file
    # named by `agent-commons ui --role-catalog`; the launcher read the `catalog:`
    # key inside the runtime profile config, so a skill added through the form
    # refused the next launch (M8, 2026-08-10 review).  `--role-catalog` here
    # names the same file with the same flag, and overrides the config key when
    # both are present.
    catalog = config.catalog
    if role_catalog is not None:
        from agent_commons.catalog import load_role_catalog

        catalog = load_role_catalog(role_catalog, workspace_root=state.repo)
    runner = None
    if config.demo:
        # No provider is launched; the demo runner completes runs so the loop
        # closes without a subscription or a billable process.
        from agent_commons.runtime.demo import DemoRunner

        runner = DemoRunner(manager.paths.state_root)
    return DelegationRuntimeService(
        manager,
        profiles=config.profiles,
        operator_limits=config.limits,
        catalog=catalog,
        runner=runner,
        telemetry=telemetry_sink(telemetry, manager),
    )


def _role_catalog_option(function: Any) -> Any:
    return click.option(
        "--role-catalog",
        type=click.Path(path_type=Path, dir_okay=False),
        help="Operator catalogue of selectable skills and tools; the same file "
        "the panel edits with `agent-commons ui --role-catalog`.",
    )(function)


def _profile_config(function: Any) -> Any:
    return click.option(
        "--profile-config",
        type=click.Path(path_type=Path, dir_okay=False, exists=True),
        help="Operator-owned strict YAML profile configuration.",
    )(function)


@cli.group("broker")
def broker_group() -> None:
    """Run and recover the optional allowlisted local delegation broker."""


@broker_group.command("profiles")
@_profile_config
@click.pass_obj
def broker_profiles(state: CLIState, profile_config: Path | None) -> None:
    """List configured profile capabilities without exposing executable argv."""

    config = load_runtime_configuration(profile_config, workspace_root=state.repo)
    state.emit(profile_summaries(config.profiles, config.limits))


@broker_group.command("preflight")
@click.argument("profile_id", type=click.Choice(tuple(item.value for item in BuiltinProfileId)))
@click.option(
    "--purpose",
    type=click.Choice(_DELEGATION_PURPOSES),
    help="Worker purpose; defaults to implementation or independent_review from the profile.",
)
@_profile_config
@click.pass_obj
def broker_preflight(
    state: CLIState,
    profile_id: str,
    purpose: str | None,
    profile_config: Path | None,
) -> None:
    """Check fixed provider flags and MCP startup without consuming a delegation attempt."""

    profiles = load_profile_registry(profile_config, workspace_root=state.repo)
    manager = CommonsManager(
        state.repo,
        state_root=state.state_root,
        state_base=state.state_base,
        state_source=state.state_source,
        read_only=True,
    )
    result = preflight_profile(
        profiles,
        profile_id,
        workspace_root=state.repo,
        state_root=manager.paths.state_root,
        purpose=purpose,
    )
    state.emit(result)
    if not result["ok"]:
        raise click.exceptions.Exit(2)


@broker_group.command("canary")
@click.option(
    "--profile",
    "profile_id",
    type=click.Choice(
        [
            BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER.value,
            BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER.value,
        ]
    ),
    default=BuiltinProfileId.CLAUDE_INDEPENDENT_REVIEWER.value,
    show_default=True,
)
@click.option(
    "--confirm-provider-run",
    is_flag=True,
    required=True,
    help="Confirm one real provider attempt that may use subscription or billable capacity.",
)
@click.option(
    "--wall-time-seconds",
    type=click.IntRange(min=30, max=1800),
    default=300,
    show_default=True,
)
@_profile_config
@click.pass_obj
def broker_canary(
    state: CLIState,
    profile_id: str,
    confirm_provider_run: bool,
    wall_time_seconds: int,
    profile_config: Path | None,
) -> None:
    """Run one isolated real-provider terminal-tool compatibility canary."""

    if not confirm_provider_run:  # pragma: no cover - Click enforces the required flag.
        raise ValidationError("provider canary requires explicit provider-run confirmation")
    config = load_runtime_configuration(profile_config, workspace_root=state.repo)
    canary = (
        run_codex_compatibility_canary
        if profile_id == BuiltinProfileId.CODEX_INDEPENDENT_REVIEWER.value
        else run_claude_compatibility_canary
    )
    result = canary(
        config.profiles,
        operator_limits=config.limits,
        wall_time_seconds=wall_time_seconds,
    )
    state.emit(result)
    if not result["ok"]:
        raise click.exceptions.Exit(2)


@broker_group.command("stop")
@click.argument("delegation_id")
@click.option("--force", is_flag=True, help="Send SIGKILL instead of SIGTERM.")
@_profile_config
@click.pass_obj
def broker_stop(
    state: CLIState,
    delegation_id: str,
    force: bool,
    profile_config: Path | None,
) -> None:
    """Terminate the live provider process group for one delegation.

    No canonical outcome is written here.  Run `broker reconcile` afterwards; it
    refuses to record an outcome while the process is still alive, so the ledger
    never claims work stopped before it actually did.
    """

    service = _runtime_service(state, profile_config)
    state.emit(service.stop_provider(delegation_id, force=force))


@broker_group.command("attempts")
@click.option(
    "--diagnostic",
    is_flag=True,
    help="Add fixed recovery hints to the sanitized local failure diagnostics.",
)
@_profile_config
@click.pass_obj
def broker_attempts(
    state: CLIState,
    diagnostic: bool,
    profile_config: Path | None,
) -> None:
    """List attempts with bounded, sanitized local failure diagnostics."""

    state.emit(_runtime_service(state, profile_config).list_attempts(diagnostic=diagnostic))


@broker_group.command("run")
@click.argument("delegation_id")
@click.argument("expected_revision")
@click.option("--idempotency-key", required=True, help="Stable launch-specific retry identity.")
@click.option("--retry", is_flag=True, help="Retry only a proven pre-start failed attempt.")
@click.option(
    "--telemetry",
    type=click.Choice(("none", "local", "otel")),
    default="none",
    show_default=True,
)
@_profile_config
@_role_catalog_option
@click.pass_obj
def broker_run(
    state: CLIState,
    delegation_id: str,
    expected_revision: str,
    idempotency_key: str,
    retry: bool,
    telemetry: str,
    profile_config: Path | None,
    role_catalog: Path | None,
) -> None:
    """Launch one requested delegation; no arbitrary command or prompt is accepted."""

    state.emit(
        _runtime_service(state, profile_config, telemetry, role_catalog).run(
            delegation_id,
            expected_revision,
            idempotency_key=idempotency_key,
            retry=retry,
        )
    )


@broker_group.command("reconcile")
@click.option(
    "--telemetry",
    type=click.Choice(("none", "local", "otel")),
    default="none",
    show_default=True,
)
@_profile_config
@click.pass_obj
def broker_reconcile(
    state: CLIState,
    telemetry: str,
    profile_config: Path | None,
) -> None:
    """Fail ambiguous post-crash attempts closed to canonical needs_operator."""

    state.emit(_runtime_service(state, profile_config, telemetry).reconcile())


@cli.group("thread")
def thread_group() -> None:
    """Exchange targeted questions, proposals, critiques, and replies."""


@thread_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def thread_list(state: CLIState, state_filter: str | None) -> None:
    """List discussions before opening duplicate work."""

    state.emit(state.manager().list_threads(state=state_filter))


@thread_group.command("open")
@click.option(
    "--type",
    "thread_type",
    required=True,
    type=click.Choice(
        (
            "question",
            "proposal",
            "critique",
            "risk",
            "help_request",
            "review_discussion",
            "decision_request",
        )
    ),
)
@click.option("--subject", required=True)
@click.option("--desired-outcome", required=True)
@click.option("--to", multiple=True, required=True)
@click.option("--related-ref", multiple=True)
@_idem
@click.pass_obj
def thread_open(
    state: CLIState,
    thread_type: str,
    subject: str,
    desired_outcome: str,
    to: tuple[str, ...],
    related_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Open a targeted discussion."""

    state.emit(
        state.manager().open_thread(
            thread_type=thread_type,
            subject=subject,
            desired_outcome=desired_outcome,
            to=to,
            related_refs=_refs(related_ref, field="related_ref"),
            idempotency_key=idempotency_key,
        )
    )


@thread_group.command("reply")
@click.argument("thread_id")
@click.argument("expected_revision")
@click.option("--body", required=True)
@_idem
@click.pass_obj
def thread_reply(
    state: CLIState,
    thread_id: str,
    expected_revision: str,
    body: str,
    idempotency_key: str | None,
) -> None:
    """Reply with exact thread revision CAS."""

    state.emit(
        state.manager().reply_thread(
            thread_id,
            expected_revision,
            body=body,
            idempotency_key=idempotency_key,
        )
    )


@thread_group.command("resolve")
@click.argument("thread_id")
@click.argument("expected_revision")
@click.option(
    "--resolution",
    required=True,
    type=click.Choice(("resolved", "accepted", "rejected", "deferred", "archived")),
)
@click.option("--summary", required=True)
@_idem
@click.pass_obj
def thread_resolve(
    state: CLIState,
    thread_id: str,
    expected_revision: str,
    resolution: str,
    summary: str,
    idempotency_key: str | None,
) -> None:
    """Resolve an open discussion."""

    state.emit(
        state.manager().resolve_thread(
            thread_id,
            expected_revision,
            resolution=resolution,
            summary=summary,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("artifact")
def artifact_group() -> None:
    """Register immutable metadata for project-local artifact revisions."""


@artifact_group.command("list")
@click.pass_obj
def artifact_list(state: CLIState) -> None:
    """List registered artifact identities and current revisions."""

    state.emit(state.manager().list_artifacts())


def _artifact_options(function: Any) -> Any:
    function = click.option("--metadata-json", default="{}", show_default=True)(function)
    function = click.option(
        "--classification",
        type=click.Choice(("public", "internal", "restricted")),
        default="internal",
        show_default=True,
    )(function)
    return click.option("--media-type", default="application/octet-stream", show_default=True)(
        function
    )


@artifact_group.command("register")
@click.argument("source", type=click.Path(path_type=Path, dir_okay=False))
@_artifact_options
@_idem
@click.pass_obj
def artifact_register(
    state: CLIState,
    source: Path,
    metadata_json: str,
    classification: str,
    media_type: str,
    idempotency_key: str | None,
) -> None:
    """Hash and register an artifact without copying its content."""

    state.emit(
        state.manager().register_artifact(
            source,
            media_type=media_type,
            classification=classification,
            metadata=_json_object(metadata_json, "metadata_json"),
            idempotency_key=idempotency_key,
        )
    )


@artifact_group.command("revise")
@click.argument("artifact_id")
@click.argument("expected_revision")
@click.argument("source", type=click.Path(path_type=Path, dir_okay=False))
@_artifact_options
@_idem
@click.pass_obj
def artifact_revise(
    state: CLIState,
    artifact_id: str,
    expected_revision: str,
    source: Path,
    metadata_json: str,
    classification: str,
    media_type: str,
    idempotency_key: str | None,
) -> None:
    """Register a new immutable revision under the same artifact identity."""

    state.emit(
        state.manager().revise_artifact(
            artifact_id,
            expected_revision,
            source,
            media_type=media_type,
            classification=classification,
            metadata=_json_object(metadata_json, "metadata_json"),
            idempotency_key=idempotency_key,
        )
    )


@cli.group("review")
def review_group() -> None:
    """Request and complete revision-bound reviews."""


@review_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def review_list(state: CLIState, state_filter: str | None) -> None:
    """List requested and completed reviews."""

    state.emit(state.manager().list_reviews(state=state_filter))


@review_group.command("request")
@click.option("--target-ref", required=True)
@click.option("--target-revision", required=True)
@click.option("--criterion", multiple=True, required=True)
@click.option("--independent/--not-independent", default=True, show_default=True)
@_idem
@click.pass_obj
def review_request(
    state: CLIState,
    target_ref: str,
    target_revision: str,
    criterion: tuple[str, ...],
    independent: bool,
    idempotency_key: str | None,
) -> None:
    """Request a review of one exact target revision."""

    state.emit(
        state.manager().request_review(
            target_ref=_ref(target_ref, field="target_ref"),
            target_revision=target_revision,
            criteria=criterion,
            independent=independent,
            idempotency_key=idempotency_key,
        )
    )


@review_group.command("complete")
@click.argument("review_id")
@click.argument("expected_revision")
@click.option("--target-revision", required=True)
@click.option(
    "--verdict",
    required=True,
    type=click.Choice(("approved", "changes_requested", "rejected", "abstained")),
)
@click.option("--summary", required=True)
@click.option("--evidence-ref", multiple=True)
@_idem
@click.pass_obj
def review_complete(
    state: CLIState,
    review_id: str,
    expected_revision: str,
    target_revision: str,
    verdict: str,
    summary: str,
    evidence_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Complete a review; independent requests reject their requester session."""

    state.emit(
        state.manager().complete_review(
            review_id,
            expected_revision,
            target_revision=target_revision,
            verdict=verdict,
            summary=summary,
            evidence_refs=_refs(evidence_ref, field="evidence_ref"),
            idempotency_key=idempotency_key,
        )
    )


@cli.group("verification")
def verification_group() -> None:
    """Record evidence-backed verification outcomes."""


@verification_group.command("list")
@click.pass_obj
def verification_list(state: CLIState) -> None:
    """List recorded verification evidence."""

    state.emit(state.manager().list_verifications())


@verification_group.command("record")
@click.option("--target-ref", required=True)
@click.option("--target-revision", required=True)
@click.option("--claim", required=True)
@click.option("--evidence-ref", multiple=True, required=True)
@click.option("--method", required=True)
@click.option("--outcome", required=True)
@_idem
@click.pass_obj
def verification_record(
    state: CLIState,
    target_ref: str,
    target_revision: str,
    claim: str,
    evidence_ref: tuple[str, ...],
    method: str,
    outcome: str,
    idempotency_key: str | None,
) -> None:
    """Record a verification bound to exact evidence and target revision."""

    state.emit(
        state.manager().record_verification(
            target_ref=_ref(target_ref, field="target_ref"),
            target_revision=target_revision,
            claim=claim,
            evidence_refs=_refs(evidence_ref, field="evidence_ref"),
            method=method,
            outcome=outcome,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("finding")
def finding_group() -> None:
    """Manage observations and promoted findings."""


@finding_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def finding_list(state: CLIState, state_filter: str | None) -> None:
    """List findings and their current truth status."""

    state.emit(state.manager().list_findings(state=state_filter))


@finding_group.command("report")
@click.option("--summary", required=True)
@click.option("--severity", required=True)
@click.option("--evidence-ref", multiple=True)
@_idem
@click.pass_obj
def finding_report(
    state: CLIState,
    summary: str,
    severity: str,
    evidence_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Report a working-layer finding."""

    state.emit(
        state.manager().report_finding(
            summary=summary,
            severity=severity,
            evidence_refs=_refs(evidence_ref, field="evidence_ref"),
            idempotency_key=idempotency_key,
        )
    )


@finding_group.command("promote")
@click.argument("finding_id")
@click.argument("expected_revision")
@click.option("--summary", required=True)
@click.option("--evidence-ref", multiple=True, required=True)
@_idem
@click.pass_obj
def finding_promote(
    state: CLIState,
    finding_id: str,
    expected_revision: str,
    summary: str,
    evidence_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Promote a finding to verified truth with evidence."""

    state.emit(
        state.manager().promote_finding(
            finding_id,
            expected_revision,
            summary=summary,
            evidence_refs=_refs(evidence_ref, field="evidence_ref"),
            idempotency_key=idempotency_key,
        )
    )


@finding_group.command("contest")
@click.argument("finding_id")
@click.argument("expected_revision")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def finding_contest(
    state: CLIState,
    finding_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Contest a reported or verified finding."""

    state.emit(
        state.manager().contest_finding(
            finding_id,
            expected_revision,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@finding_group.command("resolve")
@click.argument("finding_id")
@click.argument("expected_revision")
@click.option("--resolution", required=True)
@_idem
@click.pass_obj
def finding_resolve(
    state: CLIState,
    finding_id: str,
    expected_revision: str,
    resolution: str,
    idempotency_key: str | None,
) -> None:
    """Resolve a finding dispute."""

    state.emit(
        state.manager().resolve_finding(
            finding_id,
            expected_revision,
            resolution=resolution,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("decision")
def decision_group() -> None:
    """Propose and resolve scoped decisions."""


@decision_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def decision_list(state: CLIState, state_filter: str | None) -> None:
    """List scoped decisions and their current state."""

    state.emit(state.manager().list_decisions(state=state_filter))


@decision_group.command("propose")
@click.option("--scope", required=True)
@click.option("--proposal", required=True)
@click.option("--alternative", multiple=True)
@_idem
@click.pass_obj
def decision_propose(
    state: CLIState,
    scope: str,
    proposal: str,
    alternative: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Propose one decision for a stable scope."""

    state.emit(
        state.manager().propose_decision(
            scope=scope,
            proposal=proposal,
            alternatives=alternative,
            idempotency_key=idempotency_key,
        )
    )


@decision_group.command("accept")
@click.argument("decision_id")
@click.argument("expected_revision")
@click.option("--rationale", required=True)
@click.option("--evidence-ref", multiple=True)
@click.option("--dissent", multiple=True)
@_idem
@click.pass_obj
def decision_accept(
    state: CLIState,
    decision_id: str,
    expected_revision: str,
    rationale: str,
    evidence_ref: tuple[str, ...],
    dissent: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Accept a decision without permitting conflicting accepted scopes."""

    state.emit(
        state.manager().accept_decision(
            decision_id,
            expected_revision,
            rationale=rationale,
            evidence_refs=_refs(evidence_ref, field="evidence_ref"),
            dissent=dissent,
            idempotency_key=idempotency_key,
        )
    )


def _decision_reason_transition(
    state: CLIState,
    method: str,
    decision_id: str,
    expected_revision: str,
    field: str,
    value: str,
    idempotency_key: str | None,
) -> None:
    state.emit(
        getattr(state.manager(), method)(
            decision_id,
            expected_revision,
            idempotency_key=idempotency_key,
            **{field: value},
        )
    )


@decision_group.command("reject")
@click.argument("decision_id")
@click.argument("expected_revision")
@click.option("--rationale", required=True)
@_idem
@click.pass_obj
def decision_reject(
    state: CLIState,
    decision_id: str,
    expected_revision: str,
    rationale: str,
    idempotency_key: str | None,
) -> None:
    """Reject a proposed or deferred decision."""

    _decision_reason_transition(
        state,
        "reject_decision",
        decision_id,
        expected_revision,
        "rationale",
        rationale,
        idempotency_key,
    )


@decision_group.command("defer")
@click.argument("decision_id")
@click.argument("expected_revision")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def decision_defer(
    state: CLIState,
    decision_id: str,
    expected_revision: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Defer a proposed decision."""

    _decision_reason_transition(
        state,
        "defer_decision",
        decision_id,
        expected_revision,
        "reason",
        reason,
        idempotency_key,
    )


@decision_group.command("supersede")
@click.argument("decision_id")
@click.argument("expected_revision")
@click.option("--replacement-decision-id", required=True)
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def decision_supersede(
    state: CLIState,
    decision_id: str,
    expected_revision: str,
    replacement_decision_id: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Supersede an accepted decision with a same-scope replacement."""

    state.emit(
        state.manager().supersede_decision(
            decision_id,
            expected_revision,
            replacement_decision_id=replacement_decision_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("handoff")
def handoff_group() -> None:
    """Transfer precise context and next actions to recipients."""


@handoff_group.command("list")
@click.option("--state", "state_filter")
@click.pass_obj
def handoff_list(state: CLIState, state_filter: str | None) -> None:
    """List all handoffs, including those not addressed to this session."""

    state.emit(state.manager().list_handoffs(state=state_filter))


@handoff_group.command("create")
@click.option("--to", multiple=True, required=True)
@click.option("--completed", multiple=True)
@click.option("--active", multiple=True)
@click.option("--next-action", multiple=True, required=True)
@click.option("--blocker", multiple=True)
@click.option("--risk", multiple=True)
@click.option("--open-question", multiple=True)
@click.option("--related-ref", multiple=True)
@_idem
@click.pass_obj
def handoff_create(
    state: CLIState,
    to: tuple[str, ...],
    completed: tuple[str, ...],
    active: tuple[str, ...],
    next_action: tuple[str, ...],
    blocker: tuple[str, ...],
    risk: tuple[str, ...],
    open_question: tuple[str, ...],
    related_ref: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Create a targeted handoff."""

    state.emit(
        state.manager().create_handoff(
            to=to,
            completed=completed,
            active=active,
            next_actions=next_action,
            blockers=blocker,
            risks=risk,
            open_questions=open_question,
            related_refs=_refs(related_ref, field="related_ref"),
            idempotency_key=idempotency_key,
        )
    )


@handoff_group.command("acknowledge")
@click.argument("handoff_id")
@click.argument("expected_revision")
@click.option("--note", required=True)
@_idem
@click.pass_obj
def handoff_acknowledge(
    state: CLIState,
    handoff_id: str,
    expected_revision: str,
    note: str,
    idempotency_key: str | None,
) -> None:
    """Acknowledge a handoff addressed to this session, role, or wildcard."""

    state.emit(
        state.manager().acknowledge_handoff(
            handoff_id,
            expected_revision,
            note=note,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("claim")
def claim_group() -> None:
    """Manage advisory operational resource claims."""


@claim_group.command("acquire")
@click.option("--resource", multiple=True, required=True)
@click.option("--mode", type=click.Choice(("exclusive", "advisory")), default="exclusive")
@click.option("--ttl-seconds", type=click.IntRange(min=1), default=2 * 3600, show_default=True)
@click.option("--description", default="")
@_idem
@click.pass_obj
def claim_acquire(
    state: CLIState,
    resource: tuple[str, ...],
    mode: str,
    ttl_seconds: int,
    description: str,
    idempotency_key: str | None,
) -> None:
    """Acquire a claim and preserve its returned nonce privately."""

    state.emit(
        state.manager().acquire_claim(
            resource,
            mode=mode,
            ttl_seconds=ttl_seconds,
            description=description,
            idempotency_key=idempotency_key,
        )
    )


@claim_group.command("list")
@click.option("--all", "include_inactive", is_flag=True)
@click.pass_obj
def claim_list(state: CLIState, include_inactive: bool) -> None:
    """List active claims, or all audit state with --all."""

    state.emit(state.manager().list_claims(active_only=not include_inactive))


@claim_group.command("renew")
@click.argument("claim_id")
@click.option("--nonce", required=True)
@click.option("--ttl-seconds", type=click.IntRange(min=1), required=True)
@click.pass_obj
def claim_renew(state: CLIState, claim_id: str, nonce: str, ttl_seconds: int) -> None:
    """Renew an owned claim and rotate its nonce."""

    state.emit(state.manager().renew_claim(claim_id, nonce=nonce, ttl_seconds=ttl_seconds))


@claim_group.command("release")
@click.argument("claim_id")
@click.option("--nonce", required=True)
@click.pass_obj
def claim_release(state: CLIState, claim_id: str, nonce: str) -> None:
    """Release an owned claim."""

    state.emit(state.manager().release_claim(claim_id, nonce=nonce))


@claim_group.command("break")
@click.argument("claim_id")
@click.option("--reason", required=True)
@click.pass_obj
def claim_break(state: CLIState, claim_id: str, reason: str) -> None:
    """Break a claim using an explicit claim:break capability."""

    state.emit(state.manager().break_claim(claim_id, reason=reason))


@cli.group("event")
def event_group() -> None:
    """Inspect and maintain immutable canonical history."""


@event_group.command("show")
@click.argument("event_id")
@click.pass_obj
def event_show(state: CLIState, event_id: str) -> None:
    """Show an immutable event and the hash required for correction."""

    state.emit(state.manager().show_event(event_id))


@event_group.command("correct")
@click.argument("target_event_id")
@click.option("--expected-target-sha256", required=True)
@click.option("--replacement-payload-json", required=True)
@click.option("--supersedes", "superseded", multiple=True)
@_idem
@click.pass_obj
def event_correct(
    state: CLIState,
    target_event_id: str,
    expected_target_sha256: str,
    replacement_payload_json: str,
    superseded: tuple[str, ...],
    idempotency_key: str | None,
) -> None:
    """Correct one event payload without rewriting history."""

    state.emit(
        state.manager().correct_event(
            target_event_id,
            expected_target_sha256=expected_target_sha256,
            replacement_payload=_json_object(replacement_payload_json, "replacement_payload_json"),
            superseded_correction_event_ids=superseded,
            idempotency_key=idempotency_key,
        )
    )


@event_group.command("invalidate")
@click.argument("target_event_id")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def event_invalidate(
    state: CLIState,
    target_event_id: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Invalidate an erroneous canonical event."""

    state.emit(
        state.manager().invalidate_event(
            target_event_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@event_group.command("revoke")
@click.argument("invalidation_event_id")
@click.option("--reason", required=True)
@_idem
@click.pass_obj
def event_revoke(
    state: CLIState,
    invalidation_event_id: str,
    reason: str,
    idempotency_key: str | None,
) -> None:
    """Revoke an active invalidation."""

    state.emit(
        state.manager().revoke_invalidation(
            invalidation_event_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    )


@cli.group("receipt")
def receipt_group() -> None:
    """Inspect and recover checkout-scoped idempotency receipts."""


@receipt_group.command("status")
@click.pass_obj
def receipt_status(state: CLIState) -> None:
    """Classify receipts, ledger anchors, tombstones, and migration state."""

    state.emit(state.manager().receipt_status())


@receipt_group.command("reconcile")
@click.option("--adopt-legacy-orphan", multiple=True)
@click.option("--prepare-rollback", is_flag=True)
@click.pass_obj
def receipt_reconcile(
    state: CLIState,
    adopt_legacy_orphan: tuple[str, ...],
    prepare_rollback: bool,
) -> None:
    """Rebuild post-commit receipts or prepare one checkout for v1 rollback."""

    if prepare_rollback and adopt_legacy_orphan:
        raise click.UsageError("--prepare-rollback cannot be combined with --adopt-legacy-orphan")
    state.emit(
        state.manager().reconcile_idempotency_receipts(
            adopt_legacy_orphans=adopt_legacy_orphan,
            prepare_rollback=prepare_rollback,
        )
    )


@receipt_group.command("abandon")
@click.argument("key_digest")
@click.option("--reason", required=True)
@click.pass_obj
def receipt_abandon(state: CLIState, key_digest: str, reason: str) -> None:
    """Tombstone an orphan receipt with the receipt:abandon capability."""

    state.emit(
        state.manager().abandon_idempotency_receipt(
            key_digest,
            reason=reason,
        )
    )


@cli.group("views")
def views_group() -> None:
    """Build human-readable derived views."""


@views_group.command("build")
@click.pass_obj
def views_build(state: CLIState) -> None:
    """Rebuild Markdown views from canonical state."""

    state.emit(state.manager().build_views())


@cli.group("index")
def index_group() -> None:
    """Manage the disposable SQLite projection."""


@index_group.command("rebuild")
@click.pass_obj
def index_rebuild(state: CLIState) -> None:
    """Rebuild the disposable index from canonical files."""

    state.emit(state.manager().rebuild_index())


@cli.command("doctor")
@click.pass_context
def doctor_command(ctx: click.Context) -> None:
    """Validate history, receipts, manifests, projections, coordination, and index."""

    state: CLIState = ctx.obj
    report = state.manager().doctor()
    state.emit(report)
    if not report["ok"]:
        ctx.exit(2)


if __name__ == "__main__":  # pragma: no cover
    cli()
