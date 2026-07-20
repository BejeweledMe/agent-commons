"""Click command line interface for Agent Commons."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from agent_commons.core.refs import parse_ref
from agent_commons.errors import CommonsError, ValidationError
from agent_commons.services import CommonsManager


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
        if isinstance(state, CLIState) and state.json_output:
            state.emit(
                {
                    "ok": False,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            ctx.exit(1)
        raise click.ClickException(str(exc)) from exc


@dataclass
class CLIState:
    repo: Path
    session_id: str | None
    json_output: bool

    def manager(self) -> CommonsManager:
        return CommonsManager(self.repo, session_id=self.session_id)

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


def _refs(values: tuple[str, ...]) -> list[dict[str, str]]:
    return [parse_ref(value).as_dict() for value in values]


def _idem(function: Any) -> Any:
    return click.option(
        "--idempotency-key",
        help="Stable retry identity for this canonical write.",
    )(function)


def _expected(function: Any) -> Any:
    function = click.argument("expected_revision")(function)
    return click.argument("entity_id")(function)


@click.group(cls=CommonsGroup)
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
@click.pass_context
def cli(
    ctx: click.Context,
    repo: Path,
    session_id: str | None,
    json_output: bool,
) -> None:
    """Coordinate heterogeneous coding agents through one immutable commons."""

    ctx.obj = CLIState(repo.expanduser().resolve(), session_id, json_output)


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
) -> None:
    """Open an explicit session; preserve the returned nonce privately."""

    producer = (
        _json_object(source_producer_json, "source_producer_json") if source_producer_json else None
    )
    state.emit(
        state.manager().start_session(
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
    )


@session_group.command("show")
@click.argument("target_session_id", required=False)
@click.pass_obj
def session_show(state: CLIState, target_session_id: str | None) -> None:
    """Show one session, or active sessions when no identity is selected."""

    state.emit(state.manager().show_session(target_session_id))


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
@click.pass_obj
def orient_command(state: CLIState, max_items: int) -> None:
    """Read the role-filtered current workspace brief."""

    state.emit(state.manager().orient(max_items=max_items))


@cli.command("inbox")
@click.option("--max-items", type=click.IntRange(min=1), default=20, show_default=True)
@click.pass_obj
def inbox_command(state: CLIState, max_items: int) -> None:
    """Read open discussions and handoffs addressed to this session."""

    state.emit(state.manager().inbox(max_items=max_items))


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
            artifact_refs=_refs(artifact_ref),
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
            related_refs=_refs(related_ref),
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
            target_ref=parse_ref(target_ref).as_dict(),
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
            evidence_refs=_refs(evidence_ref),
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
            target_ref=parse_ref(target_ref).as_dict(),
            target_revision=target_revision,
            claim=claim,
            evidence_refs=_refs(evidence_ref),
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
            evidence_refs=_refs(evidence_ref),
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
            evidence_refs=_refs(evidence_ref),
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
            evidence_refs=_refs(evidence_ref),
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
            related_refs=_refs(related_ref),
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
