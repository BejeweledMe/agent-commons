"""Explicit operator recovery for the private, cross-project panel registry."""

from pathlib import Path

import click

from agent_commons.errors import ValidationError
from agent_commons.ui.project_registry import ProjectRegistry

from ._shared import CLIState


@click.group("project")
def project_group() -> None:
    """Inspect panel projects and recover a missing operational-state binding."""


@project_group.command("list")
@click.pass_obj
def list_projects(state: CLIState) -> None:
    """List project IDs, revisions and availability without private paths."""
    state.emit(ProjectRegistry().list_projects(read_only=True))


@project_group.command("recover-state")
@click.argument("project_id")
@click.argument("expected_revision")
@click.option(
    "--state-root",
    "replacement",
    type=click.Path(path_type=Path),
    required=True,
    help="Existing, owned exact state root; never a state base or a new directory.",
)
@click.option("--idempotency-key", required=True)
@click.pass_obj
def recover_state(
    state: CLIState,
    project_id: str,
    expected_revision: str,
    replacement: Path,
    idempotency_key: str,
) -> None:
    """Rebind a missing state root after verifying the registered workspace.

    Stop panels using this project before recovery, then restart the common
    panel. This changes the private registry only; it does not restore lost data.
    """
    if state.read_only:
        raise ValidationError("Project recovery is unavailable in read-only mode.")
    state.emit(
        ProjectRegistry().recover_state(
            project_id,
            expected_revision,
            state_root=replacement,
            idempotency_key=idempotency_key,
        )
    )
